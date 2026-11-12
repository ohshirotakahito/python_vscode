"""履歴（ジャーナル）を残すファイル移動と、その取り消し（実験ファイルDB と Analyzer で共通）。

計画（plan）は dict(folder, operations=[dict(source, target, size, modified_ns, status)]) で、
execute_moves() が移動前に全対応を JSON に保存し、1件ごとに状態を書き足す。undo_plan() でその JSON から戻せる。
履歴は journal_dir() のフォルダ（database/file_logs/）に保存し、どのアプリからの移動も
シグナル解析の「整理の履歴…」から元に戻せる。
"""
from collections import Counter
from datetime import datetime
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from shared import REPO_ROOT
from shared.rack_store import as_numbers, measurement_key


LOG_ROOT = REPO_ROOT / 'database' / 'file_logs'
LEGACY_LOG_ROOT = REPO_ROOT / 'filemanager'  # ファイル管理アプリを実験ファイルDBへ統合する前の置き場所


def log_dir(name):
    """記録フォルダ database/file_logs/<name>。旧 filemanager/<name> に残っている記録は、ここへ移してから返す。

    Git 管理外の記録は PC ごとにあるので、各 PC で初めて使ったときに移す。同名がすでにある項目は移さない。"""
    folder = LOG_ROOT / name
    legacy = LEGACY_LOG_ROOT / name
    if legacy.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        for item in legacy.iterdir():
            if not (folder / item.name).exists():
                try:
                    os.rename(item, folder / item.name)
                except OSError:
                    pass  # 別のアプリが同時に移した・使用中: 次の呼び出しで再試行する
        try:
            legacy.rmdir()
        except OSError:
            pass
    return folder


def journal_dir(entity):
    """移動履歴の保存先: 'ANAL' -> database/file_logs/anal_logs, 'BNAL' -> bnal_logs, 'T_stock' -> t_stock_logs。"""
    return log_dir(f'{entity.lower()}_logs')


def scan_anal(folder, entity='ANAL'):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f'{entity}フォルダが見つかりません: {folder}')
    result = []
    for parent, location in ((folder, entity), (folder / 'stocked', 'stocked')):
        if not parent.exists():
            continue
        for path in sorted(parent.iterdir()):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() != '.tdms':
                continue
            stat = path.stat()
            result.append(dict(path=str(path.absolute()), location=location, key=measurement_key(path.name),
                               size=stat.st_size, modified_ns=stat.st_mtime_ns))
    # Same measurement-number rule as the database's ANAL progress (rack_store.measurement_key / as_numbers).
    numbers = as_numbers(r['key'] for r in result)
    for row in result:
        key = numbers[row.pop('key')]
        row['number'] = key if isinstance(key, int) else None
    counts = Counter(r['number'] for r in result if r['number'] is not None)
    for row in result:
        row['candidate'] = row['number'] is not None and counts[row['number']] > 1
        match = re.search(r'@@(\d{4})_(\d{2})(\d{2})_', Path(row['path']).name)
        try:
            date = datetime.strptime(''.join(match.groups()), '%Y%m%d').date() if match else None
        except ValueError:
            date = None
        row['group'] = str(date or datetime.fromtimestamp(row['modified_ns'] / 1e9).date())
        row['date_source'] = 'ファイル名' if date else '更新日時'
    return result


