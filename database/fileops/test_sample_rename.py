import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from database.fileops import sample_rename as rename
from database.fileops.job_control import JobCancelled


class SampleRenameTests(unittest.TestCase):
    def test_100k_direct_tdms_included_and_restored(self):
        for kind in ('Sample', 'Blank'):
            folder = self.root / f'S_100K_{kind}'
            folder.mkdir()
            (folder / f'S_100K_{kind}#001.TDMS').write_bytes(b'hundred')
        (self.raw.parent / 'S_excluded.tdms').write_bytes(b'ten direct')
        planned = self.plan()
        sources = {Path(s) for s, _ in planned['operations']}
        for kind in ('Sample', 'Blank'):
            file = self.root / f'S_100K_{kind}' / f'S_100K_{kind}#001.TDMS'
            self.assertIn(file, sources)
            self.assertNotIn(str(file), rename.inventory(self.root, 'measurement_names_v5'))
        self.assertNotIn(self.raw.parent / 'S_excluded.tdms', sources)
        log = rename.execute(planned, self.base / 'history')
        for kind in ('Sample', 'Blank'):
            self.assertEqual((self.root / f'S-re_100K_{kind}' / f'S-re_100K_{kind}#001.TDMS').read_bytes(), b'hundred')
        rename.restore(rename.restore_plan(log))
        self.assertTrue((self.root / 'S_100K_Sample/S_100K_Sample#001.TDMS').exists())

    def test_bs_excluded_with_legacy_scope_preserved(self):
        for name in ('BS', 'bs'):
            with self.subTest(name=name):
                folder = self.raw / 'ANAL' / name
                folder.mkdir()
                file = folder / 'S_plot.txt'
                file.write_text('keep')
                planned = self.plan()
                self.assertNotIn(str(file), planned['snapshot'])
                self.assertIn(str(file), rename.inventory(self.root, 'measurement_names_v4'))
                log = rename.execute(planned, self.base / 'history')
                self.assertTrue((self.root / 'S-re_10k_Sample/T/ANAL' / name / file.name).exists())
                rename.restore(rename.restore_plan(log))
                self.assertEqual(file.read_text(), 'keep')
                file.unlink()
                folder.rmdir()

    def test_ps_excluded_and_legacy_history_restorable(self):
        ps = self.raw / 'ANAL' / 'PS'
        (ps / 'S_results').mkdir(parents=True)
        file = ps / 'S_results' / 'S_plot.txt'
        file.write_text('keep')
        planned = self.plan()
        self.assertFalse(any('PS' in Path(s).parts for s in planned['snapshot']))
        log = rename.execute(planned, self.base / 'history')
        moved = self.root / 'S-re_10k_Sample/T/ANAL/PS/S_results/S_plot.txt'
        self.assertEqual(moved.read_text(), 'keep')
        rename.restore(rename.restore_plan(log))
        self.assertTrue(file.exists())
        # Old histories retain their original scan scope, including PS entries.
        legacy = self.plan()
        legacy['scope'] = 'measurement_names_v3'
        legacy['snapshot'] = rename.inventory(self.root, legacy['scope'])
        old_log = rename.execute(legacy, self.base / 'legacy_history')
        rename.restore(rename.restore_plan(old_log))
        self.assertTrue(file.exists())

    def test_cancel_after_first_rename_keeps_recoverable_history(self):
        original = rename.inventory(self.root)
        planned = self.plan()
        real_rename = rename.os.rename
        stopped = []
        def rename_then_stop(source, target):
            real_rename(source, target)
            stopped.append(True)
        def check():
            if stopped:
                raise JobCancelled()
        with patch.object(rename, 'check_cancel', side_effect=check), patch.object(rename.os, 'rename', side_effect=rename_then_stop):
            with self.assertRaises(JobCancelled):
                rename.execute(planned, self.base / 'history')
        log = next((self.base / 'history').glob('*.json'))
        journal = json.loads(log.read_text(encoding='utf-8'))
        self.assertEqual(len(journal['steps']), 1)
        self.assertEqual(journal['steps'][0]['state'], 'done')
        rename.restore(rename.restore_plan(log))
        self.assertEqual(rename.inventory(self.root), original)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.experiment = self.base / 'experiment'
        self.root = self.experiment / 'S-re'
        self.raw = self.root / 'S_10k_Sample' / 'T'
        self.raw.mkdir(parents=True)
        (self.raw / 'S_10k_Sample#001.tdms').write_bytes(b'raw')
        anal = self.raw / 'ANAL'
        anal.mkdir()
        (anal / 'A@@2025_0402_0943_36_S_10k_Sample#001.tdms').write_bytes(b'anal')
        (self.raw / 'SS_10k_Sample#001.tdms').write_bytes(b'other')

    def plan(self):
        return rename.plan(self.experiment, 'S-re', 'S', 'S-re')

    def test_nested_rename_and_later_restore(self):
        original = rename.inventory(self.root)
        planned = self.plan()
        self.assertEqual(len(planned['operations']), 3)
        log = rename.execute(planned, self.base / 'history')
        self.assertTrue((self.root / 'S-re_10k_Sample' / 'T' / 'S-re_10k_Sample#001.tdms').exists())
        # Re-read the persistent journal, as after restarting the app.
        rename.restore(rename.restore_plan(log))
        self.assertEqual(rename.inventory(self.root), original)
        self.assertEqual(rename.restore_plan(log)['operations'], [])

    def test_outer_folder_also_renamed(self):
        planned = rename.plan(self.experiment, 'S-re', 'S-re', 'New')
        log = rename.execute(planned, self.base / 'history')
        self.assertTrue((self.experiment / 'New').is_dir())
        rename.restore(rename.restore_plan(log))
        self.assertTrue(self.root.is_dir())

    def test_legacy_history_without_scope_still_restores(self):
        log = rename.execute(self.plan(), self.base / 'history')
        journal = json.loads(log.read_text(encoding='utf-8'))
        del journal['scope']
        rename.save(log, journal)
        self.assertEqual(rename.restore_plan(log)['scope'], 'all')
        rename.restore(rename.restore_plan(log))
        self.assertTrue((self.raw / 'S_10k_Sample#001.tdms').exists())

    def test_conflict_blocks_all_changes(self):
        (self.raw / 'S-re_10k_Sample#001.tdms').write_bytes(b'keep')
        with self.assertRaises(ValueError):
            self.plan()
        self.assertTrue((self.raw / 'S_10k_Sample#001.tdms').exists())

    def test_only_measurement_scope_is_scanned(self):
        measurement = self.raw.parent
        (measurement / 'S_notes.txt').write_text('notes')
        (measurement / 'S_ignore.bin').write_bytes(b'ignore')
        for stocked in (measurement / 'stocked', self.raw / 'stocked'):
            stocked.mkdir()
            (stocked / 'S_saved.tdms').write_bytes(b'stock')
            (stocked / 'unrelated').mkdir()
            (stocked / 'unrelated' / 'S_deep.tdms').write_bytes(b'exclude')
        output = self.raw / 'ANAL' / 'S_results'
        output.mkdir()
        (output / 'S_summary.csv').write_text('anal')
        for excluded in (self.root / 'raw data', self.raw / 'BNAL@S', self.root / 'S_other'):
            excluded.mkdir()
            (excluded / 'S_large.tdms').write_bytes(b'exclude')
        (self.raw / 'S_ignore.txt').write_text('ignore')
        planned = self.plan()
        changed = {Path(s).name for s, _ in planned['operations']}
        self.assertTrue({'S_notes.txt', 'S_saved.tdms', 'S_results', 'S_summary.csv'} <= changed)
        self.assertFalse({'S_ignore.bin', 'S_deep.tdms', 'S_large.tdms', 'S_ignore.txt', 'S_other'} & changed)
        original_scandir = rename.os.scandir
        visited = []
        def scan(path):
            visited.append(str(path))
            return original_scandir(path)
        with patch.object(rename.os, 'scandir', side_effect=scan):
            rename.inventory(self.root)
        self.assertFalse(any('raw data' in p or 'unrelated' in p for p in visited))
        log = rename.execute(planned, self.base / 'history')
        rename.restore(rename.restore_plan(log))
        self.assertTrue((output / 'S_summary.csv').exists())

    def test_bnal_immediate_folder_names_and_restore(self):
        for container in ('BNAL', 'BNAL@S_results'):
            parent = self.raw / container
            child = parent / 'S_output'
            nested = child / 'S_deeper'
            nested.mkdir(parents=True)
            (parent / 'S_direct.tdms').write_bytes(b'unchanged')
            (nested / 'S_nested.csv').write_text('unchanged')
        planned = self.plan()
        sources = {Path(source).name for source, _ in planned['operations']}
        self.assertIn('S_output', sources)
        self.assertIn('BNAL@S_results', sources)
        self.assertFalse({'S_deeper', 'S_direct.tdms', 'S_nested.csv'} & sources)
        legacy = rename.inventory(self.root, 'measurement_names_v2')
        self.assertFalse(any('BNAL' in p for p in legacy))
        log = rename.execute(planned, self.base / 'history')
        changed = self.root / 'S-re_10k_Sample' / 'T' / 'BNAL@S-re_results' / 'S-re_output'
        self.assertTrue((changed / 'S_deeper' / 'S_nested.csv').exists())
        rename.restore(rename.restore_plan(log))
        self.assertTrue((self.raw / 'BNAL@S_results' / 'S_output' / 'S_deeper' / 'S_nested.csv').exists())

    def test_outer_and_inner_rename_then_interrupted_restore(self):
        root = self.experiment / 'S'
        self.root.rename(root)
        original = rename.inventory(root)
        log = rename.execute(rename.plan(self.experiment, 'S', 'S', 'New'), self.base / 'history')
        planned = rename.restore_plan(log)
        real_rename = rename.os.rename
        calls = []
        def fail_second(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise PermissionError('busy')
            real_rename(source, target)
        with patch.object(rename.os, 'rename', side_effect=fail_second):
            with self.assertRaises(PermissionError):
                rename.restore(planned)
        rename.restore(rename.restore_plan(log))
        self.assertEqual(rename.inventory(root), original)

    def test_stale_preview_and_modified_restore_rejected(self):
        planned = self.plan()
        (self.raw / 'extra.tdms').write_text('new')
        with self.assertRaises(ValueError):
            rename.execute(planned, self.base / 'history')
        log = rename.execute(self.plan(), self.base / 'history')
        (self.root / 'S-re_10k_Sample' / 'T' / 'S-re_10k_Sample#001.tdms').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            rename.restore_plan(log)

    def test_partial_failure_is_recoverable(self):
        original = rename.inventory(self.root)
        real_rename = rename.os.rename
        calls = []
        def fail_second(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise PermissionError('busy')
            real_rename(source, target)
        with patch.object(rename.os, 'rename', side_effect=fail_second):
            with self.assertRaises(PermissionError):
                rename.execute(self.plan(), self.base / 'history')
        log = next((self.base / 'history').glob('*.json'))
        rename.restore(rename.restore_plan(log))
        self.assertEqual(rename.inventory(self.root), original)


if __name__ == '__main__':
    unittest.main()
