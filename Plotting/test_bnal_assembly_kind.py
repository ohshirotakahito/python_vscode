"""Peptide targets are coloured as amino acids; the kind chosen in BNAL wins over the letters."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter

import bnal_assembly as asm
import bnal_source as src
from bnal_plots import POSITION, VALUE


def raw(ref, reads=5):
    rows = []
    for read in range(reads):
        rows.append({POSITION: 0, asm.BASE: 'B', VALUE: 0.0, 'L#': read})
        rows += [{POSITION: p, asm.BASE: b, VALUE: 0.5, 'L#': read} for p, b in enumerate(ref, 1)]
        rows.append({POSITION: len(ref) + 1, asm.BASE: 'B', VALUE: 0.0, 'L#': read})
    return pd.DataFrame(rows)


class KindTests(unittest.TestCase):
    def test_letters_decide_when_bnal_recorded_nothing(self):
        self.assertEqual(asm.build(raw('RRKLIHEGCL'), 'x').kind, asm.PEPTIDE)
        # H/R/W/Y are also used by nucleotide BC tables, so they alone do not make a peptide
        self.assertEqual(asm.build(raw('AUGCMHRY'), 'x').kind, asm.NUCLEOTIDE)

    def test_kind_chosen_in_bnal_wins(self):
        a = asm.build(raw('GASC'), 'x', list_kind='Peptide')
        self.assertEqual((a.kind, a.unit), (asm.PEPTIDE, 'アミノ酸'))
        self.assertIn('4 アミノ酸', a.summary())
        self.assertEqual(asm.build(raw('GASC'), 'x', list_kind='Nucleotide').kind, asm.NUCLEOTIDE)

    def test_amino_acids_have_their_own_colours(self):
        self.assertNotEqual(asm.base_color('E', kind=asm.PEPTIDE), asm.base_color('E'))
        self.assertEqual(len(set(asm.AMINO_COLORS.values())), 20)
        self.assertEqual(asm.letter_label('E', asm.PEPTIDE), 'E（負電荷）')
        self.assertEqual(asm.letter_label('M'), 'M（修飾）')

    def test_plots_draw_for_both_kinds(self):
        for a in (asm.build(raw('RRKLIHEGCL'), 'p'), asm.build(raw('AUGCM'), 'n')):
            asm.plot_pileup(a)
        asm.plot_compare([asm.build(raw('RRKLIHEGCL'), 'p'), asm.build(raw('RRKLIHEGCL'), 'q')])

    def test_list_kind_is_read_from_the_b_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'B@@x.tdms'
            cols = {'Relative Averaged Data': np.array([0.0, 0.5, 0.0]), 'MS_IDX': np.array([0.0, 1.0, 2.0]),
                    'Assigned Base': np.array(['B', 'E', 'B'])}
            with TdmsWriter(str(path)) as w:
                w.write_segment([RootObject({src.LIST_KIND_PROPERTY: 'Peptide'}), GroupObject(src.TARGET_GROUP),
                                 *[ChannelObject(src.TARGET_GROUP, k, v) for k, v in cols.items()]])
            self.assertEqual(src.read_lap_table(path).attrs['list_kind'], 'Peptide')


if __name__ == '__main__':
    unittest.main()
