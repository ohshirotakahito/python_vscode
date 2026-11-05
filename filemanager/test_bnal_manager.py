from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from filemanager.bnal_manager import load_inventory
from filemanager.anal_manager import scan_anal, analysis_groups, deletion_plan, execute_moves, undo_plan


class BnalManagerTest(unittest.TestCase):
    def test_saved_counts_and_exact_target_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute('CREATE TABLE rack_samples(root,experiment,sample,anal_tdms,anal_stocked_tdms)')
                    db.execute('CREATE TABLE rack_bnals(root,experiment,sample,target,tdms,tdms_distinct,tdms_checked)')
                    db.execute('INSERT INTO rack_samples VALUES(?,?,?,?,?)', (tmp, 'ex', 'S', 3, 1))
                    for target, count, distinct in [('seq', 6, 4), ('unscanned', None, None), ('old', 4, None)]:
                        db.execute('INSERT INTO rack_bnals VALUES(?,?,?,?,?,?,?)', (tmp, 'ex', 'S', target, count, distinct, 'saved'))
            before = path.read_bytes()
            rows = {r['target']: r for r in load_inventory(path)}
            row = rows['BNAL@seq']
            self.assertEqual((row['mixed'], row['expected'], row['excess']), (2, 4, 2))
            self.assertEqual(Path(row['anal_path']), Path(tmp) / 'ex/S/S_10k_Sample/T/BNAL@seq')
            self.assertIsNone(rows['BNAL@unscanned']['mixed'])
            self.assertIn('未計数', rows['BNAL@unscanned']['state'])
            self.assertIn('番号未調査', rows['BNAL@old']['state'])
            self.assertEqual(path.read_bytes(), before)

    def test_bnal_delete_and_restore_isolated_from_other_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'BNAL@seq'
            other = Path(tmp) / 'BNAL@other'
            folder.mkdir()
            other.mkdir()
            first = folder / 'B@@2025_0101_1200_S1_10k_Sample#001.tdms'
            second = folder / 'B@@2025_0201_1200_S1_10k_Sample#001.tdms'
            first.write_bytes(b'old')
            second.write_bytes(b'new')
            untouched = other / second.name
            untouched.write_bytes(b'other')
            rows = scan_anal(folder, 'BNAL')
            self.assertTrue(all(r['candidate'] and r['location'] == 'BNAL' for r in rows))
            self.assertEqual([g['anal'] for g in analysis_groups(rows)], [1, 1])
            log = execute_moves(deletion_plan(folder, [rows[0]]), Path(tmp) / 'logs')
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertEqual(untouched.read_bytes(), b'other')
            execute_moves(undo_plan(log), Path(tmp) / 'logs')
            self.assertEqual(first.read_bytes(), b'old')


if __name__ == '__main__':
    unittest.main()
