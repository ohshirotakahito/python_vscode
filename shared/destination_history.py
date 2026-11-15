"""Share compact directory tracking records; retain full undo journals locally.

Transfers retain their original destination. Only confirmed directory operations
after the transfer may change the destination used to look up analysis progress.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
import csv
import json
import hashlib
import math
import os
import re
import socket
import uuid
from pathlib import Path, PureWindowsPath

from shared import file_moves, machinedb
from shared.ex_info_links import path_key

SHARED_MOVES = machinedb.SEQ / 'DestinationMoves'


def timestamp(value):
    try:
        return datetime.fromisoformat(value).astimezone().timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class Move:
    source: str
    target: str
    at: float | None
    confirmed: bool = True


def journal_key(path, record):
    identity = [Path(path).name, record.get('created_at'), record.get('root', record.get('folder'))]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode('utf-8')).hexdigest()


def record_moves(record):
    moves = []
    if 'steps' in record and record.get('version') == 1:
        for step in record['steps']:
            identity = step.get('identity', [])
            if len(identity) != 4 or identity[2] is not None:
                continue
            source, target = step['source'], step['target']
            state = step.get('state')
            moves.append(Move(source, target,
                timestamp(step.get('moved_at', record.get('created_at'))),
                state in ('done', 'restoring', 'restored')))
            if state in ('restoring', 'restored'):
                moves.append(Move(target, source, timestamp(step.get('restored_at')), state == 'restored'))
    elif 'operations' in record and 'created_at' in record:
        for op in record['operations']:
            if op.get('dir'):
                moves.append(Move(op['source'], op['target'],
                    timestamp(op.get('moved_at', record['created_at'])), op.get('status') == '完了'))
    return moves


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _send(snapshot, shared_folder):
    if (snapshot.get('schema_version') != 1
            or not isinstance(snapshot.get('journal_key'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', snapshot['journal_key'])
            or type(snapshot.get('revision')) is not int or snapshot['revision'] < 1):
        raise ValueError('invalid tracking snapshot')
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    target = shared_folder / f"{snapshot['journal_key']}_{snapshot['revision']:016d}_{digest}.json"
    if not target.exists() or target.read_bytes() != raw:
        _write_atomic(target, raw)


def share_journal(path, record, shared_folder=None, warn=print):
    """Keep a durable local outbox and publish compact, immutable journal revisions.

    UNC directory mappings only: a PC-local drive letter cannot identify a share
    on another PC. Sharing errors never discard or undo the local operation.
    """
    try:
        moves = [asdict(move) for move in record_moves(record)
                 if all(PureWindowsPath(p).drive.startswith('\\\\') for p in (move.source, move.target))]
        if not moves:
            return True
        key = journal_key(path, record)
        outbox = Path(path).parent / '.tracking' / (key + '.json')
        previous = json.loads(outbox.read_text(encoding='utf-8')) if outbox.exists() else None
        if previous is not None and previous['moves'] == moves:
            snapshot = previous
        else:
            snapshot = dict(schema_version=1, journal_key=key,
                            revision=previous['revision'] + 1 if previous else 1,
                            pc=socket.gethostname(), moves=moves)
            _write_atomic(outbox, json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode('utf-8'))
        _send(snapshot, SHARED_MOVES if shared_folder is None else Path(shared_folder))
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        warn(f'保存先の追跡記録を共有できません（ローカル履歴は保持）: {path}: {exc}')
        return False


def flush_pending(folder=None, shared_folder=None, warn=print):
    """Retry this PC's saved outbox, without scanning or writing another PC's journals."""
    folder = file_moves.LOG_ROOT if folder is None else Path(folder)
    shared_folder = SHARED_MOVES if shared_folder is None else Path(shared_folder)
    success = True
    for path in folder.rglob('.tracking/*.json'):
        try:
            _send(json.loads(path.read_text(encoding='utf-8')), shared_folder)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            warn(f'保存先の追跡記録を再送できません: {path}: {exc}')
            success = False
    return success


def sync_journals(folder=None, shared_folder=None, warn=print):
    """Explicitly publish existing journals or retry sharing after an outage."""
    folder = file_moves.LOG_ROOT if folder is None else Path(folder)
    success = True
    for path in folder.rglob('*.json'):
        if {'previews', '.tracking'}.intersection(path.relative_to(folder).parts):
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8-sig'))
            success = share_journal(path, record, shared_folder, warn) and success
        except (OSError, ValueError, TypeError) as exc:
            warn(f'保存先の追跡履歴を読めません: {path}: {exc}')
            success = False
    return flush_pending(folder, shared_folder, warn) and success


def _shared_journals(folder):
    latest, problems = {}, []
    for path in sorted(folder.glob('*.json')):
        try:
            raw = path.read_bytes()
            record = json.loads(raw.decode('utf-8-sig'))
            key, revision = record['journal_key'], record['revision']
            if (record.get('schema_version') != 1 or not isinstance(key, str) or len(key) != 64
                    or type(revision) is not int or revision < 1 or not isinstance(record['moves'], list)):
                raise ValueError('invalid tracking record')
            digest = hashlib.sha256(raw).hexdigest()
            if not path.name.startswith(f'{key}_{revision:016d}_{digest}'):
                raise ValueError('tracking record checksum mismatch')
            moves = []
            for row in record['moves']:
                move = Move(**row)
                if (not isinstance(move.source, str) or not move.source or not isinstance(move.target, str)
                        or not move.target or type(move.confirmed) is not bool
                        or (move.at is not None and (type(move.at) not in (int, float) or not math.isfinite(move.at)))):
                    raise ValueError('invalid move')
                moves.append(move)
            old = latest.get(key)
            if old is None or revision > old[0]:
                latest[key] = (revision, moves, False)
            elif revision == old[0] and moves != old[1]:
                latest[key] = (revision, old[1], True)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            problems.append(Move('', '', None, False))
    for _, _, conflict in latest.values():
        if conflict:
            problems.append(Move('', '', None, False))
    return {key: moves for key, (_, moves, _) in latest.items()}, problems


def load_moves(folder=None, shared_folder=None):
    """Read local and shared journals; no legacy migration, server scans or writes.

    An explicit local folder reads only that folder unless shared_folder is also
    supplied. Otherwise the default includes SEQ/DestinationMoves. Local journals
    override shared copies of the same journal; each remote journal uses its latest revision.

    CSV rename logs have no durable undo state and cannot prove a current path.
    An unreadable JSON journal makes tracking uncertain, rather than silently
    allowing a same-named sample to be treated as the original.
    """
    include_shared = folder is None or shared_folder is not None
    folder = file_moves.LOG_ROOT if folder is None else Path(folder)
    moves = []
    local_keys = set()
    for path in sorted(folder.rglob('*.json')):
        # Preview plans are not evidence that a move happened.
        if {'previews', '.tracking'}.intersection(path.relative_to(folder).parts):
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(record, dict):
                raise ValueError('journal is not an object')
            local_keys.add(journal_key(path, record))
            moves.extend(record_moves(record))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            moves.append(Move('', '', None, False))
    for path in sorted(folder.rglob('*.csv')):
        if 'previews' in path.relative_to(folder).parts:
            continue
        try:
            with path.open(encoding='utf-8-sig', newline='') as stream:
                for row in csv.DictReader(stream):
                    if row.get('item_type') == 'folder' and row.get('old_path') and row.get('new_path'):
                        # Legacy undo writes no timestamp; either end may have been transferred later.
                        moves.extend([Move(row['old_path'], row['new_path'], None, False),
                                      Move(row['new_path'], row['old_path'], None, False)])
        except (OSError, ValueError, csv.Error):
            moves.append(Move('', '', None, False))
    if include_shared:
        shared, problems = _shared_journals(SHARED_MOVES if shared_folder is None else Path(shared_folder))
        moves.extend(problems)
        for key, rows in shared.items():
            if key not in local_keys:
                moves.extend(rows)
    return sorted(moves, key=lambda move: move.at if move.at is not None else float('inf'))


def trace(destination, transferred_at, moves):
    """(current path, changed, uncertain), using path boundaries and transfer time.

    Missing times or pending operations are never used as confirmed mappings.
    The original JSONL record is not changed, including after an undo.
    """
    current, changed, uncertain = str(destination), False, False
    since = timestamp(transferred_at)
    for move in moves:
        if not move.source:
            uncertain = True
            continue
        source, key = path_key(move.source), path_key(current)
        if key != source and not key.startswith(source + '\\'):
            continue
        if since is not None and move.at is not None and move.at <= since:
            continue
        if since is None or move.at is None or not move.confirmed:
            uncertain = True
            continue
        old = PureWindowsPath(move.source)
        current = str(PureWindowsPath(move.target) / PureWindowsPath(current).relative_to(old))
        changed = True
    return current, changed, uncertain


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='保存先追跡のローカル履歴を共有・再送する')
    parser.add_argument('--sync', action='store_true', required=True)
    args = parser.parse_args()
    raise SystemExit(0 if sync_journals() else 1)
