"""Sample name changes with durable, reversible operation journals."""
import json
import os
import re
import stat
import time
import uuid
from datetime import datetime
from pathlib import Path
try:
    from .job_control import check_cancel
except ImportError:  # jobs.py の処理用プロセスからは同じフォルダの名前で読む
    from job_control import check_cancel


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    # The journal is rewritten after every rename; antivirus / the search indexer may hold the old copy for a
    # moment, which makes os.replace fail with "access denied". Retry briefly rather than stop mid-rename.
    for attempt in range(40):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.25)


SCOPE = 'measurement_names_v6'


def inventory(root, scope=SCOPE):
    root = Path(root).absolute()
    result = {}
    last_report = 0

    def remember(path, info):
        # Windows DirEntry.stat can omit the file ID; retain it for safe undo.
        if not info.st_ino:
            info = os.stat(path, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'リンク・ジャンクションは対象にできません: {path}')
        directory = stat.S_ISDIR(info.st_mode)
        result[str(path)] = [info.st_dev, info.st_ino,
                             None if directory else info.st_size,
                             None if directory else info.st_mtime_ns]

    def visit(path, mode):
        nonlocal last_report
        check_cancel()
        print(f'確認中: {path}（対象 {len(result)}件）', flush=True)
        with os.scandir(path) as entries:
            for entry in entries:
                check_cancel()
                directory = entry.is_dir(follow_symlinks=False)
                child_mode = None
                if directory:
                    name = entry.name.casefold()
                    if ((scope in ('measurement_names_v4', 'measurement_names_v5', SCOPE) and name == 'ps')
                            or (scope in ('measurement_names_v5', SCOPE) and name == 'bs')):
                        continue  # Preserve historical scopes when restoring older journals.
                    if mode == 'all':
                        child_mode = 'all'
                    elif mode in ('root', 'measurement', 'measurement100') and name == 't':
                        child_mode = 't'
                    elif mode in ('root', 'measurement', 'measurement100', 't') and name == 'stocked':
                        child_mode = 'stocked'
                    elif mode in ('root', 'measurement', 'measurement100', 't') and name == 'anal':
                        child_mode = 'all'
                    elif (scope in ('measurement_names_v3', 'measurement_names_v4', 'measurement_names_v5', SCOPE) and mode in ('root', 'measurement', 'measurement100', 't')
                          and (name == 'bnal' or name.startswith('bnal@'))):
                        child_mode = 'bnal'
                    elif mode == 'root' and re.fullmatch(r'.+_(?:10k|100k)_(?:Sample|Blank)', entry.name, re.I):
                        child_mode = 'measurement100' if scope == SCOPE and re.search(r'_100k_', entry.name, re.I) else 'measurement'
                    if mode == 'bnal':
                        # Only the immediate folder names; do not enumerate their contents.
                        remember(Path(entry.path), entry.stat(follow_symlinks=False))
                    elif child_mode:
                        remember(Path(entry.path), entry.stat(follow_symlinks=False))
                        visit(Path(entry.path), child_mode)
                elif (mode in ('all', 'stocked') or
                      mode in ('root', 'measurement', 'measurement100') and entry.name.lower().endswith('.txt') or
                      mode == 'measurement100' and entry.name.lower().endswith('.tdms') or
                      mode == 't' and entry.name.lower().endswith('.tdms')):
                    remember(Path(entry.path), entry.stat(follow_symlinks=False))
                if time.monotonic() - last_report >= 1:
                    print(f'確認中: {path}（対象 {len(result)}件）', flush=True)
                    last_report = time.monotonic()
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(str(root))
    remember(root, info)
    visit(root, 'all' if scope == 'all' else 'root')
    return result


def identity(path):
    info = os.stat(path, follow_symlinks=False)
    directory = stat.S_ISDIR(info.st_mode)
    return [info.st_dev, info.st_ino, None if directory else info.st_size,
            None if directory else info.st_mtime_ns]


def plan(experiment, folder, old, new):
    root = Path(experiment).absolute() / folder
    snapshot = inventory(root)
    # A sample token may follow ANAL/date prefixes, and precedes _10k, #, etc.
    pattern = re.compile(r'(?<![A-Za-z0-9-])' + re.escape(old) + r'(?![A-Za-z0-9-])')
    operations = []
    for name in sorted(snapshot, key=lambda p: (-len(Path(p).parts), p)):
        check_cancel()
        source = Path(name)
        changed = pattern.sub(lambda _: new, source.name)
        if changed != source.name:
            operations.append([name, str(source.with_name(changed))])
    preflight(snapshot, operations)
    return dict(root=str(root), scope=SCOPE, snapshot=snapshot, operations=operations)


