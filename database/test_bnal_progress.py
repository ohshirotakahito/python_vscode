from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import threading
from database import bnal_progress, rackstation_view
from shared import catalog

QT, QD = r'\\QTserver\backup', r'\\QDserver\analysis'
LABELS = {catalog.root_identity(QT): 'QTserver/backup', catalog.root_identity(QD): 'QDserver/analysis'}


def sample(root, anal, experiment='BR', name='BRA'):
    return dict(root=root, experiment=experiment, sample=name, anal_tdms=anal)


def bnal(root, target, tdms, experiment='BR', name='BRA'):
    return dict(root=root, experiment=experiment, sample=name, target=target, tdms=tdms, tdms_checked='t' if tdms is not None else None)


class TargetCsvTests(TestCase):
    def test_reads_first_column_without_header_blank_lines_or_duplicates(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'list.csv'
            path.write_bytes('hsa-miR-1,UGU,22\r\n\r\n hsa-miR-1_M_8 ,UGM,22\r\nhsa-miR-1,UGU,22\r\n'.encode('utf-8-sig'))
            self.assertEqual(bnal_progress.read_targets(path), ['hsa-miR-1', 'hsa-miR-1_M_8'])
            (Path(temp) / 'b.CSV').write_text('x,y,1', encoding='cp932')
            (Path(temp) / 'note.txt').write_text('')
            self.assertEqual([n.lower() for n in bnal_progress.list_target_csvs(temp)], ['b.csv', 'list.csv'])

    def test_cp932_names_are_read(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'jp.csv'
            path.write_bytes('配列A,ACGT,4\n'.encode('cp932'))
            self.assertEqual(bnal_progress.read_targets(path), ['配列A'])


class BuildProgressTests(TestCase):
    def test_server_overview_keeps_copies_unknown_counts_and_idle_servers(self):
        rows, *_ = self.build([sample(QT, 10), sample(QD, 10)],
                              [bnal(QT, 'A', 10), bnal(QD, 'A', 4), bnal(QD, 'B', None)],
                              ['A', 'B', 'C'])
        result = {s['server']: s for s in bnal_progress.server_overview(
            rows, ['A', 'B', 'C'], ['QTserver', 'QDserver', 'QKserver'])}
        self.assertEqual((result['QTserver']['complete'], result['QTserver']['tdms']), (1, 10))
        self.assertEqual((result['QDserver']['remaining'], result['QDserver']['unknown']), (6, 1))
        self.assertEqual(result['QKserver']['targets'], 0)

    def build(self, samples, bnals, expected, show_extra=False):
        return bnal_progress.build_progress([('BR', 'BRA')], expected, samples, bnals, LABELS, show_extra)

    def test_modified_sequence_checks_use_base_on_same_share_even_outside_csv(self):
        bnals = [bnal(QT, 'A', 8), bnal(QD, 'A', 10), bnal(QT, 'A_N_7', 10), bnal(QT, 'A_M_8', 8)]
        rows, shares, total, _ = self.build([sample(QT, 10), sample(QD, 10)], bnals, ['A_N_7', 'A_M_8'])
        n, m = rows
        self.assertEqual((n['sequence_type'], n['base_target'], n['base_tdms']), ('修飾配列', 'A', 8))
        self.assertEqual((n['anal_check'], n['base_check'], n['state']),
                         ('一致', '超過 2件', '進行中（基本配列と不一致）'))
        self.assertEqual((m['anal_check'], m['base_check'], m['state']), ('不足 2件', '一致', '進行中'))
        self.assertEqual(total['complete'], 0)
        self.assertEqual(sum(s['complete'] for s in shares), 0)
        self.assertEqual(bnal_progress._leaf_cell([n])['status'], 'partial')

    def test_base_missing_uncounted_and_matching(self):
        for base_count, include_base, check, complete in [(None, False, '基本配列なし', 0),
                                                        (None, True, '未計数・確認不可', 0),
                                                        (10, True, '一致', 1)]:
            with self.subTest(base_count=base_count, include_base=include_base):
                bnals = [bnal(QT, 'A_N_7', 10)] + ([bnal(QT, 'A', base_count)] if include_base else [])
                rows, _, total, _ = self.build([sample(QT, 10)], bnals, ['A_N_7'])
                self.assertEqual(rows[0]['base_check'], check)
                self.assertEqual(total['complete'], complete)

    def test_progress_is_per_share_and_overall_counts_each_target_once(self):
        samples = [sample(QT, 100), sample(QD, 100)]
        bnals = [bnal(QT, 'A', 100), bnal(QD, 'B', 40), bnal(QT, 'C', None)]
        rows, shares, total, warnings = self.build(samples, bnals, ['A', 'B', 'C', 'D'])
        by_target = {r['target']: r for r in rows}
        self.assertEqual((by_target['A']['group'], by_target['A']['progress'], by_target['A']['state']),
                         ('QTserver/backup', '100.0%', '完了'))
        self.assertEqual((by_target['B']['group'], by_target['B']['progress']), ('QDserver/analysis', '40.0%'))
        self.assertEqual(by_target['C']['state'], '着手（TDMS未計数）')
        self.assertEqual((by_target['D']['group'], by_target['D']['state'], by_target['D']['anal_tdms']),
                         (bnal_progress.UNSTARTED_GROUP, '未着手', 100))
        shares = {s['group']: s for s in shares}
        self.assertEqual((shares['QTserver/backup']['started'], shares['QTserver/backup']['complete'],
                          shares['QTserver/backup']['uncounted'], shares['QTserver/backup']['done']), (2, 1, 1, 100))
        self.assertEqual((total['expected'], total['started'], total['complete'], total['goal']), (4, 3, 1, 4))
        self.assertAlmostEqual(total['done'], 1.4)  # A 100% + B 40% + C (uncounted) 0 + D 0
        self.assertEqual(warnings, [])

    def test_duplicate_target_on_two_shares_is_flagged_and_counted_once(self):
        samples = [sample(QT, 10), sample(QD, 10)]
        rows, _, total, _ = self.build(samples, [bnal(QT, 'A', 4), bnal(QD, 'A', 10)], ['A'])
        self.assertEqual([r['tag'] for r in rows], ['duplicate', 'duplicate'])
        self.assertEqual((total['started'], total['complete'], total['done'], total['goal']), (1, 1, 1.0, 1))

    def test_excess_missing_anal_and_differing_anal_counts_are_reported(self):
        samples = [sample(QT, 10), sample(QD, 8)]
        rows, _, total, warnings = self.build(samples, [bnal(QT, 'A', 12), bnal(QD, 'B', 3)], ['A', 'B'])
        self.assertEqual({r['target']: r['tag'] for r in rows}, {'A': 'excess', 'B': 'partial'})
        self.assertAlmostEqual(total['done'], 1 + 3 / 8)  # A capped at 100%
        self.assertEqual(len(warnings), 1)
        self.assertIn('ANAL数が異なります', warnings[0])
        rows, *_ = self.build([sample(QT, None)], [bnal(QT, 'A', 3)], ['A'])
        self.assertEqual(rows[0]['state'], 'ANALなし・要確認')

    def test_targets_outside_the_csv_are_hidden_unless_requested_and_never_counted(self):
        samples = [sample(QT, 10)]
        bnals = [bnal(QT, 'A', 10), bnal(QT, 'NPR1UTR1166_N_23', 10)]
        rows, _, total, _ = self.build(samples, bnals, ['A'])
        self.assertEqual([r['target'] for r in rows], ['A'])
        rows, shares, total, _ = self.build(samples, bnals, ['A'], show_extra=True)
        self.assertEqual([(r['target'], r['tag']) for r in rows], [('A', 'complete'), ('NPR1UTR1166_N_23', 'extra')])
        self.assertEqual((total['expected'], shares[0]['started']), (1, 1))

    def test_share_scanned_before_names_were_saved_is_not_reported_as_unstarted(self):
        samples = [dict(sample(QT, 77), bnal_folders=302), dict(sample(QD, 77), bnal_folders=1)]
        rows, _, total, warnings = self.build(samples, [bnal(QD, 'A', 77)], ['A', 'B'])
        self.assertEqual({r['target']: r['state'] for r in rows}, {'A': '完了', 'B': '未確認（要更新）'})
        self.assertEqual(len(warnings), 1)
        self.assertIn('QTserver/backup', warnings[0])
        self.assertNotIn('QDserver/analysis', warnings[0])
        self.assertEqual(total['started'], 1)

    def test_files_set_aside_in_anal_stocked_count_toward_the_original_anal(self):
        samples = [dict(sample(QT, 34), anal_stocked_tdms=20)]
        rows, _, total, warnings = self.build(samples, [bnal(QT, 'A', 54), bnal(QT, 'B', 53)], ['A', 'B'])
        self.assertEqual({r['target']: (r['anal_tdms'], r['state']) for r in rows},
                         {'A': (54, '完了'), 'B': (54, '進行中')})
        self.assertEqual(total['goal'], 2)
        self.assertAlmostEqual(total['done'], 1 + 53 / 54)
        self.assertTrue(any('退避中' in w and '20件' in w for w in warnings))

    def test_bra_case_reruns_show_as_excess_with_duplicates_and_missing_anal_is_reported(self):
        samples = [dict(sample(QT, 90), t_tdms=0, t_stocked_tdms=137)]
        bnals = [dict(bnal(QT, 'A', 153), tdms_distinct=137), dict(bnal(QT, 'B', 76), tdms_distinct=76)]
        rows, _, total, warnings = self.build(samples, bnals, ['A', 'B'])
        by_target = {r['target']: r for r in rows}
        self.assertEqual((by_target['A']['anal_tdms'], by_target['A']['bnal_tdms'], by_target['A']['rerun'],
                          by_target['A']['state']), (90, 153, 16, '超過・要確認'))
        self.assertEqual((by_target['B']['rerun'], by_target['B']['state']), (0, '進行中'))
        self.assertTrue(any('不足 QTserver/backup 47件' in w for w in warnings))

    def test_mixed_anal_generations_are_flagged_but_bnal_of_every_file_is_complete(self):
        # QTserver/analysis KUHCC10: ANAL holds a 2023 set (#1-137) and a 2026 set (#1-113); BNAL ran on all 250.
        samples = [dict(sample(QT, 250), t_tdms=0, anal_distinct=137), dict(sample(QD, 113), t_tdms=113, anal_distinct=113)]
        rows, _, total, warnings = self.build(samples, [dict(bnal(QT, 'A', 250), tdms_distinct=137)], ['A'])
        self.assertEqual((rows[0]['progress'], rows[0]['state'], rows[0]['rerun']), ('100.0%', '完了', 113))
        self.assertEqual(total['complete'], 1)
        self.assertTrue(any('重複 QTserver/backup 113件' in w for w in warnings))
        self.assertTrue(any('ANAL数が異なります' in w for w in warnings))

    def test_other_samples_and_unknown_roots_do_not_leak_in(self):
        other = r'\\Elsewhere\share'
        samples = [sample(QT, 10), sample(QT, 10, name='BRN'), sample(other, 10)]
        bnals = [bnal(QT, 'A', 5, name='BRN'), bnal(other, 'A', 10)]
        rows, _, total, _ = self.build(samples, bnals, ['A'])
        self.assertEqual([(r['group'], r['bnal_tdms']) for r in rows], [(other, 10)])
        self.assertEqual(total['complete'], 1)


class MapTests(TestCase):
    def rows(self):
        samples = [sample(QT, 100, name='DONE'), sample(QT, 100, name='HALF'), sample(QT, 50, name='NEW'),
                   dict(sample(QT, 100, name='ALMOST'), anal_stocked_tdms=0), sample(QD, 100, name='HALF')]
        bnals = [bnal(QT, 'A', 100, name='DONE'), bnal(QT, 'A_N_7', 100, name='DONE'),
                 bnal(QT, 'A', 50, name='HALF'), bnal(QD, 'A', 60, name='HALF'),
                 bnal(QT, 'A', 100, name='ALMOST'), bnal(QT, 'A_N_7', 90, name='ALMOST')]
        selected = [('BR', 'DONE'), ('BR', 'HALF'), ('BR', 'NEW'), ('BR', 'ALMOST')]
        rows, *_ = bnal_progress.build_progress(selected, ['A', 'A_N_7'], samples, bnals, LABELS)
        return rows

    def test_samples_sort_by_remaining_then_unstarted_then_nearly_done(self):
        columns, samples, rates = bnal_progress.build_map(self.rows(), ['A', 'A_N_7'], order='work')
        self.assertEqual(columns, ['A', 'A_N_7'])
        # HALF: best share 60% of A (40 left) + A_N_7 unstarted (100 left) = 140; NEW: 50 + 50 = 100.
        self.assertEqual([(s['sample'], s['remaining'], s['unstarted']) for s in samples],
                         [('HALF', 140, 1), ('NEW', 100, 2), ('ALMOST', 10, 0), ('DONE', 0, 0)])
        half = samples[0]['cells']
        self.assertEqual((half['A']['status'], half['A']['servers']), ('partial', ('QDa', 'QTb')))
        self.assertAlmostEqual(half['A']['ratio'], 0.6)
        self.assertEqual(half['A_N_7']['status'], 'unstarted')
        self.assertAlmostEqual(rates['A'], (0.6 + 0 + 1 + 1) / 4)

    def test_each_server_gets_its_own_cells_beside_the_overall_one(self):
        servers = ['QDserver', 'QTserver', 'Rackstation']
        _, samples, _ = bnal_progress.build_map(self.rows(), ['A', 'A_N_7'], servers=servers)
        half = next(s for s in samples if s['sample'] == 'HALF')
        self.assertEqual(sorted(half['by_server']), servers)
        self.assertAlmostEqual(half['by_server']['QDserver']['A']['ratio'], 0.6)
        self.assertAlmostEqual(half['by_server']['QTserver']['A']['ratio'], 0.5)
        self.assertEqual(half['by_server']['Rackstation'], {})   # no BNAL@ there: drawn empty
        self.assertNotIn('A_N_7', half['by_server']['QDserver'])
        self.assertEqual(half['cells']['A_N_7']['status'], 'unstarted')

    def test_ties_on_remaining_and_unstarted_put_the_nearly_done_sample_first(self):
        def row(name, anal, done):
            return dict(experiment='E', sample=name, target='A', group='QTserver/backup', anal_tdms=anal,
                        bnal_tdms=done, state='進行中', tag='partial', flags=())
        # Both have 5 TDMS left and no unstarted target; HIGH is 95% done, LOW only 50%.
        _, samples, _ = bnal_progress.build_map([row('LOW', 10, 5), row('HIGH', 100, 95)], ['A'], order='work')
        self.assertEqual([(s['sample'], s['remaining']) for s in samples], [('HIGH', 5), ('LOW', 5)])

    def test_default_order_is_natural_sample_name_ascending(self):
        def row(name):
            return dict(experiment='Kumamoto_N2', sample=name, target='A', group='QTserver/analysis', anal_tdms=10,
                        bnal_tdms=10 if name == 'KUHCC10' else 0, state='進行中', tag='partial', flags=())
        _, samples, _ = bnal_progress.build_map([row(n) for n in ('KUHCC10', 'kuhcc2', 'KUHCC01', 'KUHCC9')], ['A'])
        self.assertEqual([s['sample'] for s in samples], ['KUHCC01', 'kuhcc2', 'KUHCC9', 'KUHCC10'])

    def test_variants_fold_into_parent_target_and_flags_reach_the_map(self):
        rows = self.rows()
        for row in rows:
            if row['sample'] == 'ALMOST':
                row['flags'] = ('退避',)
        columns, samples, _ = bnal_progress.build_map(rows, ['A', 'A_N_7'], by_parent=True)
        self.assertEqual(columns, ['A'])
        by_name = {s['sample']: s for s in samples}
        self.assertEqual((by_name['DONE']['cells']['A']['status'], by_name['DONE']['cells']['A']['variants']), ('complete', 2))
        self.assertEqual(by_name['NEW']['cells']['A']['status'], 'unstarted')
        self.assertEqual(by_name['HALF']['cells']['A']['status'], 'partial')
        self.assertEqual(by_name['ALMOST']['flags'], ('退避',))
        # Sorting still uses the variant-level work.
        self.assertEqual(by_name['HALF']['remaining'], 140)

    def test_flags_come_from_build_progress_warnings(self):
        samples = [dict(sample(QT, 34), anal_stocked_tdms=20, t_tdms=60, anal_distinct=30), sample(QD, 50)]
        rows, *_ = bnal_progress.build_progress([('BR', 'BRA')], ['A'], samples, [], LABELS)
        self.assertEqual(set(rows[0]['flags']), {'差異', '退避', '不足', '混在'})

    def test_share_abbreviations_and_parent_names(self):
        self.assertEqual([bnal_progress.share_abbr(g) for g in ('QTserver/analysis', 'QTserver/backup',
                                                                 'Rackstation/archive', 'QDserver/analysis')],
                         ['QTa', 'QTb', 'Rr', 'QDa'])
        self.assertEqual(bnal_progress.parent_target('hsa-miR-193a-5p_M_11'), 'hsa-miR-193a-5p')
        self.assertEqual(bnal_progress.parent_target('NPR1UTR1166_N_23'), 'NPR1UTR1166')
        self.assertEqual(bnal_progress.parent_target('NPR1CpG11'), 'NPR1CpG11')


class RestoreTests(TestCase):
    def make(self, temp):
        root = Path(temp) / 'analysis'
        anal = root / 'BR' / 'BRN' / 'BRN_10k_Sample' / 'T' / 'ANAL'
        for relative in ('a21.tdms', 'Stocked/a01.tdms', 'Stocked/a01.tdms_index', 'Stocked/a21.tdms',
                         'Stocked/nested/deep.tdms'):
            (anal / relative).parent.mkdir(parents=True, exist_ok=True)
            (anal / relative).write_text(relative)
        return root, anal

    def test_plan_moves_direct_files_only_and_never_overwrites(self):
        with TemporaryDirectory() as temp:
            root, anal = self.make(temp)
            plan = bnal_progress.plan_restore(str(root), 'BR', 'BRN')
            self.assertEqual([s.name for s, _ in plan['moves']], ['a01.tdms', 'a01.tdms_index'])
            self.assertEqual([s.name for s, _ in plan['conflicts']], ['a21.tdms'])
            log = Path(temp) / 'log.csv'
            (anal / 'a01.tdms_index').write_text('appeared meanwhile')
            moved, skipped = bnal_progress.restore(plan, log)
            self.assertEqual((moved, [name for name, _ in skipped]), (1, ['a01.tdms_index']))
            self.assertEqual((anal / 'a01.tdms').read_text(), 'Stocked/a01.tdms')
            self.assertEqual((anal / 'a01.tdms_index').read_text(), 'appeared meanwhile')
            self.assertEqual((anal / 'a21.tdms').read_text(), 'a21.tdms')
            self.assertTrue((anal / 'Stocked' / 'a21.tdms').exists())
            self.assertTrue((anal / 'Stocked' / 'nested' / 'deep.tdms').exists())
            self.assertEqual(len(log.read_text(encoding='utf-8-sig').splitlines()), 4)

    def test_missing_stocked_plans_nothing(self):
        with TemporaryDirectory() as temp:
            plan = bnal_progress.plan_restore(temp, 'BR', 'BRN')
            self.assertEqual((plan['moves'], plan['conflicts'], plan['stocked']), ([], [], None))

    def test_scan_records_stocked_and_refresh_updates_counts_after_restore(self):
        with TemporaryDirectory() as temp:
            root, anal = self.make(temp)
            (anal / 'Stocked' / 'a21.tdms').unlink()
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            rackstation_view.scan_samples(dict(server='QTserver', root=str(root), kind='analysis'), db,
                                          threading.Event(), lambda *args: None)
            samples, _ = bnal_progress.load_saved(db)
            self.assertEqual((samples[0]['anal_tdms'], samples[0]['anal_stocked_tdms']), (1, 1))
            bnal_progress.restore(bnal_progress.plan_restore(samples[0]['root'], 'BR', 'BRN'), Path(temp) / 'log.csv')
            bnal_progress.refresh_sample_counts(db, samples[0]['root'], 'BR', 'BRN')
            samples, _ = bnal_progress.load_saved(db)
            self.assertEqual((samples[0]['anal_tdms'], samples[0]['anal_stocked_tdms']), (2, 0))


class OldGenerationTests(TestCase):
    def test_runs_split_only_where_numbering_restarts_at_the_lowest_number(self):
        names = ([f'A@@2023_0728_07{m:02d}_10_S_10k_Sample#{n:03d}.tdms' for m, n in zip(range(30), range(1, 6))]
                 + [f'A@@2026_0115_17{m:02d}_02_S_10k_Sample#{n:03d}.tdms' for m, n in zip(range(30), range(1, 4))]
                 + ['A@@2026_0115_1800_00_S_10k_Sample#001.tdms_index', 'note.txt'])
        runs = bnal_progress.anal_generations(names)
        self.assertEqual([(r['count'], r['first'], r['last'], r['start'][:9]) for r in runs],
                         [(5, 1, 5, '2023_0728'), (3, 1, 3, '2026_0115')])
        # A run resumed at #3 after #1-#4 is one run, not two.
        resumed = [f'A@@2022_0929_190{m}_00_S#{n:03d}.tdms' for m, n in ((0, 1), (1, 2), (2, 3), (3, 4))] + \
                  [f'A@@2022_0930_100{m}_00_S#{n:03d}.tdms' for m, n in ((0, 3), (1, 4), (2, 5))]
        self.assertEqual(len(bnal_progress.anal_generations(resumed)), 1)

    def test_old_run_files_companions_and_result_folders_move_to_old(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'analysis'
            anal = root / 'E' / 'S' / 'S_10k_Sample' / 'T' / 'ANAL'
            anal.mkdir(parents=True)
            old_stamps = ('2023_0728_0724_10', '2023_0728_0724_41')
            new_stamps = ('2026_0115_1707_02',)
            for stamp, n in list(zip(old_stamps, (1, 2))) + list(zip(new_stamps, (1,))):
                (anal / f'A@@{stamp}_S_10k_Sample#{n:03d}.tdms').write_text('x')
                (anal / f'A@@{stamp}_S_10k_Sample#{n:03d}.tdms_index').write_text('x')
                (anal / f'A@{stamp}@S_10k_Sample#{n:03d}').mkdir()
            (anal / 'stocked').mkdir()
            plan = bnal_progress.plan_old_generations(str(root), 'E', 'S')
            self.assertEqual([g['count'] for g in plan['generations']], [2, 1])
            self.assertEqual(len(plan['moves']), 6)
            self.assertTrue(all('2023_0728' in s.name for s, _ in plan['moves']))
            self.assertFalse((anal / 'old').exists())  # planning writes nothing
            moved, skipped = bnal_progress.restore(plan, Path(temp) / 'log.csv')
            self.assertEqual((moved, skipped), (6, []))
            self.assertEqual(sorted(p.name for p in anal.iterdir()),
                             sorted(['old', 'stocked', 'A@@2026_0115_1707_02_S_10k_Sample#001.tdms',
                                     'A@@2026_0115_1707_02_S_10k_Sample#001.tdms_index', 'A@2026_0115_1707_02@S_10k_Sample#001']))
            self.assertEqual(len(list((anal / 'old').iterdir())), 6)
            again = bnal_progress.plan_old_generations(str(root), 'E', 'S')
            self.assertEqual((len(again['generations']), again['moves']), (1, []))

    def test_name_already_in_old_is_a_conflict(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'analysis'
            anal = root / 'E' / 'S' / 'S_10k_Sample' / 'T' / 'ANAL'
            (anal / 'old').mkdir(parents=True)
            for name in ('A@@2023_0728_0724_10_S#001.tdms', 'A@@2026_0115_1707_02_S#001.tdms'):
                (anal / name).write_text('x')
            (anal / 'old' / 'A@@2023_0728_0724_10_S#001.tdms').write_text('earlier')
            plan = bnal_progress.plan_old_generations(str(root), 'E', 'S')
            self.assertEqual((plan['moves'], [s.name for s, _ in plan['conflicts']]), ([], ['A@@2023_0728_0724_10_S#001.tdms']))


class SavedDbTests(TestCase):
    def test_scan_then_load_saved_reads_without_server(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            self.assertEqual(bnal_progress.load_saved(db), ([], []))
            root = Path(temp) / 'analysis'
            target = root / 'BR' / 'BRA' / 'BRA_10k_Sample' / 'T'
            for relative in ('ANAL/1.tdms', 'ANAL/2.tdms', 'BNAL@A/1.tdms'):
                (target / relative).parent.mkdir(parents=True, exist_ok=True)
                (target / relative).write_bytes(b'')
            rackstation_view.scan_samples(dict(server='QTserver', root=str(root), kind='analysis'), db,
                                          threading.Event(), lambda *args: None, lambda experiment: True)
            samples, bnals = bnal_progress.load_saved(db)
            self.assertEqual(bnal_progress.sample_choices(samples), [('BR', 'BRA')])
            rows, _, total, _ = bnal_progress.build_progress([('BR', 'BRA')], ['A', 'B'], samples, bnals, {})
            self.assertEqual([(r['target'], r['progress']) for r in rows], [('A', '50.0%'), ('B', '0%')])
            self.assertEqual((total['done'], total['goal']), (0.5, 2))  # A 1/2 + B 0, in target units


class PlanTargetsTests(TestCase):
    """BNAL進捗 with each sample's own SEQlists from the sample ledger."""

    def test_samples_use_their_own_seqlists_and_unset_ones_are_reported(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'a.csv').write_text('t1,ACGU,4\nt2,ACGU,4\n', encoding='utf-8')
            (folder / 'b.csv').write_text('t3,ACGU,4\n', encoding='utf-8')
            samples = [dict(root=r'\qd\analysis', experiment='ex', sample=s, t_tdms=10, anal_tdms=10, bnal_folders=1)
                       for s in ('S1', 'S2', 'S3')]
            bnals = [dict(root=r'\qd\analysis', experiment='ex', sample='S1', target='t1', tdms=10, tdms_checked=None),
                     dict(root=r'\qd\analysis', experiment='ex', sample='S2', target='t3', tdms=4, tdms_checked=None)]
            plan = {'ex|S1': dict(seqlists=['a.csv']), 'ex|S2': dict(seqlists=['b.csv']), 'ex|S3': dict(bnal='target')}
            rows, shares, total, warnings, expected = bnal_progress.build_progress_by_plan(
                [('ex', 'S1'), ('ex', 'S2'), ('ex', 'S3')], plan, samples, bnals, {}, folder=folder)
        self.assertEqual(sorted({(r['sample'], r['target']) for r in rows}), [('S1', 't1'), ('S1', 't2'), ('S2', 't3')])
        self.assertEqual((total['expected'], total['started'], total['complete']), (3, 2, 1))
        self.assertEqual(expected, ['t1', 't2', 't3'])
        self.assertTrue(any('ex/S3' in w and 'SEQlist' in w for w in warnings))
