from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from contextlib import closing
import sqlite3
import threading
from database import anal_progress, catalog, rackstation_view

QT, QD = r'\\QTserver\backup', r'\\QDserver\analysis'
LABELS = {catalog.root_identity(QT): 'QTserver/backup', catalog.root_identity(QD): 'QDserver/analysis'}


def row(root, t, anal, experiment='BR', name='BRA', **extra):
    values = dict(root=root, experiment=experiment, sample=name, t_tdms=t, anal_tdms=anal, anal_missing_checked=1)
    values.update(extra)
    return values


class ResumePointScanTests(TestCase):
    def test_scan_records_resume_point_gaps_and_largest_number_from_all_four_listings(self):
        with TemporaryDirectory() as temp:
            t = Path(temp) / 'T'
            (t / 'stocked').mkdir(parents=True)
            (t / 'ANAL' / 'stocked').mkdir(parents=True)
            for n in range(1, 9):
                folder = t / 'stocked' if n <= 2 else t
                (folder / f'2023_0728_0724_10_BRA_10k_Sample#{n:03d}.tdms').write_bytes(b'')
            for n in (1, 2, 5):
                (t / 'ANAL' / f'A@@2023_0728_0724_10_BRA_10k_Sample#{n:03d}.tdms').write_bytes(b'')
            (t / 'ANAL' / 'stocked' / 'A@@2023_0728_0724_10_BRA_10k_Sample#006.tdms').write_bytes(b'')
            counts = rackstation_view.sample_counts(t, threading.Event())
        self.assertEqual((counts['t_tdms'], counts['t_stocked_tdms'], counts['anal_tdms'], counts['anal_stocked_tdms']),
                         (6, 2, 3, 1))
        self.assertEqual((counts['anal_resume'], counts['anal_missing'], counts['anal_last']), (3, '#003-#004,#007-#008', 6))
        self.assertEqual(anal_progress.resume_point(counts), (7, [3, 4]))

    def test_complete_and_unstarted_samples(self):
        with TemporaryDirectory() as temp:
            t = Path(temp) / 'T'
            t.mkdir()
            for n in (1, 2):
                (t / f'x#{n:03d}.tdms').write_bytes(b'')
            counts = rackstation_view.sample_counts(t, threading.Event())
            self.assertEqual((counts['anal_tdms'], counts['anal_resume'], counts['anal_last']), (None, 1, None))
            self.assertEqual(anal_progress.resume_point(counts), (1, []))
            (t / 'ANAL').mkdir()
            for n in (1, 2):
                (t / 'ANAL' / f'A@@x#{n:03d}.tdms').write_bytes(b'')
            counts = rackstation_view.sample_counts(t, threading.Event())
            self.assertEqual((counts['anal_resume'], counts['anal_missing'], counts['anal_missing_checked']), (None, None, 1))

    def test_ranges_round_trip(self):
        numbers = {1, 2, 3, 7, 9, 10, 124}
        text = rackstation_view.number_ranges(numbers)
        self.assertEqual(text, '#001-#003,#007,#009-#010,#124')
        self.assertEqual(anal_progress.parse_ranges(text), numbers)
        self.assertEqual(anal_progress.parse_ranges(None), set())

    def test_rows_scanned_before_the_columns_existed_have_unknown_resume(self):
        self.assertEqual(anal_progress.resume_point(dict(anal_missing=None)), (None, None))

    def test_old_db_gets_new_columns_and_refresh_fills_them(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            with closing(sqlite3.connect(db)) as con, con:
                con.execute('CREATE TABLE rack_samples (root TEXT NOT NULL, experiment TEXT NOT NULL, sample TEXT NOT NULL,'
                            ' t_tdms INTEGER, anal_tdms INTEGER, bnal_folders INTEGER, last_success TEXT, status TEXT,'
                            ' PRIMARY KEY(root,experiment,sample))')
                con.execute("INSERT INTO rack_samples(root,experiment,sample,t_tdms) VALUES(?,?,?,1)", (temp, 'BR', 'BRA'))
            t = Path(temp) / 'BR' / 'BRA' / 'BRA_10k_Sample' / 'T'
            t.mkdir(parents=True)
            (t / 'x#001.tdms').write_bytes(b'')
            from database import bnal_progress
            bnal_progress.refresh_sample_counts(db, temp, 'BR', 'BRA')
            with closing(sqlite3.connect(db)) as con, con:
                self.assertEqual(con.execute('SELECT anal_resume, anal_missing, anal_missing_checked FROM rack_samples').fetchone(),
                                 (1, '#001', 1))


class BuildProgressTests(TestCase):
    def test_share_without_anal_is_unstarted_and_best_share_decides_the_sample(self):
        entries, shares, total = anal_progress.build_progress(
            [row(QT, 10, None, anal_resume=1, anal_missing='#001-#010'),
             row(QD, 10, 4, anal_resume=5, anal_missing='#005-#010', anal_last=4)], LABELS)
        entry, = entries
        self.assertEqual(entry['cells']['QTserver/backup']['status'], 'unstarted')
        self.assertEqual(entry['cells']['QTserver/backup']['state'], '未着手（ANALなし）')
        self.assertEqual((entry['best']['group'], entry['status'], entry['remaining'], entry['resume']),
                         ('QDserver/analysis', 'partial', 6, 5))
        self.assertEqual(entry['flags'], ())
        self.assertEqual({s['group']: (s['unstarted'], s['partial']) for s in shares},
                         {'QDserver/analysis': (0, 1), 'QTserver/backup': (1, 0)})
        self.assertEqual((total['samples'], total['partial'], total['done'], total['goal']), (1, 1, 4, 10))

    def test_stocked_files_count_on_both_sides(self):
        entry, = anal_progress.build_progress([row(QT, 0, 90, t_stocked_tdms=137, anal_stocked_tdms=47)], LABELS)[0]
        self.assertEqual((entry['status'], entry['remaining'], entry['flags']), ('complete', 0, ('退避',)))

    def test_flags_for_gaps_mixed_generations_differing_originals_and_stale_rows(self):
        entry, = anal_progress.build_progress(
            [row(QT, 137, 90, anal_distinct=80, anal_resume=78, anal_missing='#078-#124', anal_last=137),
             row(QD, 130, 10, anal_missing_checked=None)], LABELS)[0]
        self.assertEqual(entry['best']['group'], 'QTserver/backup')
        self.assertEqual(entry['gaps'], list(range(78, 125)))
        self.assertIsNone(entry['resume'])
        self.assertEqual(anal_progress.resume_text(entry['best']), '欠番のみ')
        self.assertEqual(entry['flags'], ('差異', '欠番', '混在'))
        warnings = anal_progress.warnings_for([entry])
        self.assertTrue(any('#078-#124' in line for line in warnings))
        stale, = anal_progress.build_progress([row(QD, 130, 10, anal_missing_checked=None)], LABELS)[0]
        self.assertEqual((stale['flags'], anal_progress.resume_text(stale['best'])), (('要更新',), '要更新'))

    def test_excess_and_unknown(self):
        entries = {e['sample']: e for e in anal_progress.build_progress(
            [row(QT, 5, 6, name='A'), row(QT, None, None, name='B'), row(QT, 0, 0, name='C'), row(QT, 0, 3, name='D')],
            LABELS)[0]}
        self.assertEqual({k: e['status'] for k, e in entries.items()},
                         dict(A='excess', B='unknown', C='unknown', D='copy'))

    def test_anal_copied_without_t_is_an_analysis_copy_not_the_best_share(self):
        rack = r'\\Rackstation\analysis'
        labels = dict(LABELS, **{catalog.root_identity(rack): 'Rackstation/analysis'})
        entries, shares, total = anal_progress.build_progress(
            [row(rack, 425, 115, anal_resume=116, anal_missing='#116-#425', anal_last=115),
             row(QT, 0, 115), row(QD, 0, 90)], labels)
        entry, = entries
        self.assertEqual((entry['best']['group'], entry['status'], entry['remaining'], entry['resume']),
                         ('Rackstation/analysis', 'partial', 310, 116))
        copies = {g: c for g, c in entry['cells'].items() if c['status'] == 'copy'}
        self.assertEqual({g: c['coverage'] for g, c in copies.items()},
                         {'QTserver/backup': 1.0, 'QDserver/analysis': 90 / 115})
        self.assertEqual(anal_progress.progress_text(copies['QTserver/backup']), 'コピー（元ANALの100.0%）')
        self.assertEqual(anal_progress.resume_text(copies['QTserver/backup']), '—')
        self.assertEqual(entry['flags'], ('コピー不足',))
        self.assertEqual((total['samples'], total['partial'], total['copy'], total['done'], total['goal']), (1, 1, 0, 115, 425))
        self.assertEqual({s['group']: s['copy'] for s in shares},
                         {'QDserver/analysis': 1, 'QTserver/backup': 1, 'Rackstation/analysis': 0})

    def test_work_order_filter_and_group_order(self):
        samples = [row(QT, 10, 10, name='done'), row(QT, 10, 0, name='fresh'), row(QT, 10, 8, name='near'),
                   row(QT, 20, 10, name='big'), row(QD, 3, 0, experiment='CC', name='other')]
        entries = anal_progress.build_progress(samples, LABELS)[0]
        self.assertEqual([e['sample'] for e in anal_progress.sort_entries(entries, 'work')],
                         ['big', 'fresh', 'other', 'near', 'done'])
        self.assertEqual([e['sample'] for e in anal_progress.sort_entries(entries)],
                         ['big', 'done', 'fresh', 'near', 'other'])
        self.assertEqual({e['sample'] for e in anal_progress.filter_entries(entries, 'br/', True)}, {'big', 'fresh', 'near'})
        self.assertEqual(anal_progress.group_order(samples + [row(r'\\X\y', 1, 1)], LABELS),
                         ['QTserver/backup', 'QDserver/analysis', r'\\X\y'])
