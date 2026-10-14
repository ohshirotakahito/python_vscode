import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core


class HistorySyncTest(unittest.TestCase):
    def test_merge_deduplicate_and_repeat_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy, local = Path(directory) / 'old.csv', Path(directory) / 'new.csv'
            first = ['2026/10/15_10:00'] + ['日本語'] * 16
            second = first.copy()
            second[8] = '別サンプル'
            third = first.copy()
            third[0] = '2026/10/15_11:00'
            core.write_csv(legacy, core.HEADERS, [first, second, second])
            core.write_csv(local, core.HEADERS, [first, first, third])
            original = legacy.read_bytes()
            self.assertEqual(core.sync_history(legacy, local), (1, 1))
            saved = core.rows(local)
            self.assertEqual(saved[:2], [core.HEADERS, third])
            self.assertEqual({tuple(row) for row in saved[2:]}, {tuple(first), tuple(second)})
            self.assertEqual(legacy.read_bytes(), original)
            with patch.object(core, 'write_csv') as writer:
                self.assertEqual(core.sync_history(legacy, local), (0, 0))
                writer.assert_not_called()

    def test_create_local_and_reject_same_file(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy, local = Path(directory) / 'old.csv', Path(directory) / 'logs' / 'new.csv'
            record = ['2026/10/15_10:00'] + ['担当者'] * 16
            import csv
            with legacy.open('w', encoding='cp932', newline='') as stream:
                csv.writer(stream).writerows([record, record])
            self.assertEqual(core.sync_history(legacy, local), (1, 0))
            self.assertEqual(core.rows(local), [core.HEADERS, record])
            original = legacy.read_bytes()
            with self.assertRaises(ValueError):
                core.sync_history(legacy, legacy)
            self.assertEqual(legacy.read_bytes(), original)

    def test_missing_or_invalid_source_preserves_local(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy, local = Path(directory) / 'old.csv', Path(directory) / 'new.csv'
            core.write_csv(local, core.HEADERS, [['2026/10/15_10:00'] + ['x'] * 16])
            original = local.read_bytes()
            with self.assertRaises(FileNotFoundError):
                core.sync_history(legacy, local)
            core.write_csv(legacy, core.HEADERS, [['invalid date'] + ['x'] * 16])
            with self.assertRaises(ValueError):
                core.sync_history(legacy, local)
            self.assertEqual(local.read_bytes(), original)
