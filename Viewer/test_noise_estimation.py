"""Run with: python -m unittest discover -s Viewer -p test_noise_estimation.py"""
import unittest
import numpy as np
from noise_estimation import lower_mode_noise, analysis_bounds


class LowerModeNoiseTests(unittest.TestCase):
    def test_crop_uses_parent_view_context(self):
        full = analysis_bounds(1640000, 10000, 3000000, 1640000, 10000)
        crop = analysis_bounds(1644757, 2488, 3000000, 1640000, 10000)
        self.assertEqual(full, crop)

    def test_selection_spanning_views_is_fully_covered(self):
        lo, hi = analysis_bounds(9500, 2000, 30000, 0, 10000)
        self.assertEqual((lo, hi), (0, 20000))

    def test_background_is_not_the_largest_mode(self):
        rng = np.random.default_rng(42)
        expected = 0.006
        waveform = rng.normal(0, expected, 20000)
        waveform[rng.random(len(waveform)) < 0.65] += 0.05
        center, sigma = lower_mode_noise(waveform)
        self.assertLess(abs(center), 0.002)
        self.assertLess(abs(sigma/expected-1), 0.2)

    def test_noise_only(self):
        waveform = np.random.default_rng(1).normal(0.003, 0.006, 20000)
        center, sigma = lower_mode_noise(waveform)
        self.assertLess(abs(center-0.003), 0.002)
        self.assertLess(abs(sigma/0.006-1), 0.2)

    def test_flat_and_insufficient_data_are_rejected(self):
        for waveform in (np.ones(1000), np.arange(20), np.full(200, np.nan)):
            with self.assertRaises(ValueError):
                lower_mode_noise(waveform)


if __name__ == "__main__":
    unittest.main()
