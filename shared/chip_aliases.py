"""チップIDの旧名 → 新名の対応表（machinedb/PLAN/chip_aliases.json。共有フォルダにあり、どの PC からも同じ）。

ID の修正は一度に全部を書き換えず、段階を踏んで進める（docs/chip_id_cleanup.md）。その間は旧名と新名が両方残るので、
この表で「同じチップ」とつなぐ。各段階が済むたびに、その項目の状態（steps）を記録する。
サーバー上の実験データ（SQserver・解析サーバー）は書き換えないので、旧名はそこに残り続ける。表は消さない。

    {"schema_version": 1, "revision": 3, "updated_at": "...", "by": "PC名",
     "entries": [{"old": "SC080-4307-12", "new": "SC80-4307-12", "reason": "ロット番号の先頭の 0",
                  "merge": false, "status": "予定", "steps": {"chipdb": "", "machinedb_retired": "", ...},
                  "created_at": "...", "by": "PC名", "note": ""}]}

書くときは chip_aliases.lock を作ってから読み直し、版（revision）が同じことを確かめて一時ファイル経由で置き換える
（チップ台帳 chip_info.json と同じ決まり）。
"""
import collections
import re
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
from uuid import uuid4

FILENAME = 'chip_aliases.json'
# 進める順の段階（docs/chip_id_cleanup.md の手順と同じ）
STEPS = ('chipdb', 'machinedb_retired', 'machinedb_active', 'merge')
STEP_TITLES = {'chipdb': 'チップ台帳・履歴ファイル', 'machinedb_retired': '計測記録（引退装置）',
               'machinedb_active': '計測記録（稼働中の装置）', 'merge': '統合'}
STATUSES = ('確認待ち', '予定', '進行中', '完了', '保留')


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def table_path(shared_root):
    """共有ルート（D:\\Qstatus など）→ machinedb/PLAN/chip_aliases.json。ChipDB/DB を渡してもよい。"""
    root = Path(shared_root)
    if root.name.casefold() == 'db' and root.parent.name.casefold() == 'chipdb':
        root = root.parent.parent
    elif root.name.casefold() == 'chipdb':
        root = root.parent
    return root / 'machinedb' / 'PLAN' / FILENAME


def empty():
    return dict(schema_version=1, revision=0, entries=[])


def validate(data):
    if not isinstance(data, dict) or data.get('schema_version') != 1 or type(data.get('revision')) is not int \
            or not isinstance(data.get('entries'), list):
        raise ValueError('対応していないチップID対応表の形式です。')
    olds = set()
    for entry in data['entries']:
        if not isinstance(entry, dict) or not isinstance(entry.get('old'), str) or not isinstance(entry.get('new'), str):
            raise ValueError('チップID対応表の項目が不正です。')
        if not entry['old'] or not entry['new'] or entry['old'] == entry['new']:
            raise ValueError(f'旧名と新名が不正です: {entry.get("old")} → {entry.get("new")}')
        if entry['old'].casefold() in olds:
            raise ValueError(f'同じ旧名が 2 回あります: {entry["old"]}')
        olds.add(entry['old'].casefold())
        if entry.get('status', '予定') not in STATUSES:
            raise ValueError(f'状態が不正です: {entry.get("status")}')
    # 連鎖: ある項目の新名が、別の項目の旧名になっている（大文字小文字だけの修正 sc → SC は同じ項目なので連鎖ではない）
    news = collections.Counter(e['new'].casefold() for e in data['entries'])
    chained = [e['old'] for e in data['entries']
               if news[e['old'].casefold()] - (e['old'].casefold() == e['new'].casefold()) > 0]
    if chained:
        raise ValueError(f'新名がさらに別名に書き換えられる連鎖があります: {chained}')
    return data


def load(path):
    path = Path(path)
    try:
        return validate(json.loads(path.read_text(encoding='utf-8')))
    except FileNotFoundError:
        return empty()


def mapping(data):
    """{旧名（大文字小文字を区別しない）: 新名}。"""
    return {e['old'].casefold(): e['new'] for e in data['entries']}


def new_name(code, data):
    """旧名なら新名を、そうでなければそのまま返す。"""
    return mapping(data).get(str(code).casefold(), code)


def old_names(code, data):
    """新名 code に対応する旧名の一覧。"""
    return [e['old'] for e in data['entries'] if e['new'].casefold() == str(code).casefold()]


