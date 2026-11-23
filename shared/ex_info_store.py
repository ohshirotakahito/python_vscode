"""Ex Info metadata edits, compatible with AXN's lock/revision/backup protocol.

Only Owners and descriptions are editable here. UUIDs, names, locations and
unknown fields are preserved. This does not rename or create server folders.
"""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
from uuid import uuid4

from shared import ex_info_links

EDITABLE = {'projects': {'owner_names', 'description'}, 'samples': {'description'}}


def validate(data):
    if (not isinstance(data, dict) or data.get('schema_version') != 1
            or type(data.get('revision')) is not int or data['revision'] < 0):
        raise ValueError('Ex Infoの形式・版番号が不正です')
    keys = set()
    for group in ('projects', 'samples', 'locations'):
        if not isinstance(data.get(group), list):
            raise ValueError('Ex Infoの一覧が不正です')
        for row in data[group]:
            if not isinstance(row, dict) or not isinstance(row.get('key'), str) or row['key'] in keys:
                raise ValueError('Ex Infoのキーが不正・重複しています')
            keys.add(row['key'])
            if group != 'locations' and (not isinstance(row.get('name'), str)
                    or not isinstance(row.get('description', ''), str)):
                raise ValueError('Ex Infoの名前・説明が不正です')
    owners = [data.get('owner_names', [])] + [p.get('owner_names', []) for p in data['projects']]
    if any(not isinstance(names, list) or any(not isinstance(n, str) for n in names) for names in owners):
        raise ValueError('Ex Infoの責任者が不正です')
    projects = {p['key'] for p in data['projects']}
    samples = {s['key']: s for s in data['samples']}
    if any(row.get('project_key') not in projects for row in data['samples'] + data['locations']):
        raise ValueError('Ex Infoの研究への対応が不正です')
    for loc in data['locations']:
        key = loc.get('sample_key')
        if key and (key not in samples or samples[key]['project_key'] != loc['project_key']):
            raise ValueError('Ex Infoの保存場所の対応が不正です')
    return data


def load(path=None):
    path = ex_info_links.LEDGER if path is None else Path(path)
    try:
        return validate(json.loads(path.read_text(encoding='utf-8-sig')))
    except FileNotFoundError:
        return dict(schema_version=1, revision=0, projects=[], samples=[], locations=[])


def save_changes(baseline, changes, path=None):
    """Merge changed fields into the latest ledger; reject same-field changes.

    changes: {(group, UUID): {field: value}}. The lock is the same ex_info.lock
    used by AXN. Sync conflict copies block saving; the lock is PC-local until
    synchronized and cannot provide a distributed transaction across offline PCs.
    """
    path = ex_info_links.LEDGER if path is None else Path(path)
    validate(baseline)
    for (group, key), fields in changes.items():
        if group not in EDITABLE or not set(fields) <= EDITABLE[group]:
            raise ValueError('この画面では責任者と説明だけを編集できます')
        for field, value in fields.items():
            if field == 'owner_names':
                if not isinstance(value, list) or any(not isinstance(n, str) or not n.strip()
                        or any(c in n for c in ',<>:"/\\|?*\r\n') or any(ord(c) < 32 for c in n) for n in value):
                    raise ValueError('責任者は空欄を除き、1行に1人ずつ入力してください')
            elif not isinstance(value, str):
                raise ValueError('説明は文字列で入力してください')
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise OSError('Ex Infoは別の画面で保存中です。少し待って再読み込みしてください') from None
    temporary = path.with_name(f'.ex_info_{uuid4().hex}.tmp')
    try:
        os.close(descriptor)
        if any(p != path for p in path.parent.glob('ex_info*.json')):
            raise OSError('Ex Infoに同期競合コピーがあります。AXNのEx Infoで確認・統合してください')
        current = load(path)
        edited = copy.deepcopy(current)
        changed = False
        for (group, key), fields in changes.items():
            old = next((r for r in baseline[group] if r['key'] == key), None)
            row = next((r for r in edited[group] if r['key'] == key), None)
            if old is None or row is None:
                raise ValueError('対象が変更・削除されています。再読み込みしてください')
            for field, value in fields.items():
                default = [] if field == 'owner_names' else ''
                if value == old.get(field, default):
                    continue
                if row.get(field, default) not in (old.get(field, default), value):
                    raise ValueError('同じ項目が別の画面で変更されています。再読み込みして確認してください')
                if row.get(field, default) != value:
                    row[field] = copy.deepcopy(value)
                    row['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='microseconds')
                    changed = True
        if not changed:
            return current
        edited['owner_names'] = sorted(set(edited.get('owner_names', [])) |
            {name for p in edited['projects'] for name in p.get('owner_names', [])}, key=str.casefold)
        edited.update(revision=current['revision'] + 1,
                      updated_at=datetime.now(timezone.utc).isoformat(timespec='microseconds'), by=socket.gethostname())
        validate(edited)
        if path.exists():
            backup = path.parent / 'backups'
            backup.mkdir(exist_ok=True)
            shutil.copy2(path, backup / f'ex_info_{uuid4().hex}.json')
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(edited, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return edited
    finally:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            lock.unlink()
