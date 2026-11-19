"""ChipID → 実験データの逆引き: 履歴の操作から、SQserver（Tmp_backup / SQ_backup、フォルダ / zip）の場所とファイルを出す。"""
from pathlib import Path
import math
import tempfile
import unittest
import zipfile

from chipdb import experiment_data as ed

NAME = '20221025_0949 TOAN#2Pex1n1_phi-x 174'
ROWS = [
    ['SC23-2021-28', 'Status', 'Date', 'Operator', 'EXID', 'Operation', '302'],
    ['Arrived', '○', '-', '-', '', '', ''],
    ['Experiment', '○', '20221025_0949', 'U', NAME, 'GPSET#1', '0'],
    ['Experiment', '○', '20221025_0949', 'U', NAME, 'FC#5', '3', '0.7'],
    ['Experiment', '○', '20221025_0949', 'U', NAME, 'MA#5', '12', '2.4'],
    ['Experiment', '○', '20221025_0949', 'U', NAME, 'FC#5', '3', '0.7'],
    ['Experiment', '○', '20221025_0949', 'U', NAME, 'AC#2', '20'],
    ['Experiment', '○', '20221107_1420', 'U', '20221107_1420 TOAN#2Pex1n1_x', 'CAL#2', '1', '130'],
]


class ExperimentDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.tmp, self.sq = root / 'backup_06', root / 'AN2'
        self.tmp.mkdir()
        self.sq.mkdir()
        self.machines = {'AXN2': {'Tmp_backup': str(self.tmp), 'SQ_backup': str(self.sq), 'PC Directroy': 'D:/MCBJ'}}

    def write_zip(self):
        with zipfile.ZipFile(self.sq / f'{NAME}.zip', 'w') as archive:
            archive.writestr('FC/', '')
            archive.writestr(f'FC/R_FC#5_20221025_0949_{NAME}.txt', 'Device Code,SC23-2021-28\n')
            archive.writestr('FC/PM_FC#5_20221025_0949_.txt', 'time,I\n0,1\n')
            archive.writestr(f'FC/R_FC#6_20221025_1012_{NAME}.txt', 'x,1\n')
            archive.writestr('MA/PM_MA#5_20221025_0953_.txt', 'time,I\n')
            archive.writestr('EXZSV/big.tdms', b'0' * 100)
            archive.writestr(f'GPSET/R_GPSET#1_20221025_0949_{NAME}.txt', 'Sample Name,phi-x\n')

    def test_history_rows_become_experiments_with_their_operations(self):
        experiments = ed.chip_experiments(ROWS)
        self.assertEqual([e.name for e in experiments], [NAME, '20221107_1420 TOAN#2Pex1n1_x'])
        self.assertEqual(experiments[0].machine, 'AXN2')
        self.assertEqual(ed.operation_text(experiments[0].operations), 'FC#5 MA#5 AC#2')   # GPSET・重複は除く

    def test_zip_on_sq_backup_is_found_and_only_the_chips_operations_are_listed(self):
        self.write_zip()
        experiment = ed.chip_experiments(ROWS)[0]
        found = ed.locate(experiment, self.machines, {})
        self.assertEqual((found.place, found.zipped, found.confirmed), ('SQ_backup', True, True))
        files = ed.stage_files(found, NAME, experiment.operations)
        self.assertEqual([(op, name) for op, name, _ in files],
                         [('GPSET', f'R_GPSET#1_20221025_0949_{NAME}.txt'),
                          ('FC#5', 'PM_FC#5_20221025_0949_.txt'), ('FC#5', f'R_FC#5_20221025_0949_{NAME}.txt'),
                          ('MA#5', 'PM_MA#5_20221025_0953_.txt'), ('AC#2', '（ファイルなし）')])
        self.assertIn(' :: FC/', files[1][2])
        files = [f for f in files if f[2]]
        target = ed.extract(found, NAME, Path(self.temporary.name) / 'out')
        self.assertEqual(sorted(p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()),
                         ['FC/PM_FC#5_20221025_0949_.txt', f'FC/R_FC#5_20221025_0949_{NAME}.txt',
                          f'FC/R_FC#6_20221025_1012_{NAME}.txt', f'GPSET/R_GPSET#1_20221025_0949_{NAME}.txt',
                          'MA/PM_MA#5_20221025_0953_.txt'])   # EXZSV は取り出さない
        saved, copied = ed.save_files(found, NAME, [(stage, name) for (op, name, _) in files for stage in [op.split('#')[0]]],
                                      Path(self.temporary.name) / 'downloads')
        self.assertEqual((saved.name, copied), (NAME, 4))
        self.assertEqual(sorted(p.name for p in saved.iterdir()),          # 実験IDのフォルダにそのまま並べる
                         sorted(name for _, name, _ in files))
        self.assertEqual(ed.save_files(found, NAME, [('FC', 'PM_FC#5_20221025_0949_.txt')],
                                       Path(self.temporary.name) / 'downloads')[1], 0)   # 同じものは写し直さない

    def test_reruns_are_told_apart_and_server_folders_are_refused(self):
        self.assertEqual(ed.run_of('PM_AC#1_20261015_1205_.txt'), 'AC#1_20261015_1205')
        self.assertEqual(ed.run_of('R_AC#1_20261015_1205_20261015_1155 MSAN#3Pex1n1_LyspH7.txt'), 'AC#1_20261015_1205')
        self.assertTrue(ed.is_server_path('//SQserver/backup_01'))
        self.assertTrue(ed.is_server_path('\\\\SQserver\\data\\AN8'))
        self.assertFalse(ed.is_server_path(self.temporary.name))
        with self.assertRaises(ValueError):
            ed.save_files(None, NAME, [], '//SQserver/data')

    def test_data_stocked_next_to_data_is_searched_last(self):
        data = Path(self.temporary.name) / 'data' / 'AN8'
        stocked = Path(self.temporary.name) / 'data_stocked' / 'AN8'
        data.mkdir(parents=True)
        stocked.mkdir(parents=True)
        name = '20210826_1429 UNAN#8Pex1n1_MilliQ'
        (stocked / f'{name}.zip').write_bytes(b'')
        machines = {'AXN8': {'Tmp_backup': str(self.tmp), 'SQ_backup': str(data)}}
        experiment = ed.ChipExperiment(name, 'AXN8', [('FC', 1)])
        found = ed.locate(experiment, machines, {})
        self.assertEqual((found.place, found.zipped, found.path.name), ("data_stocked", True, f"{name}.zip"))

    def test_data_stocked_path_on_sqserver(self):
        stocked = ed.stocked_candidates('X', {'SQ_backup': '\\\\SQserver\\data\\AN8'})
        self.assertEqual([str(c.path) for c in stocked],
                         ['\\\\SQserver\\data_stocked\\AN8\\X', '\\\\SQserver\\data_stocked\\AN8\\X.zip'])
        self.assertEqual(ed.stocked_candidates('X', {'SQ_backup': ''}), [])

    def test_folder_on_tmp_backup_comes_first(self):
        self.write_zip()
        (self.tmp / NAME / 'FC').mkdir(parents=True)
        (self.tmp / NAME / 'FC' / 'PM_FC#5_x_.txt').write_text('t\n')
        found = ed.locate(ed.chip_experiments(ROWS)[0], self.machines, {})
        self.assertEqual((found.place, found.zipped), ('Tmp_backup', False))
        target = ed.extract(found, NAME, Path(self.temporary.name) / 'out')
        self.assertTrue((target / 'FC' / 'PM_FC#5_x_.txt').is_file())

    def test_unreachable_server_falls_back_to_the_upload_inventory(self):
        machines = {'AXN2': {'Tmp_backup': str(Path(self.temporary.name) / 'gone' / 'b'),
                             'SQ_backup': str(Path(self.temporary.name) / 'gone' / 'a')}}
        inventories = {'AXN2': {NAME: 'SQ_backup', '20221107_1420 TOAN#2Pex1n1_y': 'SQ_backup'}}
        experiments = ed.chip_experiments(ROWS)
        found = ed.locate(experiments[0], machines, inventories)
        self.assertEqual((found.place, found.zipped, found.confirmed), ('SQ_backup', None, False))
        self.assertIsNone(ed.locate(experiments[1], machines, inventories))
        self.assertEqual(ed.renamed_candidates(experiments[1], inventories), ['20221107_1420 TOAN#2Pex1n1_y'])

    def test_reachable_server_without_the_experiment_is_not_found(self):
        self.assertIsNone(ed.locate(ed.chip_experiments(ROWS)[0], self.machines, {'AXN2': {NAME: 'SQ_backup'}}))


