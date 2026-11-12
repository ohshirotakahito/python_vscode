"""shared/file_moves.py の記録フォルダ（database/file_logs）と、旧 filemanager/ からの移行のテスト。"""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from shared import REPO_ROOT, file_moves


class LogDirTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.new, self.old = self.root / 'database' / 'file_logs', self.root / 'filemanager'
        for name, value in (('LOG_ROOT', self.new), ('LEGACY_LOG_ROOT', self.old)):
            patcher = mock.patch.object(file_moves, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_default_location_is_under_database(self):
        with mock.patch.object(file_moves, 'LOG_ROOT', REPO_ROOT / 'database' / 'file_logs'):
            self.assertEqual(file_moves.journal_dir('T_stock'), REPO_ROOT / 'database' / 'file_logs' / 't_stock_logs')

    def test_legacy_records_move_once_and_existing_names_stay(self):
        legacy = self.old / 'anal_logs'
        (legacy / 'sub').mkdir(parents=True)
        (legacy / 'a.json').write_text('old-a', encoding='utf-8')
        (legacy / 'b.json').write_text('old-b', encoding='utf-8')
        (legacy / 'sub' / 'c.json').write_text('c', encoding='utf-8')
        (self.new / 'anal_logs').mkdir(parents=True)
        (self.new / 'anal_logs' / 'b.json').write_text('new-b', encoding='utf-8')

        folder = file_moves.journal_dir('ANAL')

        self.assertEqual(folder, self.new / 'anal_logs')
        self.assertEqual((folder / 'a.json').read_text(encoding='utf-8'), 'old-a')
        self.assertEqual((folder / 'sub' / 'c.json').read_text(encoding='utf-8'), 'c')
        # 同名は上書きせず旧フォルダに残す（消さない）
        self.assertEqual((folder / 'b.json').read_text(encoding='utf-8'), 'new-b')
        self.assertEqual((legacy / 'b.json').read_text(encoding='utf-8'), 'old-b')
        self.assertEqual(sorted(p.name for p in legacy.iterdir()), ['b.json'])

    def test_empty_legacy_folder_is_removed_and_missing_one_is_fine(self):
        (self.old / 'rename_logs').mkdir(parents=True)
        self.assertEqual(file_moves.log_dir('rename_logs'), self.new / 'rename_logs')
        self.assertFalse((self.old / 'rename_logs').exists())
        self.assertEqual(file_moves.log_dir('bnal_logs'), self.new / 'bnal_logs')  # 旧フォルダなし: 作らずに返す
        self.assertFalse((self.new / 'bnal_logs').exists())


if __name__ == '__main__':
    unittest.main()
