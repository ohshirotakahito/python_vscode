"""サンプル台帳と Ex Info の不一致リスト（理由の振り分け）の確認。"""
import unittest

from shared import ex_info_mismatch as m

ROOT = r'\\Rackstation\analysis'
DATA = dict(
    projects=[dict(key='p1', name='Suzuki'), dict(key='p2', name='Other'), dict(key='p3', name='Ox_New'),
              dict(key='p4', name='Ox_Old')],
    samples=[dict(key='linked', project_key='p1', name='A'), dict(key='noloc', project_key='p1', name='B'),
             dict(key='moved', project_key='p1', name='C'), dict(key='else', project_key='p2', name='D'),
             dict(key='old', project_key='p4', name='E')],
    locations=[dict(project_key='p1', sample_key='linked', root_path=ROOT, experiment_folder='Suzuki', sample_folder='A'),
               dict(project_key='p1', sample_key='moved', root_path=r'\\QDserver\analysis', experiment_folder='Suzuki',
                    sample_folder='C'),
               dict(project_key='p4', sample_key='old', root_path=ROOT, experiment_folder='Ox_Old', sample_folder='E')])
SAMPLES = [dict(root=ROOT, experiment='Suzuki', sample=s) for s in 'ABCDF']
PLAN = {f'Suzuki|{s}': dict(kind='核酸') for s in 'ABCDF'}
PLAN.update({'Ox_Old|E': dict(kind='核酸'), 'Suzuki|Z': dict(kind='核酸', sample_key='kept')})


class MismatchTests(unittest.TestCase):
    def test_each_reason(self):
        rows = {(r['project'], r['sample']): r for r in m.mismatches(PLAN, SAMPLES, DATA)}
        self.assertNotIn(('Suzuki', 'A'), rows)                     # linked by its location
        self.assertNotIn(('Suzuki', 'Z'), rows)                     # already holds a sample_key
        self.assertEqual(rows[('Suzuki', 'B')]['reason'], m.NO_LOCATION)
        self.assertEqual(rows[('Suzuki', 'C')]['reason'], m.OTHER_LOCATION)
        self.assertEqual(rows[('Suzuki', 'C')]['ex_info'], r'\\QDserver\analysis\Suzuki\C')
        self.assertEqual(rows[('Suzuki', 'D')]['reason'], m.OTHER_PROJECT)
        self.assertEqual(rows[('Suzuki', 'D')]['ex_info'], 'Other / D')
        self.assertEqual(rows[('Suzuki', 'F')]['reason'], m.NOT_REGISTERED)
        self.assertEqual(rows[('Ox_Old', 'E')]['reason'], m.NO_SERVER)
        self.assertTrue(all(r['hint'] for r in rows.values()))
        order = [r['reason'] for r in m.mismatches(PLAN, SAMPLES, DATA)]
        self.assertEqual(order, sorted(order, key=m.REASONS.index))


if __name__ == '__main__':
    unittest.main()
