from pathlib import Path
import tempfile
import unittest

from filemanager import bnal_stock
from filemanager.anal_manager import execute_moves, undo_plan, bnal_row_state, scan_anal, deletion_plan


def make_sample(root):
    t = Path(root) / 'ex' / 'KSN1' / 'KSN1_10k_Sample' / 'T'
    (t / 'ANAL').mkdir(parents=True)
    for n in range(1, 6):
        stem = f'A@@2021_1201_11{n:02d}_58_KSN1_10k_Sample#{n:03d}'
        (t / 'ANAL' / (stem + '.tdms')).write_bytes(b'anal')
        (t / 'ANAL' / (stem + '.tdms_index')).write_bytes(b'i')
        (t / 'ANAL' / f'A@2021_1201_11{n:02d}_58@KSN1_10k_Sample#{n:03d}').mkdir()  # ANAL result folder stays
    return t


def add_bnal(t, target, numbers, empty=()):
    folder = t / ('BNAL@' + target)
    folder.mkdir()
    for n in numbers:
        stamp = f'2026_0902_07{n:02d}'
        (folder / f'B@@{stamp}_KSN1_10k_Sample#{n:03d}.tdms').write_bytes(b'' if n in empty else b'bnal')
        result = folder / f'B@@{stamp}@KSN1_10k_Sample#{n:03d}'
        result.mkdir()
        (result / f'Depth@AS_ISX@{stamp}.txt').write_text('x')
    return folder


def names(folder):
    return sorted(p.name for p in Path(folder).iterdir() if p.name != bnal_stock.HISTORY)


class BnalStockTest(unittest.TestCase):
    def test_minimum_common_numbers_stock_anal_and_delete_ahead_bnal(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            base = add_bnal(t, 'miR-1', range(1, 6))
            n7 = add_bnal(t, 'miR-1_N_7', range(1, 6))
            m22 = add_bnal(t, 'miR-1_M_22', range(1, 6), empty={5})  # #005 interrupted: 0 bytes
            built = bnal_stock.plan(bnal_stock.scan(t, ['miR-1', 'miR-1_N_7', 'miR-1_M_22']))
            self.assertEqual(built['summary']['common'], 4)
            self.assertEqual(built['summary']['missing_text'], '#005')
            self.assertEqual(built['summary']['archive'], 4)
            self.assertEqual(built['summary']['delete'], 3)
            ops = built['plan']['operations']
            self.assertTrue(all('.整理履歴' in o['target'] for o in ops[:6]), 'deletions run before the moves')
            log = execute_moves(built['plan'], Path(tmp) / 'logs')

            anal = names(t / 'ANAL')
            self.assertEqual([n for n in anal if n.endswith('.tdms')], ['A@@2021_1201_1105_58_KSN1_10k_Sample#005.tdms'])
            self.assertIn('A@@2021_1201_1105_58_KSN1_10k_Sample#005.tdms_index', anal)
            self.assertEqual(sum(n.startswith('A@2021') for n in anal), 5, 'ANAL result folders are not moved')
            self.assertEqual(len(names(t / 'ANAL' / 'stocked')), 8)
            for folder in (base, n7, m22):
                self.assertFalse(any('#005' in n for n in names(folder)), folder)
                self.assertEqual(len(names(folder)), 8)

            undo = undo_plan(log)
            execute_moves(undo, Path(tmp) / 'logs')
            self.assertFalse((t / 'ANAL' / 'stocked').exists() and names(t / 'ANAL' / 'stocked'))
            for folder in (base, n7, m22):
                self.assertEqual(len(names(folder)), 10)
                self.assertTrue((folder / 'B@@2026_0902_0705@KSN1_10k_Sample#005' / 'Depth@AS_ISX@2026_0902_0705.txt').exists())

    def test_targets_at_different_progress_follow_the_smallest_common_set(self):
        """KSN4-like: base 177 / N_7 176 / M_8 174 / M_22 172 of 178, here with a gap in M_8."""
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / 'ex' / 'KSN4' / 'KSN4_10k_Sample' / 'T'
            (t / 'ANAL').mkdir(parents=True)
            for n in range(1, 9):
                (t / 'ANAL' / f'A@@2021_1201_11{n:02d}_58_KSN4_10k_Sample#{n:03d}.tdms').write_bytes(b'a')
            folders = dict(base=add_bnal(t, 'b', range(1, 8)), n7=add_bnal(t, 'b_N_7', range(1, 7)),
                           m8=add_bnal(t, 'b_M_8', [1, 2, 4, 5, 6]), m22=add_bnal(t, 'b_M_22', range(1, 6)))
            built = bnal_stock.plan(bnal_stock.scan(t, ['b', 'b_N_7', 'b_M_8', 'b_M_22']))
            s = built['summary']
            self.assertEqual((s['common_text'], s['missing_text']), ('#001-#002,#004-#005', '#003,#006-#008'))
            self.assertEqual([x['delete'] for x in s['targets']], [3, 2, 1, 1])  # #003 goes from M_22 too: M_8 lacks it
            execute_moves(built['plan'], Path(tmp) / 'logs')
            kept = sorted(n[-8:-5] for n in names(t / 'ANAL') if n.endswith('.tdms'))
            self.assertEqual(kept, ['003', '006', '007', '008'])
            for folder in folders.values():  # every target now holds exactly the common set
                self.assertEqual(sorted(n[-8:-5] for n in names(folder) if n.endswith('.tdms')),
                                 ['001', '002', '004', '005'])

    def test_stage_and_result_follow_the_whole_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            add_bnal(t, 'miR-1', range(1, 6))
            add_bnal(t, 'miR-1_M_22', range(1, 4))
            targets = ['miR-1', 'miR-1_M_22']
            built = bnal_stock.plan(bnal_stock.scan(t, targets))
            self.assertEqual(bnal_stock.stage(built)[0], 'todo')
            execute_moves(built['plan'], Path(tmp) / 'logs')
            self.assertEqual(bnal_stock.result_text(built['plan']),
                             'ANAL/stockedへ 3件・削除（.整理履歴へ） 2件（結果フォルダ 2件）')
            kind, text = bnal_stock.stage(bnal_stock.plan(bnal_stock.scan(t, targets)))
            self.assertEqual(kind, 'wait')
            self.assertIn('欠番 2件（#004-#005）', text)
            for n in (4, 5):
                for target in targets:
                    (t / ('BNAL@' + target) / f'B@@2026_0903_07{n:02d}_KSN1_10k_Sample#{n:03d}.tdms').write_bytes(b'b')
            built = bnal_stock.plan(bnal_stock.scan(t, targets))
            self.assertEqual(bnal_stock.stage(built)[0], 'restore')
            execute_moves(built['plan'], Path(tmp) / 'logs')
            self.assertEqual(bnal_stock.result_text(built['plan']), 'ANALへ 3件')
            self.assertEqual(bnal_stock.stage(bnal_stock.plan(bnal_stock.scan(t, targets)))[0], 'done')

    def test_split_anal_pair_follows_its_tdms(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            (t / 'ANAL' / 'stocked').mkdir()
            index = 'A@@2021_1201_1101_58_KSN1_10k_Sample#001.tdms_index'
            (t / 'ANAL' / index).rename(t / 'ANAL' / 'stocked' / index)
            add_bnal(t, 'miR-1', range(1, 6))  # complete: nothing to stock, the index goes back to its .tdms
            built = bnal_stock.plan(bnal_stock.scan(t, ['miR-1']))
            self.assertEqual(len(built['plan']['operations']), 1)
            execute_moves(built['plan'], Path(tmp) / 'logs')
            self.assertTrue((t / 'ANAL' / index).exists())

    def test_journal_save_waits_out_a_brief_lock(self):
        from unittest.mock import patch
        import os
        real, calls = os.replace, []

        def flaky(source, target):
            calls.append(target)
            if len(calls) in (2, 3):  # a scanner holds the journal for a moment mid-run
                raise PermissionError(5, 'アクセスが拒否されました。')
            return real(source, target)
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            add_bnal(t, 'miR-1', range(1, 4))
            built = bnal_stock.plan(bnal_stock.scan(t, ['miR-1']))
            with patch('shared.file_moves.os.replace', flaky), patch('shared.file_moves.time.sleep'):
                log = execute_moves(built['plan'], Path(tmp) / 'logs')
            self.assertIn('"status": "完了"', log.read_text(encoding='utf-8'))
            self.assertEqual(len(names(t / 'ANAL' / 'stocked')), 6)

    def test_unstarted_target_keeps_all_anal_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            add_bnal(t, 'miR-1', range(1, 4))
            built = bnal_stock.plan(bnal_stock.scan(t, ['miR-1', 'miR-2']))
            self.assertEqual(built['summary']['common'], 0)
            self.assertEqual(built['summary']['archive'], 0)
            self.assertEqual(built['summary']['delete'], 3)
            self.assertTrue(any('未着手' in w for w in built['warnings']))

    def test_previous_round_stocked_gap_returns_to_anal(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            stocked = t / 'ANAL' / 'stocked'
            stocked.mkdir()
            for p in (t / 'ANAL').glob('*#00[12].tdms*'):
                p.rename(stocked / p.name)
            add_bnal(t, 'miR-1', [2, 3, 4, 5])  # #001 is missing in BNAL although it was stocked
            built = bnal_stock.plan(bnal_stock.scan(t, ['miR-1']))
            self.assertEqual(built['summary']['restore'], 1)
            execute_moves(built['plan'], Path(tmp) / 'logs')
            self.assertEqual([n for n in names(t / 'ANAL') if n.endswith('.tdms')],
                             ['A@@2021_1201_1101_58_KSN1_10k_Sample#001.tdms'])

    def test_complete_targets_need_nothing_and_change_after_scan_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            add_bnal(t, 'miR-1', range(1, 6))
            built = bnal_stock.plan(bnal_stock.scan(t, ['miR-1']))
            self.assertEqual(built['plan']['operations'], [])
            add_bnal(t, 'miR-1_M_8', range(1, 3))
            built = bnal_stock.plan(bnal_stock.scan(t, ['miR-1', 'miR-1_M_8']))
            (t / 'ANAL' / 'A@@2021_1201_1101_58_KSN1_10k_Sample#001.tdms').write_bytes(b'changed!')
            with self.assertRaises(ValueError):  # checked before anything moves
                execute_moves(built['plan'], Path(tmp) / 'logs')
            self.assertFalse((t / 'ANAL' / 'stocked').exists())
            self.assertEqual(len(names(t / 'BNAL@miR-1')), 10)

    def test_bnal_duplicate_deletion_takes_result_folder_and_undoes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            folder = add_bnal(t, 'miR-1', range(1, 3))
            rerun = folder / 'B@@2026_0902_0744_KSN1_10k_Sample#002.tdms'
            rerun.write_bytes(b'again')
            rows = scan_anal(folder, 'BNAL')
            old = [r for r in rows if r['path'].endswith('0702_KSN1_10k_Sample#002.tdms')]
            plan = deletion_plan(folder, old, with_results=True)
            self.assertEqual([o.get('dir', False) for o in plan['operations']], [False, True])
            log = execute_moves(plan, Path(tmp) / 'logs')
            self.assertFalse((folder / 'B@@2026_0902_0702@KSN1_10k_Sample#002').exists())
            self.assertTrue(rerun.exists())
            execute_moves(undo_plan(log), Path(tmp) / 'logs')
            self.assertTrue((folder / 'B@@2026_0902_0702@KSN1_10k_Sample#002' / 'Depth@AS_ISX@2026_0902_0702.txt').exists())
            self.assertEqual(len(deletion_plan(folder, old)['operations']), 1, 'ANAL keeps file-only deletion')

    def test_refresh_db_saves_one_sample_with_bnal_counts(self):
        from contextlib import closing
        import sqlite3
        from filemanager.anal_manager import refresh_db, load_inventory
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            add_bnal(t, 'miR-1', range(1, 4))
            db = Path(tmp) / 'inventory.sqlite3'
            self.assertEqual(refresh_db(db, [(tmp, 'ex', 'KSN1')]), 1)
            row, = load_inventory(db)
            self.assertEqual((row['t_tdms'], row['anal_tdms'], row['bnal_folders']), (0, 5, 1))
            (t / 'BNAL@miR-1' / 'B@@2026_0902_0701_KSN1_10k_Sample#001.tdms').unlink()
            refresh_db(db, [(row['root'], 'ex', 'KSN1')])  # the saved (normalised) root works as well
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute('SELECT count(*), max(tdms) FROM rack_bnals').fetchone(), (1, 2))
                self.assertEqual(con.execute('SELECT count(*) FROM rack_samples').fetchone(), (1,))

    def test_running_database_scan_only_warns_for_the_same_share(self):
        from shared import rack_store
        from filemanager.anal_manager import scan_warning
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'inventory.sqlite3'
            self.assertEqual(scan_warning(db, [tmp]), '', 'missing DB is not an error')
            share = Path(tmp) / 'share'
            rack_store.record_root(db, share, 'QD', status='実行中', last_attempt='2026-10-22T03:00:00+00:00')
            self.assertIn('調査中', scan_warning(db, [str(share) + '\\']))
            self.assertEqual(scan_warning(db, [Path(tmp) / 'other']), '')
            rack_store.record_root(db, share, 'QD', status='取得済み')
            self.assertEqual(scan_warning(db, [share]), '')

    def test_bnal_row_state_flags_unfinished_and_stocked(self):
        row = dict(anal_tdms=169, expected=219, sample_stocked=None)
        self.assertEqual(bnal_row_state(row), (True, 'BNAL未完了 169/219（残り 50）'))
        self.assertEqual(bnal_row_state(dict(row, anal_tdms=219)), (False, 'BNAL完了'))
        self.assertEqual(bnal_row_state(dict(row, anal_tdms=219, sample_stocked=169)),
                         (True, 'BNAL完了／ANAL/stocked 169件（復帰待ち）'))
        self.assertTrue(bnal_row_state(dict(row, anal_tdms=None))[0])

    def test_restore_when_every_target_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_sample(tmp)
            add_bnal(t, 'miR-1', range(1, 6))
            add_bnal(t, 'miR-1_M_22', range(1, 5))
            execute_moves(bnal_stock.plan(bnal_stock.scan(t, ['miR-1', 'miR-1_M_22']))['plan'], Path(tmp) / 'logs')
            self.assertEqual(len(names(t / 'ANAL' / 'stocked')), 8)
            # The BNAL run makes #005 for both targets; the same plan now brings everything back.
            for target in ('miR-1', 'miR-1_M_22'):
                (t / ('BNAL@' + target) / 'B@@2026_0903_0705_KSN1_10k_Sample#005.tdms').write_bytes(b'bnal')
            built = bnal_stock.plan(bnal_stock.scan(t, ['miR-1', 'miR-1_M_22']))
            self.assertEqual((built['summary']['restore'], built['summary']['delete']), (4, 0))
            execute_moves(built['plan'], Path(tmp) / 'logs')
            self.assertEqual(sum(n.endswith('.tdms') for n in names(t / 'ANAL')), 5)
            self.assertEqual(names(t / 'ANAL' / 'stocked'), [])

if __name__ == '__main__':
    unittest.main()
