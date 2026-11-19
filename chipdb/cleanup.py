"""Preview and apply recoverable ChipDB duplicate merges; never guess conflicting values."""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import copy

ALIASES = {
    'sc49-3310-51': 'SC49-3310-51',          # 2026-11-18 ユーザー確認: 決まりに合わせて大文字（以前は小文字のまま）
    'sc91-00000': 'SC91-0000-10',
    'sc91-0000-10': 'SC91-0000-10',
    'sc77-4219-23': 'SC77-4219-23',
    'sc30-2315-51': 'SC30-2315-35',
    'sc30-2315-35': 'SC30-2315-35',
    'sc25-2115-34': 'SC25-2115-33',
    'sc25-2115-33': 'SC25-2115-33',
}
HEADER = ['ChipID', 'Status', 'Date', 'Operator', 'EXID', 'Operation', '0',
          'Info1', 'Info2', 'Info3', 'Info4', 'Info5', 'Info6']


def digest(body):
    return hashlib.sha256(body).hexdigest()


def canonical(stem):
    base = re.sub(r'_DESKTOP.*$', '', stem, flags=re.I)
    match = re.search(r'\s*\((\d+)\)$', base)
    if match and int(match[1]) >= 2:
        base = base[:match.start()]
    return ALIASES.get(base.casefold(), base)


def parse(body, code):
    text = body.decode('cp932')
    notes = []
    if '\0' in text:
        if code != 'SC77-4219-23':
            raise ValueError('NUL文字による破損があります。自動統合対象外です。')
        notes.append(f'NUL文字 {text.count(chr(0))} 個を除去。元データはバックアップに保持。')
        text = text.replace('\0', '')
    rows = [line.split(',') for line in text.splitlines()]
    if not rows:
        raise ValueError('空のCSVです。')
    header = rows.pop(0)
    if len(header) < 7:
        raise ValueError('ヘッダーが7列未満です。')
    header[0] = code
    for i in range(min(len(header), len(HEADER))):
        if i != 6 and not header[i]:
            header[i] = HEADER[i]
    footer = {}
    body_rows = []
    for row in rows:
        if row[0] in ('Break', 'END'):
            if row[0] in footer and footer[row[0]] != row:
                raise ValueError('終端行の列に相違があります。')
            footer[row[0]] = row
        elif code == 'SC77-4219-23' and not any(value for i, value in enumerate(row) if i != 6) and row[6:7] in ([''], ['0']):
            notes.append('内容のない破損行を除去。')
        else:
            body_rows.append(row)
    if len(footer) != 2:
        if code != 'SC77-4219-23':
            raise ValueError('Break / END がありません。')
        for key in ('Break', 'END'):
            footer.setdefault(key, [key, '-', '-', '-', *([''] * (len(header) - 4))])
        notes.append('欠落したBreak / ENDを復元。測定値は補完しない。')
    return header, body_rows, footer, notes


def merge_cells(first, second):
    length = max(len(first), len(second))
    first, second = first + [''] * (length - len(first)), second + [''] * (length - len(second))
    conflicts = [i + 1 for i, (a, b) in enumerate(zip(first, second)) if a and b and a != b]
    if conflicts:
        raise ValueError(f'同じ記録の非空欄データが相違（列 {conflicts}）: {first[:7]} / {second[:7]}')
    return [a or b for a, b in zip(first, second)]


def row_key(row):
    if row[0] == 'Experiment':
        values = row + [''] * 7
        return tuple(values[i] for i in (0, 2, 4, 5, 6))
    if row[0] in ('Arrived', 'Stocked', 'Cover', 'Prepration'):
        return (row[0],)
    return tuple(row)


