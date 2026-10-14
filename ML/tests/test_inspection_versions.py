"""Inspection selection and pinning without a display or scientific dependencies."""
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app_tk'),
               str(Path(__file__).resolve().parents[1])]
import inspection_versions as picker
from common.feature_store import inspection_inputs, sample_directory, training_inputs


class Variable:
    def __init__(self, value=''):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class InspectionTests(unittest.TestCase):
    def test_picker_saves_selected_old_version(self):
        record = dict(sample='Lys', feature_set='rmc', run_id='old',
                      directory='old-results', extracted_at='2026-10-01', conditions={})
        tab = SimpleNamespace(feature_set=Variable('rmc'),
            samples=SimpleNamespace(get_selected=lambda: ['Lys']),
            feature_versions={'Lys': 'old'}, version_summary=Mock(), winfo_toplevel=Mock())
        buttons = {}
        def button(*args, **kwargs):
            buttons[kwargs['text']] = kwargs['command']
            return Mock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(picker.tk, 'StringVar', Variable))
            for name in ('Toplevel', 'Text'):
                stack.enter_context(patch.object(picker.tk, name))
            for name in ('Frame', 'Label', 'Combobox', 'Entry'):
                stack.enter_context(patch.object(picker.ttk, name))
            stack.enter_context(patch.object(picker.ttk, 'Button', side_effect=button))
            versions = stack.enter_context(patch.object(picker, 'list_versions', return_value=[record]))
            picker.InspectionVersions._choose_versions(tab)
            versions.assert_called_once_with('rmc', 'Lys', experiment=None, condition_id=None)
            buttons['保存して閉じる']()
            self.assertEqual(tab.feature_versions, {'Lys': 'old'})

    def test_set_change_clears_previous_method_selection(self):
        tab = SimpleNamespace(feature_set=Variable('rmb'), version_feature_set='rmc',
                              feature_versions={'Lys': 'old'}, version_summary=Mock())
        picker.InspectionVersions._refresh_version_set(tab)
        self.assertEqual(tab.feature_versions, {})
        self.assertEqual(tab.version_feature_set, 'rmb')

    def test_all_inputs_pinned_and_context_restored_after_error(self):
        root = Path('example-root')
        records = {'A': dict(sample='A', directory='old-A', outputs=[], run_id='old'),
                   'B': dict(sample='B', directory=str(root), outputs=[], run_id='legacy')}
        with patch('common.feature_store.resolve_version', side_effect=lambda method, sample, run, root: records[sample]) as resolve:
            with self.assertRaisesRegex(RuntimeError, 'stop'):
                with inspection_inputs('rmc', ['A', 'B'], root, {'A': 'old', 'B': 'legacy'}) as selected:
                    self.assertEqual(sample_directory(root, 'A'), Path('old-A'))
                    self.assertEqual(sample_directory(root, 'B'), root)
                    self.assertEqual(len(selected), 2)
                    raise RuntimeError('stop')
            self.assertEqual(training_inputs(), [])
            self.assertEqual(resolve.call_count, 2)


if __name__ == '__main__':
    unittest.main()