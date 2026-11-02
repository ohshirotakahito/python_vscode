import math
from unittest import TestCase

import numpy as np
import pandas as pd

import bnal_modification as mod


def lap(rows):
    """rows: (file, MS_IDX, base, value[, Q])"""
    out = pd.DataFrame([r[:4] for r in rows], columns=['file', 'MS_IDX', 'Assigned Base', 'Relative Averaged Data'])
    out['Q value [x]'] = [r[4] if len(r) > 4 else np.nan for r in rows]
    return out


def site_rows(n, base, value, chunk='#001', q=None):
    return [(f'B@@2026_1022_0446_S_10k_Sample{chunk}.tdms', 22, base, value, q) for _ in range(n)]


def dataset(sample, target, rows):
    return mod.Dataset('EX', sample, target, lap(rows))


class ParseTests(TestCase):
    def test_parse_site_and_chunk(self):
        self.assertEqual(mod.parse_site('hsa-miR-3613-5p_M_22'), ('hsa-miR-3613-5p', 'M', 22))
        self.assertIsNone(mod.parse_site('hsa-miR-3613-5p'))
        self.assertEqual(mod.chunk_of('B@@2026_1022_0446_KSN1_10k_Sample#15.tdms'), '#015')
        self.assertEqual(mod.chunk_of('other.tdms'), 'other.tdms')


class CountTests(TestCase):
    def test_counts_only_the_modified_position(self):
        unmod = site_rows(80, 'C', 0.55) + [('f#001.tdms', 21, 'U', 0.4, None)] * 50
        modified = site_rows(20, 'M', 1.2) + [('f#001.tdms', 21, 'U', 0.4, None)] * 70
        sites = mod.collect_sites([dataset('A', 'seq', unmod), dataset('A', 'seq_M_22', modified)])
        self.assertEqual(len(sites), 1)
        s = sites[0]
        self.assertEqual((s.n_mod, s.n_unmod, s.n_other, s.total), (20, 80, 0, 100))
        self.assertEqual(s.site_label, 'C22→M')
        self.assertAlmostEqual(mod.raw_rate(s, ci='wilson')[0], 0.2)

    def test_other_variants_at_same_position_join_the_denominator(self):
        data = [dataset('A', 'seq', site_rows(70, 'C', 0.5)), dataset('A', 'seq_M_22', site_rows(20, 'M', 1.2)),
                dataset('A', 'seq_N_22', site_rows(10, 'N', 0.9))]
        sites = {s.kind: s for s in mod.collect_sites(data)}
        self.assertEqual((sites['M'].n_mod, sites['M'].n_other, sites['M'].total), (20, 10, 100))
        self.assertEqual((sites['N'].n_mod, sites['N'].n_other), (10, 20))

    def test_missing_unmodified_gives_nan_with_note(self):
        sites = mod.collect_sites([dataset('A', 'seq_M_22', site_rows(5, 'M', 1.2))])
        self.assertTrue(math.isnan(mod.raw_rate(sites[0])[0]))
        self.assertIn('未修飾配列が未読込', sites[0].notes)

    def test_q_filter_applies_only_when_q_exists(self):
        unmod = site_rows(10, 'C', 0.5, q=2) + site_rows(10, 'C', 0.5, q=8)
        modified = site_rows(10, 'M', 1.2, q=8)
        s = mod.collect_sites([dataset('A', 'seq', unmod), dataset('A', 'seq_M_22', modified)], min_q=5)[0]
        self.assertEqual((s.n_mod, s.n_unmod), (10, 10))
        old = mod.collect_sites([dataset('A', 'seq', site_rows(10, 'C', 0.5)),
                                 dataset('A', 'seq_M_22', site_rows(10, 'M', 1.2))], min_q=5)[0]
        self.assertEqual(old.n_unmod, 10)
        self.assertIn('q値なし（Q で絞っていない）', old.notes)

    def test_chunks_are_paired_by_file_number(self):
        unmod = site_rows(9, 'C', 0.5, '#001') + site_rows(5, 'C', 0.5, '#002')
        modified = site_rows(1, 'M', 1.2, '#001') + site_rows(5, 'M', 1.2, '#003')
        s = mod.collect_sites([dataset('A', 'seq', unmod), dataset('A', 'seq_M_22', modified)])[0]
        self.assertEqual(s.chunks.loc['#001'].tolist(), [1, 10])
        self.assertEqual(s.chunks.loc['#002'].tolist(), [0, 5])
        self.assertEqual(s.chunks.loc['#003'].tolist(), [5, 5])


class RateTests(TestCase):
    def sites(self):
        data = []
        for sample, n_mod in (('zero', 5), ('full', 85), ('test', 45)):
            data += [dataset(sample, 'seq', site_rows(100 - n_mod, 'C', 0.5, '#001') + site_rows(0, 'C', 0.5)),
                     dataset(sample, 'seq_M_22', site_rows(n_mod, 'M', 1.2, '#001'))]
        return mod.collect_sites(data)

    def rate(self, table, sample):
        return table.set_index('sample').loc[sample, 'rate']

    def test_raw_zero_and_both(self):
        sites = self.sites()
        self.assertAlmostEqual(self.rate(mod.rate_table(sites, 'raw', ci='wilson'), 'test'), 0.45)
        zero = mod.rate_table(sites, 'zero', zero=('EX', 'zero'), ci='wilson')
        self.assertAlmostEqual(self.rate(zero, 'test'), (0.45 - 0.05) / 0.95)
        both = mod.rate_table(sites, 'both', zero=('EX', 'zero'), full=('EX', 'full'), ci='wilson')
        self.assertAlmostEqual(self.rate(both, 'test'), 0.5)
        self.assertAlmostEqual(self.rate(both, 'full'), 1.0)
        self.assertAlmostEqual(self.rate(both, 'zero'), 0.0)

    def test_missing_control_is_reported(self):
        table = mod.rate_table(self.sites(), 'zero', zero=None)
        self.assertTrue(table['rate'].isna().all())
        self.assertTrue(table['note'].str.contains('0%対照にこの修飾なし').all())

    def test_wilson_and_bootstrap_bracket_the_rate(self):
        lo, hi = mod.wilson(20, 100)
        self.assertLess(lo, 0.2)
        self.assertGreater(hi, 0.2)
        chunks = pd.DataFrame({'mod': [2, 3, 1, 4], 'total': [10, 10, 10, 10]})
        lo, hi = mod.chunk_bootstrap(chunks)
        self.assertLessEqual(lo, 0.25)
        self.assertGreaterEqual(hi, 0.25)
        self.assertTrue(math.isnan(mod.chunk_bootstrap(chunks.iloc[:1])[0]))


