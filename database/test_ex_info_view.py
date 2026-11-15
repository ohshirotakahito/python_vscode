from unittest import TestCase

from database import ex_info_view as view


class ExInfoViewTests(TestCase):
    def test_projects_without_samples_and_multiple_locations_remain_visible(self):
        data = dict(projects=[dict(key='P', name='Project', owner_names=['Alice', 'Bob']),
                              dict(key='Empty', name='No samples')],
                    samples=[dict(key='S', project_key='P', name='Sample', description='notes')],
                    locations=[dict(project_key='P', sample_key='S', root_path=r'\\Server\analysis',
                                    experiment_folder='Exp', sample_folder='S')])
        rows = view.rows(data)
        self.assertEqual(len(rows), 2)
        self.assertTrue(view.matches(rows[0], 'notes', 'Alice'))
        self.assertTrue(view.matches(rows[0], '', 'Bob'))
        self.assertFalse(view.matches(rows[0], '', view.UNSET))
        self.assertTrue(view.matches(rows[1], '', view.UNSET))
        self.assertIn(r'\\Server\analysis\Exp\S', rows[0]['locations'])
        self.assertEqual(rows[1]['sample'], '')
