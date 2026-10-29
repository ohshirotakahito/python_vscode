import unittest
from pathlib import Path
from core import name_check, source_sample_name


class NameCheckTest(unittest.TestCase):
    def test_sample_name_from_source_folder(self):
        self.assertEqual(source_sample_name('20261006_1434 UNAN#4Pex0n2_o163o162'), 'o163o162')
        self.assertEqual(source_sample_name('20261005_1253 UNAN#4Pex1n1_o163o162_NG'), 'o163o162_NG')
        self.assertEqual(source_sample_name('20260730_0910 SSAN#3Pex0n1_vasopressin'), 'vasopressin')
        self.assertIsNone(source_sample_name('oldname'))
        self.assertIsNone(source_sample_name('20261006_1434 UNAN'))

    def test_check_results(self):
        root = Path(r'\SQserver\backup_07')
        match = [root / '20261006_1434 UNAN#4Pex0n2_o163o162', root / '20261006_1100 UNAN#4Pex0n1_O163O162']
        self.assertEqual(name_check(match, 'o163o162'), ('一致', []))
        label, bad = name_check([root / '20260925_0933 UNAN#4Pex0n1_o161'], 'o163o162')
        self.assertEqual(label, '不一致：o161 ≠ o163o162')
        self.assertEqual(bad, ['20260925_0933 UNAN#4Pex0n1_o161'])
        label, bad = name_check([root / 'oldname'], 'o163o162')
        self.assertTrue(label.startswith('判定不可'))
        self.assertEqual(bad, ['oldname'])


if __name__ == '__main__':
    unittest.main()
