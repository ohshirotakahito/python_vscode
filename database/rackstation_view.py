"""Analysis_server panel: every analysis server/share plus one combined table."""
from pathlib import Path, PurePosixPath
import queue
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


def sample_counts(target, cancel):
    """Counts plus TDMS bytes; on Windows DirEntry.stat() reuses the listing, so sizes cost no extra request."""
    tdms = bnals = t_bytes = 0
    anal = None
    for entry in entries(target, cancel):
        if entry.name.lower().endswith('.tdms') and entry.is_file(follow_symlinks=False):
            tdms += 1
            t_bytes += entry.stat(follow_symlinks=False).st_size
        elif entry.is_dir(follow_symlinks=False):
            if entry.name.lower().startswith('bnal@'):
                bnals += 1
            elif entry.name.lower() == 'anal':
                anal = entry.path
    anal_tdms = anal_bytes = None
    if anal is not None:
        anal_tdms = anal_bytes = 0
        for entry in entries(anal, cancel):
            if entry.name.lower().endswith('.tdms') and entry.is_file(follow_symlinks=False):
                anal_tdms += 1
                anal_bytes += entry.stat(follow_symlinks=False).st_size
    return tdms, anal_tdms, bnals, t_bytes, anal_bytes


def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rack_samples (
        root TEXT NOT NULL, experiment TEXT NOT NULL, sample TEXT NOT NULL,
        t_tdms INTEGER, anal_tdms INTEGER, bnal_folders INTEGER,
        last_success TEXT, status TEXT, PRIMARY KEY(root,experiment,sample))""")
    existing = {row[1] for row in db.execute('PRAGMA table_info(rack_samples)')}
    for column in ('t_bytes', 'anal_bytes'):
        if column not in existing:
            db.execute(f'ALTER TABLE rack_samples ADD COLUMN {column} INTEGER')
    db.execute("""CREATE TABLE IF NOT EXISTS rack_roots (
        root TEXT PRIMARY KEY, server TEXT, status TEXT, error TEXT,
        last_success TEXT, last_attempt TEXT, volume_used INTEGER, volume_total INTEGER)""")


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


def scan_group(source, path, cancel, emit):
    """Scan one share and keep its status; a failure keeps the previous successful values."""
    record_root(path, source['root'], source['server'], status='実行中', error='', last_attempt=catalog.now())
    try:
        # One cheap volume query per share; it reports the whole volume, not only this folder.
        usage = shutil.disk_usage(source['root'])
        record_root(path, source['root'], source['server'], volume_used=usage.used, volume_total=usage.total)
    except OSError:
        pass
    try:
        count = scan_samples(source, path, cancel, emit)
    except catalog.ScanCancelled as exc:
        record_root(path, source['root'], source['server'], status='中止', error=str(exc))
        raise
    except Exception as exc:
        record_root(path, source['root'], source['server'], status='失敗', error=str(exc))
        raise
    record_root(path, source['root'], source['server'], status='取得済み', last_success=catalog.now())
    return count


def scan_samples(source, path, cancel, emit):
    """Commit and publish each valid sample independently."""
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
            try:
                t_tdms, anal_tdms, bnals, t_bytes, anal_bytes = sample_counts(target, cancel)
            except (FileNotFoundError, NotADirectoryError):
                if not target.is_dir():
                    continue
                raise
            row = dict(root=root_key, experiment=experiment.name, sample=sample.name,
                       t_tdms=t_tdms, anal_tdms=anal_tdms, bnal_folders=bnals,
                       last_success=catalog.now(), status='完了', t_bytes=t_bytes, anal_bytes=anal_bytes)
            with catalog.connect(path) as db:
                db.execute(f"INSERT OR REPLACE INTO rack_samples({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                           tuple(row.values()))
            seen.append((experiment.name, sample.name))
            completed += 1
            emit('sample', row)
            emit('progress', f'取得済み {completed}サンプル ／ 経過 {time.monotonic()-started:.0f}秒 ／ 完了 {scope}')
    with catalog.connect(path) as db:
        existing = db.execute('SELECT experiment,sample FROM rack_samples WHERE root=?', (root_key,)).fetchall()
        keep = set(seen)
        db.executemany('DELETE FROM rack_samples WHERE root=? AND experiment=? AND sample=?',
                       [(root_key, r[0], r[1]) for r in existing if tuple(r) not in keep])
    return completed


def analysis_progress(row):
    total, analysed = row.get('t_tdms'), row.get('anal_tdms')
    if total is None:
        return '確認不可', 'unknown'
    if total == 0:
        return ('対象なし', 'unknown') if not analysed else ('元ファイル0件・要確認', 'excess')
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
COLUMNS = ('experiment', 'sample', 't_tdms', 'anal_tdms', 'anal_progress', 'bnal_folders', 'tdms_bytes', 'last_success', 'status')
TITLES = ('実験名', 'サンプル名', 'ファイル数（T直下）', 'ANAL解析数', 'ANAL進捗', 'BNAL@*フォルダ数', 'TDMS容量', '最終取得 UTC', '取得状態')
CAPACITY_COLUMNS = ('group', 'root', 'status', 'experiments', 'samples', 't_tdms', 'anal_tdms', 'tdms_bytes',
                    'volume', 'last_success', 'last_attempt', 'error')
CAPACITY_TITLES = ('グループ', '解析ルート', '状態', '実験数', 'サンプル数', 'T直下TDMS数', 'ANAL解析数',
                   'TDMS容量（T+ANAL）', 'ボリューム使用量 / 全体', '最終全体取得 UTC', '最終試行 UTC', 'エラー・除外')
OVERALL = '全体まとめ'


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
    controls = ttk.Frame(window, padding=8)
    controls.pack(fill='x')
    search_bar = ttk.Frame(window, padding=(8, 4))
    search_bar.pack(fill='x')
    ttk.Label(search_bar, text='実験名で検索').pack(side='left')
    search = tk.StringVar()
    ttk.Entry(search_bar, textvariable=search, width=40).pack(side='left', padx=6)
    ttk.Button(search_bar, text='検索をクリア', command=lambda: search.set('')).pack(side='left')
    summary = tk.StringVar(value='保存済み情報を読み込みます…')
    ttk.Label(window, textvariable=summary, padding=(8, 4), wraplength=1300).pack(fill='x')
    progress = ttk.Progressbar(window, mode='indeterminate')
    progress.pack(fill='x', padx=8)
    ttk.Label(window, text='保存先の状態（日時はUTC／TDMS容量はT直下・ANAL直下のTDMS合計／ボリュームは共有が載っているディスク全体）',
              padding=(8, 4)).pack(anchor='w')
    capacity_tree = parent.table(CAPACITY_COLUMNS, CAPACITY_TITLES, len(groups) + 1, parent=window)
    for col in CAPACITY_COLUMNS:
        capacity_tree.column(col, width=260 if col in ('root', 'error') else 200 if col in ('tdms_bytes', 'volume') else 130)
    capacity_tree.tag_configure('total', background=ui_theme.row('violet', 0.2))
    capacity_tree.bind('<Double-1>', lambda event: parent.show_row_details(capacity_tree))
    tabs = ttk.Notebook(window)
    tabs.pack(fill='both', expand=True, padx=8, pady=4)

    def make_tree(tab, columns, titles):
        tree = parent.table(columns, titles, 18, True, parent=tab)
        for col in columns:
            tree.column(col, width=220 if col in ('experiment', 'last_success') else 140, stretch=True)
        for tag, color in [('unstarted', ui_theme.row('danger')), ('partial', ui_theme.row('warning')),
                           ('complete', ui_theme.row('success')), ('excess', ui_theme.row('violet')),
                           ('unknown', ui_theme.row('neutral', 0.16))]:
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
            tag = ('total' if total else row['status'] if row['status'] in ('失敗', '中止', '実行中') else
                   'even' if index % 2 == 0 else '')
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
                                           lambda event, data, label=label: events.put((event, label, data)))
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
    ttk.Label(controls, text='「選択中のグループ」は下のタブで開いているグループです（全体まとめでは選べません）。').pack(side='left', padx=8)
    tabs.bind('<<NotebookTabChanged>>', update_buttons)
    update_buttons()
    window.after(150, poll)
    # Reading saved results needs no server scan or shared update lock.
    window.after(0, load)