def merge_files(paths, code):
    header, rows, footers, notes = None, [], {}, []
    for path in paths:
        other_header, incoming, incoming_footer, warnings = parse(path.read_bytes(), code)
        notes += warnings
        if header is None:
            header = other_header
        else:
            # GAP is a cumulative counter: retain the greatest declared value.
            gaps = [header[6], other_header[6]]
            if any(not gap.isdigit() for gap in gaps):
                raise ValueError('GAPカウンターが数値ではありません。')
            header[6] = other_header[6] = str(max(map(int, gaps)))
            header = merge_cells(header, other_header)
        used = set()
        for row in incoming:
            matches = [i for i, existing in enumerate(rows) if i not in used and row_key(existing) == row_key(row)]
            if matches:
                merged = None
                for i in matches:
                    try:
                        merged = merge_cells(rows[i], row)
                        break
                    except ValueError:
                        if i == matches[-1]:
                            raise
                rows[i] = merged
                used.add(i)
            else:
                rows.append(list(row))
                used.add(len(rows) - 1)
        for key, value in incoming_footer.items():
            footers[key] = merge_cells(footers[key], value) if key in footers else value
    # First/last analysis follows file order. Put dated experiments in chronological
    # order; stable sorting preserves simultaneous operations and repeated records.
    dated = [row for row in rows if row[0] == 'Experiment']
    if all(re.fullmatch(r'\d{8}_\d{4}(?:\d{2})?', row[2]) for row in dated):
        ordered = iter(sorted(dated, key=lambda row: row[2]))
        rows = [next(ordered) if row[0] == 'Experiment' else row for row in rows]
    output = [header, *rows, footers['Break'], footers['END']]
    # Prove that every input's nonempty cells and repeated record count survive.
    indexed = defaultdict(list)
    for i, row in enumerate(rows):
        indexed[row_key(row)].append((i, row))
    for path in paths:
        _, original_rows, _, _ = parse(path.read_bytes(), code)
        used = set()
        for original in original_rows:
            found = next((i for i, candidate in indexed[row_key(original)] if i not in used and
                          all(not value or (j < len(candidate) and candidate[j] == value)
                              for j, value in enumerate(original))), None)
            if found is None:
                raise ValueError(f'統合結果に元データが残らないため停止: {path.name}')
            used.add(found)
    return ''.join(','.join(row) + '\r\n' for row in output).encode('cp932'), notes


def shared_aliases(folder):
    """共有の対応表（machinedb/PLAN/chip_aliases.json）の旧名 → 新名。統合の項目は段階 5 で別に行うので、統合済みのものだけ。"""
    from shared import chip_aliases as aliases
    try:
        table = aliases.load(aliases.table_path(folder))
    except (OSError, ValueError):
        return {}
    return {e['old'].casefold(): e['new'] for e in table['entries']
            if e.get('status') != '確認待ち' and (not e.get('merge') or e.get('steps', {}).get('merge'))}


def preview(folder):
    folder = Path(folder).resolve()
    for old, new in shared_aliases(folder).items():
        ALIASES.setdefault(old, new)
    all_paths = sorted(folder.glob('*.csv'))
    groups = defaultdict(list)
    names = {p.stem.casefold(): p.stem for p in all_paths}
    for path in all_paths:
        code = canonical(path.stem)
        if code.casefold() not in ALIASES:
            code = names.get(code.casefold(), code)
        groups[code].append(path)
    # Also include byte-for-byte copies of known duplicate members (e.g. 1126.csv).
    hashes = {}
    for code, paths in groups.items():
        if len(paths) > 1:
            for path in paths:
                hashes[digest(path.read_bytes())] = code
    for code, paths in list(groups.items()):
        if len(paths) == 1 and (target := hashes.get(digest(paths[0].read_bytes()))) and target != code:
            groups[target] += paths
            del groups[code]
    plans = []
    for code, paths in groups.items():
        if len(paths) == 1 and paths[0].stem == code and code.casefold() not in ALIASES:
            continue
        target = folder / f'{code}.csv'
        paths.sort(key=lambda p: (p.name.casefold() != target.name.casefold(), p.name.casefold()))
        plan = dict(code=code, target=str(target), sources=[str(p) for p in paths],
                    hashes={str(p): digest(p.read_bytes()) for p in paths}, error='', notes=[])
        try:
            body, notes = merge_files(paths, code)
            plan.update(body=body.decode('cp932'), output_hash=digest(body), notes=notes,
                        output_rows=len(body.splitlines()), remove=[str(p) for p in paths if p.name.casefold() != target.name.casefold()])
            if len(paths) == 1 and paths[0].name == target.name and body == paths[0].read_bytes():
                continue
        except (ValueError, UnicodeError, OSError) as exc:
            plan['error'] = str(exc)
        plans.append(plan)
    return plans


