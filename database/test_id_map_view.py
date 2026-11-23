"""実験ID 対応: 実験名・サンプル名・実験ID の対応を、転送履歴・転送の記録・計画から作る。"""
from unittest import TestCase

from database import id_map_view as view
from shared.ex_info_links import Planned, Transfer


def history_row(uploaded_at, machine, eid, experiment, sample, experiment_at='2026/11/10_10:55'):
    return (uploaded_at, 'TO', machine, 'Recent_Data', experiment_at, 'TO', eid, experiment, sample) + ('',) * 8


class IdMapTests(TestCase):
    def test_each_experimental_id_is_one_row_under_its_experiment_and_sample(self):
        history = [history_row('2026/11/12_09:00', 'AXN2', 'ID-2', 'Exp', 'S1'),
                   history_row('2026/11/11_09:00', 'AXN2', 'ID-1', 'Exp', 'S1'),
                   history_row('2026/11/13_09:00', 'AXN2', 'ID-1', 'Exp', 'S1')]
        transfers = [Transfer(machine='AXN2', source='ID-1', selection='Recent_Data', root=r'\Rackstation\analysis',
                              experiment='Exp', sample='S1', kind='Sample')]
        rows = view.build_rows(history, transfers)
        self.assertEqual([r['experimental_id'] for r in rows], ['ID-1', 'ID-2'])
        first = rows[0]
        self.assertEqual((first['experiment'], first['sample'], first['machine']), ('Exp', 'S1', 'AXN2'))
        self.assertEqual(first['state'], view.TRANSFERRED)
        self.assertEqual(first['transferred'], '2026-11-11 09:00（ほか 1 回）')
        self.assertEqual(first['shares'], 'Rackstation/analysis')
        self.assertEqual(first['kind'], 'Sample')

    def test_plan_shows_as_planned_off_plan_and_not_yet_transferred(self):
        history = [history_row('2026/11/15_15:11', 'AXN2', 'ID-1', 'Suzuki', 'Kme2_pH12'),
                   history_row('2026/11/15_15:12', 'AXN2', 'ID-2', 'Suzuki', 'Glu')]
        plans = {
            'ID-1': Planned('ID-1', 'Suzuki', 'Kme2pH12', 'Sample'),          # 名前が違う送り先に転送された
            'ID-2': Planned('ID-2', 'Suzuki', 'Glu', 'Sample', root=r'\R\analysis',
                            experiment_folder='Suzuki', sample_folder='glu'),
            'ID-3': Planned('ID-3', 'Suzuki', 'Ala', 'Blank'),
        }
        rows = {(r['experimental_id'], r['sample']): r for r in view.build_rows(history, (), plans)}
        self.assertEqual(rows[('ID-1', 'Kme2_pH12')]['state'], view.OFF_PLAN)
        self.assertEqual(rows[('ID-1', 'Kme2pH12')]['state'], view.PLANNED_ONLY)
        self.assertEqual(rows[('ID-2', 'Glu')]['state'], view.AS_PLANNED)
        self.assertEqual(rows[('ID-3', 'Ala')]['state'], view.PLANNED_ONLY)
        self.assertEqual(rows[('ID-3', 'Ala')]['kind'], 'Blank')
        self.assertIn('保存先未定', rows[('ID-3', 'Ala')]['plan'])

    def test_filter_by_text_machine_and_focused_samples(self):
        rows = view.build_rows([history_row('2026/11/11_09:00', 'AXN2', '20261110 UNAN#2', 'Exp', 'S1'),
                                history_row('2026/11/11_09:00', 'AX1', 'other', 'Exp', 'S2')])
        self.assertEqual(len([r for r in rows if view.matches(r, 'unan')]), 1)
        self.assertEqual(len([r for r in rows if view.matches(r, '', 'AX1')]), 1)
        self.assertEqual(len([r for r in rows if view.matches(r, '', focus={('exp', 's2')})]), 1)
        self.assertEqual(len([r for r in rows if view.matches(r, 'exp')]), 2)

    def test_experiment_date_falls_back_to_the_measurement_folder_name(self):
        (row,) = view.build_rows([history_row('2026/11/15_15:11', 'AXN2', '20261110_1055 UNAN#2Pex1n1_X', 'E', 'S',
                                              experiment_at='')])
        self.assertEqual(row['experiment_at'], '2026-11-10 10:55')

    def test_sqserver_folders_without_transfer_are_listed_as_unsorted(self):
        history = [history_row('2026/11/11_09:00', 'AXN2', 'ID-1', 'Exp', 'S1'),
                   history_row('2026/11/11_09:00', 'AXN2', 'ID-gone', 'Exp', 'S1')]
        sq = [dict(name='ID-1', group='Recent_Data', machine='AXN2', entry_type='フォルダ', parse_status='解析済み'),
              dict(name='ID-1.zip', group='Archived_Data', machine='AXN2', entry_type='圧縮ファイル', parse_status='解析済み'),
              dict(name='Furuhata', group='Recent_Data', machine='', entry_type='フォルダ', parse_status='形式不明'),
              dict(name='ID-new', parse_status='解析済み', group='Recent_Data', machine='AXN3', entry_type='フォルダ',
                   experiment_at='2026-11-20 10:00')]
        rows = {r['experimental_id']: r for r in view.build_rows(history, sq_entries=sq)}
        self.assertEqual(rows['ID-1']['sq'], 'Recent_Data・Archived_Data')
        self.assertEqual(rows['ID-gone']['sq'], 'なし（Archived_Data・Recent_Data を確認）')
        self.assertNotIn('Furuhata', rows)
        new = rows['ID-new']
        self.assertEqual((new['experiment'], new['sample'], new['state'], new['machine']), ('', '', view.UNSORTED, 'AXN3'))
        self.assertTrue(view.matches(new, '', state=view.UNSORTED))
        self.assertFalse(view.matches(rows['ID-1'], '', state=view.UNSORTED))
        self.assertEqual(view.build_rows(history)[0]['sq'], '')    # SQ list not read yet: unknown, not missing
