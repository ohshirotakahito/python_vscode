import json

import pytest

from chipdb.registry import Store, find


def store_at(tmp_path):
    db = tmp_path / 'ChipDB' / 'DB'
    db.mkdir(parents=True)
    return Store(db)


def test_checkin_and_edit_metadata_preserve_identity(tmp_path):
    store = store_at(tmp_path)
    data = store.save_chip('SC25_01', {'state': '保管中', 'location': '棚1', 'fabricated_on': '2026-11-13'}, 0, new=True)
    chip = find(data, 'sc25-01')
    assert chip['code'] == 'SC25-01'
    assert not list(store.db.glob('*.csv'))
    assert chip['snapshot'] is None
    key = chip['key']
    # Unrecognized AXN fields must survive editing.
    chip['custom_field'] = {'retained': True}
    store.path.write_text(json.dumps(data), encoding='utf-8')
    updated = store.save_chip('SC25-01', {'user': '担当', 'state': '使用中'}, 1)
    saved = find(updated, 'SC25-01')
    assert saved['key'] == key
    assert saved['location'] == '棚1'
    assert saved['custom_field'] == {'retained': True}
    assert saved['user'] == '担当'
    backup = next((store.path.parent / 'backups').glob('chip_info_*.json'))
    assert json.loads(backup.read_text(encoding='utf-8')) == data


def test_maker_and_organization_are_saved_and_unknown_fields_survive(tmp_path):
    store = store_at(tmp_path)
    data = store.save_chip('SC91-01', {'origin': 'SCREEN', 'organization': '大阪大学 谷口研'}, 0, new=True)
    chip = data['chips'][0]
    assert (chip['origin'], chip['organization']) == ('SCREEN', '大阪大学 谷口研')
    # 別のアプリ（AXN など）が足した、ここで知らない項目も保存で消えない
    raw = json.loads(store.path.read_text(encoding='utf-8'))
    raw['chips'][0]['axn_only'] = 'keep'
    store.path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    data = store.save_chip('SC91-01', {'organization': 'Quantum Operation'}, data['revision'])
    assert data['chips'][0]['axn_only'] == 'keep' and data['chips'][0]['organization'] == 'Quantum Operation'


def test_duplicate_and_external_revision_rejected(tmp_path):
    store = store_at(tmp_path)
    store.save_chip('SC25-01', {}, 0, new=True)
    original = store.path.read_bytes()
    with pytest.raises(ValueError, match='既に存在'):
        store.save_chip('sc25_01', {}, 1, new=True)
    with pytest.raises(ValueError, match='外部'):
        store.save_chip('SC25-01', {'description': 'stale'}, 0)
    assert store.path.read_bytes() == original


def test_axn_lock_and_invalid_inputs(tmp_path):
    store = store_at(tmp_path)
    store.path.parent.mkdir(parents=True)
    lock = store.path.with_suffix('.lock')
    lock.touch()
    with pytest.raises(OSError, match='別の'):
        store.save_chip('chip', {}, 0, new=True)
    assert lock.exists()
    lock.unlink()
    for code, values in [('../chip', {}), ('chip', {'fabricated_on': '2026-02-30'}), ('chip', {'state': 'bad'})]:
        with pytest.raises(ValueError):
            store.save_chip(code, values, 0, new=True)
    assert not store.path.exists()


def test_existing_csv_metadata_keeps_live_data_and_axn_snapshot(tmp_path):
    store = store_at(tmp_path)
    path = store.db / 'chip.csv'
    body = 'chip,,,,,,1\r\nExperiment,○,20261113_1234,担当,EX1,FC#1,1,1.5,50,time\r\nBreak\r\nEND\r\n'.encode('cp932')
    path.write_bytes(body)
    with pytest.raises(ValueError, match='既に存在'):
        store.save_chip('chip', {}, 0, new=True)
    data = store.save_chip('chip', {'description': '既存チップ'}, 0)
    snapshot = find(data, 'chip')['snapshot']
    assert snapshot['text'].encode('cp932') == body
    assert snapshot['summary']['FC LAST'] == '1.5'
    assert snapshot['summary']['Operator Last'] == '担当'
    assert path.read_bytes() == body


def test_invalid_registry_is_not_overwritten(tmp_path):
    store = store_at(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError):
        store.save_chip('chip', {}, 0, new=True)
    assert store.path.read_text(encoding='utf-8') == '{broken'
    assert not store.path.with_suffix('.lock').exists()
