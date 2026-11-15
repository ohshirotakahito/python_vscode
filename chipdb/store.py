"""Lossless AXN ChipDB files, independent of AXN's device dependencies."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


def settings_path():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.config')) / 'QuantumWorkbench' / 'chip_atlas.json'


def save_root(root):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'shared_data_root': str(Path(root).resolve())}, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def default_root():
    # An explicit selection stays authoritative even when temporarily offline.
    try:
        value = json.loads(settings_path().read_text(encoding='utf-8'))['shared_data_root']
        if isinstance(value, str) and value:
            return Path(value)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    override = os.environ.get('AXN_LABVIEW_ROOT')
    if override:
        return Path(override)
    settings = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.config')) / 'AXN/settings.json'
    try:
        return Path(json.loads(settings.read_text(encoding='utf-8'))['shared_data_root'])
    except (OSError, ValueError, KeyError, TypeError):
        return Path.home() / 'Dropbox' / 'ou&t'


def db_folder(root):
    root = Path(root)
    if root.name.casefold() == 'db':
        return root
    if root.name.casefold() == 'chipdb':
        return root / 'DB'
    return root / 'ChipDB' / 'DB'


class Document:
    def __init__(self, path):
        self.path = Path(path)
        self.original = self.path.read_bytes()
        self.rows = [line.split(',') for line in self.original.decode('cp932').splitlines()]

    @property
    def experiments(self):
        return [r for r in self.rows if len(r) > 6 and r[0] == 'Experiment']

    def save(self, rows):
        if len(rows) != len(self.rows) or any(len(a) != len(b) for a, b in zip(rows, self.rows)):
            raise ValueError('行数・列数は変更できません。')
        for row in rows:
            for value in row:
                if any(c in value for c in ',\r\n\x00\v\f\x85\u2028\u2029'):
                    raise ValueError('カンマ・改行・制御文字は保存できません。')
        if [r[0] for r in rows if r][-2:] != ['Break', 'END']:
            raise ValueError('AXN形式の Break / END がありません。')
        data = ''.join(','.join(r) + '\r\n' for r in rows).encode('cp932')
        if self.path.read_bytes() != self.original:
            raise ValueError('外部で更新されています。再読み込みしてください。')
        backup_dir = self.path.parent.parent / 'WorkbenchBackups'
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f'{self.path.stem}_{datetime.now():%Y%m%d_%H%M%S_%f}.csv'
        backup.write_bytes(self.original)
        fd, name = tempfile.mkstemp(prefix='.workbench-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            if self.path.read_bytes() != self.original:
                raise ValueError('保存直前に外部更新を検出しました。再読み込みしてください。')
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self.original, self.rows = data, [list(r) for r in rows]
        return backup
