"""共有フォルダ（D:\\Qstatus）の同期の競合コピーを見つけ、安全なものだけを本体に統合する。

同期の仕組み（Synology Drive など）は、2 台の PC が同じファイルを同時に変えると、片方を
`<名前>_<PC名>_<月-日-時分秒-年>_Conflict.csv` として残す。計測記録（machinedb）やチップ履歴（ChipDB）は
行を書き足していくだけのファイルなので、競合コピーにしかない行を本体に戻せば、どちらの書き込みも失わない。

自動で行うこと（--apply）:
- **片付け:** 競合コピーの行がすべて本体にある → 競合コピーを保管場所へ移すだけ
- **統合:** 競合コピーにしかない行がある → その行を、競合コピーでの前後の行と同じ位置に本体へ差し込む（新しい順の並びを保つ）。
  比べる前に、チップIDの対応表（shared.chip_aliases）で競合コピーの旧名を新名にそろえる（チップIDの修正の後でも同じ行と分かる）
- 集計の出力（database/Scores）は作り直せるので、本体を正として競合コピーを保管場所へ移す

自動では触らず、報告だけにすること:
- 見出し（1 行目）が違う、本体がない、名前の形が想定外（CaseConflict・conflict_current・「の競合コピー」など）、
  行を書き足すだけのファイルと決められないもの（JSON・設定の CSV など）
- 本体が直近（既定 30 分以内）に書き込まれている（計測中かもしれない）

元の本体と競合コピーは、すべて `D:\\Qstatus\\database\\ConflictBackups\\<日時>\\` に残す（journal.json に何をしたか）。
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

PATTERN = re.compile(r'^(?P<stem>.+?)_(?P<pc>[A-Za-z0-9][A-Za-z0-9-]*)_(?P<when>\d{1,2}-\d{1,2}-\d{6}-\d{4})_(?:Case)?Conflict'
                     r'(?P<suffix>\.[A-Za-z0-9]+)$')
# 別の同期の仕組み（Dropbox など）の競合コピー: 「<名前> (<人> の競合コピー 2026-06-22).csv」
DROPBOX = re.compile(r'^(?P<base>.+?) \([^()]*の競合コピー[^()]*\)(?P<suffix>\.[A-Za-z0-9]+)$')
# 「…_<PC>_<日時>_conflict_current」が続くもの
CURRENT = re.compile(r'(?:_[A-Za-z0-9][A-Za-z0-9-]*_\d{1,2}-\d{1,2}-\d{6}-\d{4}_conflict_current)+(?=\.[A-Za-z0-9]+$)')


def main_names(name):
    """競合コピーの名前から考えられる本体の名前（短い順）。PC 名に「_」が入ることがあるので（TOSHIRO_HOMEX など）、
    区切り方を何通りか出し、実在するものを使う。割り出せなければ []。"""
    original = name
    match = DROPBOX.match(name)
    if match:
        name = match['base'] + match['suffix']
    name = CURRENT.sub('', name)
    match = PATTERN.match(name)
    if not match:
        return [name] if name != original else []
    tail = f"_{match['when']}_"
    head = name[:name.rindex(tail)]                     # 「<名前>_<PC名>」
    candidates = [head[:i] + match['suffix'] for i, ch in enumerate(head) if ch == '_' and i > 0]
    return sorted(set(candidates), key=lambda c: (-len(c), c)) if candidates else []


def main_name(name):
    """本体の名前の第一候補（PC 名に「_」がない、ふつうの場合）。割り出せなければ None。"""
    names = main_names(name)
    return names[0] if names else None
SKIP_FOLDERS = {'backups', 'workbenchbackups', 'conflictbackups', 'conflicts', '#recycle', '@eadir'}
ID_COLUMNS = ('Device Code', 'Device Wafer', 'Device Lot')
MIN_AGE_S = 30 * 60
MAX_EXTRA = 200                 # 競合コピーにしかない行がこれより多いときは自動で戻さない
MIN_COMMON_COLUMNS = 10         # 見出しが違うとき、先頭から共通の列がこれ以上あれば、その列で行を比べる
MAX_PATH = 240                  # 保管場所のパスがこれより長ければ名前を短くする（Windows の上限 260 文字）


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class Item:
    copy: str
    main: str = ''
    kind: str = ''             # log / derived / other
    action: str = ''           # archive / merge / review
    reason: str = ''
    extra: int = 0             # 競合コピーにしかない行
    copy_hash: str = ''
    main_hash: str = ''
    body: bytes = field(default=b'', repr=False)

    def summary(self):
        return dict(copy=self.copy, main=self.main, kind=self.kind, action=self.action, reason=self.reason, extra=self.extra)


def find_copies(root):
    """root の下の競合コピー（保管場所の中は除く）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.casefold() not in SKIP_FOLDERS]
        for name in filenames:
            if 'conflict' in name.casefold() or '競合コピー' in name:
                out.append(Path(dirpath) / name)
    return sorted(out)


