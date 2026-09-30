from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from database import catalog, inventory_views


class PartialUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'share'
        self.root.mkdir()
        self.db = Path(self.temp.name) / 'inventory.sqlite3'
        catalog.initialize(self.db)
        self.source = dict(server='test', root=str(self.root), kind='analysis')

    def file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'123')
        return path

    def scan(self, scope='.', **kwargs):
        return catalog.scan_source(self.source, self.db, scope=scope, **kwargs)

    def paths(self):
        return {r['relative_path'] for r in catalog.find_files(self.db)}

    def test_partial_replaces_only_selected_experiment_and_preserves_full_timestamp(self):
        old = self.file('E/S/S_10k_Sample/T/old.tdms')
        other = self.file('E2/S/untouched.tdms')
        with patch.object(catalog, 'now', return_value='2026-01-01T00:00:00+00:00'):
            self.scan()
        old.unlink()
        other.unlink()
        self.file('E/S/S_10k_Sample/T/new.tdms')
        self.file('E2/not_scanned.tdms')
        with patch.object(catalog, 'now', return_value='2026-02-01T00:00:00+00:00'):
            self.scan('E')
        self.assertEqual(self.paths(), {'E/S/S_10k_Sample/T/new.tdms', 'E2/S/untouched.tdms'})
        source = catalog.sources(self.db)[0]
        self.assertEqual(source['last_success'], '2026-01-01T00:00:00+00:00')
        rows = inventory_views.analysis_inventory(self.db)
        self.assertEqual(next(r for r in rows if r['experiment'] == 'E')['last_success'], '2026-02-01T00:00:00+00:00')
        self.assertEqual(next(r for r in rows if r['experiment'] == 'E2')['last_success'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(catalog.update_history(self.db)[0]['scope'], 'E')

    def test_first_scan_can_be_one_experiment(self):
        self.file('E/S/S_10k_Sample/T/file.tdms')
        self.file('Other/not_scanned.txt')
        self.scan('E')
        self.assertEqual(len(self.paths()), 1)
        self.assertIsNone(catalog.sources(self.db)[0]['last_success'])
        self.assertEqual(len(inventory_views.analysis_inventory(self.db)), 4)

    def test_missing_and_failed_scope_preserve_previous_snapshot(self):
        self.file('E/file.tdms')
        self.scan()
        with patch.object(catalog.os, 'scandir', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                self.scan('E')
        self.assertEqual(self.paths(), {'E/file.tdms'})
        with self.assertRaises(FileNotFoundError):
            self.scan('Missing')
        self.assertEqual(self.paths(), {'E/file.tdms'})
        self.assertEqual([r['status'] for r in catalog.update_history(self.db)[:2]], ['失敗', '失敗'])

    def test_cancel_after_partial_writes_rolls_back_and_logs(self):
        self.file('E/old.txt')
        self.scan()
        self.file('E/new.txt')
        cancel = threading.Event()

        def stop_on_last_report(info):
            if info['file_count']:
                cancel.set()

        with self.assertRaises(catalog.ScanCancelled):
            self.scan('E', cancel=cancel, on_progress=stop_on_last_report)
        self.assertEqual(self.paths(), {'E/old.txt'})
        self.assertEqual(catalog.update_history(self.db)[0]['status'], '中止')

    @unittest.skipUnless(catalog.os.name == 'nt', 'Windows case-insensitive paths')
    def test_windows_case_does_not_duplicate_inventory(self):
        self.file('Experiment/S/file.txt')
        self.scan()
        self.file('Experiment/S/new.txt')
        self.scan('experiment')
        self.assertEqual(self.paths(), {'Experiment/S/file.txt', 'Experiment/S/new.txt'})

    def test_literal_wildcards_and_path_boundaries(self):
        self.file('E_1/a.txt')
        self.file('E21/a.txt')
        self.file('E_1_extra/a.txt')
        self.scan()
        self.file('E_1/new.txt')
        self.scan('E_1')
        self.assertEqual(len(self.paths()), 4)
        for scope in ('../escape', '/absolute', 'C:/outside', 'E/../E21', '\\\\server\\share'):
            with self.assertRaises(ValueError):
                self.scan(scope)

    def test_archive_file_update_and_scope_metadata(self):
        self.source['kind'] = 'sq_data'
        self.file('AN1/20260930_1234 TOAN#1Pex3n0_test.zip')
        self.file('AN1/other.zip')
        with patch.object(catalog, 'now', return_value='2026-01-01T00:00:00+00:00'):
            self.scan()
        with patch.object(catalog, 'now', return_value='2026-02-01T00:00:00+00:00'):
            self.scan('AN1/20260930_1234 TOAN#1Pex3n0_test.zip')
        groups, entries = inventory_views.sq_inventory(self.db)
        self.assertEqual(len(entries), 2)
        self.assertEqual(groups[0]['last_success'], '2026-01-01T00:00:00+00:00')
        self.assertIn('一部更新', groups[0]['status'])
        self.assertEqual(next(r for r in entries if r['name'] == 'other.zip')['last_success'], '2026-01-01T00:00:00+00:00')

    def test_upgrade_v1_and_history_backup(self):
        with catalog.connect(self.db) as db:
            db.execute('DROP TABLE updates')
            db.execute('PRAGMA user_version=1')
        self.assertEqual(catalog.update_history(self.db), [])
        catalog.initialize(self.db)
        self.file('E/a.txt')
        self.scan('E')
        backup = Path(self.temp.name) / 'export.sqlite3'
        catalog.export_snapshot(backup, self.db)
        self.assertEqual(catalog.update_history(backup), catalog.update_history(self.db))

    def test_progress_includes_empty_folders_and_final_counts(self):
        (self.root / 'E/empty').mkdir(parents=True)
        self.file('E/a.txt')
        events = []
        self.scan('E', on_progress=events.append)
        self.assertEqual(events[-1]['file_count'], 1)
        self.assertEqual(events[-1]['folder_count'], 2)
        self.assertEqual(catalog.update_history(self.db)[0]['folder_count'], 2)


if __name__ == '__main__':
    unittest.main()
