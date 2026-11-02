"""shared/progress.py の判定規則（DB・ファイル管理・Analyzer の共通の基準）のテスト。"""
from unittest import TestCase

from shared import progress


def row(t=10, t_stocked=0, anal=10, anal_stocked=0, **extra):
    return dict(t_tdms=t, t_stocked_tdms=t_stocked, anal_tdms=anal, anal_stocked_tdms=anal_stocked,
                anal_missing_checked=1, **extra)


class AnalShareTests(TestCase):
    def status(self, **kw):
        return progress.anal_share(row(**kw))['status']

    def test_states(self):
        self.assertEqual(self.status(anal=None), 'unstarted')
        self.assertEqual(self.status(anal=0), 'unstarted')
        self.assertEqual(self.status(anal=4), 'partial')
        self.assertEqual(self.status(anal=10), 'complete')
        self.assertEqual(self.status(anal=12), 'excess')

    def test_unknown_counts_are_never_taken_as_zero(self):
        cell = progress.anal_share(row(t=None, anal=5))
        self.assertEqual((cell['status'], cell['state']), ('unknown', '確認不可'))

    def test_originals_moved_to_t_stocked_still_count(self):
        # Data 退避で T 直下が空でも、T/stocked の元ファイルがあれば解析コピーではない
        self.assertEqual(self.status(t=0, t_stocked=10, anal=6), 'partial')
        self.assertEqual(self.status(t=0, t_stocked=0, anal=6), 'copy')
        self.assertEqual(self.status(t=0, t_stocked=0, anal=0), 'unknown')

    def test_stocked_anal_counts_as_analysed(self):
        self.assertEqual(self.status(anal=6, anal_stocked=4), 'complete')

    def test_share_flags(self):
        cell = progress.anal_share(row(anal=8, anal_distinct=7, anal_stocked=1, anal_last=9, anal_missing='#003'))
        self.assertEqual(progress.share_flags(cell), ['欠番', '退避', '混在'])
        unchecked = progress.anal_share(dict(row(anal=4), anal_missing_checked=0))
        self.assertEqual(progress.share_flags(unchecked), ['要更新'])
        self.assertEqual(progress.share_flags(progress.anal_share(dict(row(), anal_missing_checked=0))), [])

    def test_sample_flags_over_shares(self):
        main = progress.anal_share(row(t=10, anal=10))
        other = progress.anal_share(row(t=12, anal=12))
        short_copy = progress.anal_share(row(t=0, anal=7))
        best, flags = progress.assess_sample([main, other, short_copy])
        self.assertEqual(best['total'], 10)  # equal ratio: fewest remaining, then first
        self.assertEqual(flags, ['差異', 'コピー不足'])
        self.assertAlmostEqual(short_copy['coverage'], 0.7)
        self.assertIsNone(main.get('coverage'))


class BnalTests(TestCase):
    def test_states(self):
        self.assertEqual(progress.bnal_state(10, 10)[2], 'complete')
        self.assertEqual(progress.bnal_state(0, 10)[:2], ('0%', '未着手（BNAL@ 0件）'))  # LabVIEW の途中停止など
        self.assertEqual(progress.bnal_state(1, 10)[2], 'partial')
        self.assertEqual(progress.bnal_state(12, 10)[2], 'excess')

    def test_uncounted_and_missing_anal_are_flagged_not_hidden(self):
        self.assertEqual(progress.bnal_state(None, 10)[:2], ('未計数', '着手（TDMS未計数）'))
        self.assertEqual(progress.bnal_state(20, None)[1], 'ANALなし・要確認')
        self.assertEqual(progress.bnal_state(20, 0)[1], 'ANALなし・要確認')

    def test_names_missing_when_t_saw_more_folders(self):
        self.assertTrue(progress.bnal_names_missing(dict(bnal_folders=3), 2))
        self.assertFalse(progress.bnal_names_missing(dict(bnal_folders=2), 2))
        self.assertFalse(progress.bnal_names_missing(dict(bnal_folders=None), 0))