def kind_of(main, root):
    """log: 行を書き足すだけの記録（統合してよい）、derived: 作り直せる出力、other: 自動では触らない。"""
    rel = [p.casefold() for p in Path(main).relative_to(root).parts]
    name = Path(main).name.casefold()
    if not name.endswith('.csv'):
        return 'other'
    if rel[:2] == ['database', 'scores']:
        return 'derived'
    if rel[0] == 'chipdb':
        return 'log'
    if rel[0] == 'machinedb' and rel[1] not in ('seq', 'plan', 'seqlist', 'bc', 'samplePlan'.casefold()) and \
            (name.endswith(' data.csv') or name.endswith(' score.csv') or re.search(r' data \(\d+\)\.csv$', name)):
        return 'log'
    return 'other'


def split_lines(data):
    """[(内容, 行末)]。行末は b'\\r\\n' / b'\\n' / b''（最後の行）。"""
    out = []
    for piece in data.split(b'\n'):
        if piece.endswith(b'\r'):
            out.append((piece[:-1], b'\r\n'))
        else:
            out.append((piece, b'\n'))
    if out and out[-1][0] == b'':
        out.pop()
    elif out:
        out[-1] = (out[-1][0], b'')
    return out


def is_data_line(line):
    """見出しではなく値の行か（日付や数値で始まる）。"""
    return bool(re.match(rb'\s*[-+]?\d', line))


def header_key(line):
    return line.decode('cp932', errors='replace').rstrip(',').strip().casefold()


def alias_normalizer(header_line, alias_table):
    """競合コピーの行の旧チップIDを新名にそろえる関数（Device Code 列のある記録だけ）。"""
    if not alias_table or not alias_table.get('entries'):
        return lambda line: line
    names = [h.strip() for h in header_line.decode('cp932', errors='replace').split(',')]
    if 'Device Code' not in names:
        return lambda line: line
    index = {n: names.index(n) for n in ID_COLUMNS if n in names}
    mapping = {e['old'].encode('cp932'): e['new'].encode('cp932') for e in alias_table['entries']
               if e.get('status') not in ('確認待ち', '保留')}

    from shared import chip_aliases

    def part(code, which, before):
        text = code.decode('cp932')
        if which == 'Device Wafer':
            text = chip_aliases.old_wafer_of(text) if before else chip_aliases.wafer_of(text)
        elif which == 'Device Lot':
            text = chip_aliases.lot_of(text)
        return text.encode('cp932')

    def normalize(line):
        cells = line.split(b',')
        at = index['Device Code']
        if at < len(cells) and cells[at] in mapping:
            old, new = cells[at], mapping[cells[at]]
            for name, i in index.items():
                if i < len(cells) and cells[i] == part(old, name, True):
                    cells[i] = part(new, name, False)
            return b','.join(cells)
        return line
    return normalize


