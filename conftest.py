"""pytest をどのフォルダから実行しても shared/ を import できるようにする。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_writes_to_the_shared_transfer_record(tmp_path, monkeypatch):
    r"""転送・移動の追跡記録（D:\Qstatus）にテストが書かないよう、一時フォルダへ向ける。"""
    from shared import ex_info_links
    monkeypatch.setattr(ex_info_links, 'TRANSFERS', tmp_path / 'SEQ' / 'Transfers.jsonl')
    from shared import destination_history
    monkeypatch.setattr(destination_history, 'SHARED_MOVES', tmp_path / 'SEQ' / 'DestinationMoves')
