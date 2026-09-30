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

BASE = Path(__file__).resolve().parent
DB = Path(r'D:\My_Dropbox\ou&t\machinedb\SEQ')
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
    items, warnings, numbers = [], [], {}

    pending = []

    def add(src, dst):
        if src.is_symlink() or dst.is_symlink():
            raise ValueError(f'リンクは扱えません: {src} / {dst}')
        pending.append((src, dst))

    for rate, relative, ext, width in [('10k', 'EXSV', '.txt', 3), ('100k', 'EXZSV', '.tdms', 4)]:
        prefix = f'{sample}_{rate}_{kind}'
        dest = target / prefix
        if not dest.is_dir() or (rate == '10k' and not (dest / 'T').is_dir()):
            raise ValueError(f'保存先をcreate_folders.pyで作成してください: {dest}')
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
        if item.raw:
            if item.destination.exists() and not item.destination.is_file():
                raise ValueError(f'保存先がファイルではありません: {item.destination}')
            continue
        stat = item.source.stat()
        if item.snapshot and (stat.st_size, stat.st_mtime_ns) != item.snapshot:
            raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
        if item.destination.exists() and not item.destination.is_file():
            raise ValueError(f'保存先がファイルではありません: {item.destination}')
        if progress:
            progress('サイズ・更新日時確認', pos + 1, len(plan.items), str(item.source))
    for pos, item in enumerate(plan.items, 1):
        dst = item.destination
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
            if not item.raw:
                sha = signature(item.source, report('既存比較・コピー元', pos - 1, item.source))
                if item.sha256 and sha != item.sha256:
                    raise ValueError(f'プレビュー後にコピー元が変わりました: {item.source}')
                if signature(dst, report('既存比較・保存先', pos - 1, dst)) != sha:
                    raise ValueError(f'競合: {dst}')
                item.sha256 = sha
            log(f'{"既存raw dataスキップ（内容未検証）" if item.raw else "重複スキップ"}: {dst}')
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
        log(f'{pos}/{len(plan.items)} コピー完了: {dst.name}')
        if progress:
            progress('コピー・検証', pos, len(plan.items), str(dst))
    for root, dirs, files in os.walk(plan.source):
        (plan.target / 'raw data' / plan.source.name / Path(root).relative_to(plan.source)).mkdir(parents=True, exist_ok=True)


def record(plan, context, legacy, local):
    now = datetime.now().strftime('%Y/%m/%d_%H:%M')
    match = re.match(r'(\d{8})_(\d{4})', plan.source.name)
    date = datetime.strptime(''.join(match.groups()), '%Y%m%d%H%M').strftime('%Y/%m/%d_%H:%M') if match else ''
    row = [now, context['operator'], context['machine'], context['selection'], date, context['uploader'], plan.source.name, plan.target.parent.name, plan.target.name]
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
        results.append((set_no, str(p.source), str(p.target), state, detail))
        log(f'{number}/{len(jobs)} {state}: {p.source} → {p.target} {detail}')
        write_csv(report_path, ['set','source','target','result','detail'], results)
    log(f'一括結果: {report_path}')
    return results
