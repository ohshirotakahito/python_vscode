"""同期の競合コピーの統合。"""
import os
import time
from pathlib import Path

from shared import sync_conflicts as sc

HEADER = 'Experimental ID,Device Code,Device Wafer,Device Lot,FC#,FC Time'


def write(path, rows, header=HEADER, old=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('\r\n'.join([header] + rows) + '\r\n').encode('cp932'))
    if old:
        stamp = time.time() - 3600
        os.utime(path, (stamp, stamp))


def test_subset_copy_is_archived_and_extra_rows_are_merged_in_place(tmp_path):
    db = tmp_path / 'machinedb' / 'FC'
    main = db / 'AXN2 FC data.csv'
    # 本体は新しい順: 14:30（あとで本体だけに書かれた）, 14:11, 14:02
    write(main, ['e3,SC91-4419-28,SC91-4419,SC91,3,20261113_14:30', 'e2,SC91-4419-28,SC91-4419,SC91,2,20261113_14:11',
                 'e1,SC91-4419-28,SC91-4419,SC91,1,20261113_14:02'])
    # 競合コピー: 14:20 の行（本体にない）が 14:11 の前にある。旧名の行は新名にそろえれば本体にある
    copy = db / 'AXN2 FC data_DESKTOP-08FPRB2_11-12-174441-2026_Conflict.csv'
    write(copy, ['e2b,SC91-4419-28,SC91-4419,SC91,9,20261113_14:20', 'e2,SC91-4419-28,SC91-4419,SC91,2,20261113_14:11',
                 'e1,SC91-4419-28,SC91-4419,SC91,1,20261113_14:02'])
    subset = db / 'AXN2 FC data_DESKTOP-A23SEE0_5-29-172340-2026_Conflict.csv'
    write(subset, ['e0,SC91-4419--07,SC91-4419-,SC91,1,20261011_13:22'])
    write(main, main.read_bytes().decode('cp932').splitlines()[1:] + ['e0,SC91-4419-07,SC91-4419,SC91,1,20261011_13:22'])
    table = dict(entries=[dict(old='SC91-4419--07', new='SC91-4419-07', status='完了')])
    items = {Path(i.copy).name: i for i in sc.plan(tmp_path, table)}
    assert items[copy.name].action == 'merge' and items[copy.name].extra == 1
    assert items[subset.name].action == 'archive'                                       # 旧名をそろえれば本体にある
    session, results = sc.apply(list(items.values()), tmp_path, alias_table=table)
    lines = main.read_bytes().decode('cp932').split('\r\n')
    assert lines[1:5] == ['e3,SC91-4419-28,SC91-4419,SC91,3,20261113_14:30', 'e2b,SC91-4419-28,SC91-4419,SC91,9,20261113_14:20',
                          'e2,SC91-4419-28,SC91-4419,SC91,2,20261113_14:11', 'e1,SC91-4419-28,SC91-4419,SC91,1,20261113_14:02']
    assert main.read_bytes().endswith(b'\r\n') and not copy.exists() and not subset.exists()
    assert (session / 'copies' / 'machinedb' / 'FC' / copy.name).exists()
    assert (session / 'originals' / 'machinedb' / 'FC' / main.name).exists() and (session / 'journal.json').exists()
    assert [r['status'] for r in results] == ['done', 'done']


