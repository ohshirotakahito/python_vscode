import unittest

from chipdb.gap_repair import repair_rows


def exp(gap, op='X'):
    return ['Experiment', '○', 'd', 'u', 'id', op, str(gap), '', '']


class RepairTest(unittest.TestCase):
    def gaps(self, rows):
        return [r[6] for r in rows]

    def test_user_example(self):
        rows = [['C', 'Status', 'Date', 'Op', 'EXID', 'Operation', '20190765', '', ''],
                exp(270), exp(271), exp(20190671), exp(20190671), exp(20190672), exp(20190765)]
        new, changes = repair_rows(rows)
        self.assertEqual(self.gaps(new), ['365', '270', '271', '271', '271', '272', '365'])
        self.assertEqual(len(changes), 5)

    def test_stops_at_normal_value_and_restarts(self):
        rows = [['C'] + [''] * 5 + ['5'], exp(10), exp(20190101), exp(20190105), exp(30), exp(20200000), exp(20200002)]
        new, _ = repair_rows(rows)
        self.assertEqual(self.gaps(new), ['5', '10', '10', '14', '30', '30', '32'])

    def test_first_row_anomalous_starts_from_zero(self):
        rows = [['C'] + [''] * 5 + ['x'], exp(20190101), exp(20190110)]
        new, _ = repair_rows(rows)
        self.assertEqual(self.gaps(new), ['x', '0', '9'])

    def test_other_kinds_and_non_numeric_untouched(self):
        rows = [['C'] + [''] * 5 + [''], exp(5), ['Stocked', '○', '-', '-', '', '', '20190101'],
                exp('NaN'), exp(20190101), exp(20190103)]
        new, _ = repair_rows(rows)
        self.assertEqual(self.gaps(new), ['', '5', '20190101', 'NaN', '5', '7'])


    def test_non_date_overflow_is_rejected(self):
        rows = [['C'] + [''] * 5 + ['0'], exp(7270), exp(16146), exp(37422)]
        with self.assertRaises(ValueError):
            repair_rows(rows)


if __name__ == '__main__':
    unittest.main()
