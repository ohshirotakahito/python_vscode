"""Scoring App のテスト（ネットワークも AXN の実データも使わない。AXN リポジトリは axn_bridge が見つける場所のもの）。"""
import json
import zipfile
from pathlib import Path

import pytest

from scoring import axn_bridge, experiment_files, experiment_ledger, machine_data, rescore, score_store

NAME = '20240220_0833 ANAN#2Pex1n1_LE'
MACHINE = {'Machine DBCode': 'AXN2', 'PC Directroy': r'Q:\no_such_drive\MCBJ', 'Tmp_backup': '', 'SQ_backup': ''}

try:
    MODS = axn_bridge.load()
except axn_bridge.AxnNotFound:
    MODS = None
needs_axn = pytest.mark.skipif(MODS is None, reason='AXN リポジトリが無い')


# --- 合成データ: 1 回分の GG（FC・MA・TA・AC・CAL）→ EXSV 2 回 → CAL だけやり直し ---
def _result(stage, no, when, fields):
    return f'R_{stage}#{no}_{when}_{NAME}.txt', '\n'.join(f'{k},{v}' for k, v in fields.items()) + '\n'


def ac_log(cut_at=40, points=400):
    """50 ms の AC ログ: 1.2 G0 相当の電流 → カット → 20 pA 以下 → 0.3 pA。bias 100 mV, SR 0 Ω → 1 G0 = 7.748 uA。"""
    header = 'Time [s],K Bias (mV),K Current (uA),RM Motor (mm),AM Motor (PLS) ,Piezo (V),Piezo IDX,A1 Bias [mV],A2 Bias [mV],A3 Bias [mV]'
    rows = [header]
    for k in range(points):
        if k < cut_at - 3:
            current = 7.748 * 1.0  # 1.0 G0
        elif k < cut_at:
            current = 7.748 * 0.5  # 0.8 G0 より下、まだカットではない
        elif k < cut_at + 101:
            current = 5e-3           # 5 nA: Cut KA1 より下
        else:
            current = 3e-7           # 0.3 pA
        rows.append(f'{k * 0.05:.2f},100.0,{current:.9f},0.1,100,70.0,30000,100.7,100.7,-100.7')
    return '\n'.join(rows) + '\n'


def exsv_logs(n=40):
    pml = ['Time [s],K Bias (mV),TIA Current [pA],RM Motor [mm],Piezo [V],TIA RMS [pA],Signal [a] ,A1 Bias [mV],A2 Bias [mV],A3 Bias [mV]']
    pml += [f'{k * 0.5:.3f},100,5.0,0.1,70,{1.8 + 0.1 * (k % 3):.3f},3,100.7,100.7,-100.7' for k in range(n)]
    pm = ['Time [s],K Bias (mV),K Current (uA),RM Motor (mm),AM Motor (PLS) ,Piezo (V),Piezo IDX,A1 Bias [mV],A2 Bias [mV],A3 Bias [mV]']
    pm += [f'{k * 0.05:.3f},100,0.0,0.1,23,{70.0 + 0.001 * (k % 2):.6f},30000,100.7,100.7,-100.7' for k in range(n * 10)]
    return '\n'.join(pml) + '\n', '\n'.join(pm) + '\n'


