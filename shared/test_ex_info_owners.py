"""責任者の候補が AXN の owner_picker.candidates と同じ決まりになっていることの確認。"""
import unittest

from shared import ex_info_owners


class CandidateTests(unittest.TestCase):
    def test_members_remembered_names_and_project_owners_without_other(self):
        data = dict(owner_names=['Daicel'], projects=[dict(owner_names=['Takahagi', 'Ohshiro'])], samples=[], locations=[])
        names = ex_info_owners.candidates(data, members=['Ohshiro', 'Une', 'Other', ' '])
        self.assertEqual(names, ['Daicel', 'Ohshiro', 'Takahagi', 'Une'])

    def test_member_table_is_optional(self):
        data = dict(projects=[], samples=[], locations=[])
        self.assertEqual(ex_info_owners.candidates(data, members=[]), [])


if __name__ == '__main__':
    unittest.main()
