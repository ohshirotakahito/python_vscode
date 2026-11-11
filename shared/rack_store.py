"""解析サーバーのサンプル集計（rack_samples / rack_roots / rack_bnals テーブル）の取り決めと読み書き。

database アプリの Analysis_server 調査が全体を書き、filemanager はファイル移動後に refresh_sample で
1サンプルだけ同じ形で書き直し、filemanager・Plotting・database の各画面がこれを読む。
測定番号の数え方（measurement_key）もここで決める。テーブルの列や意味を変えるときは shared/README.md を参照。
"""
from pathlib import Path
import os
import re
import shutil
import threading
import time

from shared import catalog

ANALYSIS_GROUPS = (('Rackstation', 'analysis'), ('Rackstation', 'archive'), ('Rackstation', 'backup'),
                   ('QTserver', 'analysis'), ('QTserver', 'backup'),
                   ('QDserver', 'analysis'), ('QDserver', 'backup'), ('QKserver', 'analysis'))


def group_label(server, share):
    return f'{server}/{share}'


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
    tdms = bnals = t_bytes = results = 0
    anal = t_stocked = anal_stocked = None
    t_keys = []
    for entry in entries(target, cancel):
        if entry.name.lower().endswith('.tdms') and entry.is_file(follow_symlinks=False):
            if entry.name.upper().startswith(RESULT_PREFIXES):
                results += 1  # an analysis result left in T is not an original measurement
                continue
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
    originals = t_keys + (t_stocked_keys or [])
    analysed = (anal_numbers or set()) | set(anal_stocked_keys or [])
    t_number, anal_number = as_numbers(originals), as_numbers(analysed)
    originals, analysed = [t_number[k] for k in originals], {anal_number[k] for k in analysed}
    resume, missing = missing_numbers(originals, analysed)
    last = max((key for key in analysed if isinstance(key, int)), default=None)
    return dict(t_tdms=tdms, t_results_tdms=results, anal_tdms=anal_tdms, bnal_folders=bnals, t_bytes=t_bytes,
                anal_bytes=anal_bytes,
                t_stocked_tdms=None if t_stocked_keys is None else len(t_stocked_keys),
                anal_stocked_tdms=None if anal_stocked_keys is None else len(anal_stocked_keys),
                anal_distinct=None if anal_numbers is None else len(anal_numbers),
                anal_resume=resume, anal_missing=missing, anal_last=last, anal_missing_checked=1)


# A@@ / B@@ directly in T are analysis results put in the wrong place: counted apart (t_results_tdms), not as T
RESULT_PREFIXES = ('A@@', 'B@@')
NUMBER = re.compile(r'#(\d+)\.tdms$', re.I)
# Originals carry the number right after the sample folder name and more '#' later
# ('KSN3_10k_Sample#001 D_... ANAN#1Pex1n2_..._EXSV#10_....tdms'); ANAL ends with it ('..._Sample#001.tdms').
SAMPLE_NUMBER = re.compile(r'_Sample#(\d+)', re.I)


# analysis results start with the run time: 'A@@2026_0902_0701_00_', 'B@@2026_0902_0735_' (result folder '...@')
RUN_STAMP = re.compile(r'^[AB]@@?(?:\d{4}_\d{4}_\d{4}(?:_\d{2})?[_@])?', re.I)
HEAD_NUMBER = re.compile(r'(.*?)#(\d+)')
SERIES_KEY = re.compile(r'(.+)#(\d{3,})')


def measurement_key(name):
    """The measurement a T / ANAL / BNAL file belongs to.

    '<sample>_10k_Sample#NNN' -> NNN (int). Other originals are told apart by their first word, which the Python
    ANAL keeps as its name and LabVIEW keeps at the start of the full original name:
    'ABex1T01#001 2013..._001.lvm.tdms', 'A@@2019_0219_1524_52_ABex1T01#001 2013....tdms' and
    'A@@2026_1104_1250_36_ABex1T01#001.tdms' -> 'ABex1T01#001' (str; ABex1T02#001 is another measurement)."""
    match = SAMPLE_NUMBER.search(name)
    if match:
        return int(match[1])
    base = RUN_STAMP.sub('', name)
    if base.lower().endswith('.tdms'):
        base = base[:-len('.tdms')]
    head = base.split(' ')[0]
    match = HEAD_NUMBER.fullmatch(head)
    if match:
        return f'{match[1]}#{int(match[2]):03d}' if match[1] else int(match[2])
    return head


def as_numbers(keys):
    """{key: '#NNN' number or the key} for the measurement keys of one folder side (T + T/stocked, or ANAL +
    ANAL/stocked). 'prefix#NNN' keys count as numbers when that side has the one numbering only, so the resume
    point, gaps and T -> stocked work as for '_Sample#NNN' (also when T was renamed after the analysis); with
    several prefixes ('ABex1T01#001', 'ABex1T02#001') a number alone would not tell them apart."""
    keys = set(keys)
    series = {m[1] for k in keys if isinstance(k, str) and (m := SERIES_KEY.fullmatch(k))}
    one = len(series) == 1 and not any(isinstance(k, int) for k in keys)
    return {k: int(SERIES_KEY.fullmatch(k)[2]) if one and isinstance(k, str) and SERIES_KEY.fullmatch(k) else k
            for k in keys}


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
    for column in ('t_bytes', 'anal_bytes', 'anal_stocked_tdms', 't_stocked_tdms', 'anal_distinct', 't_results_tdms'):
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