def write_experiment(root, device='SC25-2118-40', with_ac_log=True):
    """root/<NAME>/ に合成した実験フォルダを作り、そのパスを返す。"""
    folder = root / NAME
    files = {}
    fc = {'Experimental ID': NAME, 'Device Code': device, 'Machine selection': 'AXN#2', 'FC#': '1', 'FC Time': '20240220_08:34',
          '# 1FB Position [mm]': '0.0835', '# 2FB Position [mm]': '0.0899', '# 3FB Position [mm]': '0.0903', 'FB success? ': 'Yes',
          'FC Chip R[Ω]': '49.6'}
    files[('FC',) + _result('FC', 1, '20240220_0834', fc)[:1]] = _result('FC', 1, '20240220_0834', fc)[1]
    ma = {'MA#': '1', 'MA Time': '20240220_08:34', 'M Auto L SHW [um]': '15.25', 'M Auto L delta SHW [%]': '27.5',
          'M Auto L std SHW [%]': '19.2', 'M Auto Center Drift (um)': '-1.76'}
    files[('MA',) + _result('MA', 1, '20240220_0834', ma)[:1]] = _result('MA', 1, '20240220_0834', ma)[1]
    ta = {'TA#': '1', 'TA Time': '20240220_08:36', 'T Auto L SHW [um]': '21.17', 'T Auto L delta SHW [%]': '-2.99',
          'T Auto L std SHW [%]': '15.4', 'T Auto Center Drift (um)': '-1.79'}
    files[('TA',) + _result('TA', 1, '20240220_0836', ta)[:1]] = _result('TA', 1, '20240220_0836', ta)[1]
    ac = {'AC#': '1', 'AC Time': '20240220_08:38', 'AC ex Time [s]': '11.10', 'AC G0 current [uA]': '7.748', 'AC nG0': '1.0000',
          'AC QC': 'Good'}
    files[('AC',) + _result('AC', 1, '20240220_0838', ac)[:1]] = _result('AC', 1, '20240220_0838', ac)[1]
    if with_ac_log:
        files[('AC', 'PM_AC#1_20240220_0838_.txt')] = ac_log()
    cal = {'CAL#': '2', 'CAL Time': '20240220_08:38', 'CAL DU [%]': '2.11', 'CAL Residue [a]': '0.77', 'CAL Hysterisis [V]': '0.277',
           'CAL L Slope [pm/V]': '35.4', 'CAL Number': '10'}
    files[('CAL',) + _result('CAL', 2, '20240220_0838', cal)[:1]] = _result('CAL', 2, '20240220_0838', cal)[1]
    for no, when in ((1, '20240220_0844'), (2, '20240220_0856')):
        exsv = {'EXSV#': str(no), 'EXSV Time': when[:8] + '_' + when[9:11] + ':' + when[11:], 'EXSV Delta Piezo [V]': '0.7164',
                'EXSV Set Distance [nm] ': '0.5400', 'EXSV Set Tunnel Current [pA] ': '22.76', 'EXSV Target BL Current [pA] ': '9.22',
                'EXSV Total Signal # [n] ': '650', 'EXSV Signal Rate [/s]': '1.3', 'EXSV Range in [%] ': '40.0'}
        files[('EXSV',) + _result('EXSV', no, when, exsv)[:1]] = _result('EXSV', no, when, exsv)[1]
        pml, pm = exsv_logs()
        files[('EXSV', f'PML_EXSV#{no}_{when}__SGM D0.540nm.txt')] = pml
        files[('EXSV', f'PM_EXSV#{no}_{when}__SGM D0.540nm.txt')] = pm
        files[('EXSV', f'D_{NAME}_EXSV#{no}_{when}_SGM.txt')] = 'big data not read\n'
    cal2 = dict(cal, **{'CAL#': '3', 'CAL Time': '20240220_09:10', 'CAL DU [%]': '1.0'})
    files[('CAL',) + _result('CAL', 3, '20240220_0910', cal2)[:1]] = _result('CAL', 3, '20240220_0910', cal2)[1]
    for (sub, filename), text in files.items():
        (folder / sub).mkdir(parents=True, exist_ok=True)
        (folder / sub / filename).write_bytes(text.encode('cp932'))
    return folder


def zip_experiment(folder, target, with_top_folder=False):
    with zipfile.ZipFile(target, 'w') as zf:
        for path in folder.rglob('*'):
            if path.is_file():
                rel = path.relative_to(folder).as_posix()
                zf.write(path, f'{folder.name}/{rel}' if with_top_folder else rel)
    return target


@pytest.fixture
def machines(tmp_path):
    sq = tmp_path / 'SQ' / 'AN2'
    sq.mkdir(parents=True)
    tmp = tmp_path / 'backup_06'
    tmp.mkdir()
    info = dict(MACHINE, Tmp_backup=str(tmp), SQ_backup=str(sq))
    return {'AXN2': info}


