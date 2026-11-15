"""計測の計画（MeasurementLinks.jsonl・ex_info.json）と転送の記録（Transfers.jsonl）のテスト。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from shared import ex_info_links as links

ID = '20261111_1351 UNAN#2Pex3n1_LAB'


def link(name=ID, **extra):
    record = dict(schema_version=1, key='L1', experimental_id=name, project_key='P1', sample_key='S1',
                  project='TIT', sample='LAB', kind='Sample', location_key='', destination='',
                  assigned_at='2026-11-11T04:51:45+00:00', by='toshiros-NI')
    record.update(extra)
    return record


def location(key='LOC1', sample_key='S1', root=r'\\Rackstation\analysis', **extra):
    return dict(dict(key=key, project_key='P1', sample_key=sample_key, root_path=root,
                     experiment_folder='TIT', sample_folder='LAB'), **extra)


class PlanTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.links = Path(self.tmp.name) / 'MeasurementLinks.jsonl'
        self.ledger = Path(self.tmp.name) / 'ex_info.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, records, locations=()):
        self.links.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        self.ledger.write_text(json.dumps(dict(schema_version=1, locations=list(locations))), encoding='utf-8')

    def load(self):
        return links.load_plans(self.links, self.ledger)

    def test_no_links_file_means_no_plans(self):
        self.assertEqual(self.load(), {})

    def test_main_location_of_the_sample_when_the_link_has_no_destination(self):
        self.write([link()], [location()])
        plan = self.load()[ID]
        self.assertEqual((plan.destination, plan.kind, plan.sample_key), (r'\\Rackstation\analysis\TIT\LAB', 'Sample', 'S1'))
        self.assertTrue(plan.decided)

    def test_the_link_location_wins_over_the_main_location(self):
        self.write([link(location_key='LOC2')],
                   [location(is_main=True), location('LOC2', root=r'\\QTserver\analysis', sample_folder='LAB2')])
        self.assertEqual(self.load()[ID].destination, r'\\QTserver\analysis\TIT\LAB2')

    def test_destination_text_of_the_link_when_its_location_is_unknown(self):
        self.write([link(destination=r'\\QDserver\analysis\TIT\LAB')], [])
        self.assertEqual(self.load()[ID].root, r'\\QDserver\analysis')

    def test_not_decided_with_several_locations_and_no_main(self):
        self.write([link()], [location(), location('LOC2', root=r'\\QDserver\backup')])
        plan = self.load()[ID]
        self.assertFalse(plan.decided)
        self.assertEqual(plan.destination, '')
        self.assertIn('保存先未定', plan.text)
        self.assertIsNone(plan.matches(r'\\Rackstation\analysis', 'TIT', 'LAB', 'Sample'))

    def test_last_link_of_a_name_wins_and_broken_lines_are_skipped(self):
        self.write([link(kind='Sample'), link(kind='Blank')], [location()])
        with self.links.open('a', encoding='utf-8') as stream:
            stream.write('{"experimental_id": "cut by a sy')
        self.assertEqual(self.load()[ID].kind, 'Blank')

    def test_unreadable_ledger_leaves_the_plan_undecided(self):
        self.write([link()])
        self.ledger.write_text('{broken', encoding='utf-8')
        plan = self.load()[ID]
        self.assertEqual((plan.project, plan.sample, plan.decided), ('TIT', 'LAB', False))

    def test_matches_ignores_case_and_trailing_separators(self):
        self.write([link()], [location()])
        plan = self.load()[ID]
        self.assertTrue(plan.matches('\\\\RACKSTATION\\analysis\\', 'tit', 'lab', 'Sample'))
        self.assertFalse(plan.matches(r'\\Rackstation\analysis', 'TIT', 'LAB', 'Blank'))
        self.assertFalse(plan.matches(r'\\QTserver\analysis', 'TIT', 'LAB', 'Sample'))


class TransferRecordTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'SEQ' / 'Transfers.jsonl'

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_carries_the_plan_keys_and_whether_it_went_as_planned(self):
        plan = links.Planned(ID, 'TIT', 'LAB', 'Sample', 'P1', 'S1', 'L1', r'\\Rackstation\analysis', 'TIT', 'LAB')
        links.append_transfer(links.transfer_record('AXN2', ID, 'Recent_Data', '\\\\Rackstation\\analysis\\',
                                                    'TIT', 'LAB', 'Sample', plan, uploader='UN'), self.path)
        links.append_transfer(links.transfer_record('AXN2', ID, 'Recent_Data', r'\\QTserver\analysis',
                                                    'TIT', 'LAB', 'Sample', plan), self.path)
        first, second = links.load_transfers(self.path)
        self.assertEqual((first.root, first.destination, first.sample_key, first.planned, first.uploader),
                         (r'\\Rackstation\analysis', r'\\Rackstation\analysis\TIT\LAB', 'S1', True, 'UN'))
        self.assertFalse(second.planned)
        line = json.loads(self.path.read_text(encoding='utf-8').splitlines()[0])
        self.assertEqual((line['schema_version'], line['destination']), (1, r'\\Rackstation\analysis\TIT\LAB'))

    def test_transfer_without_a_plan_has_no_keys(self):
        links.append_transfer(links.transfer_record('AXN2', 'E1', 'Recent_Data', r'\\QDserver\analysis',
                                                    'Exp', 'S1', 'Blank'), self.path)
        (record,) = links.load_transfers(self.path)
        self.assertEqual((record.planned, record.sample_key, record.kind), (None, '', 'Blank'))

    def test_unknown_schema_lines_are_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(dict(schema_version=2, source='E1', root='x')) + '\n', encoding='utf-8')
        self.assertEqual(links.load_transfers(self.path), [])

    def test_split_root(self):
        self.assertEqual(links.split_root(r'\\Rackstation\analysis\Exp\S1'), (r'\\Rackstation\analysis', 'Exp', 'S1'))
        self.assertIsNone(links.split_root(r'D:\Exp\S1'))
        self.assertIsNone(links.split_root(r'\\Rackstation\analysis\Exp'))
