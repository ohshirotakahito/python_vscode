"""Run: python -m unittest shared.test_app_release"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, call

from shared import app_release
from tools.app_version import bump


class AppReleaseTests(unittest.TestCase):
    def test_launcher_covers_every_registered_application(self):
        from launcher import APPS
        registered = set(app_release.load_releases()['apps'])
        self.assertEqual({app.app_id for app in APPS} | {'workbench'}, registered)

    def test_independent_bump_records_history_without_changing_other_apps(self):
        for part, expected in [('patch', '0.1.1'), ('minor', '0.2.0'), ('major', '1.0.0')]:
            with self.subTest(part=part):
                data = app_release.load_releases()
                data['apps']['viewer']['version'] = '0.1.0'
                data['apps']['viewer']['releases'] = [
                    dict(version='0.1.0', date='2026-11-15', notes=['Initial'])]
                before = copy.deepcopy(data)
                self.assertEqual(bump(data, 'viewer', part, 'Keep selection', '2026-11-15'), expected)
                self.assertEqual(data['apps']['viewer']['releases'][1:], before['apps']['viewer']['releases'])
                for key in data['apps'].keys() - {'viewer'}:
                    self.assertEqual(data['apps'][key], before['apps'][key])

    def test_manifest_rejects_mismatched_or_duplicate_versions(self):
        for mode in ('mismatch', 'duplicate', 'invalid', 'empty_note'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                data = app_release.load_releases()
                app = data['apps']['viewer']
                if mode == 'mismatch':
                    app['version'] = '999.0.0'
                elif mode == 'duplicate':
                    app['releases'].append(copy.deepcopy(app['releases'][0]))
                elif mode == 'invalid':
                    app['version'] = app['releases'][0]['version'] = '01.0.0'
                else:
                    app['releases'][0]['notes'] = []
                path = Path(directory) / 'versions.json'
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    app_release.load_releases(path)

    def test_direct_tk_startup_sets_version_and_icon(self):
        root = Mock()
        root.title.return_value = 'Viewer'
        with patch('shared.update_ui.tk_controls') as controls, patch('shared.terminal_profile.require'), patch('shared.app_icons.set_taskbar_identity') as identity:
            app_release.configure_tk(root, 'viewer')
            controls.assert_called_once_with(root, 'viewer')
            identity.assert_called_once_with('viewer')
        root.title.assert_called_with('Viewer · version ' + app_release.version('viewer'))
        from shared.app_icons import icon_path
        root.iconbitmap.assert_has_calls([call(default=str(icon_path('viewer'))), call(str(icon_path('viewer')))])

    def test_every_app_has_its_own_valid_icon(self):
        from shared.app_icons import SYMBOLS, icon_path
        apps = set(app_release.load_releases()['apps']) - {'workbench'}
        self.assertEqual(set(SYMBOLS), apps)
        icons = []
        for app_id in apps:
            data = icon_path(app_id).read_bytes()
            self.assertEqual(data[:4], b'\x00\x00\x01\x00')
            self.assertGreaterEqual(int.from_bytes(data[4:6], 'little'), 7)
            icons.append(data)
        self.assertEqual(len(set(icons)), len(apps))
        self.assertEqual(icon_path('workbench'), app_release.ICON)

    def test_blank_release_note_is_rejected_without_changes(self):
        data = app_release.load_releases()
        before = copy.deepcopy(data)
        with self.assertRaises(ValueError):
            bump(data, 'viewer', 'patch', '  ', '2026-11-15')
        self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main()
