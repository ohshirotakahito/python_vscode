"""The selectable RF pipeline must produce browsable runs and reusable predictions."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import matplotlib
matplotlib.use('Agg')
import numpy as np
import pandas as pd

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parents[1] / 'app'),
               str(Path(__file__).resolve().parents[1] / 'app_tk')]
from common import paths, training_core
from common.trained_models import list_models, load_model
from tab_train import PIPELINES
import train_randomforest_tsfresh as pipeline


def test_rmc_rf_trains_exports_and_reloads_with_manual_features(tmp_path):
    assert PIPELINES['rmc + RandomForest（tsfresh併用）']['module'] == 'train_randomforest_tsfresh'
    rng = np.random.default_rng(2)
    labels = np.repeat(['A', 'B', 'C'], 35)
    signal = np.repeat([0., 3., 6.], 35) + rng.normal(0, .1, len(labels))
    frame = pd.DataFrame({'sample': labels, 'duration': signal,
                          'value__mean': signal + rng.normal(0, .01, len(labels)),
                          'unused': rng.normal(size=len(labels))})
    training_core.reset()
    training_core.configure(selected_features=['duration', 'value__mean'], max_rounds=12,
                             n_cv_folds=2, tune=True, n_trials=1, balance='weight')
    progress = []
    try:
        with patch.object(paths, 'RESULTS_ROOT', tmp_path), \
                patch.object(pipeline, 'N_JOBS', 1), \
                patch.object(pipeline, 'USE_TSFRESH_FEATURE_SELECTION', True), \
                patch.object(pipeline, 'build_combined_dataset', return_value=(frame, ['value__mean'])) as build, \
                patch.object(pipeline, 'apply_tsfresh_feature_selection', side_effect=lambda df, cols, **kw: cols) as auto, \
                patch('matplotlib.pyplot.show'):
            directory = pipeline.run_analysis.__wrapped__(['A', 'B', 'C'],
                progress_callback=lambda current, total, message: progress.append(current))
            assert list_models('rmc')[0]['algorithm'] == 'randomforest'
            model = load_model(directory)
            assert model.feature_columns == ['duration', 'value__mean']
            probs = model.predict_proba(frame[['duration', 'value__mean']])
            assert probs.shape == (105, 3)
            np.testing.assert_allclose(probs.sum(axis=1), 1.)
            assert (np.array(model.class_names)[probs.argmax(axis=1)] == labels).mean() > .95
            assert len(auto.call_args.args[0]) < len(frame)
            assert auto.call_args.args[1] == ['value__mean']
            assert build.call_args.kwargs['fc_parameters'] is pipeline.FC_PARAMETERS
            assert (directory.parent / f'{directory.name}.zip').is_file()
            for name in ('classification_report.txt', 'confusion_matrix.csv', 'model.pkl',
                         'feature_importance.png', 'step1_feature_set_comparison.csv'):
                assert (directory / name).is_file(), name
            manifest = json.loads((directory / 'run_manifest.json').read_text(encoding='utf-8'))
            assert manifest['training']['requested_features'] == ['duration', 'value__mean']
            assert progress[-1] == 5
    finally:
        training_core.reset()
