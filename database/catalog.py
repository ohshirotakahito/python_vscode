"""SQLite inventory. Source files are only stat'ed; contents are never opened."""
from pathlib import Path, PurePosixPath, PureWindowsPath
from datetime import datetime, timezone
import os
import re
import sqlite3
import time
from contextlib import contextmanager, closing

DEFAULT_DB = Path(__file__).resolve().parent / 'inventory.sqlite3'
SOURCE_KINDS = ('analysis', 'experiment', 'sq_recent', 'sq_data', 'sq_stocked')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def connect(path=DEFAULT_DB, readonly=False):
    path = Path(path).resolve()
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2) if readonly else sqlite3.connect(path, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path=DEFAULT_DB):
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY, server TEXT NOT NULL, root TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL, last_success TEXT, last_attempt TEXT,
                status TEXT NOT NULL DEFAULT '未取得', error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS files (
                source_id INTEGER NOT NULL, relative_path TEXT NOT NULL,
                folder TEXT NOT NULL, experiment TEXT NOT NULL, sample TEXT NOT NULL,
                frequency TEXT NOT NULL, specimen TEXT NOT NULL, category TEXT NOT NULL,
                extension TEXT NOT NULL, size INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
                PRIMARY KEY(source_id, relative_path));
            CREATE INDEX IF NOT EXISTS files_groups ON files(source_id, experiment, sample);
            CREATE TABLE IF NOT EXISTS folders (
                source_id INTEGER NOT NULL, relative_path TEXT NOT NULL,
                PRIMARY KEY(source_id, relative_path));
            CREATE TABLE IF NOT EXISTS updates (
                id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, scope TEXT NOT NULL,
                started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL,
                file_count INTEGER NOT NULL DEFAULT 0, folder_count INTEGER NOT NULL DEFAULT 0,
                elapsed REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS updates_source ON updates(source_id,id);
            PRAGMA user_version=2;
        ''')


def classify(relative, kind):
    parts = relative.parts
    directories = parts[:-1]
    experiment = directories[0] if directories else ''
    sample = directories[1] if kind == 'analysis' and len(directories) > 1 else ''
    frequency = specimen = ''
    for part in directories:
        match = re.search(r'_(10k|100k)_(Sample|Blank)$', part, re.I)
        if match:
            frequency, specimen = match.group(1).lower(), match.group(2).capitalize()
    lower = [p.lower() for p in directories]
    if kind != 'analysis':
        category = '実験原本'
    elif 'stocked' in lower:
        category = 'stocked'
    elif 'raw data' in lower:
        category = 'raw data'
    elif 'anal' in lower:
        category = 'ANAL'
    elif any(p.startswith('bnal@') for p in lower):
        category = 'BNAL'
    elif lower and lower[-1] == 't':
        category = 'T直下'
    else:
        category = 'その他'
    return experiment, sample, frequency, specimen, category


class ScanCancelled(Exception):
    pass


def normalize_scope(scope):
    value = str(scope).replace('\\', '/')
    if value in ('', '.'):
        return '.'
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or PureWindowsPath(value).drive or '..' in candidate.parts or ':' in value:
        raise ValueError('更新対象は共有内の相対パスで指定してください（.. は使えません）。')
    return candidate.as_posix()


def update_history(path=DEFAULT_DB, limit=1000):
    with connect(path, readonly=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='updates'").fetchone():
            return []
        return [dict(r) for r in db.execute('''SELECT u.*,s.server,s.root FROM updates u
            JOIN sources s ON s.id=u.source_id ORDER BY u.id DESC LIMIT ?''', (limit,))]


def scope_state(source, scope, updates):
    """Latest attempt covering this whole scope; a child update cannot freshen a parent."""
    covering = [u for u in updates if u['scope'] == '.' or scope == u['scope'] or scope.startswith(u['scope'] + '/')]
    success = next((u for u in covering if u['status'] == '完了'), None)
    result = dict(last_success=success['finished'] if success else source['last_success'],
                  status=covering[0]['status'] if covering else source['status'])
    latest_id = covering[0]['id'] if covering else 0
    if any(u['id'] > latest_id and (scope == '.' or u['scope'].startswith(scope + '/')) for u in updates):
        result['status'] = '一部更新（更新履歴を確認）'
    return result


def scan_source(source, path=DEFAULT_DB, cancel=None, progress=None, scope='.', on_progress=None):
    """Atomically replace one source only after a complete successful traversal.

    Any inaccessible entry or cancellation rolls back its entire snapshot.
    Symlinks and Windows reparse points are skipped (no junction traversal).
    """
    root = Path(source['root'])
    if not root.is_absolute():
        raise ValueError('保存先は絶対パスで指定してください。')
    if source['kind'] not in SOURCE_KINDS:
        raise ValueError('不明な保存先種別です。')
    root_key = os.path.normcase(os.path.normpath(str(root)))
    scope = normalize_scope(scope)
    target = root if scope == '.' else root.joinpath(*PurePosixPath(scope).parts)
    attempted = now()
    with connect(path) as db:
        db.execute('INSERT OR IGNORE INTO sources(server,root,kind) VALUES(?,?,?)',
                   (source['server'], root_key, source['kind']))
        source_id = db.execute('SELECT id FROM sources WHERE root=?', (root_key,)).fetchone()[0]
        previous_kind = db.execute('SELECT kind FROM sources WHERE id=?', (source_id,)).fetchone()[0]
        if scope != '.' and previous_kind != source['kind']:
            raise ValueError('保存先の種別を変更した場合は、先に共有全体を更新してください。')
        job_id = db.execute('INSERT INTO updates(source_id,scope,started,status) VALUES(?,?,?,?)',
                            (source_id, scope, attempted, '実行中')).lastrowid
    count = skipped = 0
    folder_count = 0
    started = last_report = time.monotonic()

    def report(current, force=False):
        nonlocal last_report
        stamp = time.monotonic()
        if force or stamp - last_report >= 0.2:
            last_report = stamp
            if progress:
                progress(f"{source['server']}: {count:,}件 / {folder_count:,}フォルダ確認中: {current}")
            if on_progress:
                on_progress(dict(id=job_id, file_count=count, folder_count=folder_count,
                                 elapsed=stamp-started, current=str(current)))

    try:
        report(target, True)
        # Reject junctions/symlinks in a requested subpath, including its ancestors.
        check = root
        for part in (() if scope == '.' else PurePosixPath(scope).parts):
            check = check / part
            info = check.lstat()
            if check.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('リンク・再解析ポイントは更新対象に指定できません。')
        if scope != '.':
            # Windows accepts differently cased input, while SQLite keys retain
            # actual names. Resolve spelling before replacing that subtree.
            scope = target.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
            target = root.joinpath(*PurePosixPath(scope).parts)
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE updates SET scope=? WHERE id=?', (scope, job_id))
            for table in ('files', 'folders'):
                if scope == '.':
                    db.execute(f'DELETE FROM {table} WHERE source_id=?', (source_id,))
                else:
                    db.execute(f'''DELETE FROM {table} WHERE source_id=? AND
                        (relative_path=? OR substr(relative_path,1,?)=?)''',
                        (source_id, scope, len(scope)+1, scope+'/'))
            if scope != '.':
                for parent in target.relative_to(root).parents:
                    db.execute('INSERT OR IGNORE INTO folders VALUES(?,?)', (source_id, parent.as_posix()))

            def add_file(file, info):
                nonlocal count
                relative = file.relative_to(root)
                db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (source_id, relative.as_posix(), relative.parent.as_posix(),
                            *classify(relative, source['kind']), relative.suffix.lower(), info.st_size, info.st_mtime_ns))
                count += 1

            if target.is_file():
                if scope == '.':
                    raise ValueError('共有のルートにはフォルダを指定してください。')
                add_file(target, target.stat())
                stack = []
            else:
                stack = [target]
            while stack:
                if cancel and cancel.is_set():
                    raise ScanCancelled('中止しました。前回の記録を保持しています。')
                folder = stack.pop()
                db.execute('INSERT OR IGNORE INTO folders VALUES(?,?)', (source_id, folder.relative_to(root).as_posix()))
                folder_count += 1
                report(folder)
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if cancel and cancel.is_set():
                            raise ScanCancelled('中止しました。前回の記録を保持しています。')
                        info = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                            skipped += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            add_file(Path(entry.path), info)
                        report(folder)
            if cancel and cancel.is_set():
                raise ScanCancelled('中止しました。前回の記録を保持しています。')
            db.execute('UPDATE sources SET server=?,kind=?,last_success=CASE WHEN ? THEN ? ELSE last_success END,last_attempt=?,status=?,error=? WHERE id=?',
                       (source['server'], source['kind'], scope == '.', now(), attempted, '取得済み' if scope == '.' else '一部更新',
                        f'リンク・再解析ポイント {skipped}件を除外' if skipped else '', source_id))
            db.execute('UPDATE updates SET finished=?,status=?,file_count=?,folder_count=?,elapsed=?,error=? WHERE id=?',
                       (now(), '完了', count, folder_count, time.monotonic()-started,
                        f'リンク等 {skipped}件除外' if skipped else '', job_id))
            report(target, True)
            if cancel and cancel.is_set():
                raise ScanCancelled('中止しました。前回の記録を保持しています。')
    except Exception as exc:
        with connect(path) as db:
            db.execute('UPDATE sources SET last_attempt=?,status=?,error=? WHERE id=?',
                       (attempted, '中止' if isinstance(exc, ScanCancelled) else '失敗', str(exc), source_id))
            db.execute('UPDATE updates SET finished=?,status=?,file_count=?,folder_count=?,elapsed=?,error=? WHERE id=?',
                       (now(), '中止' if isinstance(exc, ScanCancelled) else '失敗', count, folder_count,
                        time.monotonic()-started, str(exc), job_id))
        raise
    return count


def sources(path=DEFAULT_DB):
    with connect(path, readonly=True) as db:
        return [dict(row) for row in db.execute('''
            SELECT s.*, (SELECT COUNT(*) FROM files f WHERE f.source_id=s.id) AS file_count,
            (SELECT COALESCE(SUM(size),0) FROM files f WHERE f.source_id=s.id) AS bytes,
            (SELECT COUNT(*) FROM folders f WHERE f.source_id=s.id) AS folder_count
            FROM sources s ORDER BY server,root''')]


def summary(path=DEFAULT_DB, search=''):
    with connect(path, readonly=True) as db:
        return [dict(row) for row in db.execute('''
            SELECT s.server,s.root,s.kind,s.last_success,f.experiment,f.sample,
            f.frequency,f.specimen,f.category,COUNT(*) AS file_count,SUM(f.size) AS bytes
            FROM files f JOIN sources s ON s.id=f.source_id
            WHERE instr(lower(s.root || '/' || f.relative_path),lower(?))>0
            GROUP BY s.id,f.experiment,f.sample,f.frequency,f.specimen,f.category
            ORDER BY s.server,f.experiment,f.sample,f.frequency,f.specimen,f.category''', (search,))]


def find_files(path=DEFAULT_DB, search='', limit=1000, offset=0):
    """Read-only paginated API; paths use / even when the source is Windows."""
    if not 1 <= limit <= 10000 or offset < 0:
        raise ValueError('limitは1～10000、offsetは0以上で指定してください。')
    with connect(path, readonly=True) as db:
        return [dict(row) for row in db.execute('''
            SELECT s.server,s.root,s.last_success,f.* FROM files f JOIN sources s ON s.id=f.source_id
            WHERE instr(lower(s.root || '/' || f.relative_path),lower(?))>0
            ORDER BY s.root,f.relative_path LIMIT ? OFFSET ?''', (search, limit, offset))]


def export_snapshot(destination, path=DEFAULT_DB):
    """SQLite backup includes committed WAL contents for another offline PC."""
    if Path(destination).resolve() == Path(path).resolve():
        raise ValueError('使用中のDBとは別の保存先を指定してください。')
    with connect(path, readonly=True) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
