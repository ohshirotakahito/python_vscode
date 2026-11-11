"""Experiment copy planning and verified, non-overwriting execution."""
from pathlib import Path
import csv
import hashlib
import os
import re
import shutil
import uuid
import time
from dataclasses import dataclass
from datetime import datetime

from shared.folder_layout import create_sample_folders
from shared.seq_records import HEADERS, SEQ_DB as DB, history, machines, rows  # noqa: F401  (app.py も core 経由で使う)

BASE = Path(__file__).resolve().parent


def sampled_signature(path):
    """Hash size and three 64 KiB samples; equality indicates a candidate only."""
    before = path.stat()
    size, block = before.st_size, 64 * 1024
    result = hashlib.sha256(str(size).encode('ascii'))
    with path.open('rb') as stream:
        for offset in sorted({0, max(0, (size - block) // 2), max(0, size - block)}):
            stream.seek(offset)
            result.update(stream.read(block))
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f'読み取り中に変更されました: {path}')
    return result.hexdigest()


def source_sample_name(folder_name):
    """コピー元フォルダ名「日付_時刻 装置条件_サンプル名」からサンプル名を取り出す（形が違えばNone）。

    例: '20261006_1434 UNAN#4Pex0n2_o163o162' → 'o163o162'
        '20261005_1253 UNAN#4Pex1n1_o163o162_NG' → 'o163o162_NG'
    """
    parts = folder_name.split(' ', 1)
    if len(parts) != 2 or '_' not in parts[1]:
        return None
    return parts[1].split('_', 1)[1] or None


def name_check(sources, sample):
    """コピー元フォルダ名のサンプル名と保存先サンプル名の照合。

    戻り値: ('一致' | '不一致：…' | '判定不可：…', 一致しないコピー元名のリスト)。
    一致しなくても正しい場合があるので、判定は確認の目安として使う。
    """
    names = [Path(source).name for source in sources]
    unknown = [name for name in names if source_sample_name(name) is None]
    different = [name for name in names
                 if name not in unknown and source_sample_name(name).casefold() != sample.casefold()]
    if different:
        found = '、'.join(dict.fromkeys(source_sample_name(name) for name in different))
        return f'不一致：{found} ≠ {sample}', different
    if unknown:
        return '判定不可：名前の形が違う', unknown
    return '一致', []


def destination_warnings(target, kind):
    """Report archived files and ongoing analysis in the selected destination."""
    target = Path(target)
    warnings = []
    for rate in ('10k', '100k'):
        folder = target / f'{target.name}_{rate}_{kind}'
        for directory in (folder, folder / 'T'):
            stocked = directory / 'stocked'
            if stocked.is_dir():
                count = sum(p.is_file() for p in stocked.rglob('*'))
                if count:
                    warnings.append(f'退避ファイルあり（{count}件）: {stocked} '
                                    '— 採番は退避ファイルを参照しないため、番号がずれる可能性があります。')
        anal = folder / 'T' / 'ANAL'
        if anal.is_dir():
            count = sum(p.is_file() and p.suffix.lower() == '.tdms' for p in anal.rglob('*'))
            if count:
                warnings.append(f'解析進行中（ANAL内TDMS {count}件）: {anal}')
    return warnings


def inspect_set_data(spec, check_duplicates=False, progress=None, quick=False):
    """Inspect direct acquisition TDMS files, including renamed destination files."""
    def files(folder):
        try:
            return [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.tdms']
        except FileNotFoundError:
            return []

    counts, duplicates = [], []
    target = Path(spec['target'])
    for rate, relative in [('10k', 'EXSV/T'), ('100k', 'EXZSV')]:
        sources = [p for source in spec['sources'] for p in files(Path(source) / relative)]
        destination = target / f"{target.name}_{rate}_{spec['context']['kind']}"
        if rate == '10k':
            destination /= 'T'
        targets = files(destination)
        counts.extend((len(sources), len(targets)))
        if not check_duplicates:
            continue
        by_size = {}
        for p in targets:
            by_size.setdefault(p.stat().st_size, []).append(p)
        hashes = {}
        for pos, source in enumerate(sources):
            candidates = by_size.get(source.stat().st_size, [])
            if progress:
                progress('TDMS重複確認', pos, len(sources), str(source))
            if candidates:
                fingerprint = sampled_signature if quick else signature
                source_hash = fingerprint(source)
                for dest in candidates:
                    if dest not in hashes:
                        hashes[dest] = fingerprint(dest)
                    if hashes[dest] == source_hash:
                        duplicates.append((rate, source, dest))
        if progress:
            progress('TDMS重複確認', len(sources), len(sources), rate)
    return tuple(counts), duplicates


def create_destinations(root, experiment, samples):
    """Use the shared standard folder layout for new destinations."""
    names = [experiment, *samples]
    if not samples:
        raise ValueError('サンプル名を入力してください。')
    for name in names:
        if (not name or name in ('.', '..') or name.endswith((' ', '.'))
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name)):
            raise ValueError(f'使用できないフォルダ名です: {name!r}')
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f'保存先解析ルートが存在しません: {root}')
    for sample in samples:
        create_sample_folders(root, experiment, sample)
    return sorted(p.name for p in root.iterdir() if p.is_dir()), sorted(p.name for p in (root / experiment).iterdir() if p.is_dir())


def sync_history(legacy, local):
    """Merge missing legacy records into the local CSV, deduplicating all 17 fields."""
    legacy, local = Path(legacy), Path(local)
    if legacy.resolve() == local.resolve() or (local.exists() and os.path.samefile(legacy, local)):
        raise ValueError('既存CSVと試用版履歴CSVには別のファイルを指定してください')
    records = history(legacy, local)
    existing = rows(local) if local.exists() else []
    local_records = [tuple(row[:17]) for row in existing
                     if len(row) >= 17 and row[0] and row[0] != HEADERS[0]]
    added = len(set(records) - set(local_records))
    removed = len(local_records) - len(set(local_records))
    if added or removed or not local.exists():
        write_csv(local, HEADERS, records)
    return added, removed


def write_csv(path, headers, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(records)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def digest(path, progress=None):
    h = hashlib.sha256()
    size, count, last = Path(path).stat().st_size, 0, 0.0
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
            count += len(block)
            if progress and time.monotonic() - last >= 0.2:
                progress(count, size)
                last = time.monotonic()
    if progress:
        progress(count, size)
    return h.hexdigest()


def signature(path, progress=None):
    stat = path.stat()
    value = digest(path, progress)
    after = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f'読み取り中に変更されました: {path}')
    return value


def natural(path):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)', path.name)]


