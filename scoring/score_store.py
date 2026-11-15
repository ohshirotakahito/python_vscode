"""後から計算したスコアの保存先: D:\\Qstatus\\database\\Scores（計測時の Score.csv とは別のファイル）。

各行に計算元（計測時 / 再構成）・時代（LabVIEW / Python）・基準の版（AXN のコミットと定数の指紋）を持つ
（引き継ぎ資料「設計上のお願い」）。列名は AXN の Score.csv と同じ名前を使い、同じ列が同じ意味になるようにする。
UTF-8（BOM 付き）CSV、新しい順。書くときは一時ファイルに書いてから置き換える（同期の途中で半分にならない）。
"""
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import socket
from uuid import uuid4

from shared import machinedb

from . import machine_data

ENCODING = 'utf-8-sig'
MEASURED, RECONSTRUCTED = '計測時', '再構成'
ERA_PYTHON, ERA_LABVIEW, ERA_UNKNOWN = 'Python', 'LabVIEW', '不明'
# Python 版 AXN で計測を始めた日（それより前は LabVIEW）。era.json で装置ごとに変えられる
DEFAULT_ERA_SWITCH = {'AXN2': '2026-11-01', 'AXN3': '2026-11-01'}

PROVENANCE = ('Machine', 'Source', 'Era', 'Criteria', 'Files', 'Computed at UTC', 'Computed by')
AC_SCORE_FIELDS = ('AC Score', 'AC Score Detail', 'AC Score n0.8-5G0 Point [n]', 'AC Score n0.9-1.2G0 Point [n]',
                   'AC Score 20uA Drop [s]', 'AC Score Low Drop [s]', 'AC Score End I [pA]')
AC_COLUMNS = ('Experimental ID', 'Device Code', 'AC#', 'AC Time', 'AC QC') + AC_SCORE_FIELDS
EXSV_RUN_FIELDS = ('Experimental ID', 'Device Code', 'EXSV#', 'EXSV Time', 'EXSV Set Distance [nm]',
                   'EXSV Set Tunnel Current [pA]', 'EXSV Target BL Current [pA]', 'EXSV Total Signal # [n]')
EXSV_SCORE_FIELDS = ('EXSV Score', 'EXSV Score Detail', 'EXSV Score Signals [/s]', 'EXSV Score RMS [pA]',
                     'EXSV Score RMS change', 'EXSV Score Range in [%]', 'EXSV Score Piezo path [V]',
                     'EXSV Score Start-end [V]', 'EXSV Score Piezo std [V]')
EXSV_COLUMNS = EXSV_RUN_FIELDS + EXSV_SCORE_FIELDS
GG_STAGE_COLUMNS = tuple(f'{stage} score' for stage in machine_data.STAGES)
GG_COLUMNS = (('Experimental ID', 'Device Code', 'Round', 'Round start', 'Round end', 'AutoGG Score', 'Stages scored',
               'Stages used', 'Stages missing') + GG_STAGE_COLUMNS + ('Decision',))


class Kind:
    def __init__(self, name, filename, columns, key, time_column, number_column=None):
        self.name, self.filename, self.columns, self.key, self.time_column = name, filename, columns, key, time_column
        self.number_column = number_column
        self.all_columns = tuple(columns) + PROVENANCE

    def row_key(self, row):
        return tuple(str(row.get(k, '')) for k in self.key)


KINDS = {
    'AC': Kind('AC', 'ac_scores.csv', AC_COLUMNS, ('Source', 'Experimental ID', 'AC#', 'AC Time'), 'AC Time', 'AC#'),
    'EXSV': Kind('EXSV', 'exsv_scores.csv', EXSV_COLUMNS, ('Source', 'Experimental ID', 'EXSV#', 'EXSV Time'),
                 'EXSV Time', 'EXSV#'),
    'AutoGG': Kind('AutoGG', 'autogg_scores.csv', GG_COLUMNS, ('Source', 'Experimental ID', 'Round start'),
                   'Round start', 'Round'),
}
SCORE_COLUMN = {'AC': 'AC Score', 'EXSV': 'EXSV Score', 'AutoGG': 'AutoGG Score'}
LEDGER_NAME = 'experiment_scores.csv'


