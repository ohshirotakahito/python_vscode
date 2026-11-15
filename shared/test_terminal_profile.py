import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared import terminal_profile as profile


class TerminalTests(unittest.TestCase):
    def test_new_terminal_defaults_to_exmanager_only(self):
        with tempfile.TemporaryDirectory() as directory:
            data = profile.load(Path(directory) / 'terminal.json')
        self.assertTrue(profile.allowed('workbench', data))
        self.assertTrue(profile.allowed('exmanager', data))
        self.assertEqual(data['apps'], ['exmanager'])
        self.assertFalse(profile.allowed('analyzer', data))

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