@dataclass
class Item:
    source: Path
    destination: Path
    sha256: str
    status: str
    raw: bool = False
    snapshot: tuple = ()


@dataclass
class Plan:
    items: list
    warnings: list
    source: Path
    target: Path
    numbers: dict


def plan(source, target, sample, kind, progress=None, verify=False, reserved=None):
    source, target = Path(source), Path(target)
    if kind not in ('Sample', 'Blank') or sample != target.name:
        raise ValueError('保存先サンプル・区分が不正です')
    if not source.is_dir() or not target.is_dir():
        raise ValueError('コピー元と保存先サンプルフォルダが必要です')
    if source.resolve() == target.resolve() or source.resolve() in target.resolve().parents or target.resolve() in source.resolve().parents:
        raise ValueError('コピー元と保存先が重なっています')
    items, warnings, numbers = [], destination_warnings(target, kind), {}

    pending = []

    def add(src, dst):
        if src.is_symlink() or dst.is_symlink():
            raise ValueError(f'リンクは扱えません: {src} / {dst}')
        pending.append((src, dst))

    for rate, relative, ext, width in [('10k', 'EXSV', '.txt', 3), ('100k', 'EXZSV', '.tdms', 4)]:
        prefix = f'{sample}_{rate}_{kind}'
        dest = target / prefix
        if not dest.is_dir() or (rate == '10k' and not (dest / 'T').is_dir()):
            raise ValueError(f'保存先を実験ファイルDBの「ファイル操作」→「フォルダ作成」で作成してください: {dest}')
        index, highest, occupied = {}, 0, {}
        pattern = re.compile(re.escape(prefix) + r'#(\d+) (.+)')
        for directory in ([dest, dest / 'T'] if rate == '10k' else [dest]):
            candidates = list(directory.iterdir())
            candidates.extend(p for p in (reserved or {}) if p.parent == directory and p not in candidates)
            for f in candidates:
                match = pattern.fullmatch(f.stem) if f in (reserved or {}) or f.is_file() else None
                if match:
                    n, stem = int(match[1]), match[2]
                    if stem in index and index[stem] != n:
                        raise ValueError(f'既存番号が不整合です: {stem}')
                    if n in occupied and occupied[n] != stem:
                        raise ValueError(f'既存番号が重複しています: {n}')
                    index[stem], occupied[n] = n, stem
                    highest = max(highest, n)
        folder = source / relative
        numbers[rate] = []
        if not folder.is_dir():
            warnings.append(f'コピー元フォルダなし: {folder}')
            continue
        files = sorted([
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() == ext
            and (rate != '10k' or f.name.startswith('D_'))
        ], key=natural)
        paired = set()
        for f in files:
            pair = folder / 'T' / (f.stem + '.tdms')
            if rate == '10k' and not pair.is_file():
                warnings.append(f'TDMS不足のため解析用コピー保留: {f.name}')
                continue
            paired.add(pair.name)
            if f.stem not in index:
                highest += 1
                index[f.stem] = highest
            n = index[f.stem]
            numbers[rate].append(n)
            name = f'{prefix}#{n:0{width}d} {f.name}'
            add(f, dest / name)
            if rate == '10k':
                add(pair, dest / 'T' / (Path(name).stem + '.tdms'))
        if rate == '10k' and (folder / 'T').is_dir():
            for f in (folder / 'T').iterdir():
                if f.is_file() and f.name.startswith('D_') and f.suffix.lower() == '.tdms' and f.name not in paired:
                    warnings.append(f'TXT不足のため解析用コピー保留: {f.name}')
    raw = target / 'raw data' / source.name
    for root, dirs, files in os.walk(source):
        for name in dirs:
            if (Path(root) / name).is_symlink():
                raise ValueError('コピー元にリンクフォルダがあります')
        for name in files:
            src = Path(root) / name
            add(src, raw / src.relative_to(source))
    for pos, (src, dst) in enumerate(pending):
        def report(label, path):
            def update(count, size):
                if progress:
                    progress('プレビュー照合', pos, len(pending), f'{label}: {path} ({count / 1048576:.1f}/{size / 1048576:.1f} MiB)')
            return update
        is_raw = raw in dst.parents
        if reserved and dst in reserved and reserved[dst] != src:
            raise ValueError(f'転送セット間で保存先が重複しています: {dst}')
        stat = src.stat()
        sha = signature(src, report('コピー元', src)) if verify and not is_raw else ''
        status = '新規'
        if dst.exists():
            if is_raw or not verify:
                status = '既存（内容未検証）' if dst.is_file() else '競合'
                if not is_raw and dst.is_file() and dst.stat().st_size != stat.st_size:
                    status = '競合'
            else:
                status = 'コピー済み' if dst.is_file() and signature(dst, report('保存先', dst)) == sha else '競合'
        items.append(Item(src, dst, sha, status, is_raw, (stat.st_size, stat.st_mtime_ns)))
        if progress:
            progress('プレビュー照合', pos + 1, len(pending), str(src))
    return Plan(items, warnings, source, target, numbers)