def merge_lines(main_lines, copy_lines):
    """競合コピーにしかない行を、競合コピーで次に来る行（本体にもある行）の直前に差し込む。
    後ろに本体にもある行がなければ、最後に一致した行の直後（一致が 1 つもなければ見出しの直後）。
    戻り値は (新しい行の一覧, 足した数)。

    main_lines / copy_lines: 見出しを除いた行の内容（行末なし）。同じ内容の行が複数あるときは数で比べる。"""
    positions = {}
    for i, line in enumerate(main_lines):
        positions.setdefault(line, []).append(i)
    used = set()
    before, after = {}, {}          # 本体の位置 → その前 / 後ろに差し込む行
    cursor = -1
    pending = []
    added = 0
    for line in copy_lines:
        candidates = [i for i in positions.get(line, []) if i not in used]
        if candidates:
            later = [i for i in candidates if i > cursor]
            chosen = later[0] if later else candidates[0]
            used.add(chosen)
            before.setdefault(chosen, []).extend(pending)
            pending = []
            cursor = chosen
        else:
            pending.append(line)
            added += 1
    after.setdefault(cursor, []).extend(pending)
    result = after.get(-1, [])[:]
    for i, line in enumerate(main_lines):
        result.extend(before.get(i, []))
        result.append(line)
        result.extend(after.get(i, []))
    return result, added


def plan_item(copy, root, alias_table=None, now=None, min_age_s=MIN_AGE_S):
    copy = Path(copy)
    item = Item(copy=str(copy))
    names = main_names(copy.name)
    if not names:
        item.action, item.reason = 'review', '名前から本体を割り出せない'
        return item
    main = next((copy.with_name(n) for n in names if copy.with_name(n).exists()), copy.with_name(names[0]))
    item.main = str(main)
    if not main.exists():
        item.action, item.reason = 'review', '本体がない（競合コピーだけが残っている）'
        return item
    item.kind = kind_of(main, root)
    if item.kind == 'other':
        item.action, item.reason = 'review', '行を書き足すだけのファイルと決められない（自動で統合しない）'
        return item
    main_data, copy_data = main.read_bytes(), copy.read_bytes()
    item.main_hash, item.copy_hash = digest(main_data), digest(copy_data)
    if item.kind == 'derived':
        item.action, item.reason = 'archive', '作り直せる出力: 本体を正として競合コピーを片付ける'
        return item
    now = time.time() if now is None else now
    if now - main.stat().st_mtime < min_age_s:
        item.action, item.reason = 'review', f'本体が {min_age_s // 60} 分以内に書き込まれている（計測中かもしれない。次回に回す）'
        return item
    mains, copies = split_lines(main_data), split_lines(copy_data)
    if not mains or not copies:
        item.action, item.reason = 'review', '本体か競合コピーが空'
        return item
    headed = not is_data_line(mains[0][0])          # BL・RMS・SR の Data.csv などは見出しがなく 1 行目から値
    if headed != (not is_data_line(copies[0][0])):
        item.action, item.reason = 'review', '見出しの有無が違う'
        return item
    if headed and header_key(mains[0][0]) != header_key(copies[0][0]):
        # 列が足された新しい形の本体など: 先頭から共通の列で見て、競合コピーの行がすべて本体にあれば片付ける（戻しはしない）
        names = lambda line: [h.strip().casefold() for h in line.decode('cp932', errors='replace').split(',')]  # noqa: E731
        main_cols, copy_cols = names(mains[0][0]), names(copies[0][0])
        common = next((i for i, (a, b) in enumerate(zip(main_cols, copy_cols)) if a != b), min(len(main_cols), len(copy_cols)))
        if common >= MIN_COMMON_COLUMNS:
            key = lambda line: tuple(line.split(b',')[:common])  # noqa: E731
            normalize = alias_normalizer(mains[0][0], alias_table)
            main_keys = {key(content) for content, _ in mains[1:]}
            missing = [c for c, _ in copies[1:] if key(normalize(c)) not in main_keys]
            if not missing:
                item.action = 'archive'
                item.reason = f'見出しは違うが、共通の先頭 {common} 列で見ると競合コピーの行はすべて本体にある'
                return item
            item.extra = len(missing)
        item.action, item.reason = 'review', '見出し（1 行目）が違い、本体にない行がある'
        return item
    normalize = alias_normalizer(mains[0][0], alias_table) if headed else (lambda line: line)
    skip = 1 if headed else 0
    main_body = [content for content, _ in mains[skip:]]
    copy_body = [normalize(content) for content, _ in copies[skip:]]
    missing = Counter(copy_body) - Counter(main_body)
    item.extra = sum(missing.values())
    if not item.extra:
        item.action, item.reason = 'archive', '競合コピーの行はすべて本体にある'
        return item
    if set(missing) <= set(main_body):
        # 本体にある行が何度も繰り返されているだけ（LabVIEW が TA data.csv に古い FC の行を書き足し続ける既知の不具合など）
        item.action = 'archive'
        item.reason = f'本体にない {item.extra} 行は、本体にある行の繰り返しだけ（失われる記録はない）'
        return item
    if item.extra > MAX_EXTRA or item.extra > len(main_body):
        item.action = 'review'
        item.reason = (f'競合コピーにしかない行が {item.extra} 行と多い（本体 {len(main_body)} 行）。本体が作り直されたか、'
                       f'同じ行が何度も書き足された可能性があるので自動では戻さない')
        return item
    merged, added = merge_lines(main_body, copy_body)
    eol = mains[0][1] or b'\r\n'
    last_eol = mains[-1][1]
    lines = ([mains[0][0]] if headed else []) + merged
    item.body = b''.join(line + eol for line in lines[:-1]) + lines[-1] + (last_eol if last_eol else b'')
    item.action, item.reason = 'merge', f'競合コピーにしかない {added} 行を本体に戻す'
    return item


