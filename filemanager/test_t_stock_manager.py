from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from filemanager.anal_manager import execute_moves, undo_plan
from filemanager.t_stock_manager import load_inventory, plan_moves, scan_t, summary


def make_t(root):
    t = Path(root) / 'ex' / 's1' / 's1_10k_Sample' / 'T'
    (t / 'ANAL' / 'stocked').mkdir(parents=True)
    for n in range(1, 7):
        (t / f'A_s1#{n:03d}.tdms').write_bytes(b'x' * n)
    (t / 'A_s1#001.tdms_index').write_bytes(b'i')
    (t / 'nonumber.tdms').write_bytes(b'n')
    # ANAL holds #1, #2 (stocked), #4, #5 (empty = interrupted) -> #3 is a gap, #5 and #6 unfinished.
    for n, folder in ((1, 'ANAL'), (2, 'ANAL/stocked'), (4, 'ANAL')):
        (t / folder / f'A@@2026_0101_0000_00_s1#{n:03d}.tdms').write_bytes(b'a')
    (t / 'ANAL' / 'A@@2026_0101_0000_00_s1#005.tdms').touch()
    return t


class TStockTest(unittest.TestCase):
    def test_restore_reunites_a_pair_split_by_a_stopped_run(self):
        """CT17: the run stopped after #054.tdms went back to T but before its .tdms_index did."""
        with tempfile.TemporaryDirectory() as tmp:
            t = make_t(tmp)
            (t / 'stocked').mkdir()
            (t / 'A_s1#001.tdms_index').rename(t / 'stocked' / 'A_s1#001.tdms_index')  # split pair
            (t / 'A_s1#002.tdms').rename(t / 'stocked' / 'A_s1#002.tdms')             # still to restore
            (t / 'stocked' / 'A_s1#009.tdms_index').write_bytes(b'lonely')             # no .tdms anywhere
            plan, skipped = plan_moves(scan_t(t), restore=True)
            execute_moves(plan, Path(tmp) / 'logs')
            self.assertTrue((t / 'A_s1#001.tdms_index').exists())
            self.assertTrue((t / 'A_s1#002.tdms').exists())
            self.assertEqual(sorted(p.name for p in (t / 'stocked').iterdir()), ['A_s1#009.tdms_index'])
            self.assertIn(('A_s1#009.tdms_index', '対応する.tdmsがない付属ファイル'), skipped)

    def test_archive_keeps_a_pair_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_t(tmp)
            (t / 'stocked').mkdir()
            (t / 'A_s1#001.tdms_index').rename(t / 'stocked' / 'A_s1#001.tdms_index')
            plan, _ = plan_moves(scan_t(t), restore=False)  # #001 is analysed: its .tdms joins the index
            self.assertEqual(sum('#001' in o['source'] for o in plan['operations']), 1)

    def test_archive_moves_only_analysed_numbers_with_companions(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_t(tmp)
            scan = scan_t(t)
            s = summary(scan)
            self.assertEqual((s['t'], s['t_analysed'], s['t_unknown'], s['gaps']), (7, 3, 1, [3]))
            plan, skipped = plan_moves(scan, restore=False)
            moved = sorted(Path(o['source']).name for o in plan['operations'])
            self.assertEqual(moved, ['A_s1#001.tdms', 'A_s1#001.tdms_index', 'A_s1#002.tdms', 'A_s1#004.tdms'])
            self.assertEqual(skipped, [('nonumber.tdms', '#番号なし')])
            log = execute_moves(plan, Path(tmp) / 'logs')
            self.assertEqual(sorted(p.name for p in t.glob('*.tdms*')),
                             ['A_s1#003.tdms', 'A_s1#005.tdms', 'A_s1#006.tdms', 'nonumber.tdms'])
            self.assertTrue((t / 'stocked' / 'A_s1#001.tdms_index').exists())
            # Undo from the journal puts everything back.
            execute_moves(undo_plan(log), Path(tmp) / 'logs')
            self.assertFalse(any((t / 'stocked').iterdir()))

    def test_real_original_names_number_after_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / 'T'
            (t / 'ANAL').mkdir(parents=True)
            name = 'KSN3_10k_Sample#{:03d} D_20211130_0943 ANAN#1Pex1n2_KSN-3_EXSV#10_20211130_1138_SGM  D0.540nm_G092'
            for n in (1, 2):
                (t / (name.format(n) + '.tdms')).write_bytes(b'x')
                (t / (name.format(n) + '.tdms_index')).write_bytes(b'i')
            (t / 'ANAL' / 'A@@2021_1206_1937_51_KSN3_10k_Sample#001.tdms').write_bytes(b'a')
            (t / 'ANAL' / 'A@@2021_1206_1937_51_KSN3_10k_Sample#001.tdms_index').write_bytes(b'a')
            scan = scan_t(t)
            self.assertEqual(sorted((f['number'], f['analysed']) for f in scan['files']), [(1, True), (2, False)])
            plan, _ = plan_moves(scan, restore=False)
            self.assertEqual(len(plan['operations']), 2)  # #001 .tdms + .tdms_index

    def test_keep_last_and_restore_skips_name_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_t(tmp)
            plan, skipped = plan_moves(scan_t(t), restore=False, keep_last=True)
            self.assertNotIn('A_s1#004.tdms', [Path(o['source']).name for o in plan['operations']])
            self.assertIn(('A_s1#004.tdms', '最後の解析番号（書きかけ対策でTに残す）'), skipped)
            execute_moves(plan, Path(tmp) / 'logs')
            (t / 'A_s1#002.tdms').write_bytes(b'conflict')
            plan, skipped = plan_moves(scan_t(t), restore=True)
            self.assertEqual(skipped, [('A_s1#002.tdms', '移動先に同名あり')])
            execute_moves(plan, Path(tmp) / 'logs')
            self.assertEqual([p.name for p in (t / 'stocked').iterdir()], ['A_s1#002.tdms'])
            self.assertTrue((t / 'A_s1#001.tdms_index').exists())

    def test_inventory_targets_partial_and_stocked_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'inv.sqlite3'
            with closing(sqlite3.connect(db)) as con, con:
                con.execute('CREATE TABLE rack_samples(root, experiment, sample, t_tdms, t_stocked_tdms, anal_tdms, anal_stocked_tdms)')
                con.executemany('INSERT INTO rack_samples VALUES(?,?,?,?,?,?,?)', [
                    (tmp, 'ex', 'partial', 10, 0, 4, 0), (tmp, 'ex', 'done', 10, 0, 10, 0),
                    (tmp, 'ex', 'unstarted', 10, 0, 0, 0), (tmp, 'ex', 'copy', 0, 0, 5, 0),
                    (tmp, 'ex', 'stocked_done', 0, 10, 10, 0)])
            rows = {r['sample']: r for r in load_inventory(db)}
            self.assertEqual({k for k, r in rows.items() if r['target']}, {'partial', 'stocked_done'})
            self.assertEqual(rows['partial']['ratio'], 0.4)
            self.assertTrue(rows['partial']['t_path'].endswith(str(Path('partial/partial_10k_Sample/T'))))


if __name__ == '__main__':
    unittest.main()
