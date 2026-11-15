from pathlib import Path

import pytest

from chipdb.store import Document, db_folder


def make_document(tmp_path):
    folder = tmp_path / 'ChipDB' / 'DB'
    folder.mkdir(parents=True)
    path = folder / 'chip.csv'
    path.write_bytes('Header,,,,,,1\r\nExperiment,○,20261113,担当,EX1,CAL#1,1,2.5\r\nBreak\r\nEND\r\n'.encode('cp932'))
    return Document(path)


def test_save_preserves_axn_format_and_backup(tmp_path):
    doc = make_document(tmp_path)
    original = doc.original
    rows = [list(r) for r in doc.rows]
    rows[1][3] = '修正'
    backup = doc.save(rows)
    assert backup.read_bytes() == original
    assert Document(doc.path).rows == rows
    assert b'\r\n' in doc.path.read_bytes()


def test_rejects_external_update(tmp_path):
    doc = make_document(tmp_path)
    doc.path.write_bytes(b'external update')
    with pytest.raises(ValueError, match='外部'):
        doc.save(doc.rows)
    assert doc.path.read_bytes() == b'external update'


def test_rejects_invalid_field(tmp_path):
    doc = make_document(tmp_path)
    rows = [list(r) for r in doc.rows]
    rows[1][3] = 'bad,value'
    with pytest.raises(ValueError):
        doc.save(rows)
    assert doc.path.read_bytes() == doc.original


def test_folder_variants(tmp_path):
    assert db_folder(tmp_path) == tmp_path / 'ChipDB' / 'DB'
    assert db_folder(tmp_path / 'ChipDB') == tmp_path / 'ChipDB' / 'DB'
    assert db_folder(tmp_path / 'ChipDB' / 'DB') == tmp_path / 'ChipDB' / 'DB'


def test_selected_root_persists_even_offline(tmp_path, monkeypatch):
    from chipdb import store
    monkeypatch.setattr(store, 'settings_path', lambda: tmp_path / 'settings.json')
    monkeypatch.setenv('AXN_LABVIEW_ROOT', str(tmp_path / 'other'))
    selected = tmp_path / 'Qstatus'
    store.save_root(selected)
    assert not selected.exists()
    assert store.default_root() == selected.resolve()
