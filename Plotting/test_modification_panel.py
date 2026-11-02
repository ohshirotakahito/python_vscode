"""Group-mode display and exports use registered members, including hidden selections."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

import bnal_modification as mod
from test_bnal_modification import dataset, site_rows

spec = importlib.util.spec_from_file_location('bnal_plotting_app_tests', Path(__file__).with_name('app.py'))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def value(x):
    return SimpleNamespace(get=lambda: x)


def panel():
    sites = mod.collect_sites([dataset(sample, target, site_rows(n, base, signal))
                              for sample in ['A', 'B']
                              for target, n, base, signal in [('seq', 80, 'C', .5), ('seq_M_22', 20, 'M', 1.2)]])
    return SimpleNamespace(_job=None, _dirty=False, sites=sites,
        group_mode=value(True), sample_groups={'任意群': [('EX', 'A'), ('EX', 'B')]},
        group_aggregation=value(next(iter(mod.GROUP_METHODS))),
        method=value(next(iter(mod.METHODS))), ci=value(next(iter(mod.CI_METHODS))),
        selected_sample_keys=set(), remember_sample_selection=Mock(),
        zero=value(''), full=value(''), control_key=Mock(return_value=None),
        target_sequences=['seq'], target_listbox=Mock(curselection=Mock(return_value=(0,))),
        tree=Mock(), ci_combo=Mock(), fill_tree=Mock(), render=Mock(), min_q=value(0))


def test_registered_members_drive_group_display_not_main_sample_selection():
    p = panel()
    app.ModificationPanel.draw(p)
    assert len(p.table) == 1
    assert p.table.iloc[0]['n_samples'] == 2
    assert p.table.iloc[0]['rate'] == .2
    assert p.display_sites[0].sample == '任意群'
    p.tree.heading.assert_called_with('sample', text='グループ')
    p.ci_combo.configure.assert_called_with(state='disabled')


def test_switch_back_uses_sample_filter_and_original_detail():
    p = panel()
    p.group_mode = value(False)
    p.selected_sample_keys = {('EX', 'B')}
    app.ModificationPanel.draw(p)
    assert p.table['sample'].tolist() == ['B']
    assert p.display_sites is p.sites
    p.ci_combo.configure.assert_called_with(state='readonly')


def test_csv_recomputes_and_records_group_membership_and_method(tmp_path):
    p = panel()
    p.draw = lambda: app.ModificationPanel.draw(p)
    p.table = None
    with patch.object(app, 'OUTPUT_DIR', tmp_path), patch.object(app.messagebox, 'askyesno', return_value=False):
        app.ModificationPanel.save_csv(p)
    paths = list(tmp_path.glob('*_modification_group_raw.csv'))
    assert len(paths) == 1
    table = pd.read_csv(paths[0])
    assert table.iloc[0]['group_name'] == '任意群'
    assert table.iloc[0]['members'] == 'EX / A; EX / B'
    assert table.iloc[0]['aggregation'] == 'mean'
    assert table.iloc[0]['ci'] == 'sample_bootstrap'
