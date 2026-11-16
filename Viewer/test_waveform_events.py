from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

import numpy as np
from nptdms import ChannelObject, TdmsWriter

from waveform_events import detect_file, jump_to_event, preview_bursts, saved_events


class WaveformEventTests(unittest.TestCase):
    def test_saved_tables_preserve_units_ids_and_absolute_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'anal.tdms'
            with TdmsWriter(path) as writer:
                writer.write_segment([ChannelObject('B Table', name, values) for name, values in {
                    'B # [n]': ['7'], 'B Start [s]': ['29.8336'], 'B End [s]': ['29.8761'],
                    'B Signal Count [n]': ['2'], 'B BL [pA]': ['24.0326']}.items()] + [
                    ChannelObject('S Table', name, values) for name, values in {
                    'S # [n]': ['31', '32'], 'Signal S [s]': ['29.84', 'NaN'],
                    'Signal E [s]': ['29.841', '29.842'], 'Signal [pA]': ['45.6', '20'],
                    'S Peak Position [s]': ['29.8405', '29.8415']}.items()])
            result = saved_events(path)
            self.assertEqual(result['bursts'][0]['id'], 7)
            self.assertAlmostEqual(result['bursts'][0]['duration_ms'], 42.5)
            self.assertEqual(result['bursts'][0]['value'], 2)
            self.assertEqual(result['signals'][0]['value'], 45.6)
            self.assertEqual(result['skipped'], 1)

    def test_burst_preview_requires_two_close_peaks_and_clips_margin(self):
        rows = [dict(start=t, end=t+.001, peak_time=t+.0005, baseline=20.) for t in (0., .004, .4)]
        bursts = preview_bursts(rows, 10., 20., 1.)
        self.assertEqual(len(bursts), 1)
        self.assertEqual(bursts[0]['value'], 2)
        self.assertEqual(bursts[0]['start'], 0)
        with self.assertRaises(ValueError):
            preview_bursts(rows, -1)

    def test_whole_file_detection_owns_boundary_peaks_once(self):
        rng = np.random.default_rng(4)
        data = rng.normal(0., .001, 24000)
        data[9995:10005] += 1.
        data[10050:10060] += 1.
        settings = dict(chunk_size=10000, baseline_window=101, baseline_percentile=10.,
                        sg_window=11, sg_poly=3, pad_for_std=100,
                        noise_method='mad', threshold_k1=2., threshold_k2=8., min_width_ms=.2)
        rows = detect_file(data, .0001, settings, threading.Event())
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0]['start'], 1.)
        self.assertGreater(rows[0]['end'], 1.)
        self.assertEqual(len(preview_bursts(rows)), 1)
        cancelled = threading.Event()
        cancelled.set()
        self.assertIsNone(detect_file(data, .0001, settings, cancelled))

    def test_click_centers_absolute_region_and_highlights_it(self):
        recorded = {}
        browser = SimpleNamespace(n=1000000, dt=.0001,
            on_window_size_submit=lambda width, **kw: recorded.update(width=float(width)),
            on_select=lambda start, end: recorded.update(selection=(start, end)), draw=lambda: None)
        jump_to_event(browser, dict(start=29.8336, end=29.8761))
        left = browser._view_start_override * browser.dt
        self.assertLess(left, 29.8336)
        self.assertGreater(left + recorded['width'], 29.8761)
        self.assertEqual(recorded['selection'], (29.8336, 29.8761))
        with self.assertRaises(ValueError):
            jump_to_event(browser, dict(start=200., end=201.))


if __name__ == '__main__':
    unittest.main()
