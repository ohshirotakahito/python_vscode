"""Git fast-forward updates; running versions stay fixed until restart."""
import subprocess
import sys
from dataclasses import dataclass

from shared.app_release import ROOT, load_releases, validate_releases


def git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True,
                            encoding='utf-8', errors='replace', timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


@dataclass
class UpdateState:
    running: str
    installed: str
    available: str | None
    behind: int
    target: str

    @property
    def restart_needed(self):
        return self.running != self.installed


def check(app_id, running):
    installed = load_releases()['apps'][app_id]['version']
    git('fetch', '--quiet')
    target = git('rev-parse', '--symbolic-full-name', '@{upstream}')
    if not git('ls-tree', '--name-only', target, '--', 'app_versions.json'):
        # An older distribution has no release manifest yet. Do not offer an
        # unversioned update or confuse this with a connection failure.
        return UpdateState(running, installed, None, 0, '')
    # Validate the remote manifest using exactly the same rules as local releases.
    import json
    remote = validate_releases(json.loads(git('show', f'{target}:app_versions.json')))
    available = remote['apps'][app_id]['version']
    behind = int(git('rev-list', '--count', f'HEAD..{target}'))
    return UpdateState(running, installed, available, behind, git('rev-parse', target))


def install(state):
    if state.available is None or not state.target:
        raise RuntimeError('配布元のバージョン情報が未公開のため更新できません。')
    if git('status', '--porcelain'):
        raise RuntimeError('ローカルの変更があるため更新できません。変更を保存・コミットしてから再確認してください。')
    git('merge', '--ff-only', state.target)


def restart(close):
    """Ask the normal close handler first, so applications can veto closing."""
    if close() is False:
        return
    subprocess.Popen([sys.executable, *sys.argv], cwd=str(__import__('pathlib').Path.cwd()))
