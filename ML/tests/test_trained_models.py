"""Models trained in ③ are saved once and reused for mixed prediction without retraining."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from common import paths, trained_models, training_core
from test_training_core import events


class TrainedModelsTest(unittest.TestCase):
    def tearDown(self):
        training_core.reset()

    def test_save_list_load_reproduces_predictions(self):
        frame = events()
        training_core.configure(max_rounds=30)
        split = training_core.make_split(frame)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(paths, 'RESULTS_ROOT', Path(folder)):
            for algorithm in training_core.ALGORITHMS:
                run_dir = Path(folder) / 'rmc' / algorithm / f'20260101_000000_train_{algorithm}'
                run_dir.mkdir(parents=True)
                result = training_core.train_classifier(frame, ['a', 'b', 'noise'], algorithm, split, n_jobs=1)
                trained_models.save_model(run_dir, result, algorithm, 'rmc', ['S0', 'S1', 'S2'],
                                          fc_parameters_mode='minimal')
                model = trained_models.load_model(run_dir)
                probs = model.predict_proba(frame.iloc[split['test']])
                np.testing.assert_array_equal(probs.argmax(axis=1), result['y_pred'])
                self.assertEqual(model.class_names, ['S0', 'S1', 'S2'])
            listed = trained_models.list_models('rmc')
            self.assertEqual({m['algorithm'] for m in listed}, {'xgboost', 'lightgbm'})
            self.assertEqual(trained_models.list_models('rmb'), [])

    def test_same_combination_is_grouped_and_differences_are_marked(self):
        frame = events()
        split = training_core.make_split(frame)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(paths, 'RESULTS_ROOT', Path(folder)):
            for stamp, balance in (('20260101_000000', 'under'), ('20260102_000000', 'weight')):
                training_core.configure(max_rounds=10, balance=balance)
                run_dir = Path(folder) / 'rmc' / 'xgboost' / f'{stamp}_train_S'
                run_dir.mkdir(parents=True)
                result = training_core.train_classifier(frame, ['a', 'b'], 'xgboost', split, n_jobs=1)
                trained_models.save_model(run_dir, result, 'xgboost', 'rmc', ['S2', 'S0', 'S1'],
                                          details={'training': training_core.manifest_config(split, result)})
            groups = trained_models.group_models(trained_models.list_models('rmc'))
            self.assertEqual(list(groups), [('rmc', ('S0', 'S1', 'S2'))])
            models = groups[('rmc', ('S0', 'S1', 'S2'))]
            self.assertEqual([m['balance'] for m in models], ['weight', 'under'])
            table = {label: (values, differs) for _g, label, values, differs
                     in trained_models.comparison_table(models)}
            self.assertTrue(table['クラスの偏りへの対処'][1])
            self.assertFalse(table['アルゴリズム'][1])
            self.assertEqual(table['学習用 / テスト用イベント'][0][0], f"{len(split['train']):,} / {len(split['test']):,}")
            self.assertIn('クラス別F1：S0', table)

    def test_missing_feature_is_reported(self):
        frame = events()
        training_core.configure(max_rounds=10)
        split = training_core.make_split(frame)
        with tempfile.TemporaryDirectory() as folder:
            result = training_core.train_classifier(frame, ['a', 'b'], 'xgboost', split, n_jobs=1)
            trained_models.save_model(folder, result, 'xgboost', 'rmb', ['S0', 'S1', 'S2'])
            with self.assertRaisesRegex(RuntimeError, '特徴量'):
                trained_models.load_model(folder).predict_proba(frame.drop(columns=['b']))


if __name__ == '__main__':
    unittest.main()
