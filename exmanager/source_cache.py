"""Last source-folder listing per machine/selection, so a rescan can be skipped."""
from datetime import datetime
import json
import os
from pathlib import Path

import pc_config

DEFAULT_PATH = pc_config.DEFAULT_DIR / 'source_cache.json'


def _key(machine, selection):
    return f'{machine}|{selection}'


def _read(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(machine, selection, path=DEFAULT_PATH):
    """Return {'updated', 'root', 'folders': [[name, count10k, count100k], ...]} or None."""
    entry = _read(path).get(_key(machine, selection))
    if not isinstance(entry, dict) or not isinstance(entry.get('folders'), list):
        return None
    return entry


def save(machine, selection, root, folders, path=DEFAULT_PATH, now=None):
    path = Path(path)
    data = _read(path)
    entry = dict(updated=(now or datetime.now()).strftime('%Y/%m/%d %H:%M:%S'), root=str(root),
                 folders=[[name, *counts] for name, counts in folders])
    data[_key(machine, selection)] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return entry
