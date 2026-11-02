from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from database import inventory_views, sq_workflow
from shared import catalog


class SQWorkflowTests(TestCase):
    def test_normal_update_excludes_archived_stocked_and_unrelated(self):
        sources = [dict(server='SQserver', kind='experiment', root=root) for root in
                   (r'\\SQserver\backup_01', r'\\SQserver\backup_08', r'\\SQserver\backup_any',
                    r'\\SQserver\data\AN1', r'\\SQserver\data_stocked\AN1')]
        self.assertEqual([s['root'] for s, _ in sq_workflow.jobs_for_stage(sources, 'Recent_Data')],
                         [s['root'] for s in sources[:2]])
        self.assertEqual(len(sq_workflow.jobs_for_stage(sources, 'Archived_Data')), 1)
        self.assertEqual(len(sq_workflow.jobs_for_stage(sources, 'Stocked_Data')), 1)

    def test_txt_pairs_only_include_direct_d_prefix_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'backup_01'
            for relative in ('experiment/EXSV/D_a.txt', 'experiment/EXSV/D_b.TXT',
                             'experiment/EXSV/ignore.txt', 'experiment/EXSV/T/D_a.tdms',
                             'experiment/EXSV/T/D_orphan.tdms', 'experiment/EXSV/sub/D_nested.txt'):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('test')
            db = Path(temp) / 'test.sqlite3'
            catalog.initialize(db)
            catalog.scan_source(dict(server='SQserver', root=str(root), kind='experiment'), db)
            self.assertEqual(sq_workflow.acquisition_counts(db, str(root), 'experiment'), (2, 1, 1))

    def test_archived_uses_sq_backup_mapping_and_stocked_has_no_rate(self):
        groups = [dict(root=r'\\SQserver\data\AN9', container='AN9', group='data', folder_count=2),
                  dict(root=r'\\SQserver\data_stocked\AN9', container='AN9', group='data_stocked', folder_count=0)]
        entries = [dict(root=groups[0]['root'], container='AN9', group='data', entry_type='フォルダ', name=name)
                   for name in ('started', 'pending')]
        record = [''] * 17
        record[0] = '2026/10/15_12:30'
        record[2], record[6] = 'AXN7', 'started'
        earlier = record.copy()
        earlier[0] = '2026/10/14_09:15'
        with patch.object(sq_workflow.seq_records, 'machines', return_value={'AXN7': {'SQ_backup': groups[0]['root']}}), patch.object(sq_workflow.seq_records, 'history', return_value=[record, earlier]):
            sq_workflow.enrich(groups, entries)
        self.assertEqual(groups[0]['started_count'], 1)
        self.assertEqual(groups[0]['pending_count'], 1)
        self.assertEqual(groups[0]['started_rate'], '50.0%')
        self.assertEqual(groups[1]['started_rate'], '')
        self.assertEqual(entries[0]['analysis_started_at'], '2026-10-14 09:15')
        self.assertEqual(entries[1]['analysis_started_at'], '')