# --- experiment_files ---
def test_locate_prefers_tmp_backup_then_folder_then_zip(tmp_path, machines):
    info = machines['AXN2']
    assert experiment_files.locate(NAME, info) is None
    zip_experiment(write_experiment(tmp_path / 'src'), Path(info['SQ_backup']) / f'{NAME}.zip')
    exp = experiment_files.locate(NAME, info)
    assert exp.location.place == 'SQ_backup' and exp.location.zipped
    write_experiment(Path(info['SQ_backup']))
    assert not experiment_files.locate(NAME, info).location.zipped
    write_experiment(Path(info['Tmp_backup']))
    assert experiment_files.locate(NAME, info).location.place == 'Tmp_backup'


@needs_axn
def test_candidates_follow_the_same_order_as_axn(tmp_path):
    if MODS.data_locations is None:
        pytest.skip('この AXN には data_locations が無い')
    table = tmp_path / 'Machine_N.csv'
    table.write_bytes('Machine DBCode,PC Directroy,Tmp_backup,SQ_backup\r\nAXN2,D:\\MCBJ,\\\\SQserver\\backup_06,\\\\SQserver\\data\\AN2\r\n'
                      .encode('cp932'))
    row = machine_data.machines(table)['AXN2']
    for this_machine in (None, 'AXN2', 'AXN3'):
        theirs = MODS.data_locations.candidates('AXN2', NAME, this_machine, table)
        ours = experiment_files.candidates(NAME, row, 'AXN2', this_machine)
        assert [(Path(c.path), c.zipped) for c in ours] == [(Path(c.path), c.is_zip) for c in theirs]
        assert [c.place == 'PC' for c in ours] == [c.kind == 'this PC' for c in theirs]


def test_zip_and_folder_read_the_same(tmp_path):
    folder = write_experiment(tmp_path / 'src')
    plain = experiment_files.Experiment(NAME, experiment_files.Location('SQ_backup', folder))
    nested = zip_experiment(folder, tmp_path / 'nested.zip', with_top_folder=True)
    flat = zip_experiment(folder, tmp_path / 'flat.zip')
    for target in (nested, flat):
        with experiment_files.Experiment(NAME, experiment_files.Location('SQ_backup', target, zipped=True)) as zipped:
            assert zipped.names('AC') == plain.names('AC')
            assert [r.fields for r in zipped.results('EXSV')] == [r.fields for r in plain.results('EXSV')]
            assert zipped.pm_log('AC', 1, '20240220_0838') == plain.pm_log('AC', 1, '20240220_0838')
            assert zipped.device_code() == 'SC25-2118-40'
            assert zipped.has_folder('AutoGG') is False


def test_results_are_sorted_and_fields_keep_trailing_spaces(tmp_path):
    folder = write_experiment(tmp_path / 'src')
    exp = experiment_files.Experiment(NAME, experiment_files.Location('SQ_backup', folder))
    cal = exp.results('CAL')
    assert [r.no for r in cal] == [2, 3] and cal[0].time.hour == 8
    assert exp.results('EXSV')[0].fields['EXSV Set Distance [nm] '] == '0.5400'
    assert experiment_files.parse_result_name('PM_AC#1_20240220_0838_.txt') is None


# --- machine_data / score_store ---
def test_machine_of_and_times():
    assert machine_data.machine_of(NAME) == 'AXN2'
    assert machine_data.machine_of('20190917_1604 ANAX#2Pex0n1_let7') is None
    assert machine_data.experiment_time(NAME).strftime('%Y-%m-%d %H:%M') == '2024-02-20 08:33'
    assert machine_data.run_time('20240220_08:38') == machine_data.run_time('20240220_0838')


