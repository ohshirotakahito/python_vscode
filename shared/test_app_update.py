import unittest
from unittest.mock import patch
import json

from shared import app_update
from shared.app_release import load_releases


class UpdateTests(unittest.TestCase):
    def test_check_distinguishes_running_installed_and_remote(self):
        manifest = load_releases()
        installed = manifest['apps']['viewer']['version']
        with patch.object(app_update, 'git', side_effect=[
                '', 'refs/remotes/origin/main', 'app_versions.json', json.dumps(manifest), '2', 'abc123']):
            state = app_update.check('viewer', '0.0.0')
        self.assertTrue(state.restart_needed)
        self.assertEqual(state.installed, installed)
        self.assertEqual(state.available, installed)
        self.assertEqual(state.behind, 2)

    def test_unpublished_manifest_is_an_informational_state(self):
        from shared.update_ui import description
        with patch.object(app_update, 'git', side_effect=['', 'refs/remotes/origin/main', '']):
            state = app_update.check('viewer', '0.0.0')
        self.assertIsNone(state.available)
        self.assertEqual(state.behind, 0)
        self.assertTrue(state.restart_needed)
        self.assertIn('未公開', description(state))
        with patch.object(app_update, 'git') as git:
            with self.assertRaises(RuntimeError):
                app_update.install(state)
            git.assert_not_called()

    def test_network_failure_is_not_reported_as_unpublished(self):
        with patch.object(app_update, 'git', side_effect=RuntimeError('connection failed')):
            with self.assertRaisesRegex(RuntimeError, 'connection failed'):
                app_update.check('viewer', '0.0.0')

    def test_dirty_checkout_is_never_merged(self):
        with patch.object(app_update, 'git', return_value=' M source.py') as git:
            with self.assertRaises(RuntimeError):
                app_update.install(app_update.UpdateState('1', '1', '2', 1, 'abc'))
            git.assert_called_once_with('status', '--porcelain')

    def test_clean_checkout_only_fast_forwards_to_checked_commit(self):
        with patch.object(app_update, 'git', return_value='') as git:
            app_update.install(app_update.UpdateState('1', '1', '2', 1, 'abc'))
            self.assertEqual(git.call_args.args, ('merge', '--ff-only', 'abc'))

    def test_cancelled_close_does_not_launch_duplicate(self):
        with patch.object(app_update.subprocess, 'Popen') as launch:
            app_update.restart(lambda: False)
            launch.assert_not_called()

    def test_successful_close_restarts_same_entrypoint(self):
        with patch.object(app_update.subprocess, 'Popen') as launch:
            app_update.restart(lambda: True)
            self.assertEqual(launch.call_args.args[0], [app_update.sys.executable, *app_update.sys.argv])


if __name__ == '__main__':
    unittest.main()
