"""サンプル台帳（shared/sample_plan.py）と SEQlist・BC の読み込み（shared/bnal_lists.py）のテスト。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from shared import bnal_lists, sample_plan


class SamplePlanTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'SamplePlan' / 'sample_plan.json'

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_writes_only_the_given_fields(self):
        k = sample_plan.key('ex', 'S1')
        sample_plan.update({k: dict(kind='核酸', bnal='target')}, who='a', path=self.path)
        sample_plan.update({k: dict(bc='000.csv')}, who='b', path=self.path)
        entry = sample_plan.load(self.path)[k]
        self.assertEqual((entry['kind'], entry['bnal'], entry['bc'], entry['updated_by']), ('核酸', 'target', '000.csv', 'b'))

    def test_changes_made_meanwhile_on_another_pc_are_kept(self):
        a, b = sample_plan.key('ex', 'A'), sample_plan.key('ex', 'B')
        sample_plan.update({a: dict(bnal='target')}, path=self.path)
        snapshot = sample_plan.load(self.path)                           # PC 1 opens the ledger
        sample_plan.update({b: dict(bnal='excluded')}, path=self.path)  # PC 2 saves meanwhile
        sample_plan.update({a: dict(bnal='excluded')}, path=self.path)  # PC 1 saves its own change
        self.assertNotIn(b, snapshot)
        self.assertEqual({k: e['bnal'] for k, e in sample_plan.load(self.path).items()}, {a: 'excluded', b: 'excluded'})

    def test_clearing_every_field_removes_the_sample(self):
        k = sample_plan.key('ex', 'S1')
        sample_plan.update({k: dict(bnal='target')}, path=self.path)
        sample_plan.update({k: dict(bnal=None)}, path=self.path)
        self.assertEqual(sample_plan.load(self.path), {})

    def test_invalid_values_are_refused_before_writing(self):
        with self.assertRaises(ValueError):
            sample_plan.update({sample_plan.key('ex', 'S1'): dict(kind='DNA')}, path=self.path)
        with self.assertRaises(ValueError):
            sample_plan.update({sample_plan.key('ex', 'S1'): dict(bnal='yes')}, path=self.path)
        self.assertFalse(self.path.exists())

    def test_missing_ledger_reads_as_empty(self):
        self.assertEqual(sample_plan.load(self.path), {})

    def test_dropbox_conflict_copies_are_found(self):
        sample_plan.update({sample_plan.key('ex', 'S1'): dict(bnal='target')}, path=self.path)
        copy = self.path.with_name("sample_plan (PC2's conflicted copy 2026-11-02).json")
        copy.write_text('{}', encoding='utf-8')
        self.assertEqual(sample_plan.conflicted_copies(self.path), [copy])

    def test_import_from_shares_keeps_existing_values_and_reports_conflicts(self):
        sample_plan.update({sample_plan.key('ex', 'kept'): dict(bnal='excluded')}, path=self.path)
        groups = {r'\\qd\analysis|ex|S1': 'target', r'\\qt\analysis|ex|S1': 'target',
                  r'\\qd\analysis|ex|both': 'target', r'\\qt\analysis|ex|both': 'excluded',
                  r'\\qd\analysis|ex|kept': 'target'}
        imported, conflicts = sample_plan.import_bnal_groups(groups, path=self.path)
        self.assertEqual(imported, ['ex|S1'])
        self.assertEqual(conflicts, {'ex|both': ['excluded', 'target']})
        plan = sample_plan.load(self.path)
        self.assertEqual((plan['ex|S1']['bnal'], plan['ex|kept']['bnal']), ('target', 'excluded'))
        self.assertNotIn('ex|both', plan)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['version'], 1)


class BnalListsTests(TestCase):
    def test_kinds_are_told_from_contents_in_one_folder(self):
        with TemporaryDirectory() as tmp:
            seq, bc = Path(tmp, 'SEQlist'), Path(tmp, 'BC')
            seq.mkdir()
            bc.mkdir()
            (seq / 'mir.csv').write_text('miR-1,UGGAAUGUAAAGAAGUAUGUAU,22\nmiR-2,ACGU,4\n', encoding='cp932')
            (seq / 'pep.csv').write_text('AVP,CYFQNCPRG,9\nOXT,CYIQNCPLG,9\n', encoding='cp932')
            (seq / 'broken.csv').write_bytes(b'')
            (bc / 'base.csv').write_text('0,B,Baseline,0,0,2\n1,G,Guanine,0,1,2\n2,A,Adenine,0,0.7,2\n', encoding='cp932')
            amino = ''.join(f'{i},{n[0]},{n},0,{i / 10},2\n' for i, n in enumerate(sorted(bnal_lists.AMINO_NAMES), 1))
            (bc / 'amino.csv').write_text('0,B,Baseline,0,0,2\n' + amino, encoding='cp932')
            self.assertEqual(bnal_lists.seq_files(seq, 'Peptide'), {'pep.csv': 'Peptide'})
            self.assertEqual(list(bnal_lists.seq_files(seq, 'Nucleotide')), ['broken.csv', 'mir.csv'])
            self.assertEqual(bnal_lists.bc_files(bc, 'Peptide'), {'amino.csv': 'Amino acid'})
            self.assertEqual(bnal_lists.bc_files(bc, 'Nucleotide'), {'base.csv': 'Base'})
