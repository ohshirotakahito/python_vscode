import json
from zipfile import ZipFile

import pytest

from scoring import machine_data, report_history


@pytest.mark.parametrize('zipped', [False, True])
def test_saved_reports(tmp_path, monkeypatch, zipped):
    name = '20261125_1004 UNAN#8Pex5n1_o163'
    monkeypatch.setattr(machine_data, 'machines', lambda: {'AXN8': {'SQ_backup': str(tmp_path)}})
    files = {
        'AC/R_AC#1_20261125_1006_record.txt': 'AC QC,Good\n備考,確認済み'.encode('cp932'),
        'AC/PM_AC#1_20261125_1006_.txt': b'log',
        'PSET/D_PSET#1_20261125_1007.txt': b'log',
        'AutoGG/AutoGG_review.json': json.dumps({
            'finished': '2026-11-25T10:08:00', 'overall': {'score': 42.4},
        }).encode(),
    }
    if zipped:
        with ZipFile(tmp_path / f'{name}.zip', 'w') as archive:
            for path, data in files.items():
                archive.writestr(f'{name}/{path}', data)
    else:
        for path, data in files.items():
            target = tmp_path / name / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    location, reports = report_history.load(name, 'AXN8')
    assert location.endswith(f'{name}.zip' if zipped else name)
    assert [report.operation for report in reports] == ['Auto GG', 'PSET#1', 'AC#1']
    assert reports[0].summary == '参考スコア 42.4 / 100'
    assert reports[1].summary == '記録ファイル 1 件'
    assert reports[2].summary == 'AC QC = Good'
    assert '備考,確認済み' in reports[2].details
    assert 'PM_AC#1' in reports[2].details
    assert report_history.load(name, 'AXN2') == ('', [])
