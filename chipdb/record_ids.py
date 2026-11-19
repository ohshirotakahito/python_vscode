"""計測記録（machinedb/<工程>/<装置> <工程> data.csv など）のチップIDを、旧名 → 新名の対応表に従って直す。

チップIDの修正の段階 3・4（docs/chip_id_cleanup.md）。LabVIEW と AXN が計測のたびに書き足すファイルなので、
- 中身はバイトのまま扱い、直すのは「Device Code」列が旧名と完全に一致する行の、Device Code・Device Wafer・Device Lot の
  3 つのセルだけ（Wafer・Lot は旧名から作った値と一致するときだけ新名から作った値に）。文字コード（cp932）・改行・
  行の順番・ほかの列はそのまま
- 作業前に元のファイルを全部バックアップし、直したセルの一覧（journal.json）を残す（元に戻せるように）
- 置き換える直前にもう一度読み、計画を作ったときと同じであることを確かめる。変わっていたら（計測で書き足された）
  そのファイルは止める
- 同期の競合コピー（*_Conflict.csv）は対象にしない（段階 8 で別に確かめる）
"""
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from shared import chip_aliases

ID_COLUMNS = ('Device Code', 'Device Wafer', 'Device Lot')
RETIRED_NAMES = ('AX1 ', 'AX2 ', 'AX3 ')           # 引退した装置の記録
CONFLICT = re.compile(r'_Conflict', re.I)


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class FilePlan:
    path: str
    group: str                       # retired / active
    hash: str
    mtime_ns: int
    changes: list = field(default_factory=list)     # [(行番号, 列名, 旧, 新)]
    body: bytes = b''

    @property
    def rows(self):
        return len({c[0] for c in self.changes})


def group_of(path, machinedb):
    """引退した装置（AX1・AX2）か、装置名のない古い共通ファイルなら retired。それ以外は active（稼働中の装置）。"""
    name = Path(path).name
    if name.startswith(RETIRED_NAMES) or not re.match(r'(AXN?\d+|[A-Z]_Local) ', name):
        return 'retired'
    return 'active'


def plan_file(path, mapping, machinedb):
    """1 ファイルの計画。mapping: {旧名: 新名}（大文字小文字そのまま、完全一致）。直す所がなければ None。"""
    path = Path(path)
    stat = path.stat()
    data = path.read_bytes()
    lines = data.split(b'\n')
    if not lines:
        return None
    header = lines[0].rstrip(b'\r').decode('cp932', errors='replace').split(',')
    names = [h.strip() for h in header]
    if 'Device Code' not in names:
        return None
    index = {name: names.index(name) for name in ID_COLUMNS if name in names}
    old_bytes = {old.encode('cp932'): new.encode('cp932') for old, new in mapping.items()}
    changes, out = [], [lines[0]]
    for number, line in enumerate(lines[1:], start=2):
        ending = b'\r' if line.endswith(b'\r') else b''
        cells = line[:-1].split(b',') if ending else line.split(b',')
        code_at = index['Device Code']
        if code_at < len(cells) and cells[code_at] in old_bytes:
            old, new = cells[code_at], old_bytes[cells[code_at]]
            pairs = {'Device Code': (old, new),
                     'Device Wafer': (chip_aliases.old_wafer_of(old.decode()).encode(),
                                      chip_aliases.wafer_of(new.decode()).encode()),
                     'Device Lot': (chip_aliases.lot_of(old.decode()).encode(), chip_aliases.lot_of(new.decode()).encode())}
            for name, at in index.items():
                before, after = pairs[name]
                if at < len(cells) and cells[at] == before and before != after:
                    cells[at] = after
                    changes.append((number, name, before.decode('cp932'), after.decode('cp932')))
            line = b','.join(cells) + ending
        out.append(line)
    if not changes:
        return None
    return FilePlan(str(path), group_of(path, machinedb), digest(data), stat.st_mtime_ns, changes, b'\n'.join(out))


def plan(machinedb, mapping):
    """machinedb の全 csv（競合コピーを除く）の計画。"""
    machinedb = Path(machinedb)
    plans = []
    for path in sorted(machinedb.glob('*/*.csv')):
        if CONFLICT.search(path.name):
            continue
        try:
            result = plan_file(path, mapping, machinedb)
        except OSError:
            continue
        if result:
            plans.append(result)
    return plans


def apply(plans, backup_root, groups=('retired',)):
    """計画を適用する（groups に入るファイルだけ）。戻り値は (セッションのフォルダ, 結果の一覧)。

    結果: dict(path, status='done' / 'skipped: 理由', rows, cells)。1 ファイルずつ、直前に変わっていないかを確かめる。"""
    session = Path(backup_root) / ('chipid_records_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    (session / 'originals').mkdir(parents=True)
    journal = dict(started=datetime.now().isoformat(timespec='seconds'), groups=list(groups), files=[])

    def record():
        (session / 'journal.json').write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding='utf-8')
    record()
    results = []
    for item in plans:
        if item.group not in groups:
            continue
        path = Path(item.path)
        entry = dict(path=item.path, group=item.group, rows=item.rows, cells=len(item.changes),
                     changes=item.changes, hash_before=item.hash)
        data = path.read_bytes()
        if digest(data) != item.hash or path.stat().st_mtime_ns != item.mtime_ns:
            entry['status'] = 'skipped: 計画の後に変わっている（計測で書き足された可能性）'
        else:
            backup = session / 'originals' / path.parent.name / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(data)
            if digest(backup.read_bytes()) != item.hash:
                raise OSError(f'バックアップの検証に失敗: {path}')
            fd, temporary = tempfile.mkstemp(prefix='.chipid-', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(item.body)
                    stream.flush()
                    os.fsync(stream.fileno())
                if digest(path.read_bytes()) != item.hash:                 # 置き換える直前にもう一度
                    entry['status'] = 'skipped: 置き換える直前に変わった'
                else:
                    os.replace(temporary, path)
                    entry['status'] = 'done'
                    entry['hash_after'] = digest(path.read_bytes())
            finally:
                Path(temporary).unlink(missing_ok=True)
        journal['files'].append(entry)
        record()
        results.append(entry)
    journal['finished'] = datetime.now().isoformat(timespec='seconds')
    record()
    return session, results


def restore(session):
    """apply の逆: バックアップから元に戻す（直した後に書き足されていたら、そのファイルは止める）。"""
    journal = json.loads((Path(session) / 'journal.json').read_text(encoding='utf-8'))
    restored = []
    for entry in journal['files']:
        if entry.get('status') != 'done':
            continue
        path = Path(entry['path'])
        if digest(path.read_bytes()) != entry['hash_after']:
            restored.append((entry['path'], 'skipped: 直した後に変わっている'))
            continue
        backup = Path(session) / 'originals' / path.parent.name / path.name
        path.write_bytes(backup.read_bytes())          # バックアップは消さずに残す
        restored.append((entry['path'], 'restored'))
    return restored
