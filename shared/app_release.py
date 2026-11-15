"""Independent application releases, shared by direct and launcher startup."""
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'app_versions.json'
ICON = ROOT / 'assets' / 'quantum-workbench.ico'


def load_releases(path=MANIFEST):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return validate_releases(data)


def validate_releases(data):
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported application release manifest')
    for app_id, app in data['apps'].items():
        releases = app['releases']
        if not releases or app['version'] != releases[0]['version']:
            raise ValueError(f'{app_id}: current version must match latest release')
        versions = [release['version'] for release in releases]
        if any(not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', v)
               for v in versions) or len(versions) != len(set(versions)):
            raise ValueError(f'{app_id}: invalid or duplicate release version')
        ordered = [tuple(map(int, v.split('.'))) for v in versions]
        if ordered != sorted(ordered, reverse=True):
            raise ValueError(f'{app_id}: releases must be newest first')
        for release in releases:
            date.fromisoformat(release['date'])
            if not release['notes'] or any(not isinstance(note, str) or not note.strip()
                                           for note in release['notes']):
                raise ValueError(f'{app_id}: release notes are required')
    return data


def version(app_id):
    return load_releases()['apps'][app_id]['version']


def window_title(app_id, title):
    return f'{title} · version {version(app_id)}'


def configure_tk(root, app_id):
    """Call after assigning the application's normal title."""
    from shared.terminal_profile import require
    require(app_id)
    from shared.app_icons import icon_path, set_taskbar_identity
    set_taskbar_identity(app_id)
    root.title(window_title(app_id, root.title()))
    root.app_id = app_id
    from shared.update_ui import tk_controls
    tk_controls(root, app_id)
    app_icon = icon_path(app_id)
    if app_icon.exists():
        import tkinter
        try:
            root.iconbitmap(default=str(app_icon))
            root.iconbitmap(str(app_icon))
        except tkinter.TclError:
            # Non-Windows Tk may not support ICO. Version display still works.
            pass

