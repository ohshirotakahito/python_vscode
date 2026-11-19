"""library.Index: 変わった履歴CSVだけを読み直す索引。"""
import os
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from chipdb import library


def write_chip(path, gap=1, day='20261113', fc='1.5'):
    path.write_bytes(f'{path.stem},,,,,,{gap}\r\nExperiment,OK,{day}_1000,User,EX1,FC#1,1,{fc}\r\nBreak\r\nEND\r\n'
                     .encode('cp932'))


class IndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / 'ChipDB' / 'DB'
        self.db.mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'local')})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.a, self.b = self.db / 'chip-a.csv', self.db / 'chip-b.csv'
        write_chip(self.a, gap=1)
        write_chip(self.b, gap=100, day='20261114')

    def paths(self):
        return sorted(self.db.glob('*.csv'))

    def test_first_update_reads_all_and_keeps_summary_and_dates(self):
        index = library.Index(self.db)
        result = index.update(self.paths())
        self.assertEqual((result.read, result.reused, result.removed), (2, 0, 0))
        entry = index.entries[self.b]
        self.assertEqual(entry.record['GAP#'], '100')
        self.assertEqual(entry.kinds, {'FC': 1})
        self.assertEqual(library.Index(self.db).entries[self.b].kinds, {'FC': 1})     # 控えにも残る
        self.assertEqual(entry.usage_dates, {date(2026, 11, 14)})
        self.assertTrue(library.cache_path(self.db).exists())

    def test_second_update_reads_only_changed_new_and_drops_removed(self):
        library.Index(self.db).update(self.paths())
        write_chip(self.a, gap=7, fc='2.25')             # 変わったファイル
        os.utime(self.a, ns=(1, 1_700_000_000_000_000_000))
        new = self.db / 'chip-c.csv'
        write_chip(new)
        self.b.unlink()
        index = library.Index(self.db)                  # 控えから読み込む（別の起動を想定）
        result = index.update(self.paths())
        self.assertEqual((result.read, result.reused, result.removed), (2, 0, 1))
        self.assertEqual(index.entries[self.a].record['GAP#'], '7')
        self.assertEqual(index.entries[self.a].record['FC Last'], '2.25')
        self.assertNotIn(self.b, index.entries)
        result = index.update(self.paths())
        self.assertEqual((result.read, result.reused), (0, 2))

    def test_force_and_rule_change_read_everything_again(self):
        library.Index(self.db).update(self.paths())
        self.assertEqual(library.Index(self.db).update(self.paths(), force=True).read, 2)
        with mock.patch.object(library, 'rules_version', return_value='other'):
            index = library.Index(self.db)
            self.assertEqual(index.entries, {})
            self.assertEqual(index.update(self.paths()).read, 2)

    def test_broken_file_is_an_error_not_a_crash(self):
        self.a.write_bytes(b'')
        bad = self.db / 'chip-bad.csv'
        bad.write_bytes(b'\x82')                         # cp932 で読めない
        result = library.Index(self.db).update(self.paths())
        reasons = dict(result.errors)
        self.assertIn(str(self.a), reasons)
        self.assertIn(str(bad), reasons)
        self.assertEqual(result.read, 3)

    def test_cancel_stops_and_keeps_what_was_read(self):
        index = library.Index(self.db)
        self.assertIsNone(index.update(self.paths(), cancelled=lambda: True))
        self.assertEqual(index.update(self.paths()).read, 2)


if __name__ == '__main__':
    unittest.main()