def test_inventory_reads_both_lists(tmp_path):
    (tmp_path / 'AXN2 SQ inventory.csv').write_text('Scanned,2026-11-15 12:35:30,PC,\\\\SQserver\\backup_06\\\nExperiment,Files,Bytes\nA,1,2\n',
                                                     encoding='utf-8-sig')
    (tmp_path / 'AXN2 SQ archive inventory.csv').write_text('Scanned,x,PC,\\\\SQserver\\data\\AN2\nExperiment,Files,Bytes,Modified\nA,1,2,3\nB,1,2,3\n',
                                                             encoding='utf-8-sig')
    assert machine_data.inventory('AXN2', tmp_path) == {'A': 'Tmp_backup + SQ_backup', 'B': 'SQ_backup'}


def test_store_upsert_replaces_same_key_and_keeps_newest_first(tmp_path):
    prov = score_store.provenance('AXN2', score_store.RECONSTRUCTED, 'LabVIEW', 'axn abc', 'x')
    rows = [dict({'Experimental ID': NAME, 'AC#': '1', 'AC Time': '20240220_08:38', 'AC Score': '88.2'}, **prov),
            dict({'Experimental ID': NAME, 'AC#': '2', 'AC Time': '20240220_09:38', 'AC Score': '50.0'}, **prov)]
    assert score_store.upsert('AC', rows, tmp_path) == (2, 0)
    rows[0]['AC Score'] = '90.0'
    assert score_store.upsert('AC', rows[:1], tmp_path) == (0, 1)
    measured = dict(rows[0], Source=score_store.MEASURED)
    assert score_store.upsert('AC', [measured], tmp_path) == (1, 0)
    saved = score_store.read('AC', tmp_path)
    assert [r['AC#'] for r in saved] == ['2', '1', '1'] and saved[1]['AC Score'] == '90.0'
    assert list(saved[0]) == list(score_store.KINDS['AC'].all_columns)
    assert score_store.path('AC', tmp_path).read_bytes().startswith(b'\xef\xbb\xbf')
    assert score_store.remove('AC', lambda r: r['Source'] == score_store.MEASURED, tmp_path) == 1


def test_era_by_date_and_combine():
    assert score_store.era_by_date(machine_data.experiment_time('20261028_2359 x')) == score_store.ERA_LABVIEW
    assert score_store.era_by_date(machine_data.experiment_time('20261029_0000 x')) == score_store.ERA_UNKNOWN
    assert score_store.era_by_date(None) == score_store.ERA_UNKNOWN
    combine = score_store.combine_eras
    assert combine(['Python', 'Python', '不明']) == 'Python'
    assert combine(['Python', 'LabVIEW']) == combine(['混在']) == '混在'
    assert combine([]) == combine(['不明', '']) == '不明'


def _log(times):
    return [[f'{t:.2f}', '100.0', '9.9'] for t in times]


def test_grid_era_tells_labview_from_python():
    assert rescore.grid_era(_log([k * 0.05 for k in range(200)])) == score_store.ERA_LABVIEW
    # Python: 実時刻（0.05 秒の刻みから外れる）。最初が 0.00 でも判定できる
    jitter = [0.0] + [k * 0.05 + (0.01 if k % 4 else 0.0) for k in range(1, 200)]
    assert rescore.grid_era(_log(jitter)) == score_store.ERA_PYTHON
    assert rescore.grid_era(_log([k * 0.05 for k in range(10)])) == score_store.ERA_UNKNOWN     # 短すぎる
    assert rescore.grid_era(None) == score_store.ERA_UNKNOWN


class FakeExp:
    """results / pm_log だけを持つ実験（時代の判定のテスト用）。"""

    def __init__(self, name, results, logs):
        self.name, self._results, self._logs = name, results, logs

    def results(self, stage):
        return [r for r in self._results if r.stage == stage]

    def pm_log(self, stage, no, when):
        return self._logs.get((stage, no, when))


def _r(stage, no, when, **fields):
    return experiment_files.Result(stage, no, when, dict(fields), f'R_{stage}#{no}_{when}_x.txt')


