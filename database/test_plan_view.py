"""サンプル台帳タブ: 選択したサンプルへの変更の組み立て（「変更しない」の項目は触らない）。"""
from pathlib import Path
from unittest import TestCase

from database import plan_view

KEEP = plan_view.KEEP


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

    def test_saved_share_is_not_probed_twice(self):
        got = plan_view.refresh_targets({('ex', 'A')}, self.samples, self.roots, lambda p: True)
        self.assertEqual(len(got), 2)
        self.assertEqual(plan_view.refresh_targets({('zz', 'Q')}, self.samples, self.roots, lambda p: False), [])
