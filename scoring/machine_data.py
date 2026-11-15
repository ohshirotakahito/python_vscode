"""machinedb（D:\Qstatus\machinedb）の装置一覧・各工程の data.csv・計測時の Score.csv を読む（読むだけ）。"""
from datetime import datetime
from pathlib import Path
import re

from shared import machinedb, seq_records

ENCODINGS = ('cp932', 'utf-8-sig')
STAGES = ('FC', 'MA', 'TA', 'AC', 'CAL')          # Auto GG の 1 回分（AMP Check・Gap Check は除く）
SCORE_KINDS = ('EXSV', 'AC', 'AutoGG')
_MACHINE_RE = re.compile(r'AN#(\d+)P')              # '20240220_0833 ANAN#2Pex1n1_LE' → AXN2


def machines(path=None):
    """{'AXN2': {'Tmp_backup': ..., 'SQ_backup': ..., 'PC Directroy': ...}, ...}（Machine_N.csv）。"""
    path = seq_records.MACHINE_CSV if path is None else Path(path)
    return seq_records.machines(path)


def measurement_machines(table=None):
    """計測装置のコード（Tmp_backup が UNC のもの）、名前順。"""
    table = machines() if table is None else table
    return sorted(code for code, info in table.items()
                  if str(info.get('Tmp_backup', '')).startswith('\\') and code.upper().startswith('AXN'))


def machine_of(experimental_id):
    """Experimental ID から装置コード（'AN#2P' → 'AXN2'）。読めなければ None。"""
    m = _MACHINE_RE.search(experimental_id or '')
    return f'AXN{int(m.group(1))}' if m else None


def experiment_time(experimental_id):
    """'20240220_0833 ANAN#2Pex1n1_LE' → datetime(2024, 2, 20, 8, 33)。読めなければ None。"""
    try:
        return datetime.strptime(str(experimental_id)[:13], '%Y%m%d_%H%M')
    except ValueError:
        return None


def run_time(when):
    """工程の時刻 '20240220_08:38' / '20240220_0838' → datetime。読めなければ None。"""
    text = str(when).strip().replace(':', '')
    try:
        return datetime.strptime(text[:13], '%Y%m%d_%H%M')
    except ValueError:
        return None


def decode(data):
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode(ENCODINGS[0], errors='replace')


def read_table(path):
    """(header, rows) を AXN と同じ区切り方（カンマで split）で読む。無ければ ([], [])。"""
    path = Path(path)
    if not path.exists():
        return [], []
    lines = decode(path.read_bytes()).splitlines()
    if not lines:
        return [], []
    return lines[0].split(','), [line.split(',') for line in lines[1:] if line.strip(',')]


def data_rows(machine, stage, root=None):
    """machinedb\<stage>\<machine> <stage> data.csv の行（新しい順のまま）。列名は元のまま（末尾の空白つき）。"""
    root = machinedb.ROOT if root is None else Path(root)
    header, rows = read_table(root / stage / f'{machine} {stage} data.csv')
    return [dict(zip(header, row)) for row in rows]


def score_history_path(machine, kind, root=None):
    root = machinedb.ROOT if root is None else Path(root)
    return root / kind / f'{machine} {kind} Score.csv'


def score_history(machine, kind, root=None):
    """計測時の Score.csv（AXN が書く）の行を {列名: 値} で。無ければ []。"""
    header, rows = read_table(score_history_path(machine, kind, root))
    return [dict(zip(header, row)) for row in rows]


def upload_dir():
    """OUNmain03 が毎時書く SQserver の一覧（AXN upload-inventory）の置き場所。"""
    return machinedb.ROOT.parent / 'database' / 'Upload'


def inventory(machine, folder=None):
    """{Experimental ID: 'Tmp_backup' / 'SQ_backup' / 'Tmp_backup + SQ_backup'}: SQserver にあるその装置の実験。

    `<machine> SQ inventory.csv`（Tmp_backup）と `<machine> SQ archive inventory.csv`（SQ_backup）。
    1 行目は書いた日時など、2 行目が列名、3 行目から実験。"""
    import csv
    folder = upload_dir() if folder is None else Path(folder)
    out = {}
    for filename, place in ((f'{machine} SQ inventory.csv', 'Tmp_backup'),
                            (f'{machine} SQ archive inventory.csv', 'SQ_backup')):
        target = folder / filename
        if not target.exists():
            continue
        with open(target, encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
        for row in rows[2:]:
            if row and row[0]:
                out[row[0]] = f'{out[row[0]]} + {place}' if row[0] in out else place
    return out


def scored_machines(root=None):
    """Score.csv が 1 つでもある装置。"""
    root = machinedb.ROOT if root is None else Path(root)
    names = set()
    for kind in SCORE_KINDS:
        names.update(p.name[:-len(f' {kind} Score.csv')] for p in (root / kind).glob(f'* {kind} Score.csv'))
    return sorted(names)