class GroupRateTests(TestCase):
    def sites(self):
        data = []
        for sample, n, k in [('A', 100, 20), ('B', 10, 8), ('zero', 100, 10), ('full', 100, 90)]:
            data += [dataset(sample, 'seq', site_rows(n-k, 'C', .5)),
                     dataset(sample, 'seq_M_22', site_rows(k, 'M', 1.2))]
        return mod.collect_sites(data)

    def test_equal_sample_mean_and_pooled_ratio_differ(self):
        groups = {'任意名': [('EX', 'A'), ('EX', 'B')]}
        mean, details = mod.group_rate_table(self.sites(), groups)
        pooled, _ = mod.group_rate_table(self.sites(), groups, aggregation='pooled')
        self.assertAlmostEqual(mean.iloc[0]['rate'], .5)
        self.assertAlmostEqual(pooled.iloc[0]['rate'], 28/110)
        self.assertEqual(mean.iloc[0]['n_samples'], 2)
        self.assertEqual(mean.iloc[0]['chunks'], 2)  # identical #001 in different samples stays separate
        self.assertEqual(len(details[0].chunks), 2)
        self.assertEqual(len(details[0].values['seq_M_22']), 28)
        self.assertEqual(details[0].total, 110)
        self.assertIn('EX / A', mean.iloc[0]['members'])
        self.assertEqual(mean.iloc[0]['interval_method'], 'sample_bootstrap')

    def test_group_controls_and_sample_interval(self):
        groups = {'G': [('EX', 'A'), ('EX', 'B')]}
        table, _ = mod.group_rate_table(self.sites(), groups, 'both', zero=('EX', 'zero'), full=('EX', 'full'))
        row = table.iloc[0]
        self.assertAlmostEqual(row['rate'], .5)
        self.assertAlmostEqual(row['low'], (.2-.1)/.8)
        self.assertAlmostEqual(row['high'], (.8-.1)/.8)
        self.assertEqual(row['control_uncertainty'], 'not_included')
        zero, _ = mod.group_rate_table(self.sites(), groups, 'zero', zero=('EX', 'zero'))
        self.assertAlmostEqual(zero.iloc[0]['rate'], (.5-.1)/.9)

    def test_missing_controls_produce_nan(self):
        table, _ = mod.group_rate_table(self.sites(), {'G': [('EX', 'A')]}, 'both')
        self.assertTrue(math.isnan(table.iloc[0]['rate']))
        self.assertIn('対照', table.iloc[0]['note'])

    def test_missing_or_incomplete_members_are_reported_and_excluded(self):
        sites = self.sites() + mod.collect_sites([dataset('bad', 'seq_M_22', site_rows(20, 'M', 1.2))])
        groups = {'G': [('EX', 'A'), ('EX', 'bad'), ('EX', 'missing')], 'empty': [('EX', 'none')]}
        table, _ = mod.group_rate_table(sites, groups)
        row = table.iloc[0]
        self.assertAlmostEqual(row['rate'], .2)
        self.assertEqual((row['n_samples'], row['n_members']), (1, 3))
        self.assertIn('有効サンプル 1/3', row['note'])
        self.assertTrue(math.isnan(row['low']))
        self.assertTrue(math.isnan(table.iloc[1]['rate']))

    def test_group_identity_includes_experiment_and_blank_names_are_generated(self):
        groups = mod.validate_groups([('', [('EX1', 'S')]), ('', [('EX2', 'S')])])
        self.assertEqual(list(groups), ['Group A', 'Group B'])
        for invalid in [[('G', [])], [('G', [('EX', 'A')]), ('H', [('EX', 'A')])],
                        [('G', [('EX', 'A')]), ('G', [('EX', 'B')])]]:
            with self.assertRaises(ValueError):
                mod.validate_groups(invalid)

    def test_multi_site_multi_group_and_plot(self):
        from matplotlib.figure import Figure
        sites = self.sites() + mod.collect_sites([dataset('A', 'seq', site_rows(80, 'C', .5)),
                                                 dataset('A', 'seq_N_22', site_rows(20, 'N', .9))])
        groups = {'A群': [('EX', 'A')], 'B群': [('EX', 'B')], 'C群': [('EX', 'zero')]}
        table, details = mod.group_rate_table(sites, groups)
        self.assertEqual(len(table), 6)
        self.assertEqual(set(table['sample']), set(groups))
        figure = mod.plot_rates(table, Figure(), detail=details[0])
        self.assertEqual(len(figure.axes), 2)
        self.assertIn('サンプル率の平均', figure.axes[0].get_title())
