import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import os

from filemanager.anal_manager import (load_inventory, scan_anal, move_plan, execute_moves,
                                      analysis_groups, deletion_plan, undo_plan, matches_inventory_query,
                                      extract_clashes)


class AnalManagerTest(unittest.TestCase):
    def test_exact_experiment_query_excludes_related_names(self):
        row = dict(root='//server/Kumamoto', experiment='Kumamoto', sample='sample')
        self.assertTrue(matches_inventory_query(row, ' kumamoto ', True))
        for experiment in ('Kumamoto_N', 'Kumamoto_N2', 'Kimamoto_N2'):
            other = dict(row, experiment=experiment)
            self.assertFalse(matches_inventory_query(other, 'Kumamoto', True))
        self.assertTrue(matches_inventory_query(dict(row, experiment='Kumamoto_N2'), 'Kumamoto'))
        self.assertTrue(matches_inventory_query(row, '', True))
        self.assertTrue(matches_inventory_query(row, 'sample'))
        self.assertFalse(matches_inventory_query(row, 'sample', True))

    def test_space_separated_words_all_have_to_match(self):
        row = dict(root=r'\\qdserver\analysis' + '\\', experiment='Kumamoto', sample='KSN4', target='BNAL@miR-1')
        self.assertTrue(matches_inventory_query(row, 'kumamoto qdserver'))
        self.assertTrue(matches_inventory_query(row, ' KSN4  analysis miR-1 '))
        self.assertFalse(matches_inventory_query(row, 'kumamoto qtserver'))
        self.assertFalse(matches_inventory_query(row, 'Kumamoto qdserver', True), 'exact compares the experiment only')

    def test_extract_clashes_lists_numbers_doubled_in_anal(self):
        files = [dict(location='ANAL', number=98), dict(location='stocked', number=1), dict(location='stocked', number=98),
                 dict(location='stocked', number=None)]
        self.assertEqual(extract_clashes(files, [f for f in files if f['location'] == 'stocked']), [98])
        self.assertEqual(extract_clashes(files, files[1:2]), [])

    def test_saved_inventory_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'inventory.sqlite3'
            with closing(sqlite3.connect(db)) as con:
                with con:
                    con.execute('CREATE TABLE rack_samples(root, experiment, sample, anal_tdms, anal_distinct, anal_stocked_tdms, last_success)')
                    con.execute('INSERT INTO rack_samples VALUES(?,?,?,?,?,?,?)',
                                (tmp, 'ex', 'sample', 5, 3, 2, '2026-10-20'))
            before = db.read_bytes()
            row, = load_inventory(db)
            self.assertEqual(row['mixed'], 2)
            self.assertEqual(Path(row['anal_path']), Path(tmp) / 'ex/sample/sample_10k_Sample/T/ANAL')
            self.assertEqual(db.read_bytes(), before)

    def test_archive_restore_and_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'ANAL'
            folder.mkdir()
            (folder / 'sample old#001.tdms').write_bytes(b'old')
            (folder / 'sample new#001.TDMS').write_bytes(b'new')
            (folder / 'ignored.txt').touch()
            rows = scan_anal(folder)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r['candidate'] for r in rows))
            plan = move_plan(folder, [rows[0]])
            log = execute_moves(plan, Path(tmp) / 'logs')
            self.assertFalse(Path(rows[0]['path']).exists())
            self.assertEqual(json.loads(log.read_text(encoding='utf-8'))['operations'][0]['status'], '完了')
            archived = [r for r in scan_anal(folder) if r['location'] == 'stocked']
            execute_moves(move_plan(folder, archived, restore=True), Path(tmp) / 'logs')
            self.assertTrue(Path(rows[0]['path']).exists())

    def test_stale_preview_and_collision_do_not_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'ANAL'
            folder.mkdir()
            file = folder / 'a#001.tdms'
            file.write_bytes(b'one')
            plan = move_plan(folder, scan_anal(folder))
            file.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                execute_moves(plan, Path(tmp) / 'logs')
            (folder / 'stocked').mkdir()
            (folder / 'stocked' / file.name).write_bytes(b'existing')
            rows = [r for r in scan_anal(folder) if r['location'] == 'ANAL']
            with self.assertRaises(ValueError):
                move_plan(folder, rows)
            self.assertEqual(file.read_bytes(), b'changed')
            self.assertEqual((folder / 'stocked' / file.name).read_bytes(), b'existing')

    def test_groups_keep_counts_and_unique_numbers_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ('A@@2025_0308_old#001.tdms', 'A@@2025_0308_old#002.tdms',
                         'A@@2025_0819_new#001.tdms'):
                (folder / name).write_bytes(b'content')
            groups = analysis_groups(scan_anal(folder))
            self.assertEqual([g['age'] for g in groups], ['古い', '新しい'])
            self.assertEqual([g['count'] for g in groups], [2, 1])
            self.assertEqual([g['unique'] for g in groups], [1, 0])

    def test_delete_group_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'ANAL'
            folder.mkdir()
            older = folder / 'A@@2025_0308_old#001.tdms'
            newer = folder / 'A@@2025_0819_new#001.tdms'
            older.write_bytes(b'old analysis')
            newer.write_bytes(b'new analysis')
            rows = scan_anal(folder)
            selected = [r for r in rows if r['group'] == '2025-08-19']
            log = execute_moves(deletion_plan(folder, selected), Path(tmp) / 'logs')
            self.assertTrue(older.exists())
            self.assertFalse(newer.exists())
            self.assertEqual(len(scan_anal(folder)), 1)
            execute_moves(undo_plan(log), Path(tmp) / 'logs')
            self.assertEqual(newer.read_bytes(), b'new analysis')
            with self.assertRaises(ValueError):
                undo_plan(log)  # Cannot restore twice over existing files.

    def test_extract_undo_and_content_change_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'ANAL'
            (folder / 'stocked').mkdir(parents=True)
            source = folder / 'stocked' / 'a#001.tdms'
            source.write_bytes(b'old')
            plan = move_plan(folder, scan_anal(folder), restore=True)
            plan['action'] = 'extract'
            log = execute_moves(plan, Path(tmp) / 'logs')
            target = folder / source.name
            self.assertTrue(target.exists())
            undo = undo_plan(log)
            execute_moves(undo, Path(tmp) / 'logs')
            self.assertTrue(source.exists())
            log = execute_moves(move_plan(folder, scan_anal(folder), restore=True), Path(tmp) / 'logs')
            stat = target.stat()
            target.write_bytes(b'new')
            import os
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            with self.assertRaises(ValueError):
                undo_plan(log)

    @unittest.skipUnless(os.name == 'nt', 'Windows rename behavior')
    def test_partial_deletion_can_undo_completed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'ANAL'
            folder.mkdir()
            first, second = folder / 'a#001.tdms', folder / 'b#002.tdms'
            first.write_bytes(b'a')
            second.write_bytes(b'b')
            plan = deletion_plan(folder, scan_anal(folder))
            rename = os.rename
            def fail_second(source, target):
                if Path(source) == second:
                    raise PermissionError('file in use')
                return rename(source, target)
            logs = Path(tmp) / 'logs'
            with patch('filemanager.anal_manager.os.rename', side_effect=fail_second):
                with self.assertRaises(RuntimeError):
                    execute_moves(plan, logs)
            log, = logs.glob('*.json')
            record = json.loads(log.read_text(encoding='utf-8'))
            self.assertEqual(record['status'], '途中停止')
            self.assertEqual([o['status'] for o in record['operations']], ['完了', '予定'])
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            execute_moves(undo_plan(log), logs)
            self.assertEqual(first.read_bytes(), b'a')
            self.assertEqual(second.read_bytes(), b'b')


if __name__ == '__main__':
    unittest.main()
