"""計測記録のチップIDの書き換え（段階 3・4）。"""
from pathlib import Path

from chipdb import record_ids

HEADER = 'Experimental ID,Device Code,Device Wafer,Device Lot,Device Condition,Machine selection,FC#,Note,,,'


def write(path, rows, header=HEADER):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('\r\n'.join([header] + rows) + '\r\n').encode('cp932'))


def test_only_exact_device_code_rows_change_and_everything_else_is_kept(tmp_path):
    db = tmp_path / 'machinedb'
    retired = db / 'FC' / 'AX2 FC data.csv'
    write(retired, ['20190618_1513 ANAX#2_x,SC080-4307-12,SC080-4307,SC080,New,AX#2,1,ｃｍ SC080-4307-12 note,,,',
                    '20190618_1600 ANAX#2_y,SC080-4307-120,SC080-4307,SC080,New,AX#2,2,,,,',      # 似ているが別
                    '20190619_0900 ANAX#2_z,SC25-2115-2,SC25-2115,SC25,New,AX#2,3,,,,'])
    active = db / 'FC' / 'AXN2 FC data.csv'
    write(active, ['20261113_1401 JMAN#2_a,SC91-4419--07,SC91-4419-,SC91,New,AXN#2,1,,,,'])
    write(db / 'FC' / 'AXN2 FC data_DESKTOP-X_Conflict.csv', ['x,SC91-4419--07,SC91-4419-,SC91,,,,,,,'])
    mapping = {'SC080-4307-12': 'SC80-4307-12', 'SC25-2115-2': 'SC25-2115-02', 'SC91-4419--07': 'SC91-4419-07'}
    plans = record_ids.plan(db, mapping)
    by_name = {Path(p.path).name: p for p in plans}
    assert sorted(by_name) == ['AX2 FC data.csv', 'AXN2 FC data.csv']             # 競合コピーは対象外
    assert by_name['AX2 FC data.csv'].group == 'retired' and by_name['AXN2 FC data.csv'].group == 'active'
    assert by_name['AX2 FC data.csv'].rows == 2
    original_active = active.read_bytes()
    session, results = record_ids.apply(plans, tmp_path / 'backups', groups=('retired',))
    text = retired.read_bytes().decode('cp932')
    assert text.splitlines()[1] == '20190618_1513 ANAX#2_x,SC80-4307-12,SC80-4307,SC80,New,AX#2,1,ｃｍ SC080-4307-12 note,,,'
    assert ',SC080-4307-120,SC080-4307,SC080,' in text                              # 完全一致でないものは触らない
    assert ',SC25-2115-02,SC25-2115,SC25,' in text                                   # Wafer・Lot は変わらない
    assert retired.read_bytes().count(b'\r\n') == 4                                   # 改行の形も同じ
    assert active.read_bytes() == original_active                                     # 稼働中の装置はまだ
    assert [r['status'] for r in results] == ['done']
    assert (session / 'originals' / 'FC' / 'AX2 FC data.csv').exists() and (session / 'journal.json').exists()
    session2, results2 = record_ids.apply(plans, tmp_path / 'backups', groups=('active',))
    assert ',SC91-4419-07,SC91-4419,SC91,' in active.read_bytes().decode('cp932')    # 「SC91-4419-」も直る
    assert record_ids.restore(session) == [(str(retired), 'restored')]
    assert ',SC080-4307-12,SC080-4307,SC080,' in retired.read_bytes().decode('cp932')


def test_file_written_after_planning_is_skipped(tmp_path):
    db = tmp_path / 'machinedb'
    active = db / 'MA' / 'AXN8 MA data.csv'
    write(active, ['20190809_1040 ANAN#8_a,sc77-4218-22,sc77-4218,sc77,Used,AXN#8,1,,,,'])
    plans = record_ids.plan(db, {'sc77-4218-22': 'SC77-4218-22'})
    lines = active.read_bytes().split(b'\r\n')
    active.write_bytes(b'\r\n'.join([lines[0], b'20261118_2100 new row,SC91-0000-10,,,,,,,,,'] + lines[1:]))  # 計測で書き足し
    before = active.read_bytes()
    _, results = record_ids.apply(plans, tmp_path / 'backups', groups=('active',))
    assert results[0]['status'].startswith('skipped') and active.read_bytes() == before
