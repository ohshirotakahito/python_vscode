"""サンプル台帳タブ: 選択したサンプルへの変更の組み立て（「変更しない」の項目は触らない）。"""
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
