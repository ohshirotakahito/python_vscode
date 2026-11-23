"""サンプル台帳タブ: 選択したサンプルへの変更の組み立て（「変更しない」の項目は触らない）。"""
from pathlib import Path
from unittest import TestCase

from database import plan_view
from shared import ex_info_owners

KEEP = plan_view.KEEP


class OwnerTests(TestCase):
    def test_owner_comes_from_registered_location_and_multiple_owners_can_filter(self):
        root = r'\\Server\analysis'
        rows = [dict(experiment='Folder', sample='A'), dict(experiment='Folder', sample='B')]
        samples = [dict(root=root.upper(), **row) for row in rows]
        data = dict(projects=[dict(key='P', name='Different display name', owner_names=['Alice', 'Bob'])],
                    samples=[dict(key='S', project_key='P', name='Different sample name')],
                    locations=[dict(project_key='P', sample_key='S', root_path=root,
                                    experiment_folder='Folder', sample_folder='A')])
        a, b = ex_info_owners.attach(rows, samples, data)
        self.assertEqual(a['owner_names'], ['Alice', 'Bob'])
        self.assertTrue(plan_view.owner_matches(a, 'Alice'))
        self.assertTrue(plan_view.owner_matches(a, 'Bob'))
        self.assertFalse(plan_view.owner_matches(b, 'Alice'))
        self.assertTrue(plan_view.owner_matches(b, 'Ex Info未照合'))
        self.assertTrue(plan_view.owner_matches(b, plan_view.ALL_OWNERS))
        data['projects'][0]['owner_names'] = []
        a, _ = ex_info_owners.attach(rows, samples, data)
        self.assertTrue(plan_view.owner_matches(a, '責任者未設定'))

    def test_same_names_on_another_share_do_not_assign_an_owner_and_conflicts_are_explicit(self):
        rows = [dict(experiment='E', sample='S')]
        samples = [dict(root=r'\\Server\analysis', **rows[0])]
        data = dict(projects=[dict(key='P', owner_names=['Alice'])],
                    samples=[dict(key='S1', project_key='P'), dict(key='S2', project_key='P')],
                    locations=[dict(project_key='P', sample_key='S1', root_path=r'\\Other\analysis',
                                    experiment_folder='E', sample_folder='S')])
        (row,) = ex_info_owners.attach(rows, samples, data)
        self.assertEqual(row['owner_state'], 'unmatched')
        loc = dict(data['locations'][0], root_path=r'\\Server\analysis')
        data['locations'] = [loc, dict(loc, sample_key='S2')]
        (row,) = ex_info_owners.attach(rows, samples, data)
        self.assertEqual(row['owner_names'], [])
        self.assertTrue(plan_view.owner_matches(row, '照合要確認'))


class ChangesTests(TestCase):
    def test_only_changed_fields_are_written(self):
        self.assertEqual(plan_view.changes_for(['ex|A', 'ex|B'], '核酸', KEEP, KEEP, KEEP),
                         {'ex|A': dict(kind='核酸'), 'ex|B': dict(kind='核酸')})
        self.assertEqual(plan_view.changes_for(['ex|A'], KEEP, KEEP, KEEP, KEEP), {})

    def test_labels_become_ledger_values(self):
        self.assertEqual(plan_view.changes_for(['ex|A'], KEEP, '対象', 'mir.csv（核酸）', '000.csv（核酸）'),
                         {'ex|A': dict(bnal='target', seqlists=['mir.csv'], bc='000.csv')})

    def test_unset_choices_clear_fields(self):
        self.assertEqual(plan_view.changes_for(['ex|A'], '未設定', '未分類', plan_view.NONE, plan_view.NONE),
                         {'ex|A': dict(kind=None, bnal=None, seqlists=[], bc=None)})


class RefreshTargetsTests(TestCase):
    """右クリック「再取得」の対象: DB にある共有＋いま T がある共有（台帳のみのサンプルも見つける）。"""
    QT, QTB, QD = r'\\QTserver\analysis', r'\\QTserver\backup', r'\\QDserver\analysis'
    samples = [dict(root=QT, experiment='ex', sample='A'), dict(root=QTB, experiment='ex', sample='B')]
    roots = [QT, QD]

    def test_saved_shares_and_shares_where_t_exists(self):
        exists = {str(Path(self.QD, 'ex', 'A', 'A_10k_Sample', 'T'))}
        got = plan_view.refresh_targets({('ex', 'A')}, self.samples, self.roots, lambda p: str(p) in exists)
        self.assertEqual(sorted(got), [(self.QD, 'ex', 'A'), (self.QT, 'ex', 'A')])

    def test_ledger_only_sample_is_found_by_probing(self):
        exists = {str(Path(self.QD, 'Mitsuzawa', '1_0', '1_0_10k_Sample', 'T'))}
        got = plan_view.refresh_targets({('Mitsuzawa', '1_0')}, self.samples, self.roots, lambda p: str(p) in exists)
        self.assertEqual(got, [(self.QD, 'Mitsuzawa', '1_0')])

    def test_why_missing_names_the_old_inner_folder(self):
        tree = {Path(self.QD, 'Mitsuzawa', 'GA_0'): ['1_0_10k_Sample', '1_0_100k_Sample', 'raw data']}

        def listdir(path):
            if path not in tree:
                raise FileNotFoundError(path)
            return tree[path]
        why = plan_view.why_missing('Mitsuzawa', 'GA_0', self.roots, listdir)
        self.assertIn(self.QD, why)
        self.assertIn('GA_0_10k_Sample/T がない', why)
        self.assertIn('中は 1_0_10k_Sample', why)
        self.assertEqual(plan_view.why_missing('Mitsuzawa', 'GA_9', self.roots, listdir), '')

    def test_saved_share_is_not_probed_twice(self):
        got = plan_view.refresh_targets({('ex', 'A')}, self.samples, self.roots, lambda p: True)
        self.assertEqual(len(got), 2)
        self.assertEqual(plan_view.refresh_targets({('zz', 'Q')}, self.samples, self.roots, lambda p: False), [])