def test_unsafe_cases_are_only_reported(tmp_path):
    db = tmp_path / 'machinedb'
    write(db / 'TA' / 'AXN1 TA data.csv', ['a,SC1-1-1,,,1,t'])
    write(db / 'TA' / 'AXN1 TA data_PC-1_9-01-181643-2026_Conflict.csv', ['b,SC1-1-1,,,1,t'], header='FC#,FC Time')
    write(db / 'PSET' / 'AXN4 PSET data_PC-1_12-04-123341-2025_Conflict.csv', ['x'])                   # 本体がない
    write(db / 'SEQ' / 'Machine_N.csv', ['AXN1,x'], header='Machine DBCode,Tmp_backup')
    write(db / 'SEQ' / 'Machine_N_PC-1_1-02-030405-2026_Conflict.csv', ['AXN1,y'], header='Machine DBCode,Tmp_backup')
    write(db / 'FC' / 'AXN2 FC data.csv', ['a,SC1-1-1,,,1,t'], old=False)                               # 今書き込まれた
    write(db / 'FC' / 'AXN2 FC data_PC-1_1-02-030405-2026_Conflict.csv', ['b,SC1-1-1,,,1,t'])
    write(db / 'AC' / 'MCBJ-C001_PC-1_11-12-165546-2026_CaseConflict.csv', ['x'])
    write(db / 'backups' / 'x_PC-1_1-02-030405-2026_Conflict.csv', ['x'])                              # 保管場所は見ない
    reasons = {Path(i.copy).name: (i.action, i.reason) for i in sc.plan(tmp_path, None)}
    assert len(reasons) == 5 and all(action == 'review' for action, _ in reasons.values())
    assert '見出し' in reasons['AXN1 TA data_PC-1_9-01-181643-2026_Conflict.csv'][1]
    assert '本体がない' in reasons['AXN4 PSET data_PC-1_12-04-123341-2025_Conflict.csv'][1]
    assert '決められない' in reasons['Machine_N_PC-1_1-02-030405-2026_Conflict.csv'][1]
    assert '分以内' in reasons['AXN2 FC data_PC-1_1-02-030405-2026_Conflict.csv'][1]
    assert '本体がない' in reasons['MCBJ-C001_PC-1_11-12-165546-2026_CaseConflict.csv'][1]


def test_derived_scores_copy_is_archived_and_changed_files_are_skipped(tmp_path):
    scores = tmp_path / 'database' / 'Scores'
    write(scores / 'exsv_scores.csv', ['a'], header='Experimental ID')
    copy = scores / 'exsv_scores_PC-1_11-18-185326-2026_Conflict.csv'
    write(copy, ['b'], header='Experimental ID')
    items = sc.plan(tmp_path, None)
    assert [i.action for i in items] == ['archive']
    copy.write_bytes(copy.read_bytes() + b'c\r\n')                                         # 計画の後に変わった
    _, results = sc.apply(items, tmp_path)
    assert results[0]['status'].startswith('skipped') and copy.exists()


def test_merge_lines_keeps_duplicates_by_count():
    merged, added = sc.merge_lines([b'x', b'y'], [b'x', b'x', b'y'])
    assert added == 1 and merged == [b'x', b'x', b'y']


def test_headerless_logs_and_huge_differences(tmp_path):
    db = tmp_path / 'machinedb' / 'BL'
    main = db / 'AXN2 BL Data.csv'
    write(main, ['20261118_15:12,-14.9,-14.01', '20261110_14:53,-12.8,-0.12'], header='20261118_16:00,-1.0,1.0')
    copy = db / 'AXN2 BL Data_PC-1_11-12-174343-2026_Conflict.csv'
    write(copy, ['20261110_14:53,-12.8,-0.12', '20261110_14:28,-12.7,-2.02'], header='20261118_15:12,-14.9,-14.01')
    items = sc.plan(tmp_path, None)
    assert [(i.action, i.extra) for i in items] == [('merge', 1)]
    sc.apply(items, tmp_path)
    assert main.read_bytes().decode().split('\r\n')[:4] == [
        '20261118_16:00,-1.0,1.0', '20261118_15:12,-14.9,-14.01', '20261110_14:53,-12.8,-0.12', '20261110_14:28,-12.7,-2.02']
    ta = tmp_path / 'machinedb' / 'TA'
    write(ta / 'AXN1 TA data.csv', ['a,SC1-1-1,,,1,t'])
    write(ta / 'AXN1 TA data_PC-1_9-01-181643-2026_Conflict.csv', ['old,SC36-2613-14,,,1,t'] * 300)
    item = next(i for i in sc.plan(tmp_path, None) if 'TA' in i.copy)
    assert item.action == 'review' and '多い' in item.reason