def preflight(snapshot, operations):
    virtual = set(snapshot)
    folded = {p.casefold() for p in virtual}
    directories = {p for p, info in snapshot.items() if info[2] is None}
    siblings = {}
    for source, target in operations:
        check_cancel()
        if source not in virtual:
            raise ValueError(f'変更元がありません: {source}')
        parent = os.path.dirname(target)
        if parent not in siblings:
            try:
                with os.scandir(parent) as entries:
                    siblings[parent] = {e.name.casefold() for e in entries}
            except FileNotFoundError:
                siblings[parent] = set()
        if target.casefold() in folded or os.path.basename(target).casefold() in siblings[parent]:
            raise ValueError(f'変更先が存在します（上書きしません）: {target}')
        directory = source in directories
        affected = [p for p in virtual if p == source or p.startswith(source + os.sep)] if directory else [source]
        for p in affected:
            virtual.remove(p)
            folded.remove(p.casefold())
            moved = target + p[len(source):]
            virtual.add(moved)
            folded.add(moved.casefold())
            if p in directories:
                directories.remove(p)
                directories.add(moved)


def execute(preview, history_dir):
    root = Path(preview['root'])
    if any({'ps', 'bs'}.intersection(part.casefold() for part in Path(source).relative_to(root).parts)
           for source, _ in preview['operations']):
        raise ValueError('PS・BSフォルダ内を含む古い変更予定です。変更予定を再表示してください。')
    if inventory(preview['root'], preview.get('scope', 'all')) != preview['snapshot']:
        raise ValueError('確認後に対象が変わりました。変更予定を再表示してください。')
    preflight(preview['snapshot'], preview['operations'])
    path = Path(history_dir) / (datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex + '.json')
    journal = dict(version=1, created_at=datetime.now().isoformat(), root=preview['root'],
                   scope=preview.get('scope', 'all'), steps=[])
    save(path, journal)
    print(f'復元用履歴: {path}', flush=True)
    for source, target in preview['operations']:
        check_cancel()
        step = dict(source=source, target=target, identity=identity(source), state='pending')
        journal['steps'].append(step)
        save(path, journal)
        if os.path.lexists(target):
            raise FileExistsError(target)
        os.rename(source, target)
        step['state'] = 'done'
        save(path, journal)
        print(f'変更: {source} → {target}', flush=True)
    return path


def restore_plan(path):
    journal = json.loads(Path(path).read_text(encoding='utf-8'))
    if journal.get('version') != 1:
        raise ValueError('サンプル名変更の履歴ではありません。')
    operations = []
    identities = []
    for step in reversed(journal['steps']):
        check_cancel()
        if step['state'] == 'restored':
            continue
        source, target = step['source'], step['target']
        if (step['state'] == 'restoring' and os.path.lexists(source)
                and not os.path.lexists(target) and identity(source) == step['identity']):
            continue
        if step['state'] == 'pending' and os.path.lexists(source) and not os.path.lexists(target):
            continue
        operations.append([target, source])
        identities.append(step)
    # Parent folder changes are undone first; locate the current sample root.
    root = journal['root']
    for step in journal['steps']:
        if step['source'] == root and os.path.lexists(step['target']):
            root = step['target']
    scope = journal.get('scope', 'all')
    snapshot = inventory(root, scope)
    # Simulate path moves while checking identity, including nested renames.
    virtual = dict(snapshot)
    for (source, target), step in zip(operations, identities):
        check_cancel()
        if virtual.get(source) != step['identity']:
            raise ValueError(f'変更後に内容が変わったか、対象を特定できません: {source}')
        affected = ([p for p in virtual if p == source or p.startswith(source + os.sep)]
                    if step['identity'][2] is None else [source])
        moved = {target + p[len(source):]: virtual.pop(p) for p in affected}
        virtual.update(moved)
    preflight(snapshot, operations)
    return dict(root=root, scope=scope, snapshot=snapshot, operations=operations, history=str(path))


def restore(preview):
    current = restore_plan(preview['history'])
    if current != preview:
        raise ValueError('確認後に対象が変わりました。復元予定を再表示してください。')
    path = Path(preview['history'])
    journal = json.loads(path.read_text(encoding='utf-8'))
    for source, target in preview['operations']:
        check_cancel()
        if os.path.lexists(target):
            raise FileExistsError(target)
        step = next(s for s in reversed(journal['steps'])
                    if s['target'] == source and s['source'] == target)
        step['state'] = 'restoring'
        save(path, journal)
        os.rename(source, target)
        step['state'] = 'restored'
        save(path, journal)
        print(f'復元: {source} → {target}', flush=True)
