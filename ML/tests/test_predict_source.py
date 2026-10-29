"""Mixed prediction selects a trained model from ③ and pins only the mixed inputs, without a display."""
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
    def test_mix_choices_exclude_the_selected_model_classes(self):
        tab = NS(experiment_combo=Mock(), experiment=Mock(), _experiments=['A'],
                 _sample_names=['Glu', 'Lys', 'Mix', 'Legacy'],
                 _source_versions={'Glu': [{'ex': 'A'}], 'Lys': [{'ex': 'B'}],
                                   'Mix': [{'ex': 'A'}], 'Legacy': [{'ex': None}]},
                 mix=Mock(), source_hint=Mock())
        tab._selected_model = lambda: {'class_names': ['Glu', 'Lys']}
        tab._update_mix_options = lambda: tab_predict.PredictTab._update_mix_options(tab)
        tab.experiment_combo.current.return_value = 1
        tab_predict.PredictTab._filter_samples(tab)
        tab.mix.set_items.assert_called_with(['Mix'])
        tab.experiment_combo.current.return_value = 0
        tab_predict.PredictTab._filter_samples(tab)
        tab.mix.set_items.assert_called_with(['Mix', 'Legacy'])

    def test_without_model_offers_training_tab_and_does_not_run(self):
        opened = Mock()
        tab = NS(mix=Mock(), runner=Mock(), _selected_model=lambda: None, _selected_group=lambda: [])
        tab._open_train = opened
        with patch.object(tab_predict.messagebox, 'askyesno', return_value=True):
            tab_predict.PredictTab._run(tab)
        opened.assert_called_once()
        tab.runner.run.assert_not_called()

    def test_combination_with_several_models_requires_a_choice(self):
        tab = NS(mix=Mock(), runner=Mock(), _selected_model=lambda: None,
                 _selected_group=lambda: [{'class_names': ['A', 'B']}] * 2, _open_train=Mock())
        with patch.object(tab_predict.messagebox, 'showinfo') as info:
            tab_predict.PredictTab._run(tab)
        info.assert_called_once()
        tab._open_train.assert_not_called()
        tab.runner.run.assert_not_called()

    def test_prediction_uses_selected_model_and_pins_mix_inputs(self):
        model = {'run_dir': Path('model_run'), 'feature_set': 'rmc', 'class_names': ['Glu', 'Lys']}
        tab = NS(mix=Mock(), experiment=Mock(), threshold=Mock(), runner=Mock(), progress=Mock(),
                 _selected_model=lambda: model, _selected_group=lambda: [], mix_file_rules=[])
        tab.mix.get_selected.return_value = ['Mix']
        tab.experiment.get.return_value = 'A'
        tab.threshold.get.return_value = 0.8
        active = []
        records = [{'sample': 'Mix', 'run_id': 'Mix-A'}]

        @contextmanager
        def pinned(method, names, root, versions):
            self.assertEqual((method, names, versions), ('rmc', ['Mix'], {'Mix': 'Mix-A'}))
            active.append(True)
            try:
                yield records
            finally:
                active.clear()
        with tempfile.TemporaryDirectory() as folder:
            def prediction(model_dir, test_smns, **kwargs):
                self.assertTrue(active)
                self.assertEqual((model_dir, test_smns), (model['run_dir'], ['Mix']))
                return Path(folder)
            with patch.object(tab_predict, 'resolve_version', side_effect=lambda method, name, **kw:
                              {'run_id': name + '-A'}), \
                    patch.object(tab_predict, 'inspection_inputs', side_effect=pinned), \
                    patch.object(tab_predict.trained_models, 'predict_mix', side_effect=prediction) as run:
                tab_predict.PredictTab._run(tab)
                tab.runner.run.call_args.args[0](Mock())
                run.assert_called_once()
            self.assertFalse(active)
            self.assertEqual(json.loads((Path(folder) / 'feature_inputs.json').read_text(encoding='utf-8')), records)


if __name__ == '__main__':
    unittest.main()
