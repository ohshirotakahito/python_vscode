"""小さな合成データで分割・前処理・保存までを検証する。"""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import train_xgboost_tsfresh_optimized as training


class OptimizedTrainingTests(unittest.TestCase):
    def test_validation_does_not_influence_imputation(self):
        train = pd.DataFrame({'value__mean': [1., 3., np.nan]})
        valid = pd.DataFrame({'value__mean': [np.nan, 100000.]})
        a, b, state = training.prepare(train, valid, np.array([0, 1, 0]), False, 'all', True)
        self.assertEqual(state['medians']['value__mean'], 2.)
        self.assertEqual(a.iloc[2, 0], 2.)
        self.assertEqual(b.iloc[0, 0], 2.)

    def test_groups_are_disjoint(self):
        y = np.repeat(np.arange(3), 20)
        groups = np.repeat(np.arange(30), 2)
        for tr, va in training.group_splits(y, groups, 5, 42):
            self.assertFalse(set(groups[tr]) & set(groups[va]))
            self.assertEqual(set(y[va]), {0, 1, 2})

    def test_weighted_training_keeps_all_rows(self):
        y = np.array([0] * 8 + [1] * 2)
        frame = pd.DataFrame({'x': np.arange(10)})
        matrix = training.training_data(frame, y, '1', 42)
        self.assertEqual(matrix.num_row(), 10)
        self.assertAlmostEqual(float(matrix.get_weight()[y == 0].sum()),
                               float(matrix.get_weight()[y == 1].sum()))

    def test_multiclass_selection_preserves_column_order(self):
        y = np.repeat(np.arange(4), 20)
        frame = pd.DataFrame({'value__mean': y.astype(float),
                              'value__maximum': np.arange(80) % 2,
                              'shape__extra': np.arange(80)})
        a, b, state = training.prepare(frame, frame.iloc[:4], y, True, 'all', True)
        self.assertIn('value__mean', a.columns)
        self.assertIn('shape__extra', a.columns)
        self.assertEqual(list(a.columns), list(b.columns))
        self.assertEqual(list(a.columns), state['columns'])

    def test_small_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rng = np.random.default_rng(5)
            samples = ['A', 'B', 'C', 'D']
            for label, sample in enumerate(samples):
                rows, waves = [], []
                for event in range(40):
                    row = dict(event_id=event, sample=sample, sample_name=f'file{event // 4}',
                               ex_id=f'run_{sample}', signal=10 + label, baseline=20,
                               duration=10, distance=.58)
                    row.update({f'wave_{i}': rng.normal() for i in range(12)})
                    rows.append(row)
                    values = rng.normal(label, .1, 16)
                    waves.extend(dict(id=event, time=t, value=v) for t, v in enumerate(values))
                pd.DataFrame(rows).to_csv(root / f'{sample}_10k_Sample_ANAL_meta.csv', index=False)
                pd.DataFrame(waves).to_csv(root / f'{sample}_10k_Sample_ANAL_tsfresh_input.csv', index=False)
            args = SimpleNamespace(samples=samples, data_root=root, distance=.58,
                                   group_column='sample_name', fc_mode='minimal', folds=2,
                                   trials=0, rounds=4, patience=2, batch_events=100, jobs=1,
                                   seed=42, check_only=False)
            with patch.object(training, 'ROOT', root):
                training.run(args)
            out = next((root / 'results/rmc/xgboost_optimized').iterdir())
            metrics = json.loads((out / 'test_metrics.json').read_text(encoding='utf-8'))
            self.assertTrue(0 <= metrics['macro_f1'] <= 1)
            self.assertTrue((out / 'pipeline.pkl').exists())
            splits = pd.read_csv(out / 'splits.csv')
            self.assertTrue((splits.groupby('group').partition.nunique() == 1).all())
            trials = json.loads((out / 'trials.json').read_text(encoding='utf-8'))
            self.assertEqual(len(trials), 6)
            self.assertTrue(all(min(t['rounds']) >= 1 for t in trials))


if __name__ == '__main__':
    unittest.main()
