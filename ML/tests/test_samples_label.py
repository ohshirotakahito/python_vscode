# -*- coding: utf-8 -*-
import unittest

from common.paths import MAX_LABEL_CHARS, samples_label


class SamplesLabelTest(unittest.TestCase):
    def test_short_names_unchanged(self):
        self.assertEqual(samples_label(['Guanine', 'OMeG']), 'Guanine-OMeG')

    def test_long_list_is_shortened_and_stable(self):
        smns = [f'GENE{i}_A{i}T' for i in range(30)] + [f'GENE{i}_A{i}T_WT' for i in range(30)]
        label = samples_label(smns)
        self.assertLessEqual(len(label), MAX_LABEL_CHARS + 10)
        self.assertTrue(label.startswith('GENE0_A0T-'))
        self.assertIn('件_', label)
        self.assertEqual(label, samples_label(list(smns)))
        self.assertNotEqual(label, samples_label(smns[:-1]))

    def test_single_very_long_name(self):
        label = samples_label(['X' * 200])
        self.assertLessEqual(len(label), MAX_LABEL_CHARS + 10)


if __name__ == '__main__':
    unittest.main()
