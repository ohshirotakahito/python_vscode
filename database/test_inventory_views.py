from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from database import catalog, inventory_views as views


class DomainInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'share'
        self.root.mkdir()
        self.db = Path(self.temp.name) / 'inventory.sqlite3'
        catalog.initialize(self.db)

    def folder(self, relative):
        path = self.root / relative
        path.mkdir(parents=True, exist_ok=True)
        return path

    def file(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'test')
        return path

    def scan(self, kind='analysis', root=None):
        catalog.scan_source(dict(server='test', root=str(root or self.root), kind=kind), self.db)

    def test_analysis_separates_direct_tdms_stocked_and_bnals(self):
        base = 'Experiment/S/S_10k_Sample'
        for suffix in ('T/a.TDMS', 'T/note.txt', 'T/stocked/b.tdms', 'T/stocked/note.txt',
                       'stocked/c.tdms', 'T/ANAL/d.tdms', 'T/ANAL/stocked/e.tdms',
                       'T/BNAL@one/f.tdms', 'T/BNAL@one/nested/not-direct.tdms',
                       'T/BNAL@two/g.tdms', 'T/BNAL@two/note.txt'):
            self.file(base + '/' + suffix)
        self.folder(base + '/T/BNAL@empty')
        self.folder('Experiment/S/raw data')
        self.scan()
        rows = views.analysis_inventory(self.db)
        self.assertEqual(len(rows), 4)
        row = next(r for r in rows if r['folder_state'] == '存在')
        for key, expected in dict(t_tdms=1, sibling_stocked=1, t_stocked=2,
                                  anal_tdms=1, anal_stocked=1, bnal_folders=3, bnal_tdms=2).items():
            self.assertEqual(row[key], expected, key)
        self.assertIn('BNAL@empty: 0', row['bnal_details'])
        self.assertEqual(row['raw_folders'], 'Experiment/S/raw data')
        self.assertIsNone(next(r for r in rows if r['specimen'] == 'Blank')['t_tdms'])

    def test_empty_folder_and_missing_folder_are_different(self):
        self.folder('E/S/S_100k_Blank/T/ANAL/stocked')
        self.scan()
        row = next(r for r in views.analysis_inventory(self.db) if r['folder_state'] == '存在')
        self.assertEqual(row['t_tdms'], 0)
        self.assertEqual(row['anal_stocked'], 0)
        self.assertIsNone(row['t_stocked'])
        self.assertEqual(row['bnal_folders'], 0)
        self.assertEqual(views.analysis_inventory(self.db, search='not present'), [])

    def test_sq_three_groups_and_counts(self):
        experiment = '20260930_1234 TOAN#1Pex3n0_test_name'
        recent = 'backup_AN1/' + experiment
        self.file(recent + '/EXSV/T/a.tdms')
        self.file(recent + '/EXSV/T/sub/ignored.tdms')
        self.file(recent + '/EXSV/b.tdms')
        self.file(recent + '/EXSV/note.txt')
        self.folder('backup_AN1/20260930_1400 TOAN#1Pex2n1_')
        self.folder('data/AN1/' + experiment)
        self.file('data/AN1/' + experiment + '.zip')
        self.file('data/AN1/ignore.txt')
        self.folder('data/AN2')
        self.file('data_stocked/AN1/' + experiment + '.tar.gz')
        self.scan('experiment')
        summaries, entries = views.sq_inventory(self.db)
        self.assertEqual(len(summaries), 4)
        recent_summary = next(r for r in summaries if r['group'] == 'Recent data')
        self.assertEqual(recent_summary['folder_count'], 2)
        recent_row = next(r for r in entries if r['relative_path'] == recent)
        self.assertEqual(recent_row['exsv_t_tdms'], 1)
        self.assertEqual(recent_row['exsv_tdms'], 1)
        self.assertEqual(recent_row['experiment_name'], 'test_name')
        unnamed = next(r for r in entries if r['experiment_name'] == '無名')
        self.assertIsNone(unnamed['exsv_tdms'])
        data = next(r for r in summaries if r['container'] == 'data/AN1')
        self.assertEqual((data['folder_count'], data['archive_count']), (1, 1))
        empty = next(r for r in summaries if r['container'] == 'data/AN2')
        self.assertEqual((empty['folder_count'], empty['archive_count']), (0, 0))
        self.assertEqual(len(views.sq_inventory(self.db, 'test_name')[1]), 4)

    def test_direct_group_share_and_explicit_kind(self):
        root = self.folder('data_stocked')
        self.file('data_stocked/AN1/unrecognized.zip')
        self.scan('experiment', root)
        groups, entries = views.sq_inventory(self.db)
        self.assertEqual(groups[0]['group'], 'data_stocked')
        self.assertEqual(entries[0]['parse_status'], '形式不明')
        other = self.folder('unusual_share')
        self.file('unusual_share/a/EXSV/T/file.tdms')
        self.scan('sq_recent', other)
        groups, entries = views.sq_inventory(self.db)
        self.assertEqual(next(r for r in entries if r['group'] == 'Recent data')['exsv_t_tdms'], 1)

    def test_recent_container_with_backup_subfolders(self):
        self.folder('Recent data/backup_one/exp/EXSV/T')
        self.folder('Recent data/backup_two')
        self.scan('experiment')
        groups, entries = views.sq_inventory(self.db)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['name'], 'exp')
        self.assertEqual(entries[0]['exsv_t_tdms'], 0)

    def test_names_fullwidth_unnamed_dates_and_archive_extensions(self):
        row = views.parse_experiment_name('20260930_123456 TOAN＃１Pex3n0_測定_A.tar.gz', archive=True)
        self.assertEqual(row['machine'], 'AN#1')
        self.assertEqual(row['operator'], 'TO')
        self.assertEqual((row['experiment_number'], row['repeat_number']), (3, 0))
        self.assertEqual(row['experiment_at'], '2026-09-30 12:34:56')
        self.assertEqual(row['experiment_name'], '測定_A')
        self.assertEqual(views.parse_experiment_name('20260930_1234 TOAN#1Pex3n0_')['experiment_name'], '無名')
        self.assertEqual(views.parse_experiment_name('20261340_9999 TOAN#1Pex3n0_x')['parse_status'], '日時不明')
        self.assertEqual(views.parse_experiment_name('random')['experiment_name'], '形式不明')

    def test_offline_snapshot_includes_new_views(self):
        self.folder('E/S/S_10k_Sample/T')
        self.scan()
        target = Path(self.temp.name) / 'offline.sqlite3'
        catalog.export_snapshot(target, self.db)
        self.assertEqual(views.analysis_inventory(target), views.analysis_inventory(self.db))


if __name__ == '__main__':
    unittest.main()
