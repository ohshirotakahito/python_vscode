"""Measurement sources must stay read-only and never guess ANAL correspondence."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from nptdms import ChannelObject, RootObject, TdmsWriter

from measurement_files import channel_metadata, measurement_t_folder, scan_measurement


def write_raw(path, hz=20000):
    path.parent.mkdir(parents=True, exist_ok=True)
    properties = {} if hz is None else {'wf_increment': 1 / hz}
    with TdmsWriter(path) as writer:
        writer.write_segment([ChannelObject('Data', 'Ch1', np.arange(100, dtype=float), properties=properties)])
    os.utime(path, (time.time() - 10, time.time() - 10))


def write_anal(path, original):
    path.parent.mkdir(parents=True, exist_ok=True)
    with TdmsWriter(path) as writer:
        writer.write_segment([RootObject(properties={'ANAL Condition Set': 'test-condition'}),
                              ChannelObject('AR Table', 'Filename', [original]),
                              ChannelObject('Data', 'Ch1', np.arange(100, dtype=float))])


class MeasurementFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'measurement'
        self.t = self.root / 'EXSV' / 'T'
        self.t.mkdir(parents=True)

    def scan(self):
        return scan_measurement(str(self.root), settle_seconds=0)[1]

    def test_accepts_measurement_exsv_and_t_only(self):
        for path in (self.root, self.root / 'EXSV', self.t):
            self.assertEqual(measurement_t_folder(str(path)), self.t.resolve())
        with self.assertRaises(ValueError):
            measurement_t_folder(str(self.root.parent))

    def test_exact_original_mapping_multiple_versions_and_stocked(self):
        name = 'D_20260501_1444_AN3_EXSV#1.tdms'
        raw = self.t / 'stocked' / name
        write_raw(raw)
        (self.t / (name + '_index')).write_bytes(b'index')
        write_anal(self.t / 'ANAL' / 'A@@2026_0101.tdms', name)
        write_anal(self.t / 'ANAL' / 'stocked' / 'A@@2026_0201.tdms', name)
        original = raw.read_bytes()
        rows = self.scan()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['ready'])
        self.assertEqual(rows[0]['location'], 'T/stocked')
        self.assertEqual(len(rows[0]['versions']), 2)
        self.assertIn('0201', rows[0]['versions'][0]['label'])
        self.assertEqual(rows[0]['versions'][0]['condition'], 'test-condition')
        self.assertEqual(raw.read_bytes(), original)

    def test_different_measurement_same_number_is_not_matched(self):
        write_raw(self.t / 'EXSV#1_first.tdms')
        write_anal(self.t / 'ANAL' / 'A@@2026_0101.tdms', 'EXSV#1_other.tdms')
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(r for r in rows if r['path'])['versions'], [])
        orphan = next(r for r in rows if not r['path'])
        self.assertFalse(orphan['ready'])
        self.assertEqual(len(orphan['versions']), 1)

    def test_measurement_root_anal_is_also_matched(self):
        write_raw(self.t / 'raw.tdms')
        write_anal(self.root / 'ANAL' / 'A@@root.tdms', 'raw.tdms')
        write_anal(self.root / 'EXSV' / 'ANAL' / 'A@@exsv.tdms', 'raw.tdms')
        rows = self.scan()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['versions']), 2)
        self.assertTrue(all('[ANAL]' in v['label'] or 'EXSV' in v['label'] for v in rows[0]['versions']))

    def test_duplicate_original_names_do_not_silently_choose(self):
        for parent in (self.t, self.t / 'stocked'):
            write_raw(parent / 'raw.tdms')
        write_anal(self.t / 'ANAL' / 'A@@test.tdms', 'raw.tdms')
        rows = self.scan()
        self.assertTrue(all(not r['versions'] for r in rows if r['path']))
        self.assertEqual(len(next(r for r in rows if not r['path'])['versions']), 1)

    def test_natural_measurement_order_and_missing_original_versions(self):
        for number in (10, 2, 1):
            write_raw(self.t / f'EXSV#{number}.tdms')
        self.assertEqual([r['name'] for r in self.scan()], ['EXSV#1.tdms', 'EXSV#2.tdms', 'EXSV#10.tdms'])
        write_anal(self.t / 'ANAL' / 'A@@2026_0101.tdms', 'missing.tdms')
        write_anal(self.t / 'ANAL' / 'A@@2026_0201.tdms', 'missing.tdms')
        missing = [r for r in self.scan() if not r['path']]
        self.assertEqual(len(missing), 1)
        self.assertEqual(len(missing[0]['versions']), 2)

    def test_recent_and_changing_files_are_not_ready(self):
        raw = self.t / 'raw.tdms'
        write_raw(raw)
        os.utime(raw, None)
        self.assertFalse(self.scan()[0]['ready'])
        os.utime(raw, (time.time() - 10, time.time() - 10))
        def change(_):
            with raw.open('ab') as stream:
                stream.write(b'changing')
        with patch('measurement_files.time.sleep', side_effect=change):
            self.assertFalse(self.scan()[0]['ready'])

    def test_metadata_uses_waveform_increment_and_lists_missing_channels(self):
        raw = self.t / 'raw.tdms'
        write_raw(raw)
        info = channel_metadata(str(raw), 'Data', 'Ch1')
        self.assertEqual(info['points'], 100)
        self.assertEqual(info['hz'], 20000)
        with self.assertRaisesRegex(ValueError, 'Data/Ch1'):
            channel_metadata(str(raw), 'Wrong', 'Ch2')
        write_raw(raw, hz=None)
        self.assertIsNone(channel_metadata(str(raw), 'Data', 'Ch1')['hz'])

    def test_example_waveform_auto_detection_ignores_numeric_tables(self):
        raw = self.t / 'raw.tdms'
        with TdmsWriter(raw) as writer:
            writer.write_segment([ChannelObject('Example', 'Ch1', np.arange(100, dtype=float)),
                                  *[ChannelObject('SRZ Table', f'field{i}', np.arange(10)) for i in range(100)]])
        info = channel_metadata(str(raw), 'Data', 'Ch1', auto_select=True)
        self.assertEqual((info['group'], info['channel']), ('Example', 'Ch1'))
        with self.assertRaises(ValueError) as error:
            channel_metadata(str(raw), 'Data', 'Ch1')
        self.assertLess(len(str(error.exception)), 250)

    def test_multiple_waveform_candidates_require_explicit_choice(self):
        raw = self.t / 'raw.tdms'
        with TdmsWriter(raw) as writer:
            writer.write_segment([ChannelObject(group, 'Ch1', np.arange(100, dtype=float))
                                  for group in ['Example', 'Other']])
        with self.assertRaises(ValueError):
            channel_metadata(str(raw), 'Data', 'Ch1', auto_select=True)
        self.assertEqual(channel_metadata(str(raw), 'Other', 'Ch1', auto_select=True)['group'], 'Other')


if __name__ == '__main__':
    unittest.main()
