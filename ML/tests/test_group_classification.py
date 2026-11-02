"""Explicit group labels and independent source-sample evaluation."""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import training_core as core
from common import trained_models


@pytest.fixture(autouse=True)
def reset_settings():
    core.reset()
    yield
    core.reset()


def grouped_data(n_groups=3, n_sources=4):
    groups = {f'任意群{g}': [f'S{g}_{i}' for i in range(n_sources)] for g in range(n_groups)}
    rng = np.random.default_rng(0)
    frame = pd.concat([pd.DataFrame({'sample': source, 'a': rng.normal(g, .1, 20)})
                       for g, members in enumerate(groups.values()) for source in members], ignore_index=True)
    core.configure(classification_mode='group', sample_groups=groups)
    return frame, groups


def test_explicit_labels_preserve_source_and_input():
    frame, groups = grouped_data()
    prepared = core.prepare_dataset(frame)
    assert 'original_sample' not in frame
    assert prepared.original_sample.tolist() == frame['sample'].tolist()
    assert set(prepared['sample']) == set(groups)
    assert core.normalize_groups([('', ['S1']), ('', ['S2'])]) == {'Group A': ['S1'], 'Group B': ['S2']}


@pytest.mark.parametrize('groups', [ [('X', ['S1']), ('Y', ['S1'])],
                                   [('X', []), ('Y', ['S2'])],
                                   [('X', ['S1']), ('X', ['S2'])],
                                   [('X', ['S1'])] ])
def test_invalid_definitions_rejected(groups):
    with pytest.raises(ValueError):
        core.normalize_groups(groups)


def test_missing_and_unassigned_data_rejected():
    frame, _ = grouped_data()
    with pytest.raises(ValueError):
        core.prepare_dataset(frame[frame['sample'] != 'S0_0'])
    frame.loc[0, 'sample'] = 'unassigned'
    with pytest.raises(ValueError):
        core.prepare_dataset(frame)


@pytest.mark.parametrize('n_groups', [2, 3, 4])
def test_source_split_and_all_cv_folds_are_disjoint(n_groups):
    frame, groups = grouped_data(n_groups=n_groups)
    core.configure(split_mode='sample')
    prepared = core.prepare_dataset(frame)
    split = core.make_split(prepared)
    train, test = prepared.iloc[split['train']], prepared.iloc[split['test']]
    assert not set(train.original_sample) & set(test.original_sample)
    assert set(train['sample']) == set(test['sample']) == set(groups)
    for a, b in split['cv']:
        left, right = train.iloc[a], train.iloc[b]
        assert not set(left.original_sample) & set(right.original_sample)
        assert set(left['sample']) == set(right['sample']) == set(groups)
    again = core.make_split(prepared)
    np.testing.assert_array_equal(split['test'], again['test'])


def test_two_sources_allow_event_evaluation_but_reject_nested_source_evaluation():
    frame, _ = grouped_data(n_sources=2)
    prepared = core.prepare_dataset(frame)
    assert len(core.make_split(prepared)['test']) > 0
    core.configure(split_mode='sample')
    with pytest.raises(ValueError, match='3サンプル'):
        core.make_split(prepared)


def test_group_training_saved_model_and_prediction_roundtrip(tmp_path):
    frame, groups = grouped_data()
    core.configure(split_mode='sample', max_rounds=3, n_cv_folds=2, early_stopping_rounds=2)
    prepared = core.prepare_dataset(frame)
    split = core.make_split(prepared)
    result = core.train_classifier(prepared, ['a'], 'xgboost', split)
    core.save_training_details({'model': result}, tmp_path)
    config = core.manifest_config(split, result)
    info = trained_models.save_model(tmp_path, result, 'xgboost', 'rmb', list(frame['sample'].unique()), details={'training': config})
    assert info['sample_groups'] == groups
    assert info['class_names'] == sorted(groups)
    definition = json.loads((tmp_path / 'classification_definition.json').read_text(encoding='utf-8'))
    assert definition['sample_groups'] == groups
    scores = pd.read_csv(tmp_path / 'source_sample_scores_model.csv')
    assert set(scores.target_label) == set(groups)
    assert scores.events.sum() == len(split['test'])
    loaded = trained_models.LoadedModel(tmp_path)
    np.testing.assert_allclose(loaded.predict_proba(result['X_test_raw']),
                               core.predict_proba('xgboost', result['booster'], result['X_test'], ['a'], result['selected_rounds']), rtol=1e-5)
    from common import eval_viz
    from unittest.mock import patch
    with patch.object(eval_viz, 'plot_confusion_matrices'):
        eval_viz.conmtx(result['y_test'], result['y_pred'], result['le'], save_dir=tmp_path,
                        classification_config=core.current_settings())
    report = (tmp_path / 'classification_report.txt').read_text(encoding='utf-8')
    assert '分類単位: グループ別' in report
    assert '評価方法: 元サンプル単位' in report
    for label, members in groups.items():
        assert f'{label}: {", ".join(members)}' in report


def test_different_memberships_do_not_share_model_group():
    base = {'feature_set': 'rmb', 'class_names': ['Group A', 'Group B'], 'classification_mode': 'group'}
    a = dict(base, sample_groups={'Group A': ['S1'], 'Group B': ['S2']})
    b = dict(base, sample_groups={'Group A': ['S2'], 'Group B': ['S1']})
    assert trained_models.combination_key(a) != trained_models.combination_key(b)