def checksum(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def validate_operations(plan):
    folder = Path(plan['folder']).absolute()
    suffixes = tuple(plan.get('suffixes', ('.tdms',)))
    seen = set()
    for op in plan['operations']:
        source, target = Path(op['source']), Path(op['target'])
        for path in (source, *source.parents, target.parent, *target.parent.parents):
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError(f'リンク経由の移動は扱えません: {path}')
        if not source.is_absolute() or not target.is_absolute() or source == target:
            raise ValueError('移動パスが不正です。')
        is_dir = op.get('dir', False)  # BNAL per-file result folders move as a whole
        for path in (source, target):
            if not path.resolve().is_relative_to(folder.resolve()) or (not is_dir and path.suffix.lower() not in suffixes):
                raise ValueError(f'選択した整理対象フォルダ配下のTDMS以外は移動できません: {path}')
        if os.path.lexists(target) or target in seen:
            raise ValueError(f'移動先に同名があります: {target}')
        stat = source.stat()
        if (not (source.is_dir() if is_dir else source.is_file())
                or (0 if is_dir else stat.st_size, stat.st_mtime_ns) != (op['size'], op['modified_ns'])):
            raise ValueError(f'調査後に変更されています。再調査してください: {source}')
        if op.get('sha256') and checksum(source) != op['sha256']:
            raise ValueError(f'操作後に内容が変更されています: {source}')
        seen.add(target)
    if not seen:
        raise ValueError('操作するファイルを選択してください。')


def result_folder(tdms):
    """Per-file result folder of a BNAL .tdms: 'B@@<stamp>_<rest>.tdms' -> 'B@@<stamp>@<rest>', or None."""
    tdms = Path(tdms)
    match = re.match(r'^(B@@\d{4}_\d{4}_\d{4}(?:_\d{2})?)_(.+)\.tdms$', tdms.name, re.I)
    if not match:
        return None
    folder = tdms.parent / f'{match[1]}@{match[2]}'
    return folder if folder.is_dir() and not folder.is_symlink() else None


def deletion_plan(folder, selected, with_results=False):
    """with_results: BNAL files take their result folder along (it would otherwise be left orphaned)."""
    folder = Path(folder).absolute()
    batch = uuid.uuid4().hex
    operations = []
    for row in selected:
        source = Path(row['path'])
        if source.parent not in (folder, folder / 'stocked'):
            raise ValueError('削除対象は選択した整理対象フォルダまたはstocked直下から選択してください。')
        target = folder / '.整理履歴' / batch / row['location'] / source.name
        operations.append(dict(source=str(source), target=str(target), size=row['size'],
                               modified_ns=row['modified_ns'], status='予定'))
        results = result_folder(source) if with_results else None
        if results is not None:
            operations.append(dict(source=str(results), target=str(target.parent / results.name), size=0,
                                   modified_ns=results.stat().st_mtime_ns, dir=True, status='予定'))
    plan = dict(folder=str(folder), action='delete', operations=operations)
    validate_operations(plan)
    return plan


def undo_plan(history):
    record = json.loads(Path(history).read_text(encoding='utf-8'))
    if record.get('action') == 'undo':
        raise ValueError('元に戻す操作の履歴です。元の整理操作の履歴を選択してください。')
    operations = []
    for op in record['operations']:
        if op.get('status') != '完了':
            continue
        operations.append(dict(source=op['target'], target=op['source'], size=op['size'], dir=op.get('dir', False),
                               modified_ns=op['modified_ns'], sha256=op.get('sha256', ''), status='予定'))
    plan = dict(folder=record['folder'], action='undo', undo_of=str(Path(history).absolute()), operations=operations)
    for key in ('suffixes', 'verify_content'):
        if key in record:
            plan[key] = record[key]
    validate_operations(plan)
    return plan


def move_plan(folder, selected, restore=False):
    folder = Path(folder).absolute()
    source_parent = folder / 'stocked' if restore else folder
    target_parent = folder if restore else folder / 'stocked'
    operations = []
    seen = set()
    for row in selected:
        source = Path(row['path'])
        target = target_parent / source.name
        if source.parent != source_parent or source.suffix.lower() != '.tdms':
            raise ValueError('移動元の場所が違います。取り出しはstocked直下のTDMSを選択してください。')
        # Never move through symlink/junction ancestors, including a redirected stocked.
        for path in (source, *source.parents, target_parent, *target_parent.parents):
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError(f'リンク経由の移動は扱えません: {path}')
        stat = source.stat()
        if not source.is_file() or (stat.st_size, stat.st_mtime_ns) != (row['size'], row['modified_ns']):
            raise ValueError(f'調査後に変更されています。再調査してください: {source}')
        if os.path.lexists(target):
            raise ValueError(f'移動先に同名があるため実行できません: {target}')
        if target in seen:
            raise ValueError('同じファイルが複数選択されています。')
        seen.add(target)
        operations.append(dict(source=str(source), target=str(target), size=row['size'],
                               modified_ns=row['modified_ns'], status='予定'))
    if not operations:
        raise ValueError('移動するファイルを選択してください。')
    return dict(folder=str(folder), restore=restore, operations=operations)


def replace_retrying(source, target, tries=20):
    """os.replace that waits out a brief Windows lock (virus scanner, indexer or editor reading the new journal)."""
    for attempt in range(tries):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.1 * (attempt + 1))  # up to about 20 seconds in all


def execute_moves(plan, log_dir):
    plan = copy.deepcopy(plan)
    validate_operations(plan)
    # Read contents before writes; retain fingerprints for later undo verification.
    # verify_content=False: same-share renames of large originals; size+mtime still guard every move.
    if plan.get('verify_content', True):
        for op in plan['operations']:
            if not op.get('dir'):
                op['sha256'] = checksum(op['source'])
        validate_operations(plan)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / (datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex + '.json')
    record = dict(plan, created_at=datetime.now().astimezone().isoformat(), status='実行中')

    def save():
        temp = log.with_suffix('.tmp')
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        replace_retrying(temp, log)

    save()  # Persist the complete source/target mapping before any move.
    try:
        for operation in plan['operations']:
            source, target = Path(operation['source']), Path(operation['target'])
            validate_operations(dict(plan, operations=[operation]))
            target.parent.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt' or operation.get('dir'):
                os.rename(source, target)  # Windows refuses to overwrite an existing destination.
            else:
                os.link(source, target)
                source.unlink()
            operation['status'] = '完了'
            save()
        record['status'] = '完了'
    except Exception as exc:
        record.update(status='途中停止', error=str(exc))
        raise RuntimeError(f'{exc}\n実行履歴: {log}') from exc
    finally:
        save()
    return log