def review_batch_duplicates(jobs, progress=None):
    """Resolve analysis destinations by full content without copying files."""
    samples, hashes, directories = {}, {}, {}
    def cached(cache, path, read):
        stat = path.stat()
        key = (path, stat.st_size, stat.st_mtime_ns)
        if key not in cache:
            cache[key] = read(path)
        return cache[key]
    items = [item for _, plan, _ in jobs for item in plan.items if not item.raw]
    # Acquisition counts refer to TDMS; report companion TXT separately.
    items.sort(key=lambda item: item.source.suffix.lower() != '.tdms')
    tdms_total = sum(item.source.suffix.lower() == '.tdms' for item in items)
    totals = {True: tdms_total, False: len(items) - tdms_total}
    completed = {True: 0, False: 0}
    for item in items:
        is_tdms = item.source.suffix.lower() == '.tdms'
        phase = 'コピー予定・TDMS照合' if is_tdms else 'コピー予定・付随ファイル照合（TXT等）'
        if progress:
            progress(phase, completed[is_tdms], totals[is_tdms], str(item.source))
        stat = item.source.stat()
        if item.snapshot and (stat.st_size, stat.st_mtime_ns) != item.snapshot:
            raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
        dst = item.destination
        if dst.exists():
            candidates = [dst] if dst.is_file() else []
        else:
            if dst.parent not in directories:
                # scandir reuses directory-entry metadata instead of issuing
                # several network stat calls for every source/target pair.
                index = {}
                if dst.parent.is_dir():
                    with os.scandir(dst.parent) as entries:
                        for entry in entries:
                            if entry.is_file(follow_symlinks=False):
                                path = Path(entry.path)
                                key = (path.suffix.lower(), entry.stat().st_size)
                                index.setdefault(key, []).append(path)
                directories[dst.parent] = index
            candidates = directories[dst.parent].get((item.source.suffix.lower(), stat.st_size), [])
            # Copy naming preserves the original name after the sequence number.
            # Try that match before reading samples from unrelated TDMS files.
            candidates = sorted(candidates, key=lambda path: not path.name.endswith(' ' + item.source.name))
        item.status = '競合' if dst.exists() else '新規'
        for candidate in candidates:
            if candidate.stat().st_size != stat.st_size:
                continue
            if cached(samples, item.source, sampled_signature) != cached(samples, candidate, sampled_signature):
                continue
            sha = cached(hashes, item.source, signature)
            if sha == cached(hashes, candidate, signature):
                item.destination = candidate
                item.sha256 = sha
                item.status = '内容重複スキップ'
                break
        completed[is_tdms] += 1
        if progress:
            progress(phase, completed[is_tdms], totals[is_tdms], str(item.source))
    return jobs


