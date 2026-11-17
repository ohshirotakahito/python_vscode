"""PC-local application selection; never distributed by Git."""
import json
from pathlib import Path

from shared.app_release import ROOT, load_releases

PROFILE = ROOT / '.workbench' / 'terminal.json'
DEFAULT_USER_APPS = ('exmanager', 'viewer', 'chipdb')


def load(path=PROFILE):
    path = Path(path)
    if not path.exists():
        return {'role': 'user', 'apps': list(DEFAULT_USER_APPS)}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('role') not in ('developer', 'user'):
        raise ValueError('端末のroleはdeveloperまたはuserを指定してください。')
    apps = data.get('apps', [])
    known = set(load_releases()['apps']) - {'workbench'}
    if not isinstance(apps, list) or any(not isinstance(app, str) or app not in known for app in apps):
        raise ValueError('端末設定に不明なアプリが指定されています。')
    apps = list(dict.fromkeys(apps))
    # Upgrade historical standard profiles; explicitly saved version-3 choices
    # and other custom selections remain authoritative. Never rewrite on load.
    version = data.get('profile_version', 1)
    standard = ((version == 1 and apps == ['exmanager']) or
                (version in (1, 2) and apps == ['exmanager', 'viewer']))
    if data['role'] == 'user' and standard:
        apps = list(DEFAULT_USER_APPS)
    return {'role': data['role'], 'apps': apps}


def allowed(app_id, profile=None):
    profile = load() if profile is None else profile
    return app_id == 'workbench' or profile['role'] == 'developer' or app_id in profile['apps']


def require(app_id):
    if not allowed(app_id):
        raise PermissionError('この端末ではこのアプリの使用が許可されていません。端末設定を確認してください。')


def save(role, apps, path=PROFILE):
    path = Path(path)
    known = set(load_releases()['apps']) - {'workbench'}
    if role not in ('developer', 'user') or any(app not in known for app in apps):
        raise ValueError('端末の役割またはアプリIDが不正です。')
    data = {'profile_version': 3, 'role': role, 'apps': list(dict.fromkeys(apps))}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return data