def test_main_names_from_other_conflict_styles():
    assert sc.main_name('AXN2 FC data_DESKTOP-08FPRB2_11-12-174441-2026_Conflict.csv') == 'AXN2 FC data.csv'
    assert sc.main_name('MCBJ-C001_DESKTOP-08FPRB2_11-12-165546-2026_CaseConflict.csv') == 'MCBJ-C001.csv'
    assert sc.main_name('SC89-4412-12 (OOO TTT の競合コピー 2026-07-15).csv') == 'SC89-4412-12.csv'
    assert sc.main_name('SC91-4419-05_DESKTOP-A23SEE0_6-22-150123-2026_Conflict (大城敬人 の競合コピー 2026-06-22).csv') == \
        'SC91-4419-05.csv'
    assert sc.main_name('AXN1 TA data_DESKTOP-A23SEE0_9-01-181720-2026_conflict_current_DESKTOP-A23SEE0_9-01-181722-2026'
                        '_conflict_current.csv') == 'AXN1 TA data.csv'
    assert sc.main_name('AXN2 FC data.csv') is None


def test_pc_name_with_underscore_repeated_rows_and_new_column_layout(tmp_path):
    db = tmp_path / 'machinedb'
    write(db / 'PSET' / 'AXN4 PSET data.csv', ['a,SC1-1-1,,,1,t', 'b,SC1-1-1,,,2,t'])
    write(db / 'PSET' / 'AXN4 PSET data_TOSHIRO_HOMEX_12-04-123341-2025_Conflict.csv', ['b,SC1-1-1,,,2,t'])
    write(db / 'TA' / 'AXN1 TA data.csv', ['new,SC1-1-1,,,1,t', 'old,SC36-2613-14,,,1,t'])
    write(db / 'TA' / 'AXN1 TA data_PC-1_9-01-181643-2026_Conflict.csv', ['old,SC36-2613-14,,,1,t'] * 300)
    chip = tmp_path / 'ChipDB' / 'FC'
    wide = ','.join(f'c{i}' for i in range(12))
    write(chip / 'SC89-4412-12.csv', [','.join(['r1'] + [str(i) for i in range(11)] + ['x', 'y'])],
          header=wide + ',new1,new2')
    write(chip / 'SC89-4412-12 (OOO TTT の競合コピー 2026-07-15).csv', [','.join(['r1'] + [str(i) for i in range(11)] + ['z'])],
          header=wide + ',old1')
    actions = {Path(i.copy).name: (i.action, i.reason, Path(i.main).name) for i in sc.plan(tmp_path, None)}
    assert actions['AXN4 PSET data_TOSHIRO_HOMEX_12-04-123341-2025_Conflict.csv'][::2] == ('archive', 'AXN4 PSET data.csv')
    assert actions['AXN1 TA data_PC-1_9-01-181643-2026_Conflict.csv'][0] == 'archive'
    assert '繰り返し' in actions['AXN1 TA data_PC-1_9-01-181643-2026_Conflict.csv'][1]
    assert actions['SC89-4412-12 (OOO TTT の競合コピー 2026-07-15).csv'][0] == 'archive'
    assert '共通の先頭 12 列' in actions['SC89-4412-12 (OOO TTT の競合コピー 2026-07-15).csv'][1]


def test_very_long_copy_names_are_shortened_in_the_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, 'MAX_PATH', 80)
    db = tmp_path / 'machinedb' / 'TA'
    write(db / 'AXN1 TA data.csv', ['a,SC1-1-1,,,1,t'])
    long_name = 'AXN1 TA data' + ''.join(f'_PC-1_9-01-18172{i}-2026_conflict_current' for i in range(2)) + '.csv'
    write(db / long_name, ['a,SC1-1-1,,,1,t'])
    session, results = sc.apply(sc.plan(tmp_path, None), tmp_path)
    assert results[0]['status'] == 'done' and not (db / long_name).exists()
    kept = list((session / 'copies').rglob('*.csv'))
    assert len(kept) == 1 and len(kept[0].name) < len(long_name)