def test_run_eras_in_a_mixed_experiment():
    """10-30 の実験のように、1 実験の中で LabVIEW から Python に変わる。EXSV は直前の Gap Generation の時代を引き継ぐ。"""
    grid = _log([k * 0.05 for k in range(100)])
    wall = _log([k * 0.05 + 0.013 for k in range(100)])
    results = [_r('FC', 1, '20261030_1312'), _r('CAL', 2, '20261030_1318'), _r('EXSV', 1, '20261030_1324'),
               _r('FC', 3, '20261030_1500'), _r('AC', 3, '20261030_1505'), _r('EXSV', 2, '20261030_1510'),
               _r('EXSV', 3, '20261111_1404', **{'EXSV Score': '23.6'})]
    logs = {('FC', 1, '20261030_1312'): grid, ('CAL', 2, '20261030_1318'): grid,
            ('FC', 3, '20261030_1500'): wall, ('AC', 3, '20261030_1505'): wall}
    exp = FakeExp('20261030_1311 JMAN#2Pex5n1_x', results, logs)
    eras, timeline = rescore.stage_eras(exp)
    assert eras[('FC', 1, '20261030_1312')] == eras[('CAL', 2, '20261030_1318')] == 'LabVIEW'
    assert eras[('FC', 3, '20261030_1500')] == eras[('AC', 3, '20261030_1505')] == 'Python'
    exsv = {r.no: rescore.exsv_era(exp, r, timeline) for r in exp.results('EXSV')}
    assert exsv == {1: 'LabVIEW', 2: 'Python', 3: 'Python'}
    old = FakeExp('20240220_0833 x', [_r('EXSV', 1, '20240220_0844')], {})
    assert rescore.exsv_era(old, old.results('EXSV')[0], []) == 'LabVIEW'


def test_sync_measured_eras(tmp_path):
    base = {'Experimental ID': NAME, 'AC#': '1', 'AC Time': '20261111_14:00', 'AC Score': '59.8', 'Era': '不明'}
    score_store.upsert('AC', [dict(base, Source=score_store.MEASURED),
                              dict(base, Source=score_store.RECONSTRUCTED, Era='Python'),
                              dict(base, Source=score_store.MEASURED, **{'AC#': '2'})], tmp_path)
    score_store.upsert('AutoGG', [{'Experimental ID': NAME, 'Round start': 'x', 'Source': score_store.MEASURED,
                                   'Era': '不明'}], tmp_path)
    assert score_store.sync_measured_eras(tmp_path) == 2
    rows = {(r['Source'], r['AC#']): r['Era'] for r in score_store.read('AC', tmp_path)}
    assert rows[(score_store.MEASURED, '1')] == 'Python' and rows[(score_store.MEASURED, '2')] == '不明'
    assert score_store.read('AutoGG', tmp_path)[0]['Era'] == 'Python'


# --- rescore（AXN の関数で） ---
@needs_axn
def test_rescore_experiment_rounds_and_scores(tmp_path, machines):
    write_experiment(Path(machines['AXN2']['SQ_backup']))
    outcome = rescore.rescore_experiment(NAME, 'AXN2', machines, MODS)
    assert outcome.found and outcome.era == 'LabVIEW' and outcome.skipped == []
    ac = outcome.rows['AC']
    assert len(ac) == 1 and ac[0]['Device Code'] == 'SC25-2118-40' and ac[0]['Source'] == score_store.RECONSTRUCTED
    assert ac[0]['AC Score n0.9-1.2G0 Point [n]'] == '37' and float(ac[0]['AC Score']) > 60
    assert ac[0]['Criteria'] == MODS.criteria and 'axn' in ac[0]['Criteria']
    exsv = outcome.rows['EXSV']
    assert [r['EXSV#'] for r in exsv] == ['1', '2'] and all(0 <= float(r['EXSV Score']) <= 100 for r in exsv)
    assert exsv[0]['EXSV Set Distance [nm]'] == '0.5400'
    gg = outcome.rows['AutoGG']
    assert len(gg) == 2
    first, second = gg
    assert first['Round'] == '1' and first['Round start'] == '20240220_08:34' and first['Round end'] == '20240220_08:44'
    assert first['Stages used'] == 'FC;MA;TA;AC;CAL' and first['Stages missing'] == '' and first['Stages scored'] == '5 / 5'
    assert first['AC score'] == ac[0]['AC Score']
    fc_fields = {'# 1FB Position [mm]': '0.0835', '# 2FB Position [mm]': '0.0899', '# 3FB Position [mm]': '0.0903'}
    assert first['FC score'] == f"{MODS.gg_review.evaluate('FC', fc_fields, 'completed')['score']:.1f}" == '62.9'
    # 2 回目の EXSV の後の CAL だけ: 1 工程の平均、欠けた工程が見える
    assert second['Round start'] == '20240220_09:10' and second['Round end'] == ''
    assert second['Stages used'] == 'CAL' and second['Stages missing'] == 'FC;MA;TA;AC'
    assert second['AutoGG Score'] == second['CAL score'] and second['Stages scored'] == '1 / 1'


