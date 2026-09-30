import unittest
from app import history_summary, history_detail


class HistoryDisplayTest(unittest.TestCase):
    def test_repeat_destination_and_distinct_type(self):
        row = ['2026/09/28_10:42', 'TO', 'AXN3', 'Recent_Data', '', 'SS', 'experiment', 'Sakano_00', 'OXT', 'OXT_10k_Sample', '', '1', '30', 'OXT_100k_Sample', '', '1', '240']
        older = list(row)
        older[0], older[1] = '2026/08/01_10:00', 'SS'
        blank = list(row)
        blank[9], blank[13] = 'OXT_10k_Blank', 'OXT_100k_Blank'
        self.assertEqual(history_summary([older, row, blank]), ('3件', '2か所', row[0]))
        self.assertEqual(history_detail(row), (row[0], 'Sakano_00', 'OXT', 'Sample', 'TO', 'SS'))
        self.assertEqual(history_detail(blank)[3], 'Blank')
        self.assertEqual(history_summary([]), ('なし', '0か所', '—'))


if __name__ == '__main__':
    unittest.main()
