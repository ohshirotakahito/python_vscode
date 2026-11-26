"""Amino-acid colours and the target-list kind shared by 配列アセンブリ and シグナル解析."""
import unittest

from shared import sequence_colors as sc


class SequenceColorTests(unittest.TestCase):
    def test_twenty_amino_acids_with_distinct_colours(self):
        self.assertEqual(len(sc.AMINO_ORDER), 20)
        self.assertEqual(len(set(sc.AMINO_COLORS.values())), 20)
        self.assertEqual(sc.AMINO_GROUP_OF['E'], '負電荷')
        self.assertEqual(sc.lighter('#000000', 0.5), '#808080')

    def test_recorded_kind_wins_and_old_files_use_the_letters(self):
        self.assertEqual(sc.list_kind('Peptide', 'GASC'), 'Peptide')
        self.assertEqual(sc.list_kind('Nucleotide', 'RLVETLVV'), 'Nucleotide')
        self.assertEqual(sc.list_kind('', 'RLVETLVV'), 'Peptide')
        # H/R/W/Y are in nucleotide BC tables too
        self.assertEqual(sc.list_kind(None, 'AUGCHRWY'), 'Nucleotide')


if __name__ == '__main__':
    unittest.main()
