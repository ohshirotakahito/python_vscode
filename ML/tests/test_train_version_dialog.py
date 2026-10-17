"""The extraction picker must inherit selected training classes, not the catalog's first item."""
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app_tk'),
               str(Path(__file__).resolve().parents[1] / 'app'),
               str(Path(__file__).resolve().parents[1])]
import tab_train


class Variable:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class VersionDialogTests(unittest.TestCase):
    def test_source_filter_clears_incompatible_versions_and_file_rules(self):
        tab = SimpleNamespace(experiment=Variable(''), experiment_combo=Mock(),
            _experiments=['A'], _sample_names=['Glu', 'Lys', 'Legacy'],
            _source_versions={'Glu': [{'run_id': 'a', 'ex': 'A'}, {'run_id': 'b', 'ex': 'B'}],
                              'Lys': [{'run_id': 'lys', 'ex': 'B'}],
                              'Legacy': [{'run_id': 'legacy', 'ex': None}]},
            feature_versions={'Glu': 'b'}, _set_file_filters=Mock(),
            version_summary=Mock(), samples=Mock(), source_hint=Mock())
        tab.experiment_combo.current.return_value = 1
        tab_train.TrainTab._filter_samples(tab)
        self.assertEqual(tab.experiment.get(), 'A')
        tab.samples.set_items.assert_called_once_with(['Glu'])
        self.assertEqual(tab.feature_versions, {})
        tab._set_file_filters.assert_called_once_with([])
        tab.experiment_combo.current.return_value = 0
        tab_train.TrainTab._filter_samples(tab)
        tab.samples.set_items.assert_called_with(['Glu', 'Lys', 'Legacy'])

    def test_registered_combination_requires_versions_from_selected_source(self):
        tab = self.tab([], ('Glu, Lys',))
        tab.runner = SimpleNamespace(busy=False)
        tab.experiment = Variable('A')
        tab._distance_settings = Mock(return_value=([], None, None))
        tab.fc_mode = Variable('minimal')
        tab.use_selection = Variable(True)
        with patch.object(tab_train, 'resolve_version', side_effect=FileNotFoundError('missing')) as resolve, \
                patch.object(tab_train.messagebox, 'showerror') as error:
            tab_train.TrainTab._run(tab)
            self.assertEqual(resolve.call_args.kwargs, {'experiment': 'A'})
            error.assert_called_once()

    def test_review_transfer_replaces_combinations_and_pins_versions(self):
        tab = SimpleNamespace(pipeline=Variable(next(iter(tab_train.PIPELINES))),
            samples=Mock(), combos=Mock(), version_summary=Mock(), progress=Mock(),
            _set_file_filters=Mock(), _pipeline_changed=Mock(),
            experiment=Variable('Other'), refresh_samples=Mock())
        tab.samples.listbox.get.return_value = ('Other', 'Lys', 'M1Lys')
        versions = {'Lys': 'run-a', 'M1Lys': 'run-b'}
        rules = [('events', '>=', '100'), ('events', '<=', '3000')]
        self.assertTrue(tab_train.TrainTab.apply_review_selection(
            tab, 'rmc', ['Lys', 'M1Lys'], versions, rules))
        self.assertEqual(tab.feature_versions, versions)
        self.assertEqual(tab.experiment.get(), '')
        tab.refresh_samples.assert_called_once()
        self.assertIsNot(tab.feature_versions, versions)
        tab.combos.delete.assert_called_once_with(0, 'end')
        tab.combos.insert.assert_called_once_with('end', 'Lys, M1Lys')
        self.assertEqual([c.args[0] for c in tab.samples.listbox.selection_set.call_args_list], [1, 2])
        tab._set_file_filters.assert_called_once_with(rules)
        tab._pipeline_changed.assert_not_called()

    def test_unsupported_review_feature_set_does_not_change_training(self):
        tab = SimpleNamespace(pipeline=Variable(next(iter(tab_train.PIPELINES))))
        with patch.object(tab_train.messagebox, 'showerror') as message:
            self.assertFalse(tab_train.TrainTab.apply_review_selection(tab, 'rmd', ['A', 'B'], {}, []))
        message.assert_called_once()

    def tab(self, selected, combinations=()):
        return SimpleNamespace(pipeline=Variable(next(iter(tab_train.PIPELINES))),
            samples=SimpleNamespace(get_selected=lambda: selected),
            combos=SimpleNamespace(get=lambda *_: combinations),
            feature_versions={}, winfo_toplevel=Mock())

    def check_dialog_sample(self, selected, combinations=()):
        result = {'sample': 'Lys', 'feature_set': 'rmc', 'run_id': 'legacy',
                  'directory': 'results', 'conditions': None}
        with ExitStack() as stack:
            stack.enter_context(patch.object(tab_train.tk, 'StringVar', Variable))
            for name in ('Toplevel', 'Text'):
                stack.enter_context(patch.object(tab_train.tk, name))
            for name in ('Frame', 'Label', 'Combobox', 'Entry', 'Button'):
                stack.enter_context(patch.object(tab_train.ttk, name))
            catalog = stack.enter_context(patch.object(tab_train, 'list_available_samples',
                                                       return_value=['01', 'Lys']))
            versions = stack.enter_context(patch.object(tab_train, 'list_versions', return_value=[result]))
            tab_train.TrainTab._choose_versions(self.tab(selected, combinations))
            versions.assert_called_once_with('rmc', 'Lys', experiment=None, condition_id=None)
            catalog.assert_not_called()

    def test_selected_lys_is_shown_instead_of_first_catalog_entry(self):
        self.check_dialog_sample(['Lys'])

    def test_registered_combinations_supply_classes_after_selection_cleared(self):
        self.check_dialog_sample([], ('Lys, M1Lys',))

    def test_empty_selection_does_not_pick_unrelated_class(self):
        with patch.object(tab_train.messagebox, 'showinfo') as message, \
                patch.object(tab_train.tk, 'Toplevel') as dialog:
            tab_train.TrainTab._choose_versions(self.tab([]))
        message.assert_called_once()
        dialog.assert_not_called()


if __name__ == '__main__':
    unittest.main()
