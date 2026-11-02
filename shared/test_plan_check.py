"""サンプル台帳と DB の突き合わせ（shared/plan_check.py）のテスト。"""
from unittest import TestCase

from shared import plan_check

SEQ = {'mir.csv': 'Nucleotide', 'pep.csv': 'Peptide'}
BC = {'000.csv': 'Base', '005.csv': 'Amino acid'}
READY = dict(bnal='target', kind='核酸', seqlists=['mir.csv'], bc='000.csv')


def issues(entry, in_db=True, anal='complete', started=('a',)):
    return plan_check.issues(entry, in_db, anal, set(started), SEQ, BC)


class IssueTests(TestCase):
    def test_ready_target_has_no_issue(self):
        self.assertEqual(issues(READY), [])

    def test_unclassified_and_missing_settings(self):
        self.assertEqual(issues({}), ['未分類'])
        self.assertEqual(issues(dict(bnal='target')), ['種類未設定', 'SEQlist未設定', 'BC未設定'])
        self.assertEqual(issues(dict(bnal='excluded'), started=()), [])

    def test_conflicts_come_first(self):
        found = issues(dict(READY, kind='アミノ酸'))
        self.assertEqual(found, ['SEQlistの種類違い', 'BCの種類違い'])
        self.assertEqual(plan_check.level(found), plan_check.CONFLICT)
        self.assertEqual(issues(dict(READY, seqlists=['gone.csv'], bc='gone.csv')), ['SEQlistファイルなし', 'BCファイルなし'])
        self.assertEqual(issues(dict(bnal='excluded')), ['対象外なのにBNALあり'])
        self.assertEqual(issues(READY, in_db=False), ['台帳のみ（DBになし）'])

    def test_bnal_waiting_only_after_anal_is_done(self):
        self.assertEqual(issues(READY, started=()), ['BNAL未着手'])
        self.assertEqual(issues(READY, anal='partial', started=()), [])
        self.assertEqual(plan_check.level(['未分類', 'BNAL未着手']), plan_check.MISSING)


class SampleRowTests(TestCase):
    def test_rows_join_shares_bnal_and_ledger(self):
        samples = [dict(root=r'\\qd\analysis', experiment='ex', sample='S', t_tdms=10, anal_tdms=10, anal_missing_checked=1),
                   dict(root=r'\\qt\analysis', experiment='ex', sample='S', t_tdms=0, anal_tdms=10, anal_missing_checked=1)]
        bnals = [dict(experiment='ex', sample='S', target='a', tdms=5), dict(experiment='ex', sample='S', target='b', tdms=0)]
        plan = {'ex|S': READY, 'old|X': dict(bnal='excluded')}
        rows = {r['key']: r for r in plan_check.sample_rows(samples, bnals, plan, {}, SEQ, BC)}
        self.assertEqual((rows['ex|S']['anal_status'], rows['ex|S']['bnal_started'], rows['ex|S']['issues']),
                         ('complete', 1, []))
        self.assertEqual(len(rows['ex|S']['shares']), 2)
        self.assertEqual(rows['old|X']['issues'], ['台帳のみ（DBになし）'])
