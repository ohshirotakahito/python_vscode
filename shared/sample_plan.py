"""サンプル台帳: サンプルごと（実験＋サンプル。共有は問わない）に人が決める解析の予定。

    kind     : サンプルの種類 '核酸' / 'アミノ酸'（BNAL で使う SEQlist・BC の種類が決まる）
    bnal     : BNAL に回すか 'target'（対象）/ 'excluded'（対象外）。無ければ未分類
    seqlists : 使う SEQlist の CSV 名のリスト
    bc       : 使う BC の CSV 名
    note     : メモ
    sample_key : AXN の Ex Info のサンプル（UUID）。登録した保存場所のフォルダと 1 つに決まるときだけ、
               開発PC の毎時の公開（database/publish.py → link_ex_info）が書く。名前だけでは結び付けない。
               一度結び付いたら、後で照合できなくなっても消さない（フォルダや名前を変えても同じサンプルとたどれる）

サーバーを調べた結果（実験ファイルDB）とは分けて、machinedb（D:\\Qstatus の共有フォルダ）に JSON で置き、各 PC から共有する。
保存は「最新のファイルを読み直して、変えた項目だけを書き込む」ので、別の PC の変更を消さない。
同期ソフトが作る競合コピー（Synology の ..._Conflict...、Dropbox の (conflicted copy)）は conflicted_copies() で見つけて知らせる。
"""
from datetime import datetime
import json
import os
from pathlib import Path
import socket

from shared import machinedb

KINDS = ('核酸', 'アミノ酸')
BNAL = {'target': '対象', 'excluded': '対象外'}
FIELDS = ('kind', 'bnal', 'seqlists', 'bc', 'note', 'sample_key')


def key(experiment, sample):
    return f'{experiment}|{sample}'


def load(path=None):
    """{key: entry}; an empty dict when the ledger does not exist yet."""
    path = Path(path or machinedb.SAMPLE_PLAN)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    return data.get('samples', {})


def _check(fields):
    for name, value in fields.items():
        if name not in FIELDS:
            raise ValueError(f'台帳にない項目です: {name}')
        if value is None:
            continue
        if name == 'kind' and value not in KINDS:
            raise ValueError(f'サンプルの種類は {"・".join(KINDS)} のどれかです: {value}')
        if name == 'bnal' and value not in BNAL:
            raise ValueError(f'BNAL の振り分けは target / excluded です: {value}')
        if name == 'seqlists' and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise ValueError('seqlists は CSV 名のリストです')
        if name == 'sample_key' and not (isinstance(value, str) and value.strip()):
            raise ValueError('sample_key は Ex Info のサンプルのキーです')


def update(changes, who=None, path=None):
    """changes = {key: {field: value}}; value None clears the field. Re-reads the file, applies only these
    fields, and writes it atomically. Returns the whole ledger after the update."""
    path = Path(path or machinedb.SAMPLE_PLAN)
    for fields in changes.values():
        _check(fields)
    samples = load(path)
    stamp = dict(updated_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                 updated_by=who or f'{os.environ.get("USERNAME", "")}@{socket.gethostname()}')
    for k, fields in changes.items():
        entry = {**samples.get(k, {})}
        before = {f: entry.get(f) for f in fields}
        for name, value in fields.items():
            if value is None or value == []:
                entry.pop(name, None)
            else:
                entry[name] = value
        if {f: entry.get(f) for f in fields} == before:
            continue
        if any(f in entry for f in FIELDS):
            samples[k] = {**entry, **stamp}
        else:
            samples.pop(k, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.part')
    tmp.write_text(json.dumps(dict(version=1, samples=dict(sorted(samples.items()))), ensure_ascii=False, indent=1),
                   encoding='utf-8')
    os.replace(tmp, path)
    return samples


def conflicted_copies(path=None):
    """Sync conflict copies (Synology `_Conflict`, Dropbox `conflicted copy` / `競合コピー`) next to the ledger (edits made on two PCs at once): they must be merged by hand."""
    path = Path(path or machinedb.SAMPLE_PLAN)
    if not path.parent.is_dir():
        return []
    return sorted(p for p in path.parent.glob(path.stem + '*' + path.suffix)
                  if p != path and ('conflict' in p.name.lower() or '競合' in p.name))


def import_bnal_groups(groups, who=None, path=None):
    """Bring Analyzer's former routing ({'root|experiment|sample': 'target' | 'excluded'}) into the ledger.

    A sample keeps a value already in the ledger; one sample routed both ways on different shares is not
    imported. Returns (imported keys, conflicts {key: values})."""
    by_sample = {}
    for sample_id, value in groups.items():
        _, experiment, sample = sample_id.split('|', 2)
        by_sample.setdefault(key(experiment, sample), set()).add(value)
    conflicts = {k: sorted(v) for k, v in by_sample.items() if len(v) > 1}
    current = load(path)
    changes = {k: dict(bnal=v.pop()) for k, v in by_sample.items()
               if k not in conflicts and not current.get(k, {}).get('bnal')}
    if changes:
        update(changes, who=who, path=path)
    return sorted(changes), conflicts


def link_changes(plan, keys):
    """{key: {'sample_key': ...}} for ledger entries whose (experiment, sample) now matches exactly one Ex Info
    sample (keys: shared.ex_info_owners.sample_keys) and do not hold that key yet. Entries are never created, and
    a key once recorded is not removed when the folders no longer match."""
    changes = {}
    for (experiment, sample), sample_key in keys.items():
        k = key(experiment, sample)
        if k in plan and plan[k].get('sample_key') != sample_key:
            changes[k] = dict(sample_key=sample_key)
    return changes


def link_ex_info(samples, data, who='publish', path=None):
    """Record Ex Info sample keys in the ledger (only entries that change). Returns the number recorded."""
    from shared import ex_info_owners
    plan = load(path)
    pairs = [tuple(k.split('|', 1)) for k in plan if '|' in k]
    changes = link_changes(plan, ex_info_owners.sample_keys(pairs, samples, data))
    if changes:
        update(changes, who=who, path=path)
    return len(changes)
