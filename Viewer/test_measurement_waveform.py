"""Raw display/export must bypass detection and honor the selected-file allowlist."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from tdms_signal_core import RobustTdmsMultiFolderBrowser
from test_measurement_files import write_raw


class MeasurementWaveformTests(unittest.TestCase):
    def test_raw_display_and_export_do_not_run_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            raw = folder / 'chosen.tdms'
            write_raw(raw)
            (folder / 'broken.tdms').write_bytes(b'not a TDMS')
            data = np.arange(100, dtype=float)
            with patch('tdms_signal_core.detect_pulses_hysteresis', side_effect=AssertionError('detected raw data')), \
                    patch('tdms_signal_core.robust_baseline_and_std', side_effect=AssertionError('estimated raw noise')), \
                    patch('tdms_signal_base.tdms_read_channel', side_effect=AssertionError('ignored preloaded data')):
                browser = RobustTdmsMultiFolderBrowser(
                    anal_folders=[str(folder)], file_paths=[str(raw)], preloaded_data={str(raw): data},
                    detection_enabled=False, out_root=str(folder / 'export'))
                try:
                    self.assertEqual(browser.current_tdms_path(), str(raw))
                    self.assertTrue(np.array_equal(browser.data, data))
                    self.assertFalse(browser._l_baseline.get_visible())
                    self.assertFalse(browser._l_t1.get_visible())
                    figure, axis = plt.subplots()
                    browser._render_export(axis, 0, 80, False)
                    self.assertEqual([line.get_label() for line in axis.lines], ['Signal'])
                    browser.sel_tmin, browser.sel_tmax = 0., .008
                    # Call the base export so a GUI preview is not opened.
                    from tdms_signal_base import TdmsMultiFolderBrowser
                    TdmsMultiFolderBrowser.on_save(browser, None)
                    metadata = next((folder / 'export').rglob('*.json'))
                    self.assertFalse(json.loads(metadata.read_text(encoding='utf-8'))['viewer_detection_enabled'])
                    self.assertEqual((folder / 'broken.tdms').read_bytes(), b'not a TDMS')
                finally:
                    plt.close('all')


if __name__ == '__main__':
    unittest.main()
