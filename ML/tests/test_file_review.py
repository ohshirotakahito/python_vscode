import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.file_review import add_signal_features, comparison, event_histogram, histogram_markers, file_statistics, filter_files, selected_events


def events():
    return pd.DataFrame({
        'sample': ['s'] * 4, 'sample_name': ['a', 'b', 'b', 'b'],
        'ex_id': ['ex1', 'ex2', 'ex2', 'ex2'], 'run_id': ['r'] * 4,
        'experiment': ['exp'] * 4, 'condition_id': ['c'] * 4,
        'extracted_at': ['now'] * 4, 'signal': [10., 0., 0., 0.],
        'duration': [None, 1., 2., 3.]})


def test_pooled_statistics_and_file_filters():
    frame = events()
    files = file_statistics(frame)
    chosen = filter_files(files, [('events', '>=', '2'), ('ex_id', '=', 'ex2')])
    subset = selected_events(frame, chosen)
    stats = comparison(frame, subset).set_index(['特徴量', '統計'])
    assert len(chosen) == 1
    assert len(subset) == 3
    assert stats.loc[('signal', 'mean'), '全ファイル'] == 2.5
    assert stats.loc[('signal', 'mean'), '絞り込み後'] == 0
    assert stats.loc[('duration', 'count'), '全ファイル'] == 3


def test_empty_subset_and_invalid_limit():
    frame = events()
    files = file_statistics(frame)
    chosen = filter_files(files, [('events', '>=', '100')])
    stats = comparison(frame, selected_events(frame, chosen))
    assert stats.loc[stats['統計'].eq('count'), '絞り込み後'].eq(0).all()
    assert stats.loc[stats['統計'].eq('mean'), '絞り込み後'].isna().all()
    with pytest.raises(ValueError):
        filter_files(files, [('events', '>=', 'nan')])


def test_same_filename_in_different_experiments():
    frame = events()
    frame['sample_name'] = 'same'
    files = file_statistics(frame)
    chosen = filter_files(files, [('ex_id', '=', 'ex1')])
    assert len(selected_events(frame, chosen)) == 1


def test_numeric_equality_and_blank_conditions():
    files = file_statistics(events())
    assert len(filter_files(files, [('signal.mean', '=', '10')])) == 1
    for operator in ['含む', '=', '>=']:
        with pytest.raises(ValueError):
            filter_files(files, [('events', operator, '  ')])


def test_histogram_pools_events_with_shared_bins():
    frame = events()
    files = filter_files(file_statistics(frame), [('events', '>=', '2')])
    subset = selected_events(frame, files)
    edges, all_counts, chosen_counts, total, chosen = event_histogram(frame, subset, 'signal', bins=2)
    assert edges.tolist() == [0, 5, 10]
    assert all_counts.tolist() == [3, 1]
    assert chosen_counts.tolist() == [3, 0]
    assert (total, chosen) == (4, 3)
    _, all_pct, chosen_pct, _, _ = event_histogram(frame, subset, 'signal', bins=2, percent=True)
    assert all_pct.tolist() == [75, 25]
    assert chosen_pct.tolist() == [100, 0]


def test_histogram_missing_values_empty_selection_and_all_selected():
    frame = events()
    _, all_counts, chosen_counts, total, chosen = event_histogram(frame, frame, 'duration')
    assert total == chosen == 3
    assert (all_counts == chosen_counts).all()
    _, _, chosen_counts, _, chosen = event_histogram(frame, frame.iloc[:0], 'signal', percent=True)
    assert chosen == 0
    assert chosen_counts.sum() == 0


def test_histogram_manual_range_preserves_population_denominator():
    frame = events()
    edges, counts, _, total, _ = event_histogram(frame, frame, 'signal', bins=2,
                                               value_range=(0, 2), percent=True)
    assert edges.tolist() == [0, 1, 2]
    assert counts.tolist() == [75, 0]
    assert total == 4
    for limits in [(2, 1), (1, 1), (0, float('inf'))]:
        with pytest.raises(ValueError):
            event_histogram(frame, frame, 'signal', value_range=limits)


def test_histogram_markers_use_full_values_and_displayed_modal_bin():
    frame = pd.DataFrame({'signal': [0., 1., 2., 3., 100., None, float('inf')]})
    markers = histogram_markers(frame, 'signal', [0, 2, 4])
    assert markers == {'Mean': 21.2, 'Mode*': 1., 'Q1': 1., 'Median': 2., 'Q3': 3.}
    assert histogram_markers(frame.iloc[:0], 'signal', [0, 2, 4]) == {}


def test_derived_signals_match_training_definitions():
    frame = events()
    frame['baseline'] = [100., 200., None, float('inf')]
    derived = add_signal_features(frame)
    assert derived['relative_signal'].tolist() == frame['signal'].tolist()
    assert derived['absolute_signal'].iloc[:2].tolist() == [110., 200.]
    assert derived['absolute_signal'].iloc[2:].isna().all()
    assert 'absolute_signal.mean' in file_statistics(derived)
    assert 'absolute_signal' not in frame


def test_histogram_normalized_to_ten_thousand():
    frame = events()
    subset = frame.iloc[1:]
    _, all_counts, selected_counts, _, _ = event_histogram(frame, subset, 'signal', bins=2, normalize_to=10000)
    assert all_counts.tolist() == [7500, 2500]
    assert selected_counts.tolist() == [10000, 0]
    _, _, empty_counts, _, _ = event_histogram(frame, frame.iloc[:0], 'signal', normalize_to=10000)
    assert empty_counts.sum() == 0
