"""Local PC identity and execution role; not a remote worker service."""
import hashlib
import json
import os
from pathlib import Path
import socket
import uuid

ROLES = {'transfer': '転送担当', 'viewer': '操作・閲覧'}
DEFAULT_DIR = Path(__file__).resolve().parent / '.local'


def machine_key():
    identity = socket.gethostname()
    if os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            identity = winreg.QueryValueEx(key, 'MachineGuid')[0]
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]


def save(config, directory=DEFAULT_DIR):
    if config['role'] not in ROLES or not config['display_name'].strip():
        raise ValueError('PC名と役割を指定してください')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"pc_{config['machine_key']}.json"
    temp = path.with_suffix('.tmp')
    try:
        temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def load(directory=DEFAULT_DIR, key=None):
    key = key or machine_key()
    path = Path(directory) / f'pc_{key}.json'
    if path.exists():
        config = json.loads(path.read_text(encoding='utf-8'))
        if config.get('machine_key') != key or config.get('role') not in ROLES:
            raise ValueError(f'PC設定が不正です: {path}')
        uuid.UUID(config['pc_id'])
        return config
    config = dict(machine_key=key, pc_id=str(uuid.uuid4()), display_name=socket.gethostname(), role='viewer')
    save(config, directory)
    return config