def default_dir():
    env = os.environ.get('PYVSCODE_SCORES')
    if env:
        return Path(env)
    return machinedb.ROOT.parent / 'database' / 'Scores'


def path(kind, folder=None):
    folder = default_dir() if folder is None else Path(folder)
    return folder / KINDS[kind].filename


def read(kind, folder=None):
    """保存済みの行（{列名: 値}、ファイルの順 = 新しい順）。無ければ []。"""
    target = path(kind, folder)
    if not target.exists():
        return []
    with open(target, encoding=ENCODING, newline='') as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def write_atomic(target, text):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.stem}_{uuid4().hex}.tmp')
    with open(temporary, 'w', encoding=ENCODING, newline='') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def write_csv(target, columns, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(column, '') for column in columns])
    write_atomic(target, buffer.getvalue())


def _sort_key(kind, row):
    return (str(row.get(kind.time_column, '')), str(row.get(kind.number_column, '') if kind.number_column else ''))


def upsert(kind_name, rows, folder=None):
    """行を足す（同じ鍵の行は置き換える）。新しい順に並べて書く。戻り値は (足した数, 置き換えた数)。"""
    kind = KINDS[kind_name]
    existing = read(kind_name, folder)
    by_key = {kind.row_key(row): row for row in existing}
    added = replaced = 0
    for row in rows:
        key = kind.row_key(row)
        if key in by_key:
            replaced += 1
        else:
            added += 1
        by_key[key] = {column: str(row.get(column, '')) for column in kind.all_columns}
    ordered = sorted(by_key.values(), key=lambda r: _sort_key(kind, r), reverse=True)
    write_csv(path(kind_name, folder), kind.all_columns, ordered)
    return added, replaced


def remove(kind_name, predicate, folder=None):
    """predicate(row) が真の行を消す。戻り値は消した数。"""
    kind = KINDS[kind_name]
    existing = read(kind_name, folder)
    kept = [row for row in existing if not predicate(row)]
    if len(kept) != len(existing):
        write_csv(path(kind_name, folder), kind.all_columns, kept)
    return len(existing) - len(kept)


def provenance(machine, source, era, criteria, files):
    return {'Machine': machine, 'Source': source, 'Era': era, 'Criteria': criteria, 'Files': files,
            'Computed at UTC': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            'Computed by': socket.gethostname()}


# --- 時代（LabVIEW / Python） ---
def era_path(folder=None):
    folder = default_dir() if folder is None else Path(folder)
    return folder / 'era.json'


def era_table(folder=None):
    """{装置: 'YYYY-MM-DD'}: その日以降の計測が Python 版 AXN。無ければ既定を書いて返す。"""
    target = era_path(folder)
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
        return {k: v for k, v in data.get('python_since', {}).items() if isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'_comment': 'Python 版 AXN で計測を始めた日（装置ごと）。それより前の計測は LabVIEW 時代。',
                                      'python_since': DEFAULT_ERA_SWITCH}, ensure_ascii=False, indent=2),
                          encoding='utf-8')
    except OSError:
        pass
    return dict(DEFAULT_ERA_SWITCH)


def era_of(machine, when, table):
    """計測の時代。when は datetime（実験の日時）。装置が表に無ければ LabVIEW（Python 版はまだ）、日時不明なら 不明。"""
    since = table.get(machine or '')
    if when is None:
        return ERA_UNKNOWN
    if not since:
        return ERA_LABVIEW
    try:
        return ERA_PYTHON if when >= datetime.strptime(since, '%Y-%m-%d') else ERA_LABVIEW
    except ValueError:
        return ERA_UNKNOWN


def number(value):
    try:
        x = float(value)
        return x if x == x else None
    except (TypeError, ValueError):
        return None
