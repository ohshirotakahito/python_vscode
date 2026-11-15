import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from app import App


class CopyPermissionTests(unittest.TestCase):
    def ui(self, allowed=False, sample='other', role='transfer'):
        spec = dict(sources=[Path('20261006_1434 UNAN#4Pex0n2_sample')],
                    target=Path('experiment') / sample)
        return SimpleNamespace(busy=False, pc={'role': role},
                               current=[(1, None, spec)],
                               allow_name_mismatch=Mock(get=Mock(return_value=allowed)))

    def test_mismatch_requires_explicit_confirmation(self):
        ui = self.ui()
        self.assertTrue(App.copy_block_reason(ui))
        ui.allow_name_mismatch.get.return_value = True
        self.assertEqual(App.copy_block_reason(ui), '')

    def test_matching_name_needs_no_override(self):
        self.assertEqual(App.copy_block_reason(self.ui(sample='sample')), '')

    def test_override_does_not_bypass_role_busy_or_missing_plan(self):
        ui = self.ui(allowed=True, role='operator')
        self.assertTrue(App.copy_block_reason(ui))
        ui.pc['role'] = 'transfer'
        ui.busy = True
        self.assertTrue(App.copy_block_reason(ui))
        ui.busy, ui.current = False, None
        self.assertTrue(App.copy_block_reason(ui))

    def test_new_plan_clears_confirmation(self):
        ui = self.ui(allowed=True)
        ui.sets = [object()]
        ui.close_plan_window = Mock(side_effect=RuntimeError('stop before planning'))
        with self.assertRaises(RuntimeError):
            App.preview(ui)
        ui.allow_name_mismatch.set.assert_called_once_with(False)

    def test_set_changes_clear_confirmation(self):
        ui = self.ui(allowed=True)
        ui.close_plan_window = Mock()
        ui.plan_tree = Mock(get_children=Mock(return_value=[]))
        ui.plan_label = Mock()
        ui.render_sets = Mock()
        App.refresh_sets(ui)
        ui.allow_name_mismatch.set.assert_called_once_with(False)
        self.assertIsNone(ui.current)