def reconcile_registry(folder, plans, session, dry_run=False):
    """Consolidate registered aliases using the same revision/lock as AXN."""
    from chipdb.registry import Store, snapshot, stamp, validate
    store = Store(folder)
    if not store.path.exists():
        return []
    lock = store.path.with_suffix('.lock')
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        if any(p != store.path for p in store.path.parent.glob('chip_info*.json')):
            raise ValueError('チップ台帳に同期競合コピーがあります。')
        original = store.path.read_bytes()
        data = store.load()
        changed = []

        def normalize(field, value):
            if field in ('use_started_on', 'last_used_on'):
                value = value.replace('/', '-').replace('_', ' ')
                if field == 'use_started_on':
                    value = value[:10]
            return value

        def automatic(chip, field):
            source = chip.get('info_sources', {}).get(field)
            return source is not None and chip.get(field, '') == normalize(field, source.get('value', ''))

        for plan in plans:
            if plan['error']:
                continue
            names = {Path(p).stem.casefold() for p in plan['sources']} | {plan['code'].casefold()}
            chips = [c for c in data['chips'] if c['code'].casefold() in names]
            if not chips:
                continue
            primary = next((c for c in chips if c['code'].casefold() == plan['code'].casefold()), chips[0])
            merged = copy.deepcopy(primary)
            current_snapshot = snapshot(Path(plan['target']))
            fields = set().union(*(set(c) for c in chips)) - {'key', 'code', 'snapshot', 'created_at', 'updated_at', 'info_sources'}
            sources = merged.setdefault('info_sources', {})
            for field in fields:
                manual = [c[field] for c in chips if c.get(field) not in (None, '', '未設定') and not automatic(c, field)]
                if manual:
                    if any(value != manual[0] for value in manual[1:]):
                        raise ValueError(f'{plan["code"]}: 手入力の管理情報が競合: {field}')
                    merged[field] = manual[0]
                    sources.pop(field, None)
                else:
                    provenance = next((c['info_sources'][field] for c in chips if field in c.get('info_sources', {})), None)
                    if provenance:
                        value = current_snapshot['summary'].get(provenance['field'], '')
                        merged[field] = normalize(field, value)
                        sources[field] = dict(field=provenance['field'], value=value,
                                              sha256=current_snapshot['sha256'], imported_at=stamp())
            merged.update(code=plan['code'], snapshot=current_snapshot, updated_at=stamp())
            # 直す前の表記は別名として残す（計測記録や生データには旧表記のまま残るので、探せるように。2026-11-18）
            old_names = {c['code'] for c in chips} | {Path(p).stem for p in plan['sources']}
            aliases = set(merged.get('aliases', [])) | {a for c in chips for a in c.get('aliases', [])}
            aliases |= {n for n in old_names if n != plan['code']}
            if aliases:
                merged['aliases'] = sorted(aliases, key=str.casefold)
            removed = [c['code'] for c in chips if c is not primary]
            data['chips'] = [merged if c is primary else c for c in data['chips'] if c is primary or c not in chips]
            changed.append(dict(code=plan['code'], removed=removed))
        if not changed:
            return []
        data['revision'] += 1
        data['updated_at'] = stamp()
        validate(data)
        if dry_run:
            return changed
        with (Path(session) / 'chip_info_before.json').open('xb') as stream:
            stream.write(original)
        temporary = store.path.with_name('.chip_info_cleanup.tmp')
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            if store.path.read_bytes() != original:
                raise ValueError('保存直前に台帳が変更されました。')
            os.replace(temporary, store.path)
        finally:
            temporary.unlink(missing_ok=True)
        (Path(session) / 'registry_changes.json').write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding='utf-8')
        return changed
    finally:
        lock.unlink(missing_ok=True)


def apply(folder, plans):
    folder = Path(folder).resolve()
    session = folder.parent / 'WorkbenchBackups' / ('cleanup_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    session.mkdir(parents=True, exist_ok=False)
    originals = session / 'originals'
    originals.mkdir()
    journal = dict(folder=str(folder), plans=plans, completed=[], status='preparing')

    def record():
        (session / 'report.json').write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
    record()
    try:
        safe = [plan for plan in plans if not plan['error']]
        # Back up and verify every input before changing any file.
        for plan in safe:
            for name in plan['sources']:
                path = Path(name)
                if path.parent.resolve() != folder or digest(path.read_bytes()) != plan['hashes'][name]:
                    raise ValueError(f'プレビュー後に変更されています: {name}')
                shutil.copy2(path, originals / path.name)
                if digest((originals / path.name).read_bytes()) != plan['hashes'][name]:
                    raise ValueError('バックアップの検証に失敗しました。')
        journal['status'] = 'applying'
        record()
        for plan in safe:
            target = Path(plan['target'])
            if target.parent.resolve() != folder:
                raise ValueError('保存先が対象DBの外です。')
            for name in plan['sources']:
                if digest(Path(name).read_bytes()) != plan['hashes'][name]:
                    raise ValueError(f'保存直前に変更されています: {name}')
            if target.exists() and not any(Path(p).resolve() == target.resolve() for p in plan['sources']):
                raise ValueError(f'新しい同名ファイルがあります: {target}')
            body = plan['body'].encode('cp932')
            if digest(body) != plan['output_hash']:
                raise ValueError('統合結果の検証に失敗しました。')
            fd, temporary = tempfile.mkstemp(prefix='.cleanup-', dir=folder)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            if digest(target.read_bytes()) != plan['output_hash']:
                raise ValueError('保存した統合結果の検証に失敗しました。')
            for name in plan['remove']:
                path = Path(name)
                if digest(path.read_bytes()) != plan['hashes'][name]:
                    raise ValueError(f'重複側が変更されたため削除を停止しました: {name}')
                path.unlink()
            journal['completed'].append(plan['code'])
            record()
        journal['registry_changes'] = reconcile_registry(folder, safe, session)
        journal['status'] = 'complete'
        record()
    except Exception:
        journal['status'] = 'stopped'
        record()
        raise
    return session