def replace_with_retry(source, target, attempts=10, wait_s=0.5):
    """os.replace。同期の仕組みが保存直後のファイルを一瞬掴んでいて拒否されたときは、少し待ってやり直す。"""
    import time
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(wait_s)


def save(path, data, expected_revision):
    """版を確かめてから一時ファイル経由で置き換える。戻り値は保存した内容。"""
    path = Path(path)
    validate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise OSError('チップID対応表を別の画面・PCが保存中です。少し待ってからやり直してください。') from None
    try:
        os.close(fd)
        if any(p != path for p in path.parent.glob('chip_aliases*.json')):
            raise OSError('チップID対応表に同期の競合コピーがあります。確認してください。')
        current = load(path)
        if current['revision'] != expected_revision:
            raise ValueError('チップID対応表が別の場所で更新されました。読み直してからやり直してください。')
        saved = dict(data, revision=expected_revision + 1, updated_at=stamp(), by=socket.gethostname())
        if path.exists():
            backup = path.parent / 'backups'
            backup.mkdir(exist_ok=True)
            (backup / f'chip_aliases_{uuid4().hex}.json').write_bytes(path.read_bytes())
        temporary = path.with_name(f'.chip_aliases_{uuid4().hex}.tmp')
        try:
            temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
            replace_with_retry(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return saved
    finally:
        lock.unlink(missing_ok=True)


def add_entries(path, items):
    """items: [(旧名, 新名, 理由, 統合か, 状態)]。既にある旧名は変えない。戻り値は (足した数, 保存した内容)。"""
    data = load(path)
    have = {e['old'].casefold() for e in data['entries']}
    added = 0
    entries = list(data['entries'])
    for old, new, reason, merge, status in items:
        if old.casefold() in have:
            continue
        entries.append(dict(old=old, new=new, reason=reason, merge=bool(merge), status=status,
                            steps={step: '' for step in STEPS}, created_at=stamp(), by=socket.gethostname(), note=''))
        have.add(old.casefold())
        added += 1
    if not added:
        return 0, data
    return added, save(path, dict(data, entries=entries), data['revision'])


def set_status(path, olds, status):
    """olds の項目の状態を変える（確認待ち → 予定 など）。"""
    if status not in STATUSES:
        raise ValueError(f'状態が不正です: {status}')
    data = load(path)
    targets = {o.casefold() for o in olds}
    entries = [dict(e, status=status) if e['old'].casefold() in targets else e for e in data['entries']]
    return save(path, dict(data, entries=entries), data['revision'])


def mark_step(path, olds, step, when=None):
    """olds の項目の段階 step を「済」（日時）にする。全段階が済めば状態を「完了」に。"""
    if step not in STEPS:
        raise ValueError(f'未知の段階です: {step}')
    data = load(path)
    targets = {o.casefold() for o in olds}
    when = when or stamp()
    entries = []
    for entry in data['entries']:
        entry = json.loads(json.dumps(entry))
        if entry['old'].casefold() in targets:
            entry.setdefault('steps', {s: '' for s in STEPS})[step] = when
            needed = [s for s in STEPS if s != 'merge' or entry.get('merge')]
            entry['status'] = '完了' if all(entry['steps'].get(s) for s in needed) else '進行中'
        entries.append(entry)
    return save(path, dict(data, entries=entries), data['revision'])


def wafer_of(code):
    """チップIDからウェハー（Device Wafer）: SC のIDはロットとウェハー番号の 2 つ（SC32-2416-30-2 → SC32-2416）。
    それ以外は最後の「-」より前（AXN の device_parts と同じ）。"""
    parts = str(code).split('-')
    if re.match(r'^SC\d+$', parts[0], re.I) and len(parts) >= 3 and parts[1]:
        return '-'.join(parts[:2])
    return code.rsplit('-', 1)[0] if '-' in code else code


def lot_of(code):
    """チップIDからロット（Device Lot）: 最初の「-」より前。"""
    return str(code).split('-')[0]


def old_wafer_of(code):
    """直す前の旧名から、記録に書かれているはずのウェハー（AXN は最後の「-」より前を書いていた: SC91-4419--07 → SC91-4419-）。"""
    return code.rsplit('-', 1)[0] if '-' in code else code
