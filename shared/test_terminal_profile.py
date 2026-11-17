import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared import terminal_profile as profile


class TerminalTests(unittest.TestCase):
    def test_new_terminal_defaults_include_chip_atlas(self):
        with tempfile.TemporaryDirectory() as directory:
            data = profile.load(Path(directory) / 'terminal.json')
        self.assertTrue(profile.allowed('workbench', data))
        self.assertTrue(profile.allowed('exmanager', data))
        self.assertTrue(profile.allowed('viewer', data))
        self.assertTrue(profile.allowed('chipdb', data))
        self.assertEqual(data['apps'], ['exmanager', 'viewer', 'chipdb'])
        self.assertFalse(profile.allowed('analyzer', data))

    def test_legacy_standard_user_profile_adds_viewer_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'terminal.json'
            path.write_text(json.dumps({'role': 'user', 'apps': ['exmanager']}), encoding='utf-8')
            before = path.read_bytes()
            self.assertEqual(profile.load(path)['apps'], ['exmanager', 'viewer', 'chipdb'])
            self.assertEqual(path.read_bytes(), before)

    def test_custom_and_explicit_selections_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'terminal.json'
            for apps in ([], ['analyzer'], ['viewer']):
                path.write_text(json.dumps({'role': 'user', 'apps': apps}), encoding='utf-8')
                self.assertEqual(profile.load(path)['apps'], apps)
            profile.save('user', ['exmanager'], path)
            self.assertEqual(profile.load(path)['apps'], ['exmanager'])
            profile.save('user', ['exmanager', 'viewer'], path)
            self.assertEqual(profile.load(path)['apps'], ['exmanager', 'viewer'])

    def test_old_two_app_standard_adds_chip_atlas_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'terminal.json'
            for version in (1, 2):
                path.write_text(json.dumps({'profile_version': version, 'role': 'user',
                                             'apps': ['exmanager', 'viewer']}), encoding='utf-8')
                before = path.read_bytes()
                self.assertEqual(profile.load(path)['apps'], ['exmanager', 'viewer', 'chipdb'])
                self.assertEqual(path.read_bytes(), before)
            path.write_text(json.dumps({'profile_version': 2, 'role': 'user', 'apps': ['exmanager']}), encoding='utf-8')
            self.assertEqual(profile.load(path)['apps'], ['exmanager'])

    def test_user_selection_and_developer_role(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'terminal.json'
            profile.save('user', ['analyzer', 'viewer'], path)
            data = profile.load(path)
            self.assertTrue(profile.allowed('analyzer', data))
            self.assertFalse(profile.allowed('database', data))
            profile.save('developer', [], path)
            self.assertTrue(profile.allowed('database', profile.load(path)))

    def test_invalid_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'terminal.json'
            for data in ({'role': 'admin'}, {'role': 'user', 'apps': ['unknown']}):
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    profile.load(path)

    def test_direct_start_is_denied(self):
        with patch.object(profile, 'load', return_value={'role': 'user', 'apps': ['viewer']}):
            with self.assertRaises(PermissionError):
                profile.require('analyzer')
            profile.require('viewer')

    def test_tk_gate_runs_before_controls_are_created(self):
        from shared.app_release import configure_tk
        with patch.object(profile, 'require', side_effect=PermissionError), patch('shared.update_ui.tk_controls') as ui:
            with self.assertRaises(PermissionError):
                configure_tk(None, 'analyzer')
            ui.assert_not_called()


if __name__ == '__main__':
    unittest.main()
