from unittest import TestCase

from database import transfer_ledger as tl

MACHINES = {'AXN2': {'Tmp_backup': r'\\SQserver\backup_06'}, 'AXN4': {'Tmp_backup': r'\\SQserver\backup_07'},
            'C_Local': {'Tmp_backup': r'C:\MCBJ'}}


def entry(name, container='backup_06', group='Recent_Data', **extra):
    return dict(group=group, container=container, name=name, entry_type='フォルダ', experiment_at='2026-11-01 10:00',
                **extra)


def history(uploaded_at, code, source, destination, sample):
    return (uploaded_at, 'TO', code, 'Recent_Data', '', 'UN', source, destination, sample) + ('',) * 8


def sample(experiment, name, t, anal, **extra):
    return dict(experiment=experiment, sample=name, t_tdms=t, anal_tdms=anal, anal_missing_checked=1, **extra)


class TransferLedgerTest(TestCase):
    def test_machine_comes_from_tmp_backup(self):
        self.assertEqual(tl.machine_by_backup(MACHINES), {'backup_06': 'AXN2', 'backup_07': 'AXN4'})

    def test_untransferred_experiment_gets_one_row(self):
        rows = tl.build_rows([entry('E1')], MACHINES, [], {})
        self.assertEqual([(r['machine'], r['name'], r['transfer'], r['anal_state']) for r in rows],
                         [('AXN2', 'E1', tl.NOT_TRANSFERRED, '')])

    def test_one_row_per_destination_sample_with_first_and_last_transfer(self):
        records = [history('2026/10/30_13:34', 'AXN2', 'E1', 'Exp', 'S1'),
                   history('2026/10/31_14:14', 'AXN2', 'E1', 'Exp', 'S1'),
                   history('2026/10/29_09:00', 'AXN2', 'E1', 'Exp', 'S2')]
        groups = {'QDserver/backup': [sample('Exp', 'S1', 10, 4)],
                  'QTserver/analysis': [sample('Exp', 'S1', 10, 10)]}
        rows = tl.build_rows([entry('E1')], MACHINES, records, groups)
        self.assertEqual([(r['dest_sample'], r['first_transfer'], r['last_transfer']) for r in rows],
                         [('S2', '2026-10-29 09:00', '2026-10-29 09:00'),
                          ('S1', '2026-10-30 13:34', '2026-10-31 14:14')])
        s2, s1 = rows
        self.assertEqual((s2['transfer'], s2['anal_state'], s2['locations']), (tl.TRANSFERRED, tl.NOT_FOUND, ''))
        # the most advanced share decides, every share holding the sample is listed
        self.assertEqual((s1['anal_state'], s1['anal_progress'], s1['locations']),
                         ('完了', '100.0%（10/10）', 'QDserver/backup・QTserver/analysis'))

    def test_partial_and_unstarted_anal(self):
        records = [history('2026/10/30_13:34', 'AXN2', 'E1', 'Exp', 'S1'),
                   history('2026/10/30_13:35', 'AXN2', 'E2', 'Exp', 'S2')]
        groups = {'QDserver/backup': [sample('Exp', 'S1', 10, 3), sample('Exp', 'S2', 8, None)]}
        rows = {r['name']: r for r in tl.build_rows([entry('E1'), entry('E2')], MACHINES, records, groups)}
        self.assertEqual((rows['E1']['anal_state'], rows['E1']['anal_progress']), ('進行中', '30.0%（3/10）'))
        self.assertEqual((rows['E2']['anal_state'], rows['E2']['anal_progress']), ('未着手（ANALなし）', '0.0%（0/8）'))

    def test_other_machine_history_and_other_groups_are_ignored(self):
        records = [history('2026/10/30_13:34', 'AXN4', 'E1', 'Exp', 'S1')]  # same name, other machine
        entries = [entry('E1'), entry('E9', container='backup_any'), entry('E8', group='Archived_Data'),
                   entry('E1')]  # duplicate listing
        rows = tl.build_rows(entries, MACHINES, records, {})
        self.assertEqual([(r['machine'], r['name'], r['transfer']) for r in rows],
                         [('AXN2', 'E1', tl.NOT_TRANSFERRED)])
