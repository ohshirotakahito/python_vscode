import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.clustering import analyze, save_result, feature_columns, ALGORITHMS


def events():
    rng = np.random.default_rng(12)
    x = np.vstack([rng.normal(-4, .15, (30, 2)), rng.normal(4, .15, (30, 2))])
    return pd.DataFrame({'signal': x[:, 0], 'duration': x[:, 1],
                         'sample': ['a'] * 30 + ['b'] * 30,
                         'event_id': range(60), 'ex_id': ['1'] * 60})


@pytest.mark.parametrize('algorithm', ALGORITHMS)
def test_two_groups_and_artifacts(algorithm, tmp_path):
    result = analyze(events(), ['signal', 'duration'], algorithm=algorithm, max_k=4)
    data, scores, manifest = result
    assert len(set(data.cluster) - {-1}) == 2
    if algorithm != 'DBSCAN':
        assert manifest['recommended_k'] == 2
    directory = save_result(result, tmp_path)
    assert (directory / 'clustering.png').exists()
    assert len(pd.read_csv(directory / 'events.csv')) == 60
    assert 'event_id' not in feature_columns(events())


def test_manual_override_missing_and_sampling():
    frame = events()
    frame.loc[0, 'signal'] = np.inf
    frame['empty'] = np.nan
    result = analyze(frame, ['signal', 'duration', 'empty'], k=3, max_k=4, max_events=40)
    assert result[2]['chosen_k'] == 3
    assert result[2]['analyzed_events'] == 40
    assert result[2]['excluded_features'] == ['empty']
    again = analyze(frame, ['signal', 'duration', 'empty'], k=3, max_k=4, max_events=40)
    assert result[0].source_row.tolist() == again[0].source_row.tolist()


def test_invalid_input_and_all_noise():
    with pytest.raises(ValueError):
        analyze(events(), ['event_id'])
    with pytest.raises(ValueError):
        analyze(events(), ['signal'], k=60)
    result = analyze(events(), ['signal', 'duration'], algorithm='DBSCAN', eps=1e-9)
    assert set(result[0].cluster) == {-1}
    assert np.isnan(result[2]['final']['silhouette'])
