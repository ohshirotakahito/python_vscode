import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from database import refresh_inventory as inventory
from database.scan_lock import ScanLock
from shared import catalog, rack_store


class RefreshInventoryTests(unittest.TestCase):
    def test_discovers_new_sample_and_retains_previous_on_connection_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, db = base / 'share', base / 'inventory.sqlite3'
            root.mkdir()
            source = dict(server='test-server', root=str(root), kind='analysis')
            lock_path = base / 'scan.lock'
            make_lock = lambda owner: ScanLock(owner, lock_path)
            events = []
            with patch.object(inventory, 'ScanLock', side_effect=make_lock):
                inventory.refresh(db, [source], threading.Event(), lambda *event: events.append(event))
                target = root / 'experiment' / 'new-sample' / 'new-sample_10k_Sample' / 'T'
                target.mkdir(parents=True)
                (target / 'measurement_Sample#001.tdms').touch()
                inventory.refresh(db, [source], threading.Event(), lambda *event: events.append(event))
                with closing(sqlite3.connect(db)) as connection:
                    row = connection.execute('SELECT sample,t_tdms FROM rack_samples').fetchone()
                self.assertEqual(row, ('new-sample', 1))
                root.rename(base / 'offline')
                inventory.refresh(db, [source], threading.Event(), lambda *event: events.append(event))
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM rack_samples').fetchone()[0], 1)
            self.assertIn('取得失敗', events[-1][1])

    def test_selected_group_includes_saved_custom_root(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'inventory.sqlite3'
            root = str(Path(directory) / 'custom')
            rack_store.record_root(db, root, 'custom-server', status='取得済み')
            selected = inventory.targets(db, 'custom-server/custom')
            self.assertEqual(len(selected), 1)
            self.assertEqual(catalog.root_identity(selected[0]['root']), catalog.root_identity(root))
            self.assertGreater(len(inventory.targets(db)), 1)

    def test_scan_lock_blocks_concurrent_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / 'scan.lock'
            held = ScanLock('other process', lock_path)
            self.assertTrue(held.acquire())
            try:
                with patch.object(inventory, 'ScanLock', side_effect=lambda owner: ScanLock(owner, lock_path)):
                    with self.assertRaisesRegex(RuntimeError, 'ほかのサーバー調査'):
                        inventory.refresh(Path(directory) / 'db.sqlite3', [], threading.Event(), lambda *event: None)
            finally:
                held.release()


if __name__ == '__main__':
    unittest.main()
