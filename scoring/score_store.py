"""後から計算したスコアの保存先: D:\\Qstatus\\database\\Scores（計測時の Score.csv とは別のファイル）。

各行に計算元（計測時 / 再構成）・時代（LabVIEW / Python）・基準の版（AXN のコミットと定数の指紋）を持つ
（引き継ぎ資料「設計上のお願い」）。列名は AXN の Score.csv と同じ名前を使い、同じ列が同じ意味になるようにする。
UTF-8（BOM 付き）CSV、新しい順。書くときは一時ファイルに書いてから置き換える（同期の途中で半分にならない）。
"""
import csv
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import socket
from uuid import uuid4

from shared import machinedb

from . import machine_data

ENCODING = 'utf-8-sig'
MEASURED, RECONSTRUCTED = '計測時', '再構成'
ERA_PYTHON, ERA_LABVIEW, ERA_MIXED, ERA_UNKNOWN = 'Python', 'LabVIEW', '混在', '不明'
ERAS = (ERA_PYTHON, ERA_LABVIEW, ERA_MIXED, ERA_UNKNOWN)
# Python 版 AXN の開発を始めた日。これより前の計測は必ず LabVIEW。これ以降は 1 回ごとにファイルの目印で判定する
# （同じ装置でも実験ごと、1 実験の中でも LabVIEW と Python が入れ替わるため、日付の境目では決められない。2026-11-18 確認）
PYTHON_EARLIEST = datetime(2026, 10, 29)

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
def era_by_date(when):
    """日時だけで決まる時代: Python 版の開発より前なら LabVIEW、それ以外（日時不明を含む）は 不明。"""
    return ERA_LABVIEW if when is not None and when < PYTHON_EARLIEST else ERA_UNKNOWN


def combine_eras(eras):
    """いくつかの回の時代をまとめる: 1 種類ならそれ、Python と LabVIEW が両方あれば 混在、分からなければ 不明。"""
    eras = list(eras)
    known = {e for e in eras if e in (ERA_PYTHON, ERA_LABVIEW)}
    if ERA_MIXED in eras or len(known) > 1:
        return ERA_MIXED
    return known.pop() if known else ERA_UNKNOWN


def sync_measured_eras(folder=None):
    """計測時の行の時代を、同じ回の再構成の行（ファイルの目印で判定した時代）に合わせる。戻り値は直した行数。

    計測時の Score.csv には、Python 版が計測時に書いた行と、LabVIEW の計測を後から採点した行（AXN の backfill）が
    混ざっているので、Score.csv だけでは時代が分からない。Auto GG の Score.csv は Python 版だけが書く。"""
    changed = 0
    for kind_name, kind in KINDS.items():
        rows = read(kind_name, folder)
        number = kind.number_column if kind_name != 'AutoGG' else None
        eras = {}
        if number:
            eras = {(r['Experimental ID'], r.get(number, ''), r.get(kind.time_column, '')): r.get('Era', '')
                    for r in rows if r.get('Source') == RECONSTRUCTED}
        dirty = False
        for r in rows:
            if r.get('Source') != MEASURED:
                continue
            era = ERA_PYTHON if kind_name == 'AutoGG' else eras.get(
                (r['Experimental ID'], r.get(number, ''), r.get(kind.time_column, '')))
            if era and era != r.get('Era'):
                r['Era'] = era
                dirty = True
                changed += 1
        if dirty:
            write_csv(path(kind_name, folder), kind.all_columns, rows)
    return changed


def number(value):
    try:
        x = float(value)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def remap_devices(rename, folder=None):
    """保存済みの行の Device Code を rename(code) で置き換える（チップIDの修正の後に一度）。戻り値は {kind: 直した行数}。"""
    result = {}
    for kind_name, kind in KINDS.items():
        rows = read(kind_name, folder)
        changed = 0
        for row in rows:
            new = rename(row.get('Device Code', ''))
            if new != row.get('Device Code', ''):
                row['Device Code'] = new
                changed += 1
        if changed:
            write_csv(path(kind_name, folder), kind.all_columns, rows)
        result[kind_name] = changed
    return result