class LogTests(unittest.TestCase):
    def test_pm_log_and_result_are_read_from_the_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f'{NAME}.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('MA/PM_MA#1_x_.txt', 'Time [s],K Current (uA),Piezo (V)\n0.00,9.9,80\n0.05,abc,81\n0.10\n')
                archive.writestr('MA/R_MA#1_x.txt', 'MA#,1\nM Auto L SHW [um],6.5120\n')
            found = ed.Found('SQ_backup', path, True, True)
            columns = ed.read_log(found, NAME, 'MA', 'PM_MA#1_x_.txt')
            self.assertEqual(columns['Time [s]'], [0.0, 0.05])            # 欠けた行は飛ばす
            self.assertTrue(math.isnan(columns['K Current (uA)'][1]))
            self.assertEqual(ed.read_result(found, NAME, 'MA', 'R_MA#1_x.txt')['M Auto L SHW [um]'], '6.5120')

    def test_downsample_keeps_order_and_extremes(self):
        from chipdb.log_chart import decade_ticks, downsample
        pairs = [(i * .05, (i % 7) - 3.0) for i in range(10000)]
        small = downsample(pairs, 100)
        self.assertLessEqual(len(small), 200)
        self.assertEqual([p[0] for p in small], sorted(p[0] for p in small))
        self.assertEqual((min(p[1] for p in small), max(p[1] for p in small)), (-3.0, 3.0))
        self.assertEqual(downsample(pairs[:10], 100), pairs[:10])
        self.assertEqual(decade_ticks(1e-5, 9.9), (-5, 1, [-5, -4, -3, -2, -1, 0, 1]))


if __name__ == '__main__':
    unittest.main()
