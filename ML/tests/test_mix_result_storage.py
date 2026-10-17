"""Prediction storage stays separate while historical runs remain discoverable."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app'),
               str(Path(__file__).resolve().parents[1] / 'app_tk'),
               str(Path(__file__).resolve().parents[1])]
from common import paths
from lib.mix_summary import list_mix_runs
from lib.runs import list_runs


def test_snapshot_saves_graph_table_and_settings(tmp_path):
    import pandas as pd
    from matplotlib.figure import Figure
    from tab_predict import PredictTab
    figure = Figure()
    figure.add_subplot(111).bar([0], [100])
    def variable(value):
        return SimpleNamespace(get=lambda: value)
    tab = SimpleNamespace(run={'run_dir': tmp_path}, table_df=pd.DataFrame({'count': [4]}),
                          figure=figure, _tooltip=None, unit=variable('ファイル内の一定秒数ごと'),
                          _window_sec=lambda: 10, chart_file=variable('file1'),
                          as_percent=variable(True), high_only=variable(False),
                          threshold_used=0.8, classes=['A', 'B'])
    PredictTab._save_view(tab, notify=False)
    folder = next((tmp_path / 'views').iterdir())
    assert (folder / 'prediction_chart.png').stat().st_size > 0
    assert pd.read_csv(folder / 'prediction_summary.csv')['count'].tolist() == [4]
    settings = json.loads((folder / 'display_settings.json').read_text(encoding='utf-8'))
    assert settings['chart_file'] == 'file1'
    assert settings['unit'] == 'window'


def test_mix_storage_and_legacy_discovery(tmp_path):
    with patch.object(paths, 'RESULTS_ROOT', tmp_path / 'results'), \
            patch.object(paths, 'MIX_RESULTS_ROOT', tmp_path / 'mix_results'):
        train, _ = paths.new_run('rmc', 'xgboost', ['A', 'B'], timestamp='20261017_010000')
        mix, _ = paths.new_run('rmc', 'xgboost', ['A', 'B'], timestamp='20261017_020000', run_type='mix')
        legacy = paths.RESULTS_ROOT / 'rmc/xgboost/20261016_010000_mix_A-B'
        legacy.mkdir(parents=True)
        for folder in (mix, legacy):
            (folder / 'predict_M_events.csv').write_text('file,pred_label\nf,A\n', encoding='utf-8')
            (folder / 'run_manifest.json').write_text(json.dumps({'run_type': 'mix'}))
        (train / 'run_manifest.json').write_text(json.dumps({'run_timestamp': '20261017_010000'}))
        assert mix.is_relative_to(paths.MIX_RESULTS_ROOT)
        assert [r['run_dir'] for r in list_runs()] == [train]
        assert [r['run_dir'] for r in list_mix_runs()] == [mix, legacy]