def execute(plan, log=print, progress=None):
    if any(i.status == '競合' for i in plan.items):
        raise ValueError('競合があります。コピーは開始しません')
    # Recheck every destination and source before any writes.
    def report(phase, pos, path):
        def update(count, size):
            if progress:
                progress(phase, pos, len(plan.items), f'{path} ({count / 1048576:.1f}/{size / 1048576:.1f} MiB)')
        return update

    for pos, item in enumerate(plan.items):
        item.execution_status = '確認中'
        if item.raw:
            if item.destination.exists() and not item.destination.is_file():
                raise ValueError(f'保存先がファイルではありません: {item.destination}')
            item.execution_status = '未処理'
            continue
        stat = item.source.stat()
        if item.snapshot and (stat.st_size, stat.st_mtime_ns) != item.snapshot:
            raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
        if item.destination.exists() and not item.destination.is_file():
            raise ValueError(f'保存先がファイルではありません: {item.destination}')
        if progress:
            progress('サイズ・更新日時確認', pos + 1, len(plan.items), str(item.source))
        item.execution_status = '未処理'
    for pos, item in enumerate(plan.items, 1):
        item.execution_status = '処理中'
        dst = item.destination
        # Renamed analysis files can already exist under another sequence number.
        # A sampled match only narrows candidates; full hashes decide reuse.
        if not item.raw and not dst.exists() and dst.parent.is_dir():
            candidates = [p for p in dst.parent.iterdir()
                          if p.is_file() and not p.is_symlink()
                          and p.suffix.lower() == item.source.suffix.lower()
                          and p.stat().st_size == item.source.stat().st_size]
            if candidates:
                if progress:
                    progress('重複候補確認', pos - 1, len(plan.items), str(item.source))
                sample_hash = sampled_signature(item.source)
                for candidate in candidates:
                    if sampled_signature(candidate) != sample_hash:
                        continue
                    sha = signature(item.source, report('重複照合・コピー元', pos - 1, item.source))
                    if item.sha256 and sha != item.sha256:
                        raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
                    if signature(candidate, report('重複照合・保存先', pos - 1, candidate)) == sha:
                        item.destination = dst = candidate
                        item.sha256 = sha
                        item.status = '内容重複スキップ'
                        break
        if progress:
            progress('コピー・検証', pos - 1, len(plan.items), str(dst))
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not item.raw:
            stat = item.source.stat()
            if item.snapshot and (stat.st_size, stat.st_mtime_ns) != item.snapshot:
                raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
        if dst.exists():
            if not dst.is_file():
                raise ValueError(f'競合: {dst}')
            sha = signature(item.source, report('既存比較・コピー元', pos - 1, item.source))
            if item.sha256 and sha != item.sha256:
                raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
            if signature(dst, report('既存比較・保存先', pos - 1, dst)) != sha:
                raise ValueError(f'競合: {dst}')
            item.sha256 = sha
            item.execution_status = '既存一致スキップ'
            log(f'既存一致スキップ: {dst}')
            if progress:
                progress('コピー・検証', pos, len(plan.items), str(dst))
            continue
        temp = dst.with_name('.' + uuid.uuid4().hex + '.partial')
        try:
            before = item.source.stat()
            if not item.raw and item.snapshot and (before.st_size, before.st_mtime_ns) != item.snapshot:
                raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
            hasher = hashlib.sha256() if not item.raw else None
            size, count, last = before.st_size, 0, 0.0
            update = report('ファイルコピー', pos - 1, dst)
            with item.source.open('rb') as reader, temp.open('xb') as writer:
                for block in iter(lambda: reader.read(1024 * 1024), b''):
                    writer.write(block)
                    if hasher is not None:
                        hasher.update(block)
                    count += len(block)
                    if time.monotonic() - last >= 0.2:
                        update(count, size)
                        last = time.monotonic()
            update(count, size)
            shutil.copystat(item.source, temp)
            after = item.source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or count != size or temp.stat().st_size != size:
                raise ValueError(f'コピー中に変更またはサイズ不一致: {item.source}')
            if hasher is not None:
                sha = hasher.hexdigest()
                if item.sha256 and sha != item.sha256:
                    raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
                if signature(temp, report('コピー結果検証', pos - 1, dst)) != sha:
                    raise ValueError(f'コピー検証失敗: {item.source}')
                item.sha256 = sha
            # Windows rename fails if destination exists; never overwrite.
            if os.name == 'nt':
                os.rename(temp, dst)
            else:
                os.link(temp, dst)
                temp.unlink()
        finally:
            temp.unlink(missing_ok=True)
        item.execution_status = 'コピー完了'
        log(f'{pos}/{len(plan.items)} コピー完了: {dst.name}')
        if progress:
            progress('コピー・検証', pos, len(plan.items), str(dst))
    for root, dirs, files in os.walk(plan.source):
        (plan.target / 'raw data' / plan.source.name / Path(root).relative_to(plan.source)).mkdir(parents=True, exist_ok=True)


