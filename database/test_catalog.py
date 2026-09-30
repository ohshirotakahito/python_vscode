from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from database import catalog


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / 'test.sqlite3'
        self.root = self.base / 'server'
        self.root.mkdir()
        self.source = dict(server='test', root=str(self.root), kind='analysis')
        catalog.initialize(self.db)

    def add(self, name, content=b'123'):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        return file

    def test_counts_classification_and_refresh(self):
        first = self.add('E/S/S_10k_Sample/T/ANAL/a.tdms')
        self.add('E/S/S_100k_Blank/T/stocked/b.tdms', b'12345')
        (self.root / 'empty').mkdir()
        self.assertEqual(catalog.scan_source(self.source, self.db), 2)
        rows = catalog.summary(self.db)
        self.assertEqual(sum(r['bytes'] for r in rows), 8)
        self.assertEqual({r['category'] for r in rows}, {'ANAL', 'stocked'})
        self.assertEqual(rows[0]['experiment'], 'E')
        self.assertEqual(rows[0]['sample'], 'S')
        self.assertGreater(catalog.sources(self.db)[0]['folder_count'], 1)
        first.unlink()
        self.add('E/S/new.txt', b'9')
        catalog.scan_source(self.source, self.db)
        self.assertEqual(len(catalog.find_files(self.db)), 2)
        self.assertEqual(sum(r['bytes'] for r in catalog.summary(self.db)), 6)
        self.assertEqual(len(catalog.find_files(self.db, search='new.txt')), 1)

    def test_failure_keeps_previous_and_other_source(self):
        self.add('E/original.txt')
        catalog.scan_source(self.source, self.db)
        success = catalog.sources(self.db)[0]['last_success']
        with patch.object(catalog.os, 'scandir', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                catalog.scan_source(self.source, self.db)
        state = catalog.sources(self.db)[0]
        self.assertEqual(state['file_count'], 1)
        self.assertEqual(state['last_success'], success)
        self.assertEqual(state['status'], '失敗')
        other = self.base / 'other'
        other.mkdir()
        catalog.scan_source(dict(server='SQserver', root=str(other), kind='experiment'), self.db)
        self.assertEqual(len(catalog.sources(self.db)), 2)
        self.assertEqual(len(catalog.find_files(self.db)), 1)

    def test_mid_scan_failure_rolls_back_partial_results(self):
        self.add('original.txt')
        catalog.scan_source(self.source, self.db)
        self.add('inaccessible/new.txt')
        original = catalog.os.scandir

        def fail_subfolder(folder):
            if Path(folder).name == 'inaccessible':
                raise PermissionError('subfolder denied')
            return original(folder)

        with patch.object(catalog.os, 'scandir', side_effect=fail_subfolder):
            with self.assertRaises(PermissionError):
                catalog.scan_source(self.source, self.db)
        self.assertEqual([r['relative_path'] for r in catalog.find_files(self.db)], ['original.txt'])

    def test_cancel_preserves_snapshot(self):
        self.add('old.txt')
        catalog.scan_source(self.source, self.db)
        self.add('new.txt')
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(catalog.ScanCancelled):
            catalog.scan_source(self.source, self.db, cancel=cancel)
        self.assertEqual(len(catalog.find_files(self.db)), 1)
        self.assertEqual(catalog.sources(self.db)[0]['status'], '中止')

    def test_offline_export_and_empty_success(self):
        self.source['kind'] = 'experiment'
        file = self.add('device/experiment/data.tdms')
        catalog.scan_source(self.source, self.db)
        backup = self.base / 'offline.sqlite3'
        catalog.export_snapshot(backup, self.db)
        file.unlink()
        self.assertEqual(catalog.summary(backup)[0]['category'], '実験原本')
        self.assertEqual(catalog.sources(backup)[0]['file_count'], 1)
        catalog.scan_source(self.source, self.db)
        self.assertEqual(catalog.sources(self.db)[0]['file_count'], 0)
        self.assertEqual(catalog.sources(self.db)[0]['status'], '取得済み')
        with self.assertRaises(ValueError):
            catalog.export_snapshot(self.db, self.db)


if __name__ == '__main__':
    unittest.main()
