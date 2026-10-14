"""Extraction history, backward compatibility, training pinning and cache isolation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from common import paths
from common.feature_store import (
    MANIFEST, fingerprint, list_versions, resolve_version, sample_directory,
    training_inputs, versioned_training, inspection_inputs,
)
from common.extract_catalog import load_history
from lib.catalog import list_available_samples
import extract_features_traditional as traditional
import extract_features_tsfresh as tsfresh_extract


class FeatureStoreTests(unittest.TestCase):
    def test_empty_sample_keeps_reasons_and_continues_next_sample(self):
        row = [0, '002_10k_Sample#001', '20250325_1044_test', .54,
               '002', 10, 20, 20, 0, 20, 0] + list(range(12))
        with patch.object(traditional, 'tdms_checker', return_value=1), patch.object(
                traditional, 'apick', side_effect=[([], [], 0), ([row], [], 1)]):
            traditional.run_extraction(['001', '002'], output_dir=self.root,
                files_by_sample={'001': [str(self.source)], '002': [str(self.source)]})
        manifest = next((self.root / '001').glob('*/extraction_manifest.json'))
        failed = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['event_count'], 0)
        self.assertEqual(failed['warnings'][0]['reason'], '抽出条件を満たすイベントなし')
        self.assertEqual(list_versions('rmb', '001'), [])
        self.assertEqual(resolve_version('rmb', '002')['event_count'], 1)

    def test_inspection_pins_old_and_legacy_versions_and_restores_on_error(self):
        legacy = self.root / '001_10k_Sample_ANAL_rmb.npy'
        np.save(legacy, np.ones((2, 22)))
        self.extract(value=20)
        old = resolve_version('rmb', '001')
        self.extract(value=40)
        latest = resolve_version('rmb', '001')
        with inspection_inputs('rmb', ['001'], self.root, {'001': old['run_id']}) as selected:
            self.assertEqual(selected[0]['run_id'], old['run_id'])
            self.assertEqual(sample_directory(self.root, '001'), Path(old['directory']))
        self.assertEqual(sample_directory(self.root, '001'), Path(latest['directory']))
        with self.assertRaisesRegex(RuntimeError, 'stop'):
            with inspection_inputs('rmb', ['001'], self.root, {'001': 'legacy'}):
                self.assertEqual(sample_directory(self.root, '001'), self.root)
                raise RuntimeError('stop')
        self.assertEqual(sample_directory(self.root, '001'), Path(latest['directory']))
        self.assertEqual(training_inputs(), [])
        with self.assertRaises(FileNotFoundError):
            with inspection_inputs('rmb', ['001'], self.root, {'001': 'missing'}):
                self.fail('Missing selections must not fall back to latest')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'rmb'
        self.root.mkdir()
        self.source = Path(self.temp.name) / 'source.tdms'
        self.source.write_bytes(b'fake TDMS input')
        self.patch_root = patch('common.paths.feature_dir', return_value=self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def extract(self, sample='001', value=20, fail=False, **kwargs):
        row = [0, f'{sample}_10k_Sample#001', '20250325_1044_test', 0.54,
               sample, 10, value, 20, 0, 20, 0] + list(range(12))
        with patch.object(traditional, 'tdms_checker', return_value=1), patch.object(
                traditional, 'apick', side_effect=RuntimeError('interrupted') if fail else None,
                return_value=([row], [], 1)):
            return traditional.run_extraction([sample], output_dir=self.root,
                    files_by_sample={sample: [str(self.source)]}, **kwargs)

    def test_reextract_does_not_merge_or_overwrite_previous_or_legacy(self):
        legacy = self.root / '001_10k_Sample_ANAL_rmb.npy'
        np.save(legacy, np.ones((2, 22)))
        original = legacy.read_bytes()
        self.extract(value=20)
        first = resolve_version('rmb', '001')
        old_output = Path(first['outputs'][0]['path'])
        old_bytes = old_output.read_bytes()
        self.extract(value=40)
        versions = list_versions('rmb', '001')
        self.assertEqual(len(versions), 3)
        self.assertEqual(versions[-1]['run_id'], 'legacy')
        self.assertEqual(versions[0]['event_count'], 1)
        self.assertNotEqual(versions[0]['run_id'], first['run_id'])
        self.assertEqual(old_output.read_bytes(), old_bytes)
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(np.load(versions[0]['outputs'][0]['path'], allow_pickle=True)[0, 6], '40')
        self.assertEqual(resolve_version('rmb', '001', first['run_id'])['directory'], first['directory'])
        self.assertEqual(list_available_samples('rmb'), ['001'])
        runs, files = load_history('rmb')
        self.assertEqual(len(runs), 2)
        self.assertEqual(files['sample'].tolist(), ['001', '001'])
        self.assertEqual(set(runs.run_id), {v['run_id'] for v in versions[:-1]})
        self.assertTrue(versions[0]['extracted_at'].endswith('+09:00'))

    def test_sample_without_selected_anal_is_skipped_and_next_sample_runs(self):
        row = [0, '001_10k_Sample#001', '20250325_1044_test', .54,
               '001', 10, 20, 20, 0, 20, 0] + list(range(12))
        with patch.object(traditional, 'tdms_checker', return_value=1), patch.object(
                traditional, 'apick', return_value=([row], [], 1)):
            result = traditional.run_extraction(['empty', '001'], output_dir=self.root,
                files_by_sample={'empty': [], '001': [str(self.source)]})
        self.assertEqual(result, self.root)
        self.assertFalse((self.root / 'empty').exists())
        self.assertEqual(resolve_version('rmb', '001')['event_count'], 1)

    def test_failure_excluded_and_resume_requires_identical_input(self):
        self.extract()
        first = resolve_version('rmb', '001')
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            self.extract(fail=True)
        manifests = list((self.root / '001').glob(f'*/{MANIFEST}'))
        failed = next(json.loads(p.read_text()) for p in manifests
                      if json.loads(p.read_text())['status'] == 'failed')
        self.assertEqual(resolve_version('rmb', '001')['run_id'], first['run_id'])
        self.extract(resume_run_ids={'001': failed['run_id']})
        self.assertEqual(resolve_version('rmb', '001')['run_id'], failed['run_id'])
        with self.assertRaisesRegex(ValueError, 'cannot resume'):
            self.extract(resume_run_ids={'001': failed['run_id']})

    def test_failed_resume_with_changed_input_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.extract(fail=True)
        manifest = next((self.root / '001').glob(f'*/{MANIFEST}'))
        run_id = json.loads(manifest.read_text())['run_id']
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'different inputs'):
            self.extract(resume_run_ids={'001': run_id})

    def test_explicit_experiment_and_condition_filters(self):
        self.extract(ex='group-A', extraction_conditions={'note': 'condition A'})
        first = resolve_version('rmb', '001')
        self.extract(ex='group-B', extraction_conditions={'note': 'condition B'})
        self.assertEqual(resolve_version('rmb', '001', experiment='group-A')['run_id'], first['run_id'])
        self.assertEqual(resolve_version('rmb', '001', condition_id=first['condition_id'])['run_id'], first['run_id'])
        with self.assertRaises(FileNotFoundError):
            resolve_version('rmb', '001', experiment='unknown')
        with self.assertRaises(FileNotFoundError):
            resolve_version('rmb', '001', 'missing-version')

    def test_training_pins_versions_and_records_manifest_and_restores_context(self):
        self.extract()
        first = resolve_version('rmb', '001')

        @versioned_training('rmb')
        def train(smns):
            self.extract(value=30)
            self.assertEqual(sample_directory(self.root, '001'), Path(first['directory']))
            run = Path(self.temp.name) / 'result'
            run.mkdir()
            manifest = paths.write_run_manifest(run, 'test', smns)
            return json.loads(manifest.read_text())

        manifest = train(['001'])
        self.assertEqual(manifest['feature_inputs'][0]['run_id'], first['run_id'])
        self.assertEqual(training_inputs(), [])
        self.assertNotEqual(sample_directory(self.root, '001'), Path(first['directory']))

        @versioned_training('rmb')
        def failing(smns):
            raise RuntimeError('failed')

        with self.assertRaises(RuntimeError):
            failing(['001'])
        self.assertEqual(training_inputs(), [])

    def test_legacy_only_and_changed_outputs(self):
        legacy = self.root / '001_10k_Sample_ANAL_rmb.npy'
        np.save(legacy, np.ones((1, 22)))
        self.assertEqual(resolve_version('rmb', '001')['run_id'], 'legacy')
        self.extract()
        version = resolve_version('rmb', '001')
        Path(version['outputs'][0]['path']).write_bytes(b'changed output')
        self.assertEqual(resolve_version('rmb', '001')['run_id'], 'legacy')
        with self.assertRaises(FileNotFoundError):
            resolve_version('rmb', '001', version['run_id'])

    def test_tsfresh_pair_and_cache_isolation(self):
        root = Path(self.temp.name) / 'rmc'
        def collect(*args, file_log, **kwargs):
            file_log.append((str(self.source), 1, 1))
            row = [0, '001_10k_Sample#001', '20250325_1044_test', 0.54,
                   '001', 10, 20, 20, 0, 20, 0] + list(range(12))
            return [row], [{'id': 0, 'time': 0, 'value': 0.1}, {'id': 0, 'time': 1, 'value': 0.2}]
        with patch.object(tsfresh_extract, 'collect_events_for_sample', side_effect=collect):
            tsfresh_extract.run_extraction(['001'], output_dir=root,
                                          files_by_sample={'001': [str(self.source)]})
            first = resolve_version('rmc', '001', root=root)
            tsfresh_extract.run_extraction(['001'], output_dir=root,
                                          files_by_sample={'001': [str(self.source)]})
        second = resolve_version('rmc', '001', root=root)
        from common import data_pipeline as dp
        self.assertEqual(dp._sample_paths('001', root)[0].parent, Path(second['directory']))
        self.assertNotEqual(dp.get_sample_cache_path('001', 1, Path(first['outputs'][1]['path']), {'mean': None})[0],
                            dp.get_sample_cache_path('001', 1, Path(second['outputs'][1]['path']), {'mean': None})[0])
        self.assertNotEqual(dp._sample_cache_key('001', 1, 'same', {'mean': None}),
                            dp._sample_cache_key('001', 1, 'same', {'median': None}))

    def test_partial_read_failure_not_selected(self):
        with patch.object(traditional, 'tdms_checker', return_value=3):
            with self.assertRaises(ValueError):
                traditional.run_extraction(['001'], output_dir=self.root,
                                           files_by_sample={'001': [str(self.source)]})
        self.assertEqual(list_versions('rmb', '001'), [])

    def test_successful_output_with_one_failed_source_keeps_result_and_warning(self):
        other = self.source.with_name('bad.tdms')
        other.write_bytes(b'bad file')
        row = [0, '001_10k_Sample#001', '20250325_1044_test', 0.54,
               '001', 10, 20, 20, 0, 20, 0] + list(range(12))
        with patch.object(traditional, 'tdms_checker', side_effect=[3, 1]), \
                patch.object(traditional, 'apick', return_value=([row], [], 1)):
            traditional.run_extraction(['001'], output_dir=self.root,
                files_by_sample={'001': [str(self.source), str(other)]})
        version = resolve_version('rmb', '001')
        self.assertEqual(version['status'], 'complete')
        self.assertEqual(version['event_count'], 1)
        self.assertEqual(version['warnings'], [{'path': str(other), 'reason': 'Signal列なし'}])

    def test_training_explicit_old_version_and_legacy(self):
        legacy = self.root / '001_10k_Sample_ANAL_rmb.npy'
        np.save(legacy, np.ones((1, 22)))
        self.extract()
        first = resolve_version('rmb', '001')
        self.extract(value=30)

        @versioned_training('rmb')
        def train(smns):
            return str(sample_directory(self.root, '001'))

        self.assertEqual(train(['001'], feature_versions={'001': first['run_id']}), first['directory'])
        self.assertEqual(train(['001'], feature_versions={'001': 'legacy'}), str(self.root.resolve()))

    def test_invalid_manifest_is_ignored(self):
        self.extract()
        original = resolve_version('rmb', '001')
        malformed = self.root / '001' / 'invalid'
        malformed.mkdir()
        (malformed / MANIFEST).write_text('[]')
        self.assertEqual(resolve_version('rmb', '001')['run_id'], original['run_id'])

    def test_recovery_only_changes_metadata_for_old_partial_read_rejection(self):
        from common.feature_store import recover_partial_version
        self.extract()
        version = resolve_version('rmb', '001')
        directory = Path(version['directory'])
        output = Path(version['outputs'][0]['path'])
        before = output.read_bytes()
        manifest = directory / MANIFEST
        record = json.loads(manifest.read_text())
        record.update(status='failed', error='ValueError: 001: some input files could not be read; result excluded from latest')
        manifest.write_text(json.dumps(record), encoding='utf-8')
        recovered = recover_partial_version(directory)
        self.assertEqual(recovered['status'], 'complete')
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(resolve_version('rmb', '001')['run_id'], version['run_id'])
        with self.assertRaises(ValueError):
            recover_partial_version(directory)

    def test_chronos_versions_and_model_parameters(self):
        import extract_features_chronos as chronos_extract
        root = Path(self.temp.name) / 'rmd'
        row = [0, '001_10k_Sample#001', '20250325_1044_test', 0.54,
               '001', 10, 20, 20, 0, 20, 0] + list(range(12))
        long = [{'id': 0, 'time': 0, 'value': 0.1}, {'id': 0, 'time': 1, 'value': 0.2}]
        with patch.object(chronos_extract, 'tdmslist_files', return_value=[self.source.name]), \
                patch.object(chronos_extract, 'tdms_checker', return_value=1), \
                patch.object(chronos_extract, 'apick', return_value=([row], long, 1)), \
                patch.object(chronos_extract, 'extract_chronos_embeddings', return_value=
                             pd.DataFrame({'event_id': [0], 'chronos_emb_0': [0.1]})) as embeddings:
            chronos_extract.run_extraction(['001'], output_dir=root,
                files_by_sample={'001': [str(self.source)]}, chronos_model='test-model')
            first = resolve_version('rmd', '001', root=root)
            chronos_extract.run_extraction(['001'], output_dir=root,
                files_by_sample={'001': [str(self.source)]}, chronos_model='other-model')
        second = resolve_version('rmd', '001', root=root)
        self.assertNotEqual(first['condition_id'], second['condition_id'])
        self.assertEqual(second['conditions']['chronos_model'], 'other-model')
        self.assertEqual(second['event_count'], 1)
        self.assertEqual(embeddings.call_args.kwargs['model_name'], 'other-model')
        self.assertEqual(len(list_versions('rmd', '001', root=root)), 2)


if __name__ == '__main__':
    unittest.main()
