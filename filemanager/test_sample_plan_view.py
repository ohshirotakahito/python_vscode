from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from filemanager.app import App, sample_plan_group


class SamplePlanViewTest(unittest.TestCase):
    def test_group_colors_distinguish_measurement_and_analysis_paths(self):
        cases = [('S_100K_Sample/a.TDMS', '100k'), ('S_100k_Blank/a.tdms', '100k'),
                 ('S_10k_Sample/T/a.tdms', '10k'), ('S_10k_Sample/a.txt', 'txt'),
                 ('S_10k_Sample/T/ANAL/a.tdms', 'anal'),
                 ('S_10k_Sample/T/ANAL/stocked/a.tdms', 'stocked'),
                 ('S_10k_Sample/T/BNAL@run/a.tdms', 'bnal')]
        for path, tag in cases:
            with self.subTest(path=path):
                self.assertEqual(sample_plan_group(path, 'ファイル')[0], tag)
                self.assertEqual(sample_plan_group(path.replace('/', '\\'), 'ファイル')[0], tag)

    def view(self):
        rows = [(n, 'ファイル', f'before/{n}', f'after/{n}') for n in range(1, 1001)]
        view = SimpleNamespace(sample_plan_rows=rows, sample_plan_filtered=rows, sample_plan_page=0,
                               sample_plan_tree=Mock(), sample_plan_label=Mock(), sample_plan_query=Mock())
        view.sample_plan_tree.get_children.return_value = ()
        view.show_sample_plan = lambda: App.show_sample_plan(view)
        return view

    def test_large_plan_is_paged_without_losing_rows(self):
        view = self.view()
        App.sample_plan_turn(view, 1)
        self.assertEqual(view.sample_plan_tree.insert.call_count, 300)
        self.assertEqual(view.sample_plan_tree.insert.call_args_list[0].kwargs['values'][0], 301)
        view.sample_plan_tree.insert.reset_mock()
        App.sample_plan_turn(view, 100)
        self.assertEqual(view.sample_plan_tree.insert.call_count, 100)
        self.assertEqual(view.sample_plan_tree.insert.call_args_list[-1].kwargs['values'][0], 1000)

    def test_search_matches_all_pages(self):
        view = self.view()
        view.sample_plan_query.get.return_value = 'after/999'
        App.filter_sample_plan(view)
        self.assertEqual(len(view.sample_plan_filtered), 1)
        self.assertEqual(view.sample_plan_filtered[0][0], 999)
        self.assertEqual(view.sample_plan_page, 0)


if __name__ == '__main__':
    unittest.main()
