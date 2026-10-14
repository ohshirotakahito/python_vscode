"""Changing display order must preserve each actual/predicted class pair."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.eval_viz import redraw_confusion_matrices


class ConfusionMatrixOrderTests(unittest.TestCase):
    def test_reorders_both_axes_without_changing_source_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            counts = pd.DataFrame([[10, 2], [3, 20]],
                                  index=['Actual_A', 'Actual_B'],
                                  columns=['Pred_A', 'Pred_B'])
            percentages = counts.div(counts.sum(axis=1), axis=0) * 100
            counts.to_csv(run / 'confusion_matrix.csv')
            percentages.to_csv(run / 'confusion_matrix_normalized.csv')
            output = run / 'display'
            with patch('common.eval_viz.plot_confusion_matrices') as plot:
                redraw_confusion_matrices(run, ['B', 'A'], output)
                actual, normalized, destination = plot.call_args.args
                self.assertEqual(actual.index.tolist(), ['Actual_B', 'Actual_A'])
                self.assertEqual(actual.columns.tolist(), ['Pred_B', 'Pred_A'])
                self.assertEqual(actual.to_numpy().tolist(), [[20, 3], [2, 10]])
                self.assertAlmostEqual(normalized.loc['Actual_B', 'Pred_A'], 3 / 23 * 100)
                self.assertEqual(destination, output)
            pd.testing.assert_frame_equal(pd.read_csv(run / 'confusion_matrix.csv', index_col=0), counts)
            with self.assertRaises(ValueError):
                redraw_confusion_matrices(run, ['A', 'A'], output)


if __name__ == '__main__':
    unittest.main()
