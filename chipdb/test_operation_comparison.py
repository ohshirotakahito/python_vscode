"""Whole-history comparisons must not manufacture measurement pairs."""
from datetime import date
import unittest

from chipdb.operation_comparison import (chip_points, correlations, observations, representative, count_matches,
                                         representative_limits, within_representative_limits)


def row(kind, value, experiment='EX1', actual='20260101_10:01', start='20260101_1000'):
    return ['Experiment', 'OK', start, 'User', experiment, kind+'#1', '12', str(value), '8', actual]


class OperationComparisonTests(unittest.TestCase):
    def test_representative_bounds_are_inclusive_and_independent(self):
        point = dict(x=.5, y=10.)
        self.assertTrue(within_representative_limits(point, representative_limits()))
        self.assertTrue(within_representative_limits(point, representative_limits('.5', '.5', '10', '10')))
        self.assertFalse(within_representative_limits(point, representative_limits(xmax='.4')))
        self.assertFalse(within_representative_limits(point, representative_limits(ymin='11')))
        for fields in (dict(xmin='2', xmax='1'), dict(ymax='NaN'), dict(xmin='inf'), dict(ymin='abc')):
            with self.assertRaises(ValueError):
                representative_limits(**fields)

    def test_strict_count_threshold_and_axis_choices(self):
        self.assertFalse(count_matches(5, 8))
        self.assertTrue(count_matches(6, 8))
        self.assertTrue(count_matches(5, 5, operator='>='))
        self.assertTrue(count_matches(6, 1, scope='Xのみ'))
        self.assertTrue(count_matches(1, 6, scope='Yのみ'))
        self.assertTrue(count_matches(1, 6, scope='XまたはY'))
        with self.assertRaises(ValueError):
            count_matches(6, 6, threshold=-1)

    def test_actual_time_and_explicit_fallback_preserve_line_identity(self):
        rows = [['header'], row('FC', 1), row('FC', 2, actual=''), row('FC', 3, actual='', start='bad')]
        points, omitted = observations('chip', rows, 'FC')
        self.assertEqual(omitted, 0)
        self.assertEqual([p.line for p in points], [2, 3, 4])
        self.assertEqual(points[0].time.minute, 1)
        self.assertEqual(points[1].time.minute, 0)
        self.assertEqual([p.time_source for p in points], ['実施時刻', '実験開始日時（代用）', '日時なし'])

    def test_full_history_includes_other_experiments_and_invalid_values_are_not_zero(self):
        rows = [row('FC', 1, 'EX1'), row('FC', 3, 'EX2'), row('FC', 'NaN'), row('FC', ''), row('MA', 20)]
        points, omitted = observations('chip', rows, 'FC')
        self.assertEqual([p.value for p in points], [1, 3])
        self.assertEqual(omitted, 2)
        filtered, omitted = observations('chip', rows, 'FC', valid_only=True)
        self.assertEqual([p.value for p in filtered], [1])
        self.assertEqual(omitted, 3)

    def test_date_filter_uses_operation_time_not_experiment_start(self):
        rows = [row('FC', 1, actual='20260102_10:00')]
        points, _ = observations('chip', rows, 'FC', since=date(2026,1,2), until=date(2026,1,2))
        self.assertEqual(len(points), 1)
        self.assertEqual(observations('chip', rows, 'FC', until=date(2026,1,1))[0], [])

    def test_summary_has_one_point_per_chip_not_cartesian_product(self):
        histories = {'a': [row('FC', n) for n in (1, 2, 9)] + [row('MA', n) for n in (10, 30)],
                     'b': [row('FC', 3)]}
        points, missing = chip_points(histories, 'FC', 'MA')
        self.assertEqual(len(points), 1)
        self.assertEqual((points[0]['x'], points[0]['y'], points[0]['nx'], points[0]['ny']), (2, 20, 3, 2))
        self.assertEqual(missing, ['b'])
        points, _ = chip_points(histories, 'FC', 'MA', method='平均')
        self.assertEqual(points[0]['x'], 4)

    def test_first_last_are_record_order_even_when_times_are_reversed(self):
        points, _ = observations('a', [row('FC', 3, actual='20260102_1000'), row('FC', 1)], 'FC')
        self.assertEqual(representative(points, '最初の値'), 3)
        self.assertEqual(representative(points, '最後の値'), 1)

    def test_tied_rank_correlations_and_unavailable_cases(self):
        self.assertEqual(correlations([dict(x=1,y=2), dict(x=2,y=4)]), (None,None))
        self.assertEqual(correlations([dict(x=1,y=v) for v in (1,2,3)]), (None,None))
        r, rho = correlations([dict(x=x,y=y) for x,y in ((1,10),(1,10),(2,20),(3,100))])
        self.assertLess(r, 1)
        self.assertAlmostEqual(rho, 1)


if __name__ == '__main__':
    unittest.main()


class DensityTests(unittest.TestCase):
    def test_grid_counts_points_per_cell(self):
        from chipdb.operation_comparison import density_grid
        points = [dict(x=0, y=0), dict(x=0.1, y=0.1), dict(x=10, y=10), dict(x=11, y=0)]
        grid = density_grid(points, (0, 10), (0, 10), bins=2)
        self.assertEqual(grid, [[2, 0], [0, 1]])          # 範囲外 (11, 0) は数えない

    def test_colour_is_log_scaled_and_empty_cells_unpainted(self):
        from chipdb.operation_comparison import density_colour, DENSITY_STOPS
        self.assertIsNone(density_colour(0, 10))
        self.assertEqual(density_colour(10, 10), DENSITY_STOPS[-1])
        self.assertEqual(density_colour(1, 1), DENSITY_STOPS[-1])
        self.assertNotEqual(density_colour(1, 1000), density_colour(30, 1000))
