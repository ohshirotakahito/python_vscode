"""Cross-experiment inspection selection without creating Tk windows."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app_tk'),
               str(Path(__file__).resolve().parents[1] / 'app'),
               str(Path(__file__).resolve().parents[1])]
from tab_review import ReviewTab


def test_cross_experiment_lists_distinct_sources_and_prunes_versions():
    tab = SimpleNamespace(
        experiment_combo=Mock(), experiment=Mock(), _experiments=['a', 'b'],
        _sample_names=['Lys', 'Other'],
        _source_versions={'Lys': [dict(ex='a'), dict(ex='a'), dict(ex='b'), dict(ex=None)],
                          'Other': [dict(ex='b')]},
        feature_versions={'Lys': 'old', 'a / Lys': 'pinned'},
        version_summary=Mock(), samples=Mock(), source_hint=Mock())
    tab.experiment_combo.current.return_value = 3
    ReviewTab._filter_samples(tab)
    tab.samples.set_items.assert_called_once_with(['a / Lys', 'b / Lys', 'b / Other'])
    assert tab.sample_sources == {'a / Lys': ('Lys', 'a'), 'b / Lys': ('Lys', 'b'),
                                  'b / Other': ('Other', 'b')}
    assert tab.feature_versions == {'a / Lys': 'pinned'}
    tab.experiment.set.assert_called_once_with('')


def test_load_freezes_each_source_and_requested_version():
    tab = SimpleNamespace(samples=Mock(), feature_set=Mock(), experiment=Mock(),
                          feature_versions={'a / Lys': 'older'},
                          sample_sources={'a / Lys': ('Lys', 'a'), 'b / Lys': ('Lys', 'b')},
                          runner=Mock(), progress=Mock(), _loaded=Mock())
    tab.samples.get_selected.return_value = ['a / Lys', 'b / Lys']
    tab.feature_set.get.return_value = 'rmc'
    tab.experiment.get.return_value = ''
    ReviewTab._load(tab)
    task = tab.runner.run.call_args.args[0]
    tab.sample_sources.clear()
    tab.feature_versions.clear()
    with patch('tab_review.resolve_version', side_effect=[{'run_id': 'older'}, {'run_id': 'latest-b'}]) as resolve, \
            patch('tab_review.load_events', return_value=pd.DataFrame()) as load:
        _, _, selection = task(Mock())
    assert resolve.call_args_list[0].args == ('rmc', 'Lys', 'older')
    assert resolve.call_args_list[0].kwargs == {'experiment': 'a'}
    assert resolve.call_args_list[1].args == ('rmc', 'Lys', None)
    assert resolve.call_args_list[1].kwargs == {'experiment': 'b'}
    assert load.call_args.kwargs['sample_sources'] == {
        'a / Lys': ('Lys', 'a'), 'b / Lys': ('Lys', 'b')}
    assert selection[3] == {'a / Lys': 'older', 'b / Lys': 'latest-b'}
