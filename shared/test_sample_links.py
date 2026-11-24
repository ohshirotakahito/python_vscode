"""サンプル台帳と AXN の Ex Info のサンプルを、登録した保存場所（フォルダ）で結び付けることの確認。"""
import json
import tempfile
import unittest
from pathlib import Path

from shared import ex_info_owners, sample_plan

ROOT = r'\\QDserver\analysis'
EX_INFO = dict(
    projects=[dict(key='p1', name='AXN_test')],
    samples=[dict(key='s1', project_key='p1', name='test01'), dict(key='s2', project_key='p1', name='test02'),
             dict(key='s3', project_key='p1', name='dup')],
    locations=[dict(project_key='p1', sample_key='s1', root_path=ROOT, experiment_folder='AXN_test', sample_folder='test01'),
               dict(project_key='p1', sample_key='s2', root_path=ROOT, experiment_folder='AXN_test', sample_folder='test02'),
               dict(project_key='p1', sample_key='s3', root_path=ROOT, experiment_folder='AXN_test', sample_folder='test02')])
SAMPLES = [dict(root=ROOT, experiment='AXN_test', sample='test01'), dict(root=ROOT, experiment='AXN_test', sample='test02'),
           dict(root=ROOT, experiment='Other', sample='x')]


class SampleLinkTests(unittest.TestCase):
    def test_only_a_single_registered_location_links(self):
        keys = ex_info_owners.sample_keys([('AXN_test', 'test01'), ('AXN_test', 'test02'), ('Other', 'x')], SAMPLES, EX_INFO)
        self.assertEqual(keys, {('AXN_test', 'test01'): 's1'})      # test02 has two Ex Info samples; x none

    def test_names_alone_do_not_link(self):
        keys = ex_info_owners.sample_keys([('AXN_test', 'test01')], [dict(root=r'\\QTserver\analysis',
                                                                         experiment='AXN_test', sample='test01')], EX_INFO)
        self.assertEqual(keys, {})

    def test_ledger_records_keys_only_for_existing_entries_and_keeps_them(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample_plan.json'
            sample_plan.update({sample_plan.key('AXN_test', 'test01'): dict(kind='核酸'),
                                sample_plan.key('AXN_test', 'test02'): dict(bnal='target')}, path=path)
            self.assertEqual(sample_plan.link_ex_info(SAMPLES, EX_INFO, path=path), 1)
            plan = sample_plan.load(path)
            self.assertEqual(plan['AXN_test|test01']['sample_key'], 's1')
            self.assertEqual(plan['AXN_test|test01']['kind'], '核酸')
            self.assertNotIn('sample_key', plan['AXN_test|test02'])
            self.assertNotIn('Other|x', plan)
            before = path.read_bytes()
            self.assertEqual(sample_plan.link_ex_info(SAMPLES, EX_INFO, path=path), 0)   # nothing new: not rewritten
            self.assertEqual(path.read_bytes(), before)
            moved = dict(EX_INFO, locations=[])                                         # no longer matches
            self.assertEqual(sample_plan.link_ex_info(SAMPLES, moved, path=path), 0)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['samples']['AXN_test|test01']['sample_key'], 's1')

    def test_sample_key_must_be_text(self):
        with self.assertRaises(ValueError):
            sample_plan.update({'a|b': dict(sample_key='')}, path=Path(tempfile.gettempdir()) / 'never_written.json')


if __name__ == '__main__':
    unittest.main()
