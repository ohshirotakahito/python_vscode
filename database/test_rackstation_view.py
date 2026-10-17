from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import os
import threading
from database import catalog, rackstation_view


class RackstationTests(TestCase):
    def test_saved_unc_share_matches_with_or_without_trailing_separator(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            empty = Path(temp) / 'empty'
            empty.mkdir()
            rackstation_view.scan_samples(dict(server='Rackstation', root=str(empty), kind='analysis'),
                                          db, threading.Event(), lambda *args: None)
            saved_root = '\\\\rackstation\\analysis\\'
            with catalog.connect(db) as connection:
                connection.execute('INSERT INTO rack_samples(root,experiment,sample,t_tdms,anal_tdms,bnal_folders,last_success,status) VALUES(?,?,?,?,?,?,?,?)',
                                   (saved_root, 'Kumamoto_N2', 'S', 12, 12, 2,
                                    '2026-10-16T03:34:21+00:00', '完了'))
            for query in (saved_root, saved_root.rstrip('\\')):
                with self.subTest(root=query):
                    rows = rackstation_view.inventory(db, query)
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]['experiment'], 'Kumamoto_N2')

    def test_experiment_search_is_partial_case_insensitive_and_clearable(self):
        rows = [dict(experiment='Kumamoto_N2', sample='A'),
                dict(experiment='Kumamoto_N2', sample='B'),
                dict(experiment='Other', sample='Kumamoto')]
        self.assertEqual(rackstation_view.filter_experiments(rows, ' KUMAMOTO '), rows[:2])
        self.assertEqual(rackstation_view.filter_experiments(rows, ''), rows)
        self.assertEqual(rackstation_view.filter_experiments(rows, 'missing'), [])

    def test_saved_samples_load_without_server_access_and_keep_acquisition_date(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            root = Path(temp) / 'analysis'
            target = root / 'Experiment' / 'S' / 'S_10k_Sample' / 'T'
            target.mkdir(parents=True)
            (target / 'a.tdms').write_bytes(b'')
            rackstation_view.scan_samples(dict(server='Rackstation', root=str(root), kind='analysis'),
                                          db, threading.Event(), lambda *args: None)
            with catalog.connect(db, readonly=True) as connection:
                acquired = connection.execute('SELECT last_success FROM rack_samples').fetchone()[0]
            with patch.object(rackstation_view.os, 'scandir', side_effect=AssertionError('server accessed')):
                rows = rackstation_view.inventory(db, str(root))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['last_success'], acquired)

    def test_new_table_for_other_root_does_not_hide_legacy_saved_inventory(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            root = Path(temp) / 'analysis'
            target = root / 'Experiment' / 'S' / 'S_10k_Sample' / 'T'
            target.mkdir(parents=True)
            (target / 'a.tdms').write_bytes(b'')
            catalog.scan_source(dict(server='Rackstation', root=str(root), kind='analysis'), db)
            other = Path(temp) / 'other'
            other.mkdir()
            rackstation_view.scan_samples(dict(server='QTserver', root=str(other), kind='analysis'),
                                          db, threading.Event(), lambda *args: None)
            rows = rackstation_view.inventory(db, str(root))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['experiment'], 'Experiment')
            self.assertEqual(rows[0]['t_tdms'], 1)

    def test_each_analysis_root_keeps_its_own_results(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            for index, (server, share) in enumerate((('Rackstation', 'analysis'), ('Rackstation', 'archive'),
                                                    ('QTserver', 'analysis'), ('QDserver', 'analysis')), 1):
                root = Path(temp) / server / share
                folder = root / 'Experiment' / 'S' / 'S_10k_Sample' / 'T'
                folder.mkdir(parents=True)
                for number in range(index):
                    (folder / f'{number}.tdms').write_bytes(b'')
                rackstation_view.scan_samples(dict(server=server, root=str(root), kind='analysis'),
                                              db, threading.Event(), lambda *args: None)
            for index, (server, share) in enumerate((('Rackstation', 'analysis'), ('Rackstation', 'archive'),
                                                    ('QTserver', 'analysis'), ('QDserver', 'analysis')), 1):
                rows = rackstation_view.inventory(db, str(Path(temp) / server / share))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['t_tdms'], index)

    def test_analysis_progress_and_colors(self):
        for total, analysed, expected, tag in (
            (10, 0, '0.0%', 'unstarted'), (10, 5, '50.0%', 'partial'),
            (10, 10, '100.0%', 'complete'), (10, 20, '200.0%', 'excess'),
            (0, 0, '対象なし', 'unknown'), (0, 2, '元ファイル0件・要確認', 'excess'),
            (10, None, '0%（ANAL未作成）', 'unstarted'),
        ):
            with self.subTest(total=total, analysed=analysed):
                self.assertEqual(rackstation_view.analysis_progress(dict(t_tdms=total, anal_tdms=analysed)), (expected, tag))

    def test_fast_counts_never_enter_bnal_stocked_or_nested_results(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / 'T'
            for relative in ('a.TDMS', 'ANAL/b.tdms', 'ANAL/nested/ignored.tdms',
                             'BNAL@one/deep/ignored.tdms', 'stocked/deep/ignored.tdms'):
                file = target / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'')
            scanned = []
            original = os.scandir

            def record(path):
                scanned.append(Path(path))
                return original(path)

            with patch.object(rackstation_view.os, 'scandir', side_effect=record):
                counts = rackstation_view.sample_counts(target, threading.Event())
            self.assertEqual(counts[:3], (1, 1, 1))
            self.assertEqual(scanned, [target, target / 'ANAL'])

    def test_samples_are_published_and_committed_before_next_sample(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'analysis'
            for name in ('A', 'B'):
                folder = root / 'Experiment' / name / (name + '_10k_Sample') / 'T'
                folder.mkdir(parents=True)
                (folder / 'data.tdms').write_bytes(b'data')
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            cancel = threading.Event()
            samples = []

            def emit(event, row):
                if event == 'sample':
                    samples.append(row)
                    with catalog.connect(db, readonly=True) as connection:
                        count = connection.execute('SELECT COUNT(*) FROM rack_samples').fetchone()[0]
                    self.assertEqual(count, len(samples))

            count = rackstation_view.scan_samples(dict(server='Rackstation', root=str(root), kind='analysis'), db, cancel, emit)
            self.assertEqual(count, 2)
            self.assertEqual([r['sample'] for r in samples], ['A', 'B'])
            self.assertEqual(samples[0]['t_tdms'], 1)

    def test_only_expected_structure_and_direct_counts(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'analysis'
            for relative in ('E/S/S_10k_Sample/T/a.tdms',
                             'E/S/S_10k_Sample/T/ANAL/result.tdms',
                             'E/S/S_10k_Sample/T/ANAL/nested/ignored.tdms',
                             'E/S/S_10k_Sample/T/BNAL@one/data.tdms',
                             'E/S/S_100k_Sample/T/ignored.tdms',
                             'odd/folder/unrelated.txt',
                             'E/NoT/NoT_10k_Sample/note.txt'):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('test')
            (root / 'E/S/S_10k_Sample/T/BNAL@empty').mkdir()
            db = Path(temp) / 'test.sqlite3'
            catalog.initialize(db)
            catalog.scan_source(dict(server='Rackstation', root=str(root), kind='analysis'), db)
            catalog.scan_source(dict(server='QTserver', root=str(root.parent), kind='analysis'), db)
            rows = rackstation_view.inventory(db)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual((row['experiment'], row['sample']), ('E', 'S'))
            self.assertEqual((row['t_tdms'], row['anal_tdms'], row['bnal_folders']), (1, 1, 2))

    def test_combined_table_keeps_every_group_row_with_group_label(self):
        rows = {'Rackstation/analysis': [dict(experiment='B', sample='S'), dict(experiment='A', sample='S')],
                'QTserver/analysis': [dict(experiment='A', sample='S')]}
        combined = rackstation_view.combine_groups(rows)
        self.assertEqual([(r['experiment'], r['group']) for r in combined],
                         [('A', 'Rackstation/analysis'), ('A', 'QTserver/analysis'), ('B', 'Rackstation/analysis')])
        self.assertNotIn('group', rows['QTserver/analysis'][0])

    def test_registered_groups_and_default_roots(self):
        labels = [rackstation_view.group_label(*g) for g in rackstation_view.ANALYSIS_GROUPS]
        self.assertEqual(labels, ['Rackstation/analysis', 'Rackstation/archive', 'QTserver/analysis', 'QTserver/backup',
                                  'QDserver/analysis', 'QDserver/backup', 'QKserver/analysis'])
        configured = [dict(server='QTserver', root=r'\\qt\Analysis' + '\\', kind='analysis')]
        self.assertEqual(rackstation_view.default_root(configured, 'QTserver', 'analysis'), r'\\qt\Analysis' + '\\')
        self.assertEqual(rackstation_view.default_root(configured, 'Rackstation', 'archive'), r'\\Rackstation\archive')

    def test_scan_saves_tdms_sizes_and_share_state(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            root = Path(temp) / 'analysis'
            target = root / 'E' / 'S' / 'S_10k_Sample' / 'T'
            (target / 'ANAL').mkdir(parents=True)
            (target / 'a.tdms').write_bytes(b'x' * 10)
            (target / 'ANAL' / 'b.tdms').write_bytes(b'x' * 5)
            source = dict(server='QTserver', root=str(root), kind='analysis')
            rackstation_view.scan_group(source, db, threading.Event(), lambda *args: None)
            rows = rackstation_view.inventory(db, str(root))
            self.assertEqual((rows[0]['t_bytes'], rows[0]['anal_bytes']), (10, 5))
            state = rackstation_view.root_states(db)[catalog.root_identity(root)]
            self.assertEqual(state['status'], '取得済み')
            self.assertGreater(state['volume_total'], 0)
            summary = rackstation_view.capacity_row('QTserver/analysis', str(root), rows, state)
            self.assertEqual((summary['samples'], summary['tdms_bytes']), (1, 15))

    def test_failed_scan_keeps_previous_success_and_reports_error(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            root = Path(temp) / 'analysis'
            root.mkdir()
            source = dict(server='QDserver', root=str(root), kind='analysis')
            rackstation_view.scan_group(source, db, threading.Event(), lambda *args: None)
            succeeded = rackstation_view.root_states(db)[catalog.root_identity(root)]['last_success']
            with patch.object(rackstation_view, 'scan_samples', side_effect=OSError('network down')):
                with self.assertRaises(OSError):
                    rackstation_view.scan_group(source, db, threading.Event(), lambda *args: None)
            state = rackstation_view.root_states(db)[catalog.root_identity(root)]
            self.assertEqual((state['status'], state['error'], state['last_success']), ('失敗', 'network down', succeeded))

    def test_old_table_without_sizes_is_migrated_and_shown_as_not_acquired(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inventory.sqlite3'
            catalog.initialize(db)
            with catalog.connect(db) as connection:
                connection.execute("""CREATE TABLE rack_samples (
                    root TEXT NOT NULL, experiment TEXT NOT NULL, sample TEXT NOT NULL,
                    t_tdms INTEGER, anal_tdms INTEGER, bnal_folders INTEGER,
                    last_success TEXT, status TEXT, PRIMARY KEY(root,experiment,sample))""")
                connection.execute('INSERT INTO rack_samples VALUES(?,?,?,?,?,?,?,?)',
                                   ('r', 'E', 'S', 1, 0, 0, '2026-10-16T00:00:00+00:00', '完了'))
                rackstation_view.ensure_tables(connection)
                row = dict(connection.execute('SELECT * FROM rack_samples').fetchone())
            self.assertIsNone(row['t_bytes'])
            summary = rackstation_view.capacity_row('g', 'r', [row])
            self.assertIsNone(summary['tdms_bytes'])
            total = rackstation_view.capacity_total([summary, dict(summary, tdms_bytes=7, sized_samples=1)])
            self.assertEqual((total['samples'], total['tdms_bytes']), (2, 7))
