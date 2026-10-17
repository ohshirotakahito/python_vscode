"""Source selection and pinned inputs for mixed prediction, without a display."""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app_tk'),
               str(Path(__file__).resolve().parents[1] / 'app'),
               str(Path(__file__).resolve().parents[1])]
import tab_predict


class PredictSourceTests(unittest.TestCase):
    def test_filter_applies_to_both_lists_and_all_includes_legacy(self):
        tab = NS(experiment_combo=Mock(), experiment=Mock(), _experiments=['A'],
                 _sample_names=['Glu', 'Lys', 'Mix', 'Legacy'],
                 _source_versions={'Glu': [{'ex': 'A'}], 'Lys': [{'ex': 'B'}],
                                   'Mix': [{'ex': 'A'}], 'Legacy': [{'ex': None}]},
                 pure=Mock(), mix=Mock(), source_hint=Mock())
        tab.pure.get_selected.return_value = ['Glu']
        tab._update_mix_options = lambda: tab_predict.PredictTab._update_mix_options(tab)
        tab.experiment_combo.current.return_value = 1
        tab_predict.PredictTab._filter_samples(tab)
        tab.pure.set_items.assert_called_with(['Glu', 'Mix'])
        tab.mix.set_items.assert_called_with(['Mix'])
        tab.experiment_combo.current.return_value = 0
        tab_predict.PredictTab._filter_samples(tab)
        tab.pure.set_items.assert_called_with(tab._sample_names)
        tab.mix.set_items.assert_called_with(['Lys', 'Mix', 'Legacy'])

    def test_prediction_pins_pure_and_mix_and_records_inputs(self):
        tab = NS(pure=Mock(), mix=Mock(), pipeline=Mock(), experiment=Mock(),
                 threshold=Mock(), retrain=Mock(), runner=Mock(), progress=Mock())
        tab.pure.get_selected.return_value = ['Glu', 'Lys']
        tab.mix.get_selected.return_value = ['Mix']
        tab.pipeline.get.return_value = next(iter(tab_predict.PIPELINES))
        tab.experiment.get.return_value = 'A'
        tab.threshold.get.return_value = 0.8
        tab.retrain.get.return_value = False
        active = []
        records = [{'sample': s, 'run_id': s + '-A'} for s in ['Glu', 'Lys', 'Mix']]
        @contextmanager
        def pinned(method, names, root, versions):
            self.assertEqual(names, ['Glu', 'Lys', 'Mix'])
            self.assertEqual(versions, {r['sample']: r['run_id'] for r in records})
            active.append(True)
            try:
                yield records
            finally:
                active.clear()
        with tempfile.TemporaryDirectory() as folder:
            def prediction(*args, **kwargs):
                self.assertTrue(active)
                return Path(folder)
            module = NS(run_prediction=Mock(side_effect=prediction))
            with patch.object(tab_predict, 'resolve_version', side_effect=lambda method, name, **kw:
                              {'run_id': name + '-A'}) as resolve, \
                    patch.object(tab_predict, 'inspection_inputs', side_effect=pinned), \
                    patch.object(tab_predict.importlib, 'import_module', return_value=module):
                tab_predict.PredictTab._run(tab)
                task = tab.runner.run.call_args.args[0]
                task(Mock())
                self.assertEqual(resolve.call_count, 3)
                self.assertTrue(all(c.kwargs == {'experiment': 'A'} for c in resolve.call_args_list))
            self.assertFalse(active)
            self.assertEqual(json.loads((Path(folder) / 'feature_inputs.json').read_text(encoding='utf-8')), records)


if __name__ == '__main__':
    unittest.main()