def record(plan, context, legacy, local):
    now = datetime.now().strftime('%Y/%m/%d_%H:%M')
    match = re.match(r'(\d{8})_(\d{4})', plan.source.name)
    date = datetime.strptime(''.join(match.groups()), '%Y%m%d%H%M').strftime('%Y/%m/%d_%H:%M') if match else ''
    # Preserve the legacy columns, recording the uploader in both person fields.
    row = [now, context['uploader'], context['machine'], context['selection'], date, context['uploader'], plan.source.name, plan.target.parent.name, plan.target.name]
    for rate in ('10k', '100k'):
        name = f"{plan.target.name}_{rate}_{context['kind']}"
        nums = plan.numbers[rate]
        row.extend([name, name, str(min(nums)) if nums else '', str(max(nums)) if nums else ''])
    records = history(legacy, local)
    records.append(tuple(row))
    records = sorted(set(records), key=lambda r: datetime.strptime(r[0], '%Y/%m/%d_%H:%M'), reverse=True)
    ident = uuid.uuid4().hex
    write_csv(Path(local).parent / 'manifests' / f'{ident}.csv', ['source', 'destination', 'sha256', 'preview_status', 'pc_id', 'pc_name'], [(i.source, i.destination, i.sha256, i.status, context.get('pc_id', ''), context.get('pc_name', '')) for i in plan.items])
    # Partial transfers are kept in a separate manifest, never marked complete in legacy-compatible history.
    if plan.warnings:
        return False
    write_csv(local, HEADERS, records)
    return True