@needs_axn
def test_rescore_without_ac_log_skips_ac_but_keeps_the_round_visible(tmp_path, machines):
    write_experiment(Path(machines['AXN2']['SQ_backup']), with_ac_log=False)
    outcome = rescore.rescore_experiment(NAME, 'AXN2', machines, MODS)
    assert outcome.rows['AC'] == [] and any('PM_AC' in line for line in outcome.skipped)
    first = outcome.rows['AutoGG'][0]
    assert first['Stages used'] == 'FC;MA;TA;AC;CAL' and first['AC score'] == '' and first['Stages scored'] == '4 / 5'


@needs_axn
def test_rescore_many_saves_and_ledger_joins(tmp_path, machines, monkeypatch):
    write_experiment(Path(machines['AXN2']['SQ_backup']))
    store = tmp_path / 'Scores'
    outcomes, saved = rescore.rescore_many([NAME, 'missing'], 'AXN2', store, mods=MODS, machines_table=machines)
    assert saved['AC'] == (1, 0) and saved['EXSV'] == (2, 0) and saved['AutoGG'] == (2, 0)
    assert not outcomes[1].found
    assert rescore.reconstructed_names(store) == {NAME}
    scores = experiment_ledger.load_scores(store)
    transfers = [{'装置': 'AXN2', '元の名前': NAME, '転送': '転送済み', '初回転送日時': '2024-03-01 10:00', '転送先の実験名': 'LE',
                  '転送先のサンプル名': 'LE1', '解析サーバー': 'Rackstation/analysis', 'ANAL状態': '完了', 'ANAL進捗': '100.0%（46/46）',
                  '計画どおり': ''}]
    rows = experiment_ledger.build(scores, transfers=transfers, plans={}, records=[], inventories={'AXN2': {NAME: 'SQ_backup', 'other': 'Tmp_backup'}},
                                   machines=['AXN2'])
    assert [r['name'] for r in rows] == ['other', NAME]
    joined = rows[1]
    assert joined['exsv_count'] == '2' and joined['ac_last'] == scores['AC'][0]['AC Score'] and joined['gg_missing'] == 'FC・MA・TA・AC'
    assert joined['score_source'] == score_store.RECONSTRUCTED and joined['destination'] == 'Rackstation/analysis: LE / LE1'
    assert joined['anal_state'] == '完了' and rows[0]['transfer'] == '' and rows[0]['location'] == 'Tmp_backup'
    target = experiment_ledger.write(rows, store)
    text = target.read_text(encoding='utf-8-sig')
    assert text.splitlines()[0].startswith('装置,Experimental ID,実験日時,時代')


