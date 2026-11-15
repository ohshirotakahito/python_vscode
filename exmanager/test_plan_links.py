"""計測の計画（Ex Info）から転送セットを作る判定のテスト。"""
import tempfile
import json
import unittest
from pathlib import Path

import core
from shared.ex_info_links import Planned
from shared import ex_info_links
from database import transfer_ledger

ROOTS = (r'\\Rackstation\analysis', r'\\QTserver\analysis')


def planned(name, root=r'\\Rackstation\analysis', experiment='TIT', sample='LAB', kind='Sample'):
    return Planned(name, 'TIT', sample, kind, 'P1', 'S1', 'L1', root, experiment, sample)


class PlanGroupsTest(unittest.TestCase):
    def test_planned_transfer_reaches_database_with_persisted_keys(self):
        """Exercise real copying and both history files without touching shared storage."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            analysis = base / 'analysis'
            analysis.mkdir()
            source = base / '20261113_1000_test'
            (source / 'EXSV' / 'T').mkdir(parents=True)
            (source / 'EXZSV').mkdir()
            (source / 'EXSV' / 'D_EXSV#1.txt').write_bytes(b'metadata')
            (source / 'EXSV' / 'T' / 'D_EXSV#1.tdms').write_bytes(b'measurement')
            links, ledger = base / 'MeasurementLinks.jsonl', base / 'ex_info.json'
            links.write_text(json.dumps(dict(experimental_id=source.name, key='L1',
                project_key='P1', sample_key='S1', project='TIT', sample='LAB',
                kind='Sample', location_key='LOC1')) + '\n', encoding='utf-8')
            ledger.write_text(json.dumps(dict(locations=[dict(key='LOC1', sample_key='S1',
                root_path=str(analysis), experiment_folder='TIT', sample_folder='LAB')])),
                encoding='utf-8')
            plans = ex_info_links.load_plans(links, ledger)
            groups, skipped = core.plan_groups([source], plans, [str(analysis)])
            self.assertEqual(skipped, [])
            ((root, experiment, sample, kind), sources), = groups.items()
            target = Path(root) / experiment / sample
            self.assertEqual(core.missing_destinations([target]), [target])
            self.assertFalse(target.exists())
            # Simulate approval of the displayed missing destination list.
            core.create_destinations(root, experiment, [sample])
            local, legacy = base / 'Uploader_python.csv', base / 'Uploader.csv'
            core.write_csv(legacy, core.HEADERS, [])
            transfers = base / 'SEQ' / 'Transfers.jsonl'
            context = dict(machine='AXN2', selection='Recent_Data', uploader='UN',
                           kind=kind, plans=plans, transfers_path=transfers)
            jobs = core.plan_batch([dict(sources=sources, target=target, context=context,
                                        legacy=legacy, local=local)])
            results = core.execute_batch(jobs, log=lambda _: None)
            self.assertEqual(results[0][3], '完了', results[0][4])
            self.assertEqual((target / 'raw data' / source.name / 'EXSV' / 'T' /
                              'D_EXSV#1.tdms').read_bytes(), b'measurement')
            history = core.history(legacy, local)
            self.assertEqual(len(history), 1)
            self.assertEqual(len(history[0]), 17)
            records = ex_info_links.load_transfers(transfers)
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0].planned)
            self.assertEqual((records[0].root, records[0].sample_key, records[0].link_key),
                             (str(analysis), 'S1', 'L1'))
            self.assertTrue((base / 'manifests' / (records[0].manifest + '.csv')).is_file())
            rows = transfer_ledger.build_rows(
                [dict(name=source.name, group='Recent_Data', container='backup_06')],
                {'AXN2': {'Tmp_backup': r'\\SQserver\backup_06'}}, history, {}, records)
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]['transfer'], rows[0]['as_planned'], rows[0]['sample_key']),
                             ('転送済み', 'はい', 'S1'))

    def test_sources_are_grouped_by_their_planned_destination(self):
        plans = {'A': planned('A'), 'B': planned('B'), 'C': planned('C', sample='LAB2'),
                 'D': planned('D', kind='Blank')}
        groups, skipped = core.plan_groups([Path('x') / n for n in 'ABCD'], plans, ROOTS)
        self.assertEqual({k: [p.name for p in v] for k, v in groups.items()}, {
            (ROOTS[0], 'TIT', 'LAB', 'Sample'): ['A', 'B'],
            (ROOTS[0], 'TIT', 'LAB2', 'Sample'): ['C'],
            (ROOTS[0], 'TIT', 'LAB', 'Blank'): ['D']})
        self.assertEqual(skipped, [])

    def test_root_is_written_as_in_the_root_list(self):
        groups, _ = core.plan_groups([Path('A')], {'A': planned('A', root='\\\\RACKSTATION\\Analysis\\')}, ROOTS)
        self.assertEqual(list(groups), [(ROOTS[0], 'TIT', 'LAB', 'Sample')])

    def test_unplanned_undecided_and_unknown_roots_are_left_out_with_a_reason(self):
        plans = {'B': planned('B', root=''), 'C': planned('C', root=r'\\QDserver\backup'),
                 'D': planned('D', kind='')}
        groups, skipped = core.plan_groups([Path(n) for n in 'ABCD'], plans, ROOTS)
        self.assertEqual(groups, {})
        reasons = dict(skipped)
        self.assertEqual(reasons['A'], core.NO_PLAN)
        self.assertTrue(reasons['B'].startswith('保存先未定'))
        self.assertIn(r'\\QDserver\backup', reasons['C'])
        self.assertTrue(reasons['D'].startswith('区分が不明'))

    def test_mismatches_of_a_hand_chosen_destination(self):
        plans = {'A': planned('A'), 'B': planned('B', root=''), 'C': planned('C', sample='LAB2')}
        sources = [Path(n) for n in 'ABCD']
        self.assertEqual(core.plan_mismatches(sources, plans, ROOTS[0], 'TIT', 'LAB', 'Sample'),
                         [('C', plans['C'].text)])
        self.assertEqual([n for n, _ in core.plan_mismatches(sources, plans, ROOTS[0], 'TIT', 'LAB', 'Blank')],
                         ['A', 'C'])

    def test_plan_state_of_a_set(self):
        target = Path(ROOTS[0]) / 'TIT' / 'LAB'
        plans = {'A': planned('A'), 'B': planned('B'), 'C': planned('C', sample='LAB2'), 'U': planned('U', root='')}
        self.assertEqual(core.plan_state([Path('A'), Path('B')], plans, target, 'Sample'), '計画どおり')
        self.assertEqual(core.plan_state([Path('X')], plans, target, 'Sample'), core.NO_PLAN)
        self.assertEqual(core.plan_state([Path('A'), Path('C'), Path('U'), Path('X')], plans, target, 'Sample'),
                         '計画どおり 1・計画と違う 1・保存先未定 1・計画なし 1')

    def test_missing_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            there = Path(directory) / 'TIT' / 'LAB'
            there.mkdir(parents=True)
            self.assertEqual(core.missing_destinations([there, Path(directory) / 'TIT' / 'LAB2']),
                             [Path(directory) / 'TIT' / 'LAB2'])


if __name__ == '__main__':
    unittest.main()
