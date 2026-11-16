"""Manual allowlists must match extraction names and survive training/model export."""
from pathlib import Path
import importlib
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import matplotlib

matplotlib.use('Agg')

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parents[1] / 'app_tk')]
from common.training_features import feature_catalog, select_feature_groups, RMC_FEATURES, RMB_FEATURES
from common import training_core


@pytest.fixture(autouse=True)
def reset_settings():
    training_core.reset()
    yield
    training_core.reset()


@pytest.mark.parametrize('mode', ['minimal', 'curated', 'curated_fft_abs', 'curated_fft', 'extended', 'efficient'])
def test_catalog_names_round_trip_through_tsfresh(mode):
    from tsfresh.feature_extraction.settings import from_columns
    columns = feature_catalog('rmc', mode)
    assert columns[:15] == RMC_FEATURES
    assert len(set(columns)) == len(columns)
    parsed = from_columns(columns[15:])
    assert set(parsed) == {'value'}
    assert 'mean' in parsed['value']
    assert not set(columns) & {'sample', 'event_id', 'ex_id', 'baseline', 'distance'}


def test_catalog_matches_actual_extraction():
    from tsfresh import extract_features
    from common.fc_parameters import fc_parameters_for
    frame = pd.DataFrame({'id': [1] * 32, 'time': np.arange(32), 'value': np.sin(np.arange(32))})
    result = extract_features(frame, column_id='id', column_sort='time', column_value='value',
                              default_fc_parameters=fc_parameters_for('minimal'), n_jobs=0,
                              disable_progressbar=True)
    assert set(result.columns) == set(feature_catalog('rmc')[15:])
    assert feature_catalog('rmb') == RMB_FEATURES


def test_manual_selection_can_disable_either_feature_group():
    meta, fresh = ['duration', 'wave_0'], ['value__mean', 'value__median']
    assert select_feature_groups(meta, fresh) == (meta, fresh)
    assert select_feature_groups(meta, fresh, ['duration']) == (['duration'], [])
    assert select_feature_groups(meta, fresh, ['value__mean']) == ([], ['value__mean'])
    with pytest.raises(ValueError, match='ありません'):
        select_feature_groups(meta, fresh, ['sample'])
    for invalid in ([], ['duration', 'duration'], 'duration'):
        with pytest.raises(ValueError):
            training_core.configure(selected_features=invalid)


@pytest.mark.parametrize('algorithm', ['xgboost', 'lightgbm', 'randomforest'])
def test_only_allowed_columns_reach_training_and_saved_model(tmp_path, algorithm):
    from common.trained_models import save_model, load_model
    rng = np.random.default_rng(7)
    frame = pd.DataFrame({'sample': ['A'] * 40 + ['B'] * 40,
                          'signal': np.r_[rng.normal(0, .1, 40), rng.normal(2, .1, 40)],
                          'excluded': rng.normal(size=80)})
    training_core.configure(selected_features=['signal'], n_cv_folds=2, max_rounds=8,
                             balance='none')
    split = training_core.make_split(frame)
    result = training_core.train_classifier(frame, ['signal', 'excluded'], algorithm, split, n_jobs=1)
    assert result['feature_columns'] == ['signal']
    assert result['X_test'].shape[1] == 1
    details = training_core.manifest_config(split, result)
    assert details['requested_features'] == details['used_features'] == ['signal']
    save_model(tmp_path, result, algorithm, 'rmc', ['A', 'B'], details=details)
    assert (tmp_path / 'feature_columns.txt').read_text(encoding='utf-8') == 'signal'
    model = load_model(tmp_path)
    assert model.feature_columns == ['signal']
    # An excluded column is not required by the saved model at prediction time.
    assert len(model.predict_proba(frame[['signal']])) == len(frame)


def test_training_rejects_empty_intersection_before_fitting():
    training_core.configure(selected_features=['signal'])
    with pytest.raises(ValueError, match='特徴量がありません'):
        training_core.train_classifier(pd.DataFrame(), ['excluded'], 'xgboost', {})


def test_queue_keeps_feature_selection_per_job():
    from training_queue import TrainingQueue
    selected = ['duration']
    queue = TrainingQueue()
    queue.add({'training_settings': {'selected_features': selected}})
    selected.append('wave_0')
    queue.add({'training_settings': {'selected_features': selected}})
    assert [j['settings']['training_settings']['selected_features'] for j in queue.rows()] == [
        ['duration'], ['duration', 'wave_0']]


@pytest.mark.parametrize('module_name', ['train_xgboost_tsfresh', 'train_lightgbm_tsfresh'])
@pytest.mark.parametrize('selected', [['duration'], ['value__mean'], ['duration', 'value__mean']])
def test_pipeline_applies_allowlist_before_automatic_selection(tmp_path, module_name, selected):
    module = importlib.import_module(module_name)
    training_core.configure(selected_features=selected)
    frame = pd.DataFrame({'sample': ['A', 'B']})
    class ReachedComparison(Exception):
        pass
    with patch.object(module.paths, 'new_run', return_value=(tmp_path, 'test')), \
            patch.object(module, 'build_combined_dataset', return_value=(frame, ['value__mean', 'value__median'])), \
            patch.object(training_core, 'prepare_dataset', return_value=frame), \
            patch.object(training_core, 'make_split', return_value={'train': [0]}), \
            patch.object(module, 'USE_TSFRESH_FEATURE_SELECTION', True), \
            patch.object(module, 'apply_tsfresh_feature_selection', side_effect=lambda df, cols, **kw: cols) as auto, \
            patch.object(module, 'compare_feature_sets', side_effect=ReachedComparison) as compare:
        with pytest.raises(ReachedComparison):
            module.run_analysis.__wrapped__(['A', 'B'])
    assert compare.call_args.args[1] == (['duration'] if 'duration' in selected else [])
    assert compare.call_args.args[2] == (['value__mean'] if 'value__mean' in selected else [])
    if 'value__mean' in selected:
        assert auto.call_args.args[1] == ['value__mean']
        assert len(auto.call_args.args[0]) == 1  # Fit only on training rows.
    else:
        auto.assert_not_called()


@pytest.mark.parametrize('meta,fresh', [(['duration'], []), ([], ['value__mean'])])
def test_comparison_skips_empty_feature_groups(tmp_path, meta, fresh):
    from common.ml_analysis import compare_feature_sets
    def train(frame, columns, **kwargs):
        assert columns
        return dict(macro_f1=1., per_class_f1={'A': 1., 'B': 1.}, y_test=[0, 1], y_pred=[0, 1], le=None)
    with patch('common.eval_viz.conmtx'), patch('matplotlib.pyplot.show'):
        results, comparison, names = compare_feature_sets(pd.DataFrame(), meta, fresh, train, output_dir=tmp_path)
    assert names['combined'] in results
    assert len(results) == 2
    assert comparison['n_features'].eq(1).all()


def test_single_feature_pca_can_be_saved(tmp_path):
    from common.ml_analysis import plot_dim_reduction
    frame = pd.DataFrame({'sample': ['A'] * 5 + ['B'] * 5, 'duration': np.arange(10)})
    output = tmp_path / 'pca.png'
    result = plot_dim_reduction(frame, ['duration'], save_path=output)
    assert output.is_file()
    assert result['Dim2'].eq(0).all()