def test_measured_rows_map_score_csv(tmp_path):
    root = tmp_path / 'machinedb'
    (root / 'AutoGG').mkdir(parents=True)
    (root / 'AutoGG' / 'AXN2 AutoGG Score.csv').write_text(
        'Experimental ID,Device Code,AutoGG Finished,AutoGG Started,AutoGG Score,Stages scored,FC score,MA score,TA score,AC score,CAL score,AMP Check score,Gap Check score,Decision\r\n'
        '20261111_1352 UNAN#2Pex3n2_test,SC78,2026-11-11 14:01:34,2026-11-11 13:56:17,74.9,5 / 5,100.0,57.4,67.4,59.8,89.8,,,ASZ\r\n'
        '20261111_1352 UNAN#2Pex3n2_test,SC78,2026-11-11 21:26:54,2026-11-11 21:21:52,60.3,4 / 4,100.0,65.7,,50.0,70.1,,,\r\n',
        encoding='cp932')
    rows = rescore.measured_rows('AXN2', 'AutoGG', root)
    assert rows[0]['Source'] == score_store.MEASURED and rows[0]['Era'] == 'Python'
    assert rows[0]['Round start'] == '2026-11-11 13:56:17' and rows[0]['Stages missing'] == '' and rows[0]['Decision'] == 'ASZ'
    assert rows[1]['Stages used'] == 'FC;MA;AC;CAL' and rows[1]['Stages missing'] == 'TA'
    assert rescore.measured_rows('AXN2', 'AC', root) == []


def test_summarize_prefers_reconstructed_per_kind():
    base = {'Experimental ID': NAME, 'Machine': 'AXN2', 'Era': 'LabVIEW'}
    scores = {'AC': [dict(base, Source=score_store.MEASURED, **{'AC#': '1', 'AC Time': 't1', 'AC Score': '80.0'}),
                     dict(base, Source=score_store.RECONSTRUCTED, Criteria='axn 1', **{'AC#': '1', 'AC Time': 't1', 'AC Score': '88.2'})],
              'EXSV': [dict(base, Source=score_store.MEASURED, **{'EXSV#': '1', 'EXSV Time': 't', 'EXSV Score': '40.0'}),
                       dict(base, Source=score_store.MEASURED, **{'EXSV#': '2', 'EXSV Time': 't', 'EXSV Score': '60.0'})],
              'AutoGG': []}
    summary = experiment_ledger.summarize(scores)[NAME]
    assert summary['ac_last'] == '88.2' and summary['exsv_mean'] == '50.0' and summary['exsv_max'] == '60.0'
    assert summary['score_source'] == f'{score_store.RECONSTRUCTED} + {score_store.MEASURED}' and summary['criteria'] == 'axn 1'


@needs_axn
def test_criteria_changes_with_constants():
    text = MODS.criteria
    assert text.startswith('axn ') and 'gg provisional' in text
    assert axn_bridge.criteria_text(MODS) == text


def test_device_codes_follow_the_chip_id_table(tmp_path, monkeypatch):
    from scoring import rescore as rs
    monkeypatch.setattr(rs, '_ALIAS_TABLE', dict(schema_version=1, revision=1, entries=[
        dict(old='SC25-2115-9', new='SC25-2115-09', status='完了')]))
    assert rs.current_device('SC25-2115-9') == 'SC25-2115-09'
    assert rs.current_device('SC25-2118-40') == 'SC25-2118-40' and rs.current_device('') == ''
    score_store.upsert('AC', [{'Experimental ID': NAME, 'Device Code': 'SC25-2115-9', 'AC#': '1', 'AC Time': 't',
                               'Source': score_store.RECONSTRUCTED}], tmp_path)
    assert score_store.remap_devices(rs.current_device, tmp_path) == {'AC': 1, 'EXSV': 0, 'AutoGG': 0}
    assert score_store.read('AC', tmp_path)[0]['Device Code'] == 'SC25-2115-09'


def test_ledger_columns_have_fixed_widths_and_cannot_be_dragged():
    tk = pytest.importorskip('tkinter')
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display')
    root.withdraw()
    try:
        from scoring import app
        table = app.Table(root, app.LEDGER_SHOW, app.LEDGER_SHOW, app.LEDGER_WIDTH, fixed=True)
        assert set(app.LEDGER_WIDTH) == set(app.LEDGER_SHOW)
        for column in app.LEDGER_SHOW:
            assert table.tree.column(column, 'width') == app.LEDGER_WIDTH[column]
            assert not table.tree.column(column, 'stretch')
        table.tree.identify_region = lambda x, y: 'separator'
        assert table._block_resize(type('E', (), {'x': 0, 'y': 0})()) == 'break'
    finally:
        root.destroy()
