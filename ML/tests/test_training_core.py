"""Event-level split, class balancing and tuning shared by all training pipelines."""
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import training_core


def events(files_per_class=(4, 5, 3), events_per_file=40, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for c, n_files in enumerate(files_per_class):
        for f in range(n_files):
            n = events_per_file * (c + 1)  # 偏りのあるクラス件数
            rows.append(pd.DataFrame({
                'sample': f'S{c}', 'ex_id': f'EX{f % 2}', 'sample_name': f'file{f:03d}',
                'a': rng.normal(c, 0.5, n), 'b': rng.normal(-c, 0.5, n), 'noise': rng.normal(0, 1, n)}))
    frame = pd.concat(rows, ignore_index=True)
    frame.index = [f'id{i}' for i in range(len(frame))]
    return frame


class TrainingCoreTest(unittest.TestCase):
    def tearDown(self):
        training_core.reset()

    def test_event_split_is_stratified_and_disjoint(self):
        frame = events()
        split = training_core.make_split(frame)
        self.assertFalse(set(split['train']) & set(split['test']))
        self.assertEqual(len(split['train']) + len(split['test']), len(frame))
        y = frame['sample'].to_numpy()
        for c in ('S0', 'S1', 'S2'):
            share = (y[split['test']] == c).mean()
            self.assertAlmostEqual(share, (y == c).mean(), delta=0.01)
        for fold_train, fold_valid in split['cv']:
            self.assertFalse(set(fold_train) & set(fold_valid))
        self.assertEqual(split['info']['n_cv_folds'], len(split['cv']))

    def test_too_few_events_is_rejected(self):
        frame = events().groupby('sample').head(2)
        with self.assertRaisesRegex(ValueError, '3イベント'):
            training_core.make_split(frame)

    def test_balance_methods(self):
        X = np.arange(10).reshape(-1, 1).astype(float)
        y = np.array([0] * 7 + [1] * 3)
        Xu, yu, wu = training_core.balance_training(X, y, 'under', 0)
        self.assertEqual(np.bincount(yu).tolist(), [3, 3])
        self.assertIsNone(wu)
        Xo, yo, _ = training_core.balance_training(X, y, 'over', 0)
        self.assertEqual(np.bincount(yo).tolist(), [7, 7])
        _, yw, ww = training_core.balance_training(X, y, 'weight', 0)
        self.assertEqual(len(yw), 10)
        self.assertAlmostEqual(ww[y == 0].sum(), ww[y == 1].sum())
        _, _, ws = training_core.balance_training(X, y, 'sqrt_weight', 0)
        self.assertTrue(ws[y == 1][0] / ws[y == 0][0] < ww[y == 1][0] / ww[y == 0][0])
        self.assertIsNone(training_core.balance_training(X, y, 'none', 0)[2])

    def test_train_both_algorithms_with_tuning(self):
        frame = events()
        split = training_core.make_split(frame)
        for algorithm in training_core.ALGORITHMS:
            for balance in training_core.BALANCE_METHODS:
                training_core.configure(balance=balance, tune=algorithm == 'xgboost', n_trials=2,
                                        max_rounds=30)
                result = training_core.train_classifier(frame, ['a', 'b', 'noise'], algorithm, split, n_jobs=1)
                self.assertEqual(len(result['tuning']), 3 if algorithm == 'xgboost' else 1)
                self.assertEqual(len(result['y_pred']), len(split['test']))
                self.assertGreater(result['macro_f1'], 0.8, (algorithm, balance))
                self.assertEqual(result['clf'].predict(result['X_test']).shape, result['y_pred'].shape)
        config = training_core.manifest_config(split, result)
        self.assertEqual(config['split']['test_events'], len(split['test']))

    def test_configure_rejects_unknown_values(self):
        with self.assertRaises(ValueError):
            training_core.configure(balance='smote')
        with self.assertRaises(ValueError):
            training_core.configure(n_trials=0)


if __name__ == '__main__':
    unittest.main()
