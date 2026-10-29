"""Analysis_server panel: every analysis server/share plus one combined table."""
from pathlib import Path, PurePosixPath
import queue
import re
import threading
import time
import os
import shutil
import sys
import tkinter as tk
from tkinter import ttk, messagebox
try:
    from . import catalog, inventory_views
except ImportError:
    import catalog, inventory_views

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ui_theme  # noqa: E402


def inventory(path, root=None):
    with catalog.connect(path, readonly=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='rack_samples'").fetchone():
            roots = [r[0] for r in db.execute('SELECT DISTINCT root FROM rack_samples')
                     if root is None or catalog.root_identity(r[0]) == catalog.root_identity(root)]
            placeholders = ','.join('?' for _ in roots)
            rows = [dict(row) for row in db.execute(
                f'SELECT * FROM rack_samples WHERE root IN ({placeholders}) ORDER BY last_success,experiment,sample',
                roots)] if roots else []
            if rows:
                return list({(catalog.root_identity(row['root']), row['experiment'], row['sample']): row
                             for row in rows}.values())
    return [r for r in inventory_views.analysis_inventory(path, root=root)
            if (r['server'].casefold() == 'rackstation' if root is None else catalog.root_identity(r['root']) == catalog.root_identity(root))
            and PurePosixPath(r['folder']).name == r['sample'] + '_10k_Sample'
            and r['folder_state'] == '存在' and r['t_tdms'] is not None]


def entries(path, cancel):
    """Use directory metadata already returned by SMB; never descend recursively."""
    with os.scandir(path) as iterator:
        for entry in iterator:
            if cancel.is_set():
                raise catalog.ScanCancelled('中止しました。取得済みサンプルは保存されています。')
            if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
                continue
            yield entry


def sample_counts(target, cancel, bnal_paths=None):
    """Direct counts of one sample's T as a dict of rack_samples columns.

    TDMS bytes cost no extra request on Windows (DirEntry.stat() reuses the listing).
    BNAL@ folder names come from the same T listing; pass a dict as bnal_paths to collect {target: path}.
    T/stocked (original measurement files moved out of T) and T/ANAL/stocked (ANAL files set aside to restart
    an interrupted BNAL run) are listed only when present; their counts are None when the folder does not exist."""
    tdms = bnals = t_bytes = 0
    anal = t_stocked = anal_stocked = None
    t_keys = []
    for entry in entries(target, cancel):
        if entry.name.lower().endswith('.tdms') and entry.is_file(follow_symlinks=False):
            tdms += 1
            t_bytes += entry.stat(follow_symlinks=False).st_size
            t_keys.append(measurement_key(entry.name))
        elif entry.is_dir(follow_symlinks=False):
            if entry.name.lower().startswith('bnal@'):
                bnals += 1
                if bnal_paths is not None:
                    bnal_paths[entry.name[len('BNAL@'):]] = entry.path
            elif entry.name.lower() == 'anal':
                anal = entry.path
            elif entry.name.lower() == 'stocked':
                t_stocked = entry.path
    anal_tdms = anal_bytes = anal_numbers = None
    if anal is not None:
        anal_tdms = anal_bytes = 0
        anal_numbers = set()
        for entry in entries(anal, cancel):
            if entry.name.lower().endswith('.tdms') and entry.is_file(follow_symlinks=False):
                anal_tdms += 1
                anal_bytes += entry.stat(follow_symlinks=False).st_size
                anal_numbers.add(measurement_key(entry.name))
            elif entry.name.lower() == 'stocked' and entry.is_dir(follow_symlinks=False):
                anal_stocked = entry.path
    t_stocked_keys = None if t_stocked is None else tdms_keys(t_stocked, cancel)
    anal_stocked_keys = None if anal_stocked is None else tdms_keys(anal_stocked, cancel)
    analysed = (anal_numbers or set()) | set(anal_stocked_keys or [])
    resume, missing = missing_numbers(t_keys + (t_stocked_keys or []), analysed)
    last = max((key for key in analysed if isinstance(key, int)), default=None)
    return dict(t_tdms=tdms, anal_tdms=anal_tdms, bnal_folders=bnals, t_bytes=t_bytes, anal_bytes=anal_bytes,
                t_stocked_tdms=None if t_stocked_keys is None else len(t_stocked_keys),
                anal_stocked_tdms=None if anal_stocked_keys is None else len(anal_stocked_keys),
                anal_distinct=None if anal_numbers is None else len(anal_numbers),
                anal_resume=resume, anal_missing=missing, anal_last=last, anal_missing_checked=1)


NUMBER = re.compile(r'#(\d+)\.tdms$', re.I)
# Originals carry the number right after the sample folder name and more '#' later
# ('KSN3_10k_Sample#001 D_... ANAN#1Pex1n2_..._EXSV#10_....tdms'); ANAL ends with it ('..._Sample#001.tdms').
SAMPLE_NUMBER = re.compile(r'_Sample#(\d+)', re.I)


def measurement_key(name):
    match = SAMPLE_NUMBER.search(name) or NUMBER.search(name)
    return int(match[1]) if match else name


def tdms_keys(folder, cancel):
    """'#NNN' number (or the name when it has none) of every direct .tdms in one listing."""
    return [measurement_key(entry.name) for entry in entries(folder, cancel)
            if entry.name.lower().endswith('.tdms') and entry.is_file(follow_symlinks=False)]


def tdms_numbers(folder, cancel):
    """(files, distinct '#NNN' numbers) of direct .tdms in one listing; nested folders are not entered.

    BNAL makes one file per ANAL file present, so the file count is the progress. Fewer distinct numbers
    than files is only a hint: a re-run, or two ANAL generations mixed in one folder."""
    keys = tdms_keys(folder, cancel)
    return len(keys), len(set(keys))


def number_ranges(numbers):
    """[1, 2, 3, 7, 9, 10] -> '#001-#003,#007,#009-#010'."""
    parts, numbers = [], sorted(numbers)
    for index, number in enumerate(numbers):
        if index and number == numbers[index - 1] + 1:
            parts[-1][1] = number
        else:
            parts.append([number, number])
    return ','.join(f'#{a:03d}' if a == b else f'#{a:03d}-#{b:03d}' for a, b in parts)


def missing_numbers(originals, analysed):
    """(resume number, missing ranges) of the original '#NNN' numbers not yet in ANAL (+ ANAL/stocked).

    ANAL keeps the original number, so the first missing number is where an interrupted ANAL run resumes.
    Both are None when nothing is missing; names without a number are ignored."""
    missing = {key for key in originals if isinstance(key, int)} - {key for key in analysed if isinstance(key, int)}
    return (min(missing), number_ranges(missing)) if missing else (None, None)


def save_bnals(db, root_key, experiment, sample, bnal_paths, counted, checked_at):
    """Replace the sample's BNAL@ names; targets not counted this time keep their previous counts and date.

    counted: {target: (files, distinct measurements)} from tdms_numbers."""
    previous = {row['target']: (row['tdms'], row['tdms_distinct'], row['tdms_checked']) for row in db.execute(
        'SELECT target, tdms, tdms_distinct, tdms_checked FROM rack_bnals WHERE root=? AND experiment=? AND sample=?',
        (root_key, experiment, sample))}
    db.execute('DELETE FROM rack_bnals WHERE root=? AND experiment=? AND sample=?', (root_key, experiment, sample))
    db.executemany('INSERT INTO rack_bnals(root,experiment,sample,target,tdms,tdms_distinct,tdms_checked,last_seen) '
                   'VALUES(?,?,?,?,?,?,?,?)',
                   [(root_key, experiment, sample, name,
                     *((*counted[name], checked_at) if name in counted else previous.get(name, (None, None, None))),
                     checked_at) for name in bnal_paths])


def bnal_inventory(path, root=None):
    """Saved BNAL@ targets per sample (no server access); tdms is None until counted."""
    with catalog.connect(path, readonly=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rack_bnals'").fetchone():
            return []
        rows = [dict(row) for row in db.execute('SELECT * FROM rack_bnals ORDER BY experiment,sample,target')]
    if root is None:
        return rows
    return [row for row in rows if catalog.root_identity(row['root']) == catalog.root_identity(root)]


def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rack_samples (
        root TEXT NOT NULL, experiment TEXT NOT NULL, sample TEXT NOT NULL,
        t_tdms INTEGER, anal_tdms INTEGER, bnal_folders INTEGER,
        last_success TEXT, status TEXT, PRIMARY KEY(root,experiment,sample))""")
    existing = {row[1] for row in db.execute('PRAGMA table_info(rack_samples)')}
    for column in ('t_bytes', 'anal_bytes', 'anal_stocked_tdms', 't_stocked_tdms', 'anal_distinct'):
        if column not in existing:
            db.execute(f'ALTER TABLE rack_samples ADD COLUMN {column} INTEGER')
    # anal_resume: first original '#NNN' missing from ANAL (+ ANAL/stocked); anal_missing: every missing number
    # as ranges; anal_last: largest number in ANAL (missing numbers below it are gaps, not the unfinished tail).
    # anal_missing_checked tells 'nothing missing' apart from rows scanned before these columns existed.
    for column, kind in (('anal_resume', 'INTEGER'), ('anal_missing', 'TEXT'), ('anal_last', 'INTEGER'),
                         ('anal_missing_checked', 'INTEGER')):
        if column not in existing:
            db.execute(f'ALTER TABLE rack_samples ADD COLUMN {column} {kind}')
    db.execute("""CREATE TABLE IF NOT EXISTS rack_roots (
        root TEXT PRIMARY KEY, server TEXT, status TEXT, error TEXT,
        last_success TEXT, last_attempt TEXT, volume_used INTEGER, volume_total INTEGER)""")
    # last_seen: when the name was last listed in T; tdms_checked: when its direct .tdms were last counted.
    db.execute("""CREATE TABLE IF NOT EXISTS rack_bnals (
        root TEXT NOT NULL, experiment TEXT NOT NULL, sample TEXT NOT NULL, target TEXT NOT NULL,
        tdms INTEGER, tdms_checked TEXT, last_seen TEXT,
        PRIMARY KEY(root,experiment,sample,target))""")
    # tdms_distinct: distinct '#NNN' numbers; fewer than tdms means a re-run or mixed ANAL generations.
    if 'tdms_distinct' not in {row[1] for row in db.execute('PRAGMA table_info(rack_bnals)')}:
        db.execute('ALTER TABLE rack_bnals ADD COLUMN tdms_distinct INTEGER')


def root_states(path):
    """Saved per-share state keyed by root identity; reading never touches the servers."""
    with catalog.connect(path, readonly=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rack_roots'").fetchone():
            return {}
        return {catalog.root_identity(row['root']): dict(row) for row in db.execute('SELECT * FROM rack_roots')}


def record_root(path, root, server, **fields):
    key = os.path.normcase(os.path.normpath(str(root)))
    with catalog.connect(path) as db:
        ensure_tables(db)
        db.execute('INSERT OR IGNORE INTO rack_roots(root, server) VALUES(?,?)', (key, server))
        db.execute(f"UPDATE rack_roots SET server=?, {', '.join(f'{name}=?' for name in fields)} WHERE root=?",
                   (server, *fields.values(), key))


def scan_group(source, path, cancel, emit, count_bnal=None):
    """Scan one share and keep its status; a failure keeps the previous successful values."""
    record_root(path, source['root'], source['server'], status='実行中', error='', last_attempt=catalog.now())
    try:
        # One cheap volume query per share; it reports the whole volume, not only this folder.
        usage = shutil.disk_usage(source['root'])
        record_root(path, source['root'], source['server'], volume_used=usage.used, volume_total=usage.total)
    except OSError:
        pass
    try:
        count = scan_samples(source, path, cancel, emit, count_bnal)
    except catalog.ScanCancelled as exc:
        record_root(path, source['root'], source['server'], status='中止', error=str(exc))
        raise
    except Exception as exc:
        record_root(path, source['root'], source['server'], status='失敗', error=str(exc))
        raise
    record_root(path, source['root'], source['server'], status='取得済み', last_success=catalog.now())
    return count


def scan_samples(source, path, cancel, emit, count_bnal=None):
    """Commit and publish each valid sample independently.

    count_bnal(experiment) -> bool selects experiments whose BNAL@ folders are listed for .tdms counts
    (one extra request per folder); None counts nothing and only the names are saved."""
    root = Path(source['root'])
    root_key = os.path.normcase(os.path.normpath(str(root)))
    completed = 0
    started = time.monotonic()
    seen = []
    with catalog.connect(path) as db:
        ensure_tables(db)
    for experiment in entries(root, cancel):
        if not experiment.is_dir(follow_symlinks=False):
            continue
        emit('progress', f'取得済み {completed}サンプル ／ 実験 {experiment.name}')
        for sample in entries(experiment.path, cancel):
            if not sample.is_dir(follow_symlinks=False):
                continue
            target = Path(sample.path) / (sample.name + '_10k_Sample') / 'T'
            scope = experiment.name + '/' + sample.name
            emit('progress', f'取得済み {completed}サンプル ／ 経過 {time.monotonic()-started:.0f}秒 ／ 処理中 {scope}')
            bnal_paths = {}
            try:
                counts = sample_counts(target, cancel, bnal_paths)
            except (FileNotFoundError, NotADirectoryError):
                if not target.is_dir():
                    continue
                raise
            counted = {}
            if count_bnal is not None and count_bnal(experiment.name):
                for index, (name, folder) in enumerate(sorted(bnal_paths.items()), 1):
                    emit('progress', f'取得済み {completed}サンプル ／ 処理中 {scope} ／ BNAL内訳 {index}/{len(bnal_paths)}')
                    counted[name] = tdms_numbers(folder, cancel)
            row = dict(root=root_key, experiment=experiment.name, sample=sample.name, **counts,
                       last_success=catalog.now(), status='完了')
            with catalog.connect(path) as db:
                db.execute(f"INSERT OR REPLACE INTO rack_samples({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                           tuple(row.values()))
                save_bnals(db, root_key, experiment.name, sample.name, bnal_paths, counted, row['last_success'])
            seen.append((experiment.name, sample.name))
            completed += 1
            emit('sample', row)
            emit('progress', f'取得済み {completed}サンプル ／ 経過 {time.monotonic()-started:.0f}秒 ／ 完了 {scope}')
    with catalog.connect(path) as db:
        existing = db.execute('SELECT experiment,sample FROM rack_samples WHERE root=?', (root_key,)).fetchall()
        keep = set(seen)
        gone = [(root_key, r[0], r[1]) for r in existing if tuple(r) not in keep]
        db.executemany('DELETE FROM rack_samples WHERE root=? AND experiment=? AND sample=?', gone)
        db.executemany('DELETE FROM rack_bnals WHERE root=? AND experiment=? AND sample=?', gone)
    return completed


def refresh_sample(path, root, experiment, sample, cancel=None):
    """Re-read one sample's T and count every BNAL@, saved exactly as a full scan would save that sample.

    For the file manager after it moves files: one T listing, ANAL (+stocked) and one listing per BNAL@."""
    cancel = cancel or threading.Event()
    root_key = os.path.normcase(os.path.normpath(str(root)))
    target = Path(root) / experiment / sample / (sample + '_10k_Sample') / 'T'
    if not target.is_dir():
        raise ValueError(f'Tフォルダが見つかりません: {target}')
    bnal_paths = {}
    counts = sample_counts(target, cancel, bnal_paths)
    counted = {name: tdms_numbers(folder, cancel) for name, folder in sorted(bnal_paths.items())}
    row = dict(root=root_key, experiment=experiment, sample=sample, **counts, last_success=catalog.now(), status='完了')
    with catalog.connect(path) as db:
        ensure_tables(db)
        db.execute(f"INSERT OR REPLACE INTO rack_samples({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                   tuple(row.values()))
        save_bnals(db, root_key, experiment, sample, bnal_paths, counted, row['last_success'])
    return row


def original_count(row):
    """Original measurement count: T direct plus T/stocked, or the ANAL set if that is larger (None if unknown)."""
    counts = [row[key] + (row.get(extra) or 0) for key, extra in (('t_tdms', 't_stocked_tdms'), ('anal_tdms', 'anal_stocked_tdms'))
              if row.get(key) is not None]
    return max(counts) if counts else None


def analysis_progress(row):
    # Files moved to T/stocked or set aside in ANAL/stocked still belong to the sample.
    total = None if row.get('t_tdms') is None else row['t_tdms'] + (row.get('t_stocked_tdms') or 0)
    analysed = None if row.get('anal_tdms') is None else row['anal_tdms'] + (row.get('anal_stocked_tdms') or 0)
    if total is None:
        return '確認不可', 'unknown'
    if total == 0:
        # ANAL without T: ANAL copied here to run BNAL (a work copy), not a missing original.
        return ('対象なし', 'unknown') if not analysed else ('解析用コピー（Tなし）', 'copy')
    if analysed is None:
        return '0%（ANAL未作成）', 'unstarted'
    rate = analysed / total * 100
    tag = 'excess' if analysed > total else 'complete' if analysed == total else 'unstarted' if analysed == 0 else 'partial'
    return f'{rate:.1f}%', tag


def filter_experiments(rows, query):
    query = query.strip().casefold()
    return [row for row in rows if query in row['experiment'].casefold()]


ANALYSIS_GROUPS = (('Rackstation', 'analysis'), ('Rackstation', 'archive'),
                   ('QTserver', 'analysis'), ('QTserver', 'backup'),
                   ('QDserver', 'analysis'), ('QDserver', 'backup'), ('QKserver', 'analysis'))
COLUMNS = ('experiment', 'sample', 't_tdms', 't_stocked_tdms', 'anal_tdms', 'anal_progress', 'anal_stocked_tdms',
           'bnal_folders', 'tdms_bytes', 'last_success', 'status')
TITLES = ('実験名', 'サンプル名', 'ファイル数（T直下）', 'T/stocked', 'ANAL解析数', 'ANAL進捗', 'ANAL/stocked（退避中）',
          'BNAL@*フォルダ数', 'TDMS容量', '最終取得 UTC', '取得状態')
CAPACITY_COLUMNS = ('group', 'root', 'status', 'experiments', 'samples', 't_tdms', 'anal_tdms', 'tdms_bytes',
                    'volume', 'volume_rate', 'last_success', 'last_attempt', 'error')
CAPACITY_TITLES = ('グループ', '解析ルート', '状態', '実験数', 'サンプル数', 'T直下TDMS数', 'ANAL解析数',
                   'TDMS容量（T+ANAL）', 'ボリューム使用量 / 全体', '使用率', '最終全体取得 UTC', '最終試行 UTC', 'エラー・除外')
OVERALL = '全体まとめ'
# 「保存先の状態」表の列幅（px）
CAPACITY_WIDTHS = {'group': 140, 'root': 207, 'status': 130, 'experiments': 130, 'samples': 130, 't_tdms': 130,
                   'anal_tdms': 130, 'tdms_bytes': 182, 'volume': 180, 'volume_rate': 80, 'last_success': 186,
                   'last_attempt': 130, 'error': 127}

# ボリューム使用率の危険度（Data_server・Analysis_server の両方の表で使う）
VOLUME_WARNING = 80   # %以上で黄（注意）
VOLUME_DANGER = 90    # %以上で赤（危険）
VOLUME_LEGEND = (f'行の色＝ボリューム使用率　黄：{VOLUME_WARNING}%以上（注意）　赤：{VOLUME_DANGER}%以上（危険）'
                 '　※同じディスク上の共有は同じ値です')


def volume_rate(row):
    """(使用率の表示, 行タグ)。タグは 'volume_danger' / 'volume_warning' / ''。"""
    used, total = row.get('volume_used'), row.get('volume_total')
    if not total or used is None:
        return '未取得', ''
    rate = used / total * 100
    tag = ('volume_danger' if rate >= VOLUME_DANGER else 'volume_warning' if rate >= VOLUME_WARNING else '')
    return f'{rate:.1f}%', tag


def tag_volume_rows(tree):
    tree.tag_configure('volume_warning', background=ui_theme.row('warning', 0.3))
    tree.tag_configure('volume_danger', background=ui_theme.row('danger', 0.32))


def group_label(server, share):
    return f'{server}/{share}'


def default_root(configured, server, share):
    return next((s['root'] for s in configured
                 if s['server'].casefold() == server.casefold()
                 and s['root'].replace('\\', '/').rstrip('/').rsplit('/', 1)[-1].casefold() == share.casefold()),
                '\\\\' + server + '\\' + share)


def combine_groups(rows_by_group):
    """Keep every row; the same experiment on several servers lines up next to each other."""
    order = {label: index for index, label in enumerate(rows_by_group)}
    combined = [dict(row, group=label) for label, rows in rows_by_group.items() for row in rows]
    return sorted(combined, key=lambda row: (row['experiment'], row['sample'], order[row['group']]))


def sample_bytes(row):
    if row.get('t_bytes') is None:
        return None
    return row['t_bytes'] + (row.get('anal_bytes') or 0)


def capacity_row(label, root, rows, state=None):
    """One summary line per share, computed only from saved rows."""
    state = state or {}
    sizes = [size for size in map(sample_bytes, rows) if size is not None]
    return dict(group=label, root=root, status=state.get('status') or ('取得済み' if rows else '未取得'),
                experiments=len({row['experiment'] for row in rows}), samples=len(rows),
                t_tdms=sum(row.get('t_tdms') or 0 for row in rows),
                anal_tdms=sum(row.get('anal_tdms') or 0 for row in rows),
                tdms_bytes=sum(sizes) if sizes else None, sized_samples=len(sizes),
                volume_used=state.get('volume_used'), volume_total=state.get('volume_total'),
                last_success=state.get('last_success') or max((row['last_success'] for row in rows
                                                               if row.get('last_success')), default=None),
                last_attempt=state.get('last_attempt'), error=state.get('error') or '')


def capacity_total(rows):
    """Volumes are left out: shares on one server can sit on the same volume."""
    sized = [row['tdms_bytes'] for row in rows if row['tdms_bytes'] is not None]
    return dict(group='合計', root='', status='', experiments=sum(row['experiments'] for row in rows),
                samples=sum(row['samples'] for row in rows), t_tdms=sum(row['t_tdms'] for row in rows),
                anal_tdms=sum(row['anal_tdms'] for row in rows), tdms_bytes=sum(sized) if sized else None,
                sized_samples=sum(row['sized_samples'] for row in rows), volume_used=None, volume_total=None,
                last_success=None, last_attempt=None, error='')


def acquired_range(rows):
    dates = sorted({row['last_success'] for row in rows if row.get('last_success')})
    return f'{dates[0]} ～ {dates[-1]}' if len(dates) > 1 else dates[0] if dates else '未取得'


def create_panel(parent, window, groups=ANALYSIS_GROUPS):
    labels = [group_label(server, share) for server, share in groups]
    summary = tk.StringVar(value='保存済み情報を読み込みます…')
    ttk.Label(window, textvariable=summary, padding=(8, 4), wraplength=1300).pack(fill='x')
    progress = ttk.Progressbar(window, mode='indeterminate')
    progress.pack(fill='x', padx=8)
    ttk.Label(window, text='保存先の状態（日時はUTC／TDMS容量はT直下・ANAL直下のTDMS合計／ボリュームは共有が載っているディスク全体）',
              padding=(8, 4)).pack(anchor='w')
    capacity_tree = parent.table(CAPACITY_COLUMNS, CAPACITY_TITLES, len(groups) + 1, parent=window)
    # 保存先の状態の列幅は固定（ドラッグでも変えない）
    for col, width in CAPACITY_WIDTHS.items():
        capacity_tree.column(col, width=width, minwidth=width, stretch=False)
    capacity_tree.bind('<Button-1>', lambda event: 'break'
                       if capacity_tree.identify_region(event.x, event.y) == 'separator' else None)
    capacity_tree.tag_configure('total', background=ui_theme.row('violet', 0.2))
    tag_volume_rows(capacity_tree)
    ttk.Label(window, text=VOLUME_LEGEND, style='Muted.TLabel', padding=(8, 2)).pack(anchor='w')
    capacity_tree.bind('<Double-1>', lambda event: parent.show_row_details(capacity_tree))
    # 検索は下の表（全体まとめ・各グループ）を絞り込むので、上の表と下の表の間に置く。
    # 「選択中のグループを更新」も下のタブを対象にするので、同じ行に並べる
    search_bar = ttk.Frame(window, padding=(8, 8, 8, 0))
    search_bar.pack(fill='x')
    ttk.Label(search_bar, text='実験名で検索').pack(side='left')
    search = tk.StringVar()
    ttk.Entry(search_bar, textvariable=search, width=40).pack(side='left', padx=6)
    ttk.Button(search_bar, text='検索をクリア', command=lambda: search.set('')).pack(side='left')
    # 更新ボタンの列は検索の右に並べる（ボタンは下の方で controls に追加する）
    controls = ttk.Frame(search_bar)
    controls.pack(side='left', padx=(24, 0))
    tabs = ttk.Notebook(window)
    tabs.pack(fill='both', expand=True, padx=8, pady=4)

    def make_tree(tab, columns, titles):
        tree = parent.table(columns, titles, 18, True, parent=tab)
        for col in columns:
            tree.column(col, width=220 if col in ('experiment', 'last_success') else 140, stretch=True)
        for tag, color in [('unstarted', ui_theme.row('danger')), ('partial', ui_theme.row('warning')),
                           ('complete', ui_theme.row('success')), ('excess', ui_theme.row('violet')),
                           ('copy', ui_theme.row('info')), ('unknown', ui_theme.row('neutral', 0.16))]:
            tree.tag_configure(tag, background=color)
        tree.bind('<Double-1>', lambda event: parent.show_row_details(tree))
        return tree

    overall_tab = ttk.Frame(tabs)
    tabs.add(overall_tab, text=OVERALL)
    ttk.Label(overall_tab, text='全グループの保存済み結果を1つにまとめた表です。同じ実験・サンプルは並べて表示します。',
              padding=(8, 4)).pack(fill='x')
    overall_tree = make_tree(overall_tab, ('group',) + COLUMNS, ('グループ',) + TITLES)
    group_views = {}
    for (server, share), label in zip(groups, labels):
        tab = ttk.Frame(tabs)
        tabs.add(tab, text=label)
        bar = ttk.Frame(tab, padding=(8, 4))
        bar.pack(fill='x')
        ttk.Label(bar, text='解析ルート').pack(side='left')
        root = tk.StringVar(value=default_root(parent.configured, server, share))
        ttk.Entry(bar, textvariable=root, width=55).pack(side='left', padx=6)
        group_views[label] = dict(server=server, root=root, tab=tab, tree=make_tree(tab, COLUMNS, TITLES))
    ttk.Label(window, text='ANAL進捗＝ANAL解析数÷ファイル数　赤：0%　黄：途中　緑：100%　紫：100%超・要確認　灰：対象なし／確認不可　'
                           '行をダブルクリックすると詳細を表示します。', padding=(8, 4)).pack(fill='x')
    status = tk.StringVar(value='指定構造の10k_Sample/Tを集計します。構造が異なるフォルダは対象外です。')
    ttk.Label(window, textvariable=status, padding=8, wraplength=1300).pack(fill='x')
    events = queue.Queue()
    cancel = threading.Event()
    loading = False
    scanning = False
    saved = {label: {} for label in labels}
    states = {}
    shown_roots = {}

    def size_text(value):
        return '未取得（要更新）' if value is None else parent.cell(dict(bytes=value), 'bytes')

    def fill(tree, rows, columns):
        tree.delete(*tree.get_children())
        for row in rows:
            row = dict(row)
            row['anal_progress'], tag = analysis_progress(row)
            row['tdms_bytes'] = size_text(sample_bytes(row))
            # None = stockedフォルダなし（または未取得）。退避中のファイルがあれば件数を出す
            row['anal_stocked_tdms'] = row.get('anal_stocked_tdms') or '—'
            row['t_stocked_tdms'] = row.get('t_stocked_tdms') or '—'
            tree.insert('', 'end', values=[parent.cell(row, col) for col in columns], tags=(tag,))

    def render_capacity():
        rows = [capacity_row(label, shown_roots.get(label, ''), list(saved[label].values()),
                             states.get(catalog.root_identity(shown_roots.get(label, ''))))
                for label in labels]
        capacity_tree.delete(*capacity_tree.get_children())
        for index, row in enumerate(rows + [capacity_total(rows)]):
            row = dict(row)
            total = row['group'] == '合計'
            partial = row['tdms_bytes'] is not None and row['sized_samples'] < row['samples']
            row['tdms_bytes'] = size_text(row['tdms_bytes']) + (
                f"（{row['sized_samples']}/{row['samples']}サンプル分）" if partial else '')
            row['volume'] = ('' if total else '未取得' if row['volume_total'] is None else
                             f"{size_text(row['volume_used'])} / {size_text(row['volume_total'])}")
            row['volume_rate'], volume_tag = ('', '') if total else volume_rate(row)
            tag = ('total' if total else row['status'] if row['status'] in ('失敗', '中止', '実行中') else
                   volume_tag or ('even' if index % 2 == 0 else ''))
            capacity_tree.insert('', 'end', values=[parent.cell(row, col) for col in CAPACITY_COLUMNS], tags=(tag,))

    def render(changed=None):
        query = search.get()
        for label in labels if changed is None else changed:
            rows = sorted(saved[label].values(), key=lambda row: (row['experiment'], row['sample']))
            fill(group_views[label]['tree'], filter_experiments(rows, query), COLUMNS)
        combined = combine_groups({label: list(saved[label].values()) for label in labels})
        visible = filter_experiments(combined, query)
        fill(overall_tree, visible, ('group',) + COLUMNS)
        render_capacity()
        counts = '　'.join(f'{label} {len(saved[label])}' for label in labels)
        summary.set(f'全体 表示 {len(visible)} / 保存済み {len(combined)}サンプル（{counts}）'
                    f' ／ 取得日時 UTC: {acquired_range(combined)}')

    search.trace_add('write', lambda *_: render())

    def selected_group():
        current = tabs.select()
        return next((label for label, view in group_views.items() if str(view['tab']) == current), None)

    def update_buttons(*_):
        busy = loading
        for button in (all_button, refresh_button):
            button.config(state='disabled' if busy else 'normal')
        selected_button.config(state='disabled' if busy or selected_group() is None else 'normal')
        stop_button.config(state='normal' if busy else 'disabled')

    def load(scan_labels=()):
        nonlocal loading, scanning
        if loading or (scan_labels and (parent.busy or getattr(parent, 'rack_busy', False))):
            messagebox.showinfo('更新中', '現在の処理が終わってから実行してください。', parent=window)
            return
        path = catalog.DEFAULT_DB if scan_labels else parent.db_path
        # Tk variables are read here, never from the worker thread.
        count_bnal = None
        if scan_labels and count_bnal_tdms.get():
            query = search.get().strip().casefold()
            if not query and not messagebox.askyesno(
                    'BNAL内訳の取得', '検索欄が空のため、全実験のBNAL@フォルダを1つずつ確認します。\n'
                    'サーバーへのアクセスが大幅に増えます。続けますか？', parent=window):
                return
            count_bnal = lambda experiment: query in experiment.casefold()  # noqa: E731
        roots = {label: group_views[label]['root'].get() for label in labels}
        servers = {label: group_views[label]['server'] for label in labels}
        shown_roots.update(roots)
        loading = True
        scanning = bool(scan_labels)
        if scanning:
            parent.rack_busy = True
            parent.rack_cancel = cancel
            parent.active_analysis_panel = window
        cancel.clear()
        update_buttons()
        status.set('更新中…' if scanning else '取得済みDBを読み込み中…')
        progress.start(15)

        def worker():
            results = {}
            try:
                for index, label in enumerate(scan_labels, 1):
                    if cancel.is_set():
                        results[label] = '中止'
                        continue
                    events.put(('progress', None, f'[{index}/{len(scan_labels)}] {label} を更新中…'))
                    source = dict(server=servers[label], root=roots[label], kind='analysis')
                    try:
                        count = scan_group(source, path, cancel,
                                           lambda event, data, label=label: events.put((event, label, data)),
                                           count_bnal)
                        results[label] = f'{count}サンプル'
                    except catalog.ScanCancelled:
                        results[label] = '中止（取得済み分は保存）'
                    except Exception as exc:
                        # One unreachable server must not stop the others.
                        results[label] = f'失敗: {exc}'
                rows = {label: inventory(path, roots[label]) for label in labels}
                events.put(('done', None, (rows, root_states(path), results)))
            except Exception as exc:
                events.put(('error', None, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def poll():
        nonlocal loading
        changed = set()
        try:
            while True:
                event, label, data = events.get_nowait()
                if event == 'progress':
                    status.set(data if label is None else f'{label}: {data}')
                    continue
                if event == 'sample':
                    saved[label][(data['experiment'], data['sample'])] = data
                    changed.add(label)
                    continue
                loading = False
                progress.stop()
                if scanning:
                    parent.rack_busy = False
                if event == 'error':
                    status.set('確認できません: ' + data)
                else:
                    rows, saved_states, results = data
                    states.clear()
                    states.update(saved_states)
                    for name in labels:
                        saved[name] = {(row['experiment'], row['sample']): row for row in rows[name]}
                    changed.update(labels)
                    if results:
                        status.set('更新結果: ' + '　'.join(f'{name} {text}' for name, text in results.items())
                                   + '。対象外の構造はスキップしました。')
                    else:
                        status.set('保存済み情報を表示しています。必要に応じて更新してください。')
                update_buttons()
        except queue.Empty:
            pass
        if changed:
            # Render once per poll so a fast scan does not redraw every table for each sample.
            render(changed)
        window.after(150, poll)

    selected_button = ttk.Button(controls, text='選択中のグループを更新',
                                 command=lambda: load((selected_group(),)) if selected_group() else None)
    selected_button.pack(side='left', padx=4)
    all_button = ttk.Button(controls, text='全グループを更新', command=lambda: load(tuple(labels)))
    all_button.pack(side='left', padx=4)
    refresh_button = ttk.Button(controls, text='DBを再表示', command=load)
    refresh_button.pack(side='left', padx=4)
    stop_button = ttk.Button(controls, text='更新を中止', command=cancel.set)
    stop_button.pack(side='left', padx=4)
    # BNAL@名は常に保存。tdms数はフォルダごとにサーバーへ1回アクセスするため、検索中の実験に限って任意で取得する
    count_bnal_tdms = tk.BooleanVar(value=True)
    ttk.Checkbutton(controls, text='BNAL内訳のTDMS数も取得（検索中の実験のみ）',
                    variable=count_bnal_tdms).pack(side='left', padx=4)
    ttk.Label(controls, text='「選択中のグループ」は下のタブで開いているグループです（全体まとめでは選べません）。').pack(side='left', padx=8)
    tabs.bind('<<NotebookTabChanged>>', update_buttons)
    update_buttons()
    window.after(150, poll)
    # Reading saved results needs no server scan or shared update lock.
    window.after(0, load)
