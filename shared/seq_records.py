"""SEQ の装置一覧CSVと転送履歴CSVの読み込み（exmanager が書き、database が読む）。"""
from pathlib import Path
import csv
from datetime import datetime

from shared import REPO_ROOT, machinedb

SEQ_DB = machinedb.SEQ
MACHINE_CSV = SEQ_DB / 'Machine_N.csv'
LEGACY_HISTORY = SEQ_DB / 'Uploader.csv'                          # LabVIEW版の履歴
LOCAL_HISTORY = REPO_ROOT / 'exmanager' / 'logs' / 'Uploader_python.csv'  # Python版（exmanager）の履歴
HEADERS = 'uploaded_at operator_code machine_code data_selection experiment_at uploader_code source_experiment destination_experiment sample folder_10k prefix_10k start_10k end_10k folder_100k prefix_100k start_100k end_100k'.split()


def rows(path):
    data = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'cp932'):
        try:
            return list(csv.reader(data.decode(encoding).splitlines()))
        except UnicodeDecodeError:
            pass
    raise ValueError(f'文字コードを判定できません: {path}')


def machines(path):
    data = rows(path)
    return {r[0]: dict(zip(data[0], r)) for r in data[1:] if r and r[0]}


def history(legacy, local):
    result = set()
    for path in (legacy, local):
        if not Path(path).exists():
            if Path(path) == Path(legacy):
                raise FileNotFoundError(path)
            continue
        for row in rows(path):
            if len(row) >= 17 and row[0] and row[0] != HEADERS[0]:
                result.add(tuple(row[:17]))
    return sorted(result, key=lambda r: datetime.strptime(r[0], '%Y/%m/%d_%H:%M'), reverse=True)
