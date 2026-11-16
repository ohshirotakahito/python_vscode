"""Exercise the actual fetch/fast-forward flow against a temporary local origin."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared import app_update, terminal_profile
from shared.app_release import load_releases
from tools.app_version import bump


class CheckoutTests(unittest.TestCase):
    def test_user_update_preserves_pc_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            origin, client = Path(directory) / 'origin', Path(directory) / 'client'
            origin.mkdir()

            def git(root, *args):
                return subprocess.run(['git', '-C', str(root), *args], check=True,
                                      capture_output=True, timeout=20)

            git(origin, 'init', '-b', 'main')
            git(origin, 'config', 'user.name', 'Test')
            git(origin, 'config', 'user.email', 'test@example.invalid')
            manifest = load_releases()
            previous = manifest['apps']['viewer']['version']
            path = origin / 'app_versions.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            (origin / '.gitignore').write_text('.workbench/\n', encoding='utf-8')
            git(origin, 'add', '.')
            git(origin, 'commit', '-m', 'Initial')
            git(origin, 'clone', str(origin), str(client))
            selection = client / '.workbench' / 'terminal.json'
            terminal_profile.save('user', ['viewer'], selection)
            before = selection.read_bytes()
            untracked = client / 'filemanager' / 'local.txt'
            untracked.parent.mkdir()
            untracked.write_text('local data', encoding='utf-8')
            updated = bump(manifest, 'viewer', 'patch', 'Update', '2026-11-15')
            path.write_text(json.dumps(manifest), encoding='utf-8')
            git(origin, 'add', '.')
            git(origin, 'commit', '-m', 'Update viewer')
            with patch.object(app_update, 'ROOT', client), patch.object(
                    app_update, 'load_releases', side_effect=lambda: load_releases(client / 'app_versions.json')):
                state = app_update.check('viewer', previous)
                self.assertEqual(state.behind, 1)
                self.assertEqual(state.available, updated)
                app_update.install(state)
                after = app_update.check('viewer', previous)
                self.assertTrue(after.restart_needed)
                self.assertEqual(after.behind, 0)
                # A subsequent update that collides with local data must fail safely.
                incoming = origin / 'filemanager' / 'local.txt'
                incoming.parent.mkdir()
                incoming.write_text('remote data', encoding='utf-8')
                git(origin, 'add', '.')
                git(origin, 'commit', '-m', 'Add conflicting file')
                blocked = app_update.check('viewer', previous)
                with self.assertRaises(RuntimeError):
                    app_update.install(blocked)
                self.assertEqual(untracked.read_text(encoding='utf-8'), 'local data')
                self.assertEqual(app_update.git('rev-list', '--count', 'HEAD..@{upstream}'), '1')
            self.assertEqual(selection.read_bytes(), before)
            self.assertEqual(untracked.read_text(encoding='utf-8'), 'local data')
            self.assertEqual(load_releases(client / 'app_versions.json')['apps']['viewer']['version'], updated)


if __name__ == '__main__':
    unittest.main()
