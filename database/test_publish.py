from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import unittest
from unittest.mock import patch
import csv
import json

from database import publish
from database.scan_lock import ScanLock
from shared import catalog


def setUpModule():
    # unittest discover must also keep automatic outbox retries away from production storage.
    from shared import destination_history as dh
    temporary = TemporaryDirectory()
    unittest.addModuleCleanup(temporary.cleanup)
    base = Path(temporary.name)
    for obj, name, value in ((dh, 'SHARED_MOVES', base / 'shared'),
                             (dh.file_moves, 'LOG_ROOT', base / 'logs')):
        patcher = patch.object(obj, name, value)
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


def read(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.reader(stream))


class ScanLockTests(TestCase):
    def test_only_one_holder_and_released_lock_can_be_taken_again(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'logs' / 'scan.lock'
            first, second = ScanLock('A', path), ScanLock('B', path)
            self.assertTrue(first.acquire())
            self.assertTrue(first.acquire())  # re-entrant for the same object
            self.assertFalse(second.acquire())
            self.assertIn('A', second.holder())
            first.release()
            self.assertTrue(second.acquire())
            second.release()


class SettingsTests(TestCase):
    def test_missing_file_gives_disabled_defaults_and_unknown_keys_are_dropped(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'publish.json'
            self.assertEqual(publish.load_settings(path), publish.DEFAULT_SETTINGS)
            self.assertFalse(publish.load_settings(path)['enabled'])
            path.write_text(json.dumps(dict(enabled=True, other=1)), encoding='utf-8')
            settings = publish.load_settings(path)
            self.assertTrue(settings['enabled'])
            self.assertNotIn('other', settings)
            settings['nightly_hour'] = None
            publish.save_settings(settings, path)
            self.assertIsNone(publish.load_settings(path)['nightly_hour'])

    def test_broken_file_falls_back_to_defaults(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'publish.json'
            path.write_text('{', encoding='utf-8')
            self.assertEqual(publish.load_settings(path), publish.DEFAULT_SETTINGS)


class ExportTests(TestCase):
    def test_transfer_export_uses_another_pcs_shared_journal_and_publishes_current_destination(self):
        from unittest.mock import Mock
        from shared import destination_history as dh
        from shared.ex_info_links import Transfer
        from database import transfer_ledger as tl
        with TemporaryDirectory() as temp:
            base = Path(temp)
            old, current = r'\\QTserver\analysis\Exp\S', r'\\QTserver\analysis\Exp\New'
            journal = base / 'file_logs'
            journal.mkdir()
            (journal / 'rename.json').write_text(json.dumps(dict(version=1,
                created_at='2026-11-13T01:00:00+00:00', steps=[dict(source=old, target=current,
                identity=[1, 2, None, None], state='done', moved_at='2026-11-13T01:00:00+00:00')]
            )), encoding='utf-8')
            shared_moves = base / 'shared_moves'
            self.assertTrue(dh.share_journal(journal / 'rename.json',
                json.loads((journal / 'rename.json').read_text(encoding='utf-8')), shared_moves))
            record = Transfer('AXN2', 'E1', 'Recent_Data', r'\\QTserver\analysis', 'Exp', 'S', 'Sample',
                              planned=True, transferred_at='2026-11-13T00:00:00+00:00')
            app = Mock()
            app.App.read_inventory.return_value = (None,) * 4 + ([dict(group='Recent_Data',
                container='backup_06', name='E1')],)
            history = [('2026/11/13_09:00', 'UN', 'AXN2', 'Recent_Data', '', 'UN', 'E1', 'Exp', 'S') + ('',) * 8]
            groups = {'QTserver/analysis': [dict(experiment='Exp', sample='New', t_tdms=10,
                      anal_tdms=4, anal_missing_checked=1)]}
            with patch.object(publish, '_app', return_value=app), \
                    patch.object(publish.seq_records, 'machines', return_value={'AXN2': {'Tmp_backup': r'\\SQserver\backup_06'}}), \
                    patch.object(publish.seq_records, 'history', return_value=history), \
                    patch.object(publish.ex_info_links, 'load_transfers', return_value=[record]), \
                    patch.object(publish, 'analysis_rows_by_group', return_value=groups), \
                    patch.object(dh.file_moves, 'LOG_ROOT', base / 'other_pc_logs'), \
                    patch.object(dh, 'SHARED_MOVES', shared_moves):
                out = base / 'sq_transfers.csv'
                self.assertEqual(publish.export_transfers(None, [], out, lambda row, key: row[key]), 1)
            with out.open(encoding='utf-8-sig', newline='') as stream:
                (row,) = list(csv.DictReader(stream))
            self.assertEqual(row['現在の保存先（履歴）'], current)
            self.assertEqual(row['保存先の追跡'], '履歴で追跡')
            self.assertEqual((row['転送先のサンプル名'], row['計画どおり'], row['ANAL状態']), ('S', 'はい', '進行中'))
            self.assertEqual(set(row), set(tl.TITLES) | {publish.EXPORTED_AT})

    def test_write_csv_keeps_previous_file_when_replace_fails(self):
        with TemporaryDirectory() as temp:
            out = Path(temp) / 'a' / 'x.csv'
            publish.write_csv(out, ('列',), [['値,1']])
            self.assertTrue(out.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(read(out), [['列'], ['値,1']])
            with patch.object(publish.os, 'replace', side_effect=PermissionError('open in Excel')):
                with self.assertRaises(PermissionError):
                    publish.write_csv(out, ('列',), [['新']])
            self.assertEqual(read(out), [['列'], ['値,1']])
            self.assertEqual(list(out.parent.glob('*.tmp')), [])

    def test_sq_rows_add_category_and_export_time(self):
        from database.app import App
        entry = dict(group='Recent_Data', container='backup_01', machine='AN#3', name='20260205_1701 X',
                     analysis_state='未着手', exsv_t_tdms=83, exzsv_tdms=415, experiment_at='2026-02-05 17:01',
                     analysis_started_at=None, last_success='2026-10-28T06:09:44+00:00', status='完了')
        rows = publish.sq_rows([entry, dict(entry, analysis_state='着手済み')], App.cell, 'T')
        self.assertEqual(rows[0][3], '20260205_1701 X')
        self.assertEqual(rows[0][-2:], ['未着手・TDMSあり（優先）', 'T'])
        self.assertEqual(rows[1][-2], '着手済み')

    def test_bnal_targets_follow_the_sample_ledger_and_saved_folders(self):
        samples = [dict(experiment='E', sample=name) for name in ('S1', 'S2', 'S3', 'S4')]
        plan = {'E|S1': dict(bnal='target'), 'E|S2': dict(bnal='excluded')}
        bnals = [dict(experiment='E', sample='S2'), dict(experiment='E', sample='S3')]
        self.assertEqual(publish.bnal_targets(samples, plan, bnals), [('E', 'S1'), ('E', 'S3')])

    def test_samples_without_seqlist_list_the_existing_bnal_folders(self):
        root = '\\\\qdserver\\analysis'
        samples = [dict(root=root, experiment='E', sample='S', t_tdms=10, anal_tdms=10, bnal_folders=1)]
        bnals = [dict(root=root, experiment='E', sample='S', target='ACGT', tdms=4, tdms_distinct=4,
                      tdms_checked='2026-11-11T00:00:00+00:00')]
        rows, _ = publish.bnal_progress_rows(samples, bnals, {'E|S': dict(bnal='target')},
                                             {catalog.root_identity(root): 'QDserver/analysis'})
        self.assertEqual([(r['target'], r['bnal_tdms'], r['state']) for r in rows],
                         [('ACGT', 4, publish.NO_SEQLIST_STATE)])
        self.assertEqual(rows[0]['group'], 'QDserver/analysis')

    def test_run_without_scan_writes_every_ledger_and_status(self):
        with TemporaryDirectory() as temp:
            db, out, status_path = Path(temp) / 'inv.sqlite3', Path(temp) / 'out', Path(temp) / 'status.json'
            with patch.object(publish.sample_plan, 'load', return_value={}):
                status = publish.run(publish.DEFAULT_SETTINGS, scan=False, db=db, out_dir=out,
                                     configured=[], status_path=status_path)
            self.assertTrue(status['ok'], status)
            for name in publish.FILES.values():
                self.assertTrue((out / name).exists(), name)
            self.assertEqual(read(out / publish.FILES['sq'])[0][-2:], ['区分', publish.EXPORTED_AT])
            ledger = read(out / publish.FILES['status'])
            self.assertEqual([row[1] for row in ledger[1:]],
                             [publish.FILES[name] for name in ('sq', 'analysis', 'bnal', 'transfer')])
            self.assertEqual(json.loads(status_path.read_text(encoding='utf-8'))['exports']['sq']['rows'], 0)

    def test_scan_is_skipped_but_ledgers_written_while_another_scan_holds_the_lock(self):
        with TemporaryDirectory() as temp:
            db, out = Path(temp) / 'inv.sqlite3', Path(temp) / 'out'
            lock_path = Path(temp) / 'scan.lock'
            holder = ScanLock('GUI', lock_path)
            self.assertTrue(holder.acquire())
            try:
                with patch.object(publish, 'scan_sq') as scan_sq, \
                        patch.object(publish, 'scan_analysis') as scan_analysis, \
                        patch.object(publish.sample_plan, 'load', return_value={}):
                    status = publish.run(publish.DEFAULT_SETTINGS, db=db, out_dir=out, configured=[],
                                         lock=ScanLock('publish', lock_path), status_path=Path(temp) / 's.json')
                scan_sq.assert_not_called()
                scan_analysis.assert_not_called()
            finally:
                holder.release()
            self.assertIn('省略', status['scans']['走査']['result'])
            self.assertTrue((out / publish.FILES['analysis']).exists())

    def test_full_run_scans_every_sq_stage_and_counts_bnal(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'inv.sqlite3'
            catalog.initialize(db)
            with patch.object(publish, 'scan_sq', return_value={}) as scan_sq, \
                    patch.object(publish, 'scan_analysis', return_value={}) as scan_analysis, \
                    patch.object(publish, 'export_all', return_value={}):
                publish.run(publish.DEFAULT_SETTINGS, full=True, db=db, out_dir=temp, configured=[],
                            lock=ScanLock('publish', Path(temp) / 'scan.lock'), status_path=Path(temp) / 's.json')
            self.assertEqual(scan_sq.call_args[0][2], ('Recent_Data', 'Archived_Data', 'Stocked_Data'))
            self.assertTrue(scan_analysis.call_args[0][2]('any experiment'))

    def test_scheduled_run_does_nothing_while_disabled(self):
        with patch.object(publish, 'load_settings', return_value=dict(publish.DEFAULT_SETTINGS)), \
                patch.object(publish, 'setup_logging'), patch.object(publish, 'run') as run:
            self.assertEqual(publish.main(['--scheduled']), 0)
            run.assert_not_called()
