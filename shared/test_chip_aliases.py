"""チップIDの旧名 → 新名の対応表。"""
import json
from pathlib import Path

import pytest

from shared import chip_aliases as aliases


def test_add_lookup_and_mark_steps(tmp_path):
    path = aliases.table_path(tmp_path / 'ChipDB' / 'DB')
    assert path == tmp_path / 'machinedb' / 'PLAN' / 'chip_aliases.json'
    added, data = aliases.add_entries(path, [
        ('SC080-4307-12', 'SC80-4307-12', 'ロット番号の先頭の 0', False, '予定'),
        ('SC25-2115-9', 'SC25-2115-09', 'チップ番号の 0 埋め', True, '予定')])
    assert added == 2 and data['revision'] == 1
    assert aliases.new_name('sc080-4307-12', data) == 'SC80-4307-12'
    assert aliases.new_name('SC80-4307-12', data) == 'SC80-4307-12'
    assert aliases.old_names('SC25-2115-09', data) == ['SC25-2115-9']
    assert aliases.add_entries(path, [('SC080-4307-12', 'X', '', False, '予定')])[0] == 0      # 既にある旧名は変えない
    data = aliases.mark_step(path, ['SC080-4307-12'], 'chipdb')
    entry = next(e for e in data['entries'] if e['old'] == 'SC080-4307-12')
    assert entry['status'] == '進行中' and entry['steps']['chipdb']
    for step in ('machinedb_retired', 'machinedb_active'):
        data = aliases.mark_step(path, ['SC080-4307-12'], step)
    assert next(e for e in data['entries'] if e['old'] == 'SC080-4307-12')['status'] == '完了'   # 統合なし: 3 段階で完了
    data = aliases.mark_step(path, ['SC25-2115-9'], 'chipdb')
    assert next(e for e in data['entries'] if e['old'] == 'SC25-2115-9')['status'] == '進行中'
    assert len(list((path.parent / 'backups').glob('chip_aliases_*.json'))) >= 3


def test_rejects_chains_duplicates_stale_revision_and_conflict_copies(tmp_path):
    path = aliases.table_path(tmp_path)
    aliases.add_entries(path, [('a1', 'A-01', '', False, '予定')])
    with pytest.raises(ValueError, match='連鎖'):
        aliases.add_entries(path, [('A-01', 'A-001', '', False, '予定')])
    assert aliases.add_entries(path, [('sc49-3310-51', 'SC49-3310-51', '小文字', False, '予定')])[0] == 1   # 大文字小文字だけ
    with pytest.raises(ValueError, match='2 回'):
        aliases.validate(dict(schema_version=1, revision=0, entries=[dict(old='x', new='X1'), dict(old='X', new='X2')]))
    data = aliases.load(path)
    with pytest.raises(ValueError, match='別の場所で更新'):
        aliases.save(path, data, data['revision'] - 1)
    (path.parent / 'chip_aliases_PC_Conflict.json').write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(OSError, match='競合コピー'):
        aliases.save(path, data, data['revision'])
    assert not path.with_suffix('.lock').exists()


def test_wafer_and_lot_of_ids():
    assert aliases.wafer_of('SC32-2416-30-2') == 'SC32-2416'
    assert aliases.wafer_of('SC25-2115-09') == 'SC25-2115'
    assert aliases.wafer_of('OU02-02-17') == 'OU02-02'
    assert aliases.old_wafer_of('SC91-4419--07') == 'SC91-4419-'
    assert aliases.lot_of('SC32-2416-30-2') == 'SC32'
