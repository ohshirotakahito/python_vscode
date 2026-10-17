from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from database.recent_progress import inspect_recent, inventory_counts
from database import catalog


class RecentProgressTests(unittest.TestCase):
    def test_inventory_counts_only_direct_folders_and_matching_history(self):
        with TemporaryDirectory() as temp:
            db_path = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db_path)
            root = r'\\SQserver\backup_01'
            with catalog.connect(db_path) as db:
                db.execute("INSERT INTO sources(id,server,root,kind,last_success) VALUES(1,'SQserver',?,'experiment','2026-10-15')", (root,))
                db.executemany('INSERT INTO folders VALUES(1,?)',
                               [(name,) for name in ('.', 'started', 'started/EXSV', 'started/EXSV/T', 'pending')])
            row = [''] * 17
            row[2], row[6] = 'AXN3', 'started'
            with patch('database.recent_progress.core.machines', return_value={
                'AXN3': {'Tmp_backup': root + '\\'},
            }), patch('database.recent_progress.core.history', return_value=[tuple(row)]):
                counts = inventory_counts(db_path)
            result = next(iter(counts.values()))
            self.assertEqual(result['direct_folders'], 2)
            self.assertEqual(result['analysis_started'], 1)

    def test_matches_machine_and_name_and_excludes_other_backups(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'backup_01'
            root.mkdir()
            for name in ('started', 'pending', 'other_machine'):
                (root / name).mkdir()
            (root / 'ignore.txt').write_text('')
            row = [''] * 17
            row[0], row[2], row[6] = '2026/10/15_10:00', 'AN1', 'started'
            other = row.copy()
            other[2], other[6] = 'AN2', 'other_machine'
            with patch('database.recent_progress.core.machines', return_value={
                'AN1': {'Tmp_backup': str(root)},
                'AN9': {'Tmp_backup': str(Path(temp) / 'backup_09')},
            }), patch('database.recent_progress.core.history', return_value=[tuple(row), tuple(other)]):
                summary, details = inspect_recent('machines', 'legacy', 'local')
            self.assertEqual(len(summary), 8)
            self.assertEqual(summary[0][2:6], (3, 1, 2, '33.3%'))
            self.assertEqual({r[1]: r[2] for r in details},
                             {'started': '着手済み', 'pending': '未着手', 'other_machine': '未着手'})

    def test_unreadable_history_does_not_mark_folders_pending(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'backup_08'
            (root / 'experiment').mkdir(parents=True)
            with patch('database.recent_progress.core.machines', return_value={
                'AN8': {'Tmp_backup': str(root)},
            }), patch('database.recent_progress.core.history', side_effect=FileNotFoundError('missing CSV')):
                summary, details = inspect_recent('machines', 'legacy', 'local')
            self.assertEqual(details[0][2], '確認不可')
            self.assertEqual(summary[-1][3:6], ('', '', ''))

    def test_unreachable_folder_is_not_reported_as_empty(self):
        with patch('database.recent_progress.core.machines', return_value={
            'AN1': {'Tmp_backup': 'Z:/nonexistent-test-folder/backup_01'},
        }), patch('database.recent_progress.core.history', return_value=[]):
            summary, details = inspect_recent('machines', 'legacy', 'local')
        self.assertEqual(summary[0][1], '確認不可')
        self.assertEqual(summary[0][2], '')
        self.assertEqual(details, [])
