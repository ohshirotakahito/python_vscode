"""Training must select the same whole files as the histogram rule engine."""
from pathlib import Path
import sys
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.file_review import file_statistics, filter_files, selected_events
from common.training_file_filter import _selection, prepare_selection, filter_training_rows


def events():
    return pd.DataFrame({
        'sample': ['A'] * 4 + ['B'] * 2,
        'sample_name': ['same', 'same', 'same', 'other', 'same', 'same'],
        'ex_id': ['01', '02', '02', '01', '01', '01'],
        'run_id': ['v1'] * 6, 'experiment': ['experiment'] * 6,
        'condition_id': ['condition'] * 6, 'extracted_at': ['now'] * 6,
        'signal': [10., 20., 30., 5., 40., 50.],
        'event_id': [1, 2, 3, 4, 1, 2],
    })


def test_matches_histogram_and_preserves_row_order_and_file_experiment_pairs():
    frame = events()
    rules = [('events', '>=', '2'), ('signal.mean', '>=', '20')]
    versions = {'A': 'v1', 'B': 'v1'}
    with patch('common.file_review.load_events', return_value=frame) as load:
        selection = prepare_selection('rmc', ['A', 'B'], versions, rules)
        assert load.call_args.args[3] == versions
    expected = selected_events(frame, filter_files(file_statistics(frame), rules))
    token = _selection.set(selection)
    try:
        for sample in ['A', 'B']:
            actual = filter_training_rows(frame[frame['sample'].eq(sample)], sample)
            assert actual['event_id'].tolist() == expected[expected['sample'].eq(sample)]['event_id'].tolist()
            traditional = frame[frame['sample'].eq(sample)].rename(
                columns={'sample_name': 'file', 'ex_id': 'Ex_ID'})
            assert filter_training_rows(traditional, sample, traditional=True)['event_id'].tolist() == actual['event_id'].tolist()
        assert selection['counts']['A']['selected_events'] == 2
    finally:
        _selection.reset(token)
    assert filter_training_rows(frame, 'A') is frame


def test_rejects_class_removed_by_filter():
    with patch('common.file_review.load_events', return_value=events()):
        with pytest.raises(ValueError, match='A'):
            prepare_selection('rmc', ['A', 'B'], {}, [('signal.mean', '>=', '40')])


def test_cache_selection_distinguishes_equal_event_counts():
    with patch('common.file_review.load_events', return_value=events()):
        first = prepare_selection('rmc', ['A'], {}, [('ex_id', '=', '01')])
        second = prepare_selection('rmc', ['A'], {}, [('ex_id', '=', '02')])
    assert first['counts']['A']['selected_events'] == second['counts']['A']['selected_events']
    assert first['keys'] != second['keys']


def test_training_context_is_restored_on_failure_and_manifest_records_rules(tmp_path):
    import json
    from common import paths
    from common.feature_store import versioned_training
    from common.training_file_filter import selection_manifest

    record = {'sample': 'A', 'run_id': 'pinned', 'outputs': []}
    selection = {'rules': [('events', '>=', '2')], 'keys': {'A': [('same', '02')]},
                 'counts': {'A': {'selected_files': 1, 'selected_events': 2}}}

    @versioned_training('rmc')
    def train(samples):
        assert selection_manifest() == selection
        manifest = paths.write_run_manifest(tmp_path, 'test', samples)
        saved = json.loads(manifest.read_text(encoding='utf-8'))
        assert saved['training_file_filter']['rules'] == [['events', '>=', '2']]
        assert saved['training_file_filter']['counts']['A']['selected_events'] == 2
        raise RuntimeError('cancelled')

    with patch('common.feature_store.resolve_version', return_value=record), \
            patch('common.training_file_filter.prepare_selection', return_value=selection) as prepare:
        with pytest.raises(RuntimeError, match='cancelled'):
            train(['A'], file_filter_rules=selection['rules'])
        assert prepare.call_args.args[2] == {'A': record}
    assert selection_manifest() == {}
