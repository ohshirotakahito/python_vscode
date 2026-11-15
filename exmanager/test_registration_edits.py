import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import core
from app import App


class RegistrationEditTests(unittest.TestCase):
    def specs(self):
        return [dict(sources=[Path('source-A'), Path('source-B')], target=Path('sample-1'),
                     context={'kind': 'Sample'}),
                dict(sources=[Path('source-A')], target=Path('sample-2'), context={'kind': 'Sample'})]

    def test_removes_only_selected_combination_and_keeps_other_destination(self):
        original = self.specs()
        result = core.remove_registered_sources(original, [(0, 0)])
        self.assertEqual(result[0]['sources'], [Path('source-B')])
        self.assertEqual(result[1], original[1])
        self.assertEqual(len(original[0]['sources']), 2)

    def test_removes_empty_sets_and_handles_multiple_selections(self):
        result = core.remove_registered_sources(self.specs(), [(0, 0), (0, 1)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['target'], Path('sample-2'))
        self.assertEqual(core.remove_registered_sources(self.specs(), [(0, 0), (0, 1), (1, 0)]), [])

    def test_editing_invalidates_previous_copy_plan(self):
        ui = SimpleNamespace(allow_name_mismatch=Mock(), close_plan_window=Mock(), current=['old jobs'],
                             plan_tree=Mock(), plan_label=Mock(), render_sets=Mock())
        ui.plan_tree.get_children.return_value = ('old-plan',)
        App.refresh_sets(ui)
        self.assertIsNone(ui.current)
        ui.allow_name_mismatch.set.assert_called_once_with(False)
        ui.close_plan_window.assert_called_once()
        ui.render_sets.assert_called_once()

    def test_delete_with_no_visible_selection_opens_editor(self):
        ui = SimpleNamespace(busy=False, batch_tree=Mock(), open_sets_window=Mock())
        ui.batch_tree.selection.return_value = ()
        App.remove_set(ui)
        ui.open_sets_window.assert_called_once()


if __name__ == '__main__':
    unittest.main()