def plan_batch(sets, progress=None):
    """Reserve filenames across all sets without reading file contents."""
    reserved, jobs, seen = {}, [], set()
    for set_no, spec in enumerate(sets, 1):
        for source in sorted(spec['sources'], key=lambda p: str(p).casefold()):
            key = (Path(source).resolve(), Path(spec['target']).resolve(), spec['context']['kind'])
            if key in seen:
                raise ValueError(f'同じ転送が二重登録されています: {source}')
            seen.add(key)
            callback = None
            if progress:
                callback = lambda phase, n, total, detail: progress(f'セット {set_no}/{len(sets)} 予定作成', n, total, detail)
            p = plan(source, spec['target'], Path(spec['target']).name, spec['context']['kind'], progress=callback, reserved=reserved)
            for item in p.items:
                reserved[item.destination] = item.source
            jobs.append((set_no, p, spec))
    return jobs


def execute_batch(jobs, log=print, progress=None):
    """Stop on error, persist completed histories and mark remaining jobs unstarted."""
    if any(i.status == '競合' for _, p, _ in jobs for i in p.items):
        raise ValueError('プレビューに競合があります。セットを修正してください')
    results = []
    report_path = Path(jobs[0][2]['local']).parent / 'batches' / (uuid.uuid4().hex + '.csv')
    stopped = False
    for number, (set_no, p, spec) in enumerate(jobs, 1):
        state, detail = '未実行', ''
        if not stopped:
            try:
                callback = None
                if progress:
                    callback = lambda phase, n, total, name: progress(f'セット {set_no}/{max(j[0] for j in jobs)}・実験 {number}/{len(jobs)} {phase}', n, total, name)
                execute(p, log, callback)
                complete = record(p, spec['context'], spec['legacy'], spec['local'])
                state = '完了' if complete else '警告あり'
                detail = ' / '.join(p.warnings)
            except Exception as exc:
                state, detail, stopped = '失敗（以降停止）', str(exc), True
                for item in p.items:
                    if getattr(item, 'execution_status', '') in ('確認中', '処理中'):
                        item.execution_status = '停止箇所'
        file_report = report_path.with_name(f'{report_path.stem}_{number}_files.csv')
        write_csv(file_report, ['source', 'destination', 'result'],
                  [(i.source, i.destination, getattr(i, 'execution_status', '未処理')) for i in p.items])
        log(f'ファイル別結果: {file_report}')
        results.append((set_no, str(p.source), str(p.target), state, detail))
        log(f'{number}/{len(jobs)} {state}: {p.source} → {p.target} {detail}')
        write_csv(report_path, ['set','source','target','result','detail'], results)
    log(f'一括結果: {report_path}')
    return results
