"""解析・トレンドの目盛り。"""
from datetime import datetime
import unittest

from chipdb.axis import nice_ticks, parse_time, tick_label, time_ticks


class NiceTicksTests(unittest.TestCase):
    def test_round_integers_cover_the_data(self):
        low, high, step, ticks = nice_ticks(0, 104.91)       # 画面の CAL の例: 以前は 26.23・52.46 …
        self.assertEqual((low, high, step), (0, 120, 20))
        self.assertEqual([tick_label(t, step) for t in ticks], ['0', '20', '40', '60', '80', '100', '120'])
        self.assertEqual(nice_ticks(12.3, 87.9)[3], [0, 20, 40, 60, 80, 100])
        self.assertEqual(nice_ticks(1, 137)[3], [0, 25, 50, 75, 100, 125, 150])           # 回数の軸
        self.assertEqual(tick_label(12.5, 2.5), '12.5')

    def test_small_ranges_use_round_decimals(self):
        low, high, step, ticks = nice_ticks(0.78, 1.58)
        self.assertEqual(step, 0.2)
        self.assertEqual(ticks, [0.6, 0.8, 1.0, 1.2, 1.4, 1.6])
        self.assertEqual(tick_label(1.0, step), '1.0')
        self.assertEqual(tick_label(0.25, 0.25), '0.25')

    def test_always_contains_data_and_handles_flat_and_negative(self):
        for low, high in ((0.0, 0.0), (-3.2, 7.7), (5, 5), (1e-4, 3e-4), (1000, 1003)):
            start, end, step, ticks = nice_ticks(low, high)
            self.assertLessEqual(start, min(low, high))
            self.assertGreaterEqual(end, max(low, high))
            self.assertLess(start, end)
            self.assertLessEqual(len(ticks), 8)


class TimeTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time('20220602_0847'), datetime(2022, 6, 2, 8, 47))
        self.assertEqual(parse_time('20220602_08:47'), datetime(2022, 6, 2, 8, 47))
        self.assertIsNone(parse_time('x'))

    def test_ticks_by_span(self):
        ticks, fmt = time_ticks(datetime(2022, 6, 2), datetime(2026, 11, 13))
        self.assertEqual(fmt, '%Y')
        self.assertEqual([t.year for t in ticks], [2023, 2024, 2025, 2026])
        ticks, fmt = time_ticks(datetime(2026, 3, 15), datetime(2026, 11, 13))
        self.assertEqual(fmt, '%Y-%m')
        self.assertTrue(all(t.day == 1 for t in ticks) and 3 <= len(ticks) <= 7)
        ticks, fmt = time_ticks(datetime(2026, 11, 1, 9), datetime(2026, 11, 13, 18))
        self.assertEqual(fmt, '%m-%d')
        self.assertTrue(all(t.hour == 0 for t in ticks))
        ticks, fmt = time_ticks(datetime(2026, 11, 13, 9, 5), datetime(2026, 11, 13, 18, 40))
        self.assertEqual(fmt, '%m-%d %H:%M')
        self.assertTrue(all(t.minute == 0 and datetime(2026, 11, 13, 9, 5) <= t for t in ticks))


if __name__ == '__main__':
    unittest.main()