def plan(root, alias_table=None, min_age_s=MIN_AGE_S):
    root = Path(root)
    return [plan_item(copy, root, alias_table, min_age_s=min_age_s) for copy in find_copies(root)]


def replace_with_retry(source, target, attempts=10, wait_s=0.5):
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(wait_s)


def apply(items, root, backup_root=None, alias_table=None):
    """archive / merge の項目を行う。元の本体と競合コピーは保管場所へ。戻り値は (保管場所, 結果の一覧)。"""
    root = Path(root)
    session = Path(backup_root or root / 'database' / 'ConflictBackups') / datetime.now().strftime('%Y%m%d_%H%M%S')
    journal = dict(started=datetime.now().isoformat(timespec='seconds'), items=[])
    results = []

    def keep(path, folder):
        target = session / folder / Path(path).relative_to(root)
        if len(str(target)) > MAX_PATH:
            # Windows のパスの長さの上限: 名前を短くする（元の名前は journal.json の copy / main に残る）
            name = Path(path).name
            short = f"{name[:60]}_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:10]}{Path(path).suffix}"
            target = target.with_name(short)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def record():
        session.mkdir(parents=True, exist_ok=True)
        (session / 'journal.json').write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding='utf-8')
    written = {}                    # この実行で書き換えた本体 → 書き換えた後のハッシュ
    for item in items:
        if item.action not in ('archive', 'merge'):
            continue
        copy, main = Path(item.copy), Path(item.main)
        if item.main in written and main.exists() and digest(main.read_bytes()) == written[item.main]:
            # 同じ本体の別の競合コピーで、この実行が本体を書き換えた: 今の本体で計画を作り直す
            item = plan_item(copy, root, alias_table, min_age_s=0)
            if item.action not in ('archive', 'merge'):
                continue
        result = item.summary()
        try:
            if digest(copy.read_bytes()) != item.copy_hash or digest(main.read_bytes()) != item.main_hash:
                result['status'] = 'skipped: 計画の後に変わった'
            else:
                if item.action == 'merge':
                    original = keep(main, 'originals')
                    original.write_bytes(main.read_bytes())
                    fd, temporary = tempfile.mkstemp(prefix='.merge-', dir=main.parent)
                    try:
                        with os.fdopen(fd, 'wb') as stream:
                            stream.write(item.body)
                            stream.flush()
                            os.fsync(stream.fileno())
                        if digest(main.read_bytes()) != item.main_hash:
                            raise OSError('置き換える直前に本体が変わった')
                        replace_with_retry(temporary, main)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                    result['main_hash_after'] = written[item.main] = digest(main.read_bytes())
                replace_with_retry(copy, keep(copy, 'copies'))
                result['status'] = 'done'
        except OSError as exc:
            result['status'] = f'skipped: {exc}'
        journal['items'].append(result)
        results.append(result)
        record()
    if results:
        journal['finished'] = datetime.now().isoformat(timespec='seconds')
        record()
    return session, results
