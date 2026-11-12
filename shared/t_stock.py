"""Data 退避の計画: 解析済みの元ファイル T -> T/stocked と、その逆（Analyzer で使用）。

T のファイルは ANAL か ANAL/stocked に同じ測定番号（rack_store.measurement_key）の空でない TDMS があるとき解析済み。
未解析・欠番は常に T に残す。.tdms_index など同じ名前の付属ファイルは一緒に移す。実行は file_moves.execute_moves。
"""
import os
from pathlib import Path

from shared.rack_store import as_numbers, measurement_key


SUFFIXES = ('.tdms', '.tdms_index')


def _groups(folder, orphans=False):
    """{stem: [files]} for TDMS-family files directly in folder.

    orphans=False keeps groups with a .tdms; True returns only companions whose .tdms is not here
    (left behind when a run stopped between a .tdms and its .tdms_index)."""
    groups = {}
    if not folder.is_dir():
        return groups
    for path in folder.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        groups.setdefault(path.stem, []).append(path)
    return {stem: files for stem, files in groups.items()
            if any(p.suffix.lower() == '.tdms' for p in files) != orphans}


def _anal_keys(t):
    keys = set()
    for folder in (t / 'ANAL', t / 'ANAL' / 'stocked'):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            key = measurement_key(path.name)
            # A zero-byte ANAL file is an interrupted write, not a finished analysis.
            if (path.suffix.lower() == '.tdms' and path.is_file()
                    and not path.is_symlink() and path.stat().st_size > 0):
                keys.add(key)
    return keys


def scan_t(t):
    """Current files in T and T/stocked with their analysed state (reads the share, never writes)."""
    t = Path(t)
    if not t.is_dir():
        raise ValueError(f'Tフォルダが見つかりません: {t}')
    anal_keys = _anal_keys(t)
    groups = [(location, stem, paths, next(p for p in paths if p.suffix.lower() == '.tdms'))
              for folder, location in ((t, 'T'), (t / 'stocked', 'T/stocked'))
              for stem, paths in sorted(_groups(folder).items())]
    # only '#NNN' numbers are moved: 'prefix#NNN' too when the sample has that one numbering (rack_store.as_numbers)
    anal_number = as_numbers(anal_keys)
    t_number = as_numbers(measurement_key(main.name) for *_, main in groups)
    analysed = {anal_number[k] for k in anal_keys if isinstance(anal_number[k], int)}
    files = []
    for location, stem, paths, main in groups:
        key = t_number[measurement_key(main.name)]
        number = key if isinstance(key, int) else None
        files.append(dict(location=location, stem=stem, number=number,
                          analysed=number is not None and number in analysed,
                          name=main.name, extra=sorted(p.name for p in paths if p != main),
                          size=sum(p.stat().st_size for p in paths),
                          modified_ns=main.stat().st_mtime_ns,
                          paths=[dict(path=str(p.absolute()), size=p.stat().st_size,
                                      modified_ns=p.stat().st_mtime_ns) for p in sorted(paths)]))
    orphans = [dict(location=location, stem=stem, name=paths[0].name,
                    paths=[dict(path=str(p.absolute()), size=p.stat().st_size, modified_ns=p.stat().st_mtime_ns)
                           for p in sorted(paths)])
               for folder, location in ((t, 'T'), (t / 'stocked', 'T/stocked'))
               for stem, paths in sorted(_groups(folder, orphans=True).items())]
    return dict(folder=str(t.absolute()), files=files, anal_numbers=analysed, orphans=orphans)


def summary(scan):
    files = scan['files']
    in_t = [f for f in files if f['location'] == 'T']
    numbers = {f['number'] for f in files if f['number'] is not None}
    tail = max(scan['anal_numbers'], default=None)
    gaps = sorted(n for n in numbers - scan['anal_numbers'] if tail is not None and n < tail)
    return dict(t=len(in_t), t_analysed=sum(f['analysed'] for f in in_t),
                t_unanalysed=sum(f['number'] is not None and not f['analysed'] for f in in_t),
                t_unknown=sum(f['number'] is None for f in in_t),
                stocked=sum(f['location'] == 'T/stocked' for f in files),
                anal=len(scan['anal_numbers'] & numbers), total=len(numbers), gaps=gaps, last=tail)


def plan_moves(scan, restore, keep_last=False):
    """Archive: analysed T files -> T/stocked. Restore: every T/stocked file -> T.

    Returns (plan, skipped) where skipped lists (name, reason) left in place."""
    t = Path(scan['folder'])
    source_location = 'T/stocked' if restore else 'T'
    target_parent = t if restore else t / 'stocked'
    last = max(scan['anal_numbers'], default=None)
    operations, skipped = [], []
    for f in scan['files']:
        if f['location'] != source_location:
            continue
        if not restore:
            if f['number'] is None:
                skipped.append((f['name'], '#番号なし'))
                continue
            if not f['analysed']:
                continue  # unanalysed / gap: stays in T for the analysis run
            if keep_last and f['number'] == last:
                skipped.append((f['name'], '最後の解析番号（書きかけ対策でTに残す）'))
                continue
        conflict = [p for p in f['paths'] if os.path.lexists(target_parent / Path(p['path']).name)]
        if conflict:
            skipped.append((f['name'], '移動先に同名あり'))
            continue
        for p in f['paths']:
            operations.append(dict(source=p['path'], target=str(target_parent / Path(p['path']).name),
                                   size=p['size'], modified_ns=p['modified_ns'], status='予定'))
    pair_up(scan.get('orphans', []), scan['files'], operations, skipped, {'T': t, 'T/stocked': t / 'stocked'})
    plan = dict(folder=str(t), action='restore' if restore else 'archive', suffixes=SUFFIXES,
                verify_content=False, operations=operations)
    return plan, skipped


def pair_up(orphans, files, operations, skipped, folders):
    """Send each companion left without its .tdms to the folder where that .tdms ends up after this plan."""
    moved = {Path(o['source']).stem: Path(o['target']).parent for o in operations}
    final = {f['stem']: moved.get(f['stem'], folders[f['location']]) for f in files}
    for orphan in orphans:
        target = final.get(orphan['stem'])
        if target is None:
            skipped.append((orphan['name'], '対応する.tdmsがない付属ファイル'))
            continue
        if target == folders[orphan['location']]:
            continue
        if any(os.path.lexists(target / Path(p['path']).name) for p in orphan['paths']):
            skipped.append((orphan['name'], '移動先に同名あり'))
            continue
        for p in orphan['paths']:
            operations.append(dict(source=p['path'], target=str(target / Path(p['path']).name),
                                   size=p['size'], modified_ns=p['modified_ns'], status='予定'))


def describe(plan):
    """Confirmation text: each folder pair once, then one line per TDMS with its companions."""
    pairs = {}
    for o in plan['operations']:
        source, target = Path(o['source']), Path(o['target'])
        pairs.setdefault((str(source.parent), str(target.parent)), {}).setdefault(source.stem, []).append(source.suffix)
    lines = []
    for (source, target), stems in pairs.items():
        lines += [f'移動元: {source}', f'移動先: {target}',
                  f'{len(stems)}件（付属を含め{sum(map(len, stems.values()))}ファイル）', '']
        lines += [f'  [{" + ".join(sorted(suffixes))}]  {stem}' for stem, suffixes in sorted(stems.items())]
        lines.append('')
    return '\n'.join(lines)
