"""BNAL用の退避・復帰: stock ANAL files every selected BNAL@<target> already finished, so a BNAL run fills only the gaps.

For one sample on one share and the targets of the chosen SEQlist CSVs:
  C = '#NNN' numbers with a non-empty .tdms in every BNAL@<target> (a target without a folder has none).
  ANAL直下 numbers in C -> ANAL/stocked (.tdms + .tdms_index); numbers outside C stay in (or return to) ANAL.
  BNAL@ results outside C (they will be re-made) and zero-byte BNAL .tdms (interrupted writes) are deleted
  together with their per-file result folder ('B@@<stamp>@..._Sample#NNN'), into BNAL@<target>/.整理履歴.
Deletions run before the moves. The plan is journaled by anal_manager.execute_moves and can be undone.
Once every target has every number, the same plan brings ANAL/stocked back into ANAL.
"""
from collections import Counter
import os
from pathlib import Path
import re
import uuid

from shared.rack_store import measurement_key, number_ranges as _ranges

SUFFIXES = ('.tdms', '.tdms_index')
HISTORY = '.整理履歴'
# 'B@@2026_0902_0735_KSN2_10k_Sample#001.tdms' and its result folder 'B@@2026_0902_0735@KSN2_10k_Sample#001'.
RUN_STAMP = re.compile(r'^[AB]@@?(\d{4}_\d{4}_\d{4}(?:_\d{2})?)[_@]', re.I)


def _number(name):
    key = measurement_key(name)
    return key if isinstance(key, int) else None


def _child(folder, name):
    """Case-insensitive direct child directory (not a link), or None."""
    try:
        with os.scandir(folder) as iterator:
            return next((Path(e.path) for e in iterator
                         if e.name.casefold() == name.casefold() and e.is_dir(follow_symlinks=False)), None)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _entry(path, is_dir=False):
    stat = path.stat()
    return dict(path=str(path.absolute()), size=0 if is_dir else stat.st_size, modified_ns=stat.st_mtime_ns, dir=is_dir)


def _anal_groups(folder, location, orphans=None):
    """{stem: group} of .tdms (+ same-stem .tdms_index) directly in folder.

    A companion whose .tdms is not in this folder (a run stopped between the two) is appended to orphans."""
    groups = {}
    if folder is None:
        return []
    for entry in os.scandir(folder):
        path = Path(entry.path)
        if not entry.is_file(follow_symlinks=False) or path.suffix.lower() not in SUFFIXES:
            continue
        groups.setdefault(path.stem, []).append(path)
    result = []
    for stem, paths in sorted(groups.items()):
        main = next((p for p in paths if p.suffix.lower() == '.tdms'), None)
        if main is None:
            if orphans is not None:
                orphans.append(dict(location=location, stem=stem, name=paths[0].name, paths=[_entry(p) for p in sorted(paths)]))
            continue
        result.append(dict(location=location, stem=stem, name=main.name, number=_number(main.name),
                           size=main.stat().st_size, paths=[_entry(p) for p in sorted(paths)]))
    return result


def _bnal_runs(folder):
    """One run per (stamp, number): its .tdms, same-stem companions and result folder."""
    runs = {}
    for entry in os.scandir(folder):
        path = Path(entry.path)
        if entry.is_symlink() or entry.name == HISTORY:
            continue
        is_dir = entry.is_dir(follow_symlinks=False)
        if not is_dir and path.suffix.lower() not in SUFFIXES:
            continue
        number = _number(entry.name)
        if number is None:
            continue
        stamp = RUN_STAMP.match(entry.name)
        run = runs.setdefault((stamp[1] if stamp else entry.name if is_dir else path.stem, number),
                              dict(number=number, name=None, size=None, paths=[]))
        run['paths'].append(_entry(path, is_dir))
        if not is_dir and path.suffix.lower() == '.tdms':
            run['name'], run['size'] = entry.name, entry.stat().st_size
    return sorted(runs.values(), key=lambda r: (r['number'], r['name'] or ''))


def scan(t, targets):
    """Current ANAL / ANAL/stocked / BNAL@<target> state of one T folder. Reads the share, never writes."""
    t = Path(t)
    if not t.is_dir():
        raise ValueError(f'Tフォルダが見つかりません: {t}')
    targets = list(dict.fromkeys(targets))
    if not targets:
        raise ValueError('targetリスト（CSV）を選択してください。')
    anal = _child(t, 'ANAL')
    if anal is None:
        raise ValueError(f'ANALフォルダが見つかりません: {t}')
    stocked = _child(anal, 'stocked')
    orphans = []
    result = dict(t=str(t.absolute()), anal=str(anal.absolute()), orphans=orphans,
                  files=_anal_groups(anal, 'ANAL', orphans) + _anal_groups(stocked, 'ANAL/stocked', orphans), targets=[])
    for target in targets:
        folder = _child(t, 'BNAL@' + target)
        result['targets'].append(dict(target=target, folder=str(folder.absolute()) if folder else None,
                                      runs=_bnal_runs(folder) if folder else []))
    return result


def done_numbers(target):
    return {r['number'] for r in target['runs'] if r['size']}


def common_numbers(result):
    """Numbers finished in every target ('最小の数と順番'); empty when any target has not started."""
    sets = [done_numbers(t) for t in result['targets']]
    return set.intersection(*sets) if sets else set()


def plan(result):
    """Deletions first, then ANAL moves. Returns dict(plan, rows, warnings, summary)."""
    t, anal = Path(result['t']), Path(result['anal'])
    common = common_numbers(result)
    batch = uuid.uuid4().hex
    deletions, moves, rows, warnings = [], [], [], []

    def ops(paths, parent):
        return [dict(source=p['path'], target=str(parent / Path(p['path']).name), size=p['size'],
                     modified_ns=p['modified_ns'], dir=p['dir'], status='予定') for p in paths]

    anal_numbers = [f['number'] for f in result['files'] if f['number'] is not None]
    for target in result['targets']:
        name = target['target']
        if target['folder'] is None:
            warnings.append(f'BNAL@{name} がありません（未着手）。全targetで済んだ番号がないため退避はなく、'
                            '他targetのBNALはすべて削除予定になります。')
            continue
        folder = Path(target['folder'])
        extra = sorted(done_numbers(target) - set(anal_numbers))
        if extra:
            warnings.append(f'BNAL@{name}: ANAL（stocked含む）にない番号 {len(extra)}件（{_ranges(extra)}）。超過を確認してください。')
        for run in target['runs']:
            empty = run['size'] == 0
            if empty or run['number'] not in common:
                reason = ('0バイト（書きかけ）' if empty else '先行分（再解析で作り直し）' if run['name']
                          else '結果フォルダのみ（先行分）')
                deletions += ops(run['paths'], folder / HISTORY / batch)
                rows.append(dict(plan='削除：' + reason, tag='delete', location='BNAL@' + name, run=run))
            else:
                rows.append(dict(plan='', tag='done', location='BNAL@' + name, run=run))
    duplicates = sorted(n for n, c in Counter(anal_numbers).items() if c > 1)
    if duplicates:
        warnings.append(f'ANALに同じ番号が複数あります {len(duplicates)}件（{_ranges(duplicates)}）。'
                        '世代混在の可能性があります。ANAL 重複の整理で確認してください（このまま実行もできます）。')
    taken = {f['name'].casefold() for f in result['files'] if f['location'] == 'ANAL'}
    stocked_names = {f['name'].casefold() for f in result['files'] if f['location'] == 'ANAL/stocked'}
    missing = sorted(set(anal_numbers) - common)
    for f in result['files']:
        stay = f['location'] == 'ANAL' and (f['number'] is None or f['number'] not in common)
        if not missing:  # every target has every ANAL number: the BNAL run is over, bring the stocked files back
            if f['location'] == 'ANAL/stocked':
                if f['name'].casefold() in taken:
                    rows.append(dict(plan='移動しない：ANALに同名あり', tag='skip', location=f['location'], run=f))
                    continue
                moves += ops(f['paths'], anal)
                rows.append(dict(plan='復帰（→ ANAL、全target完了）', tag='restore', location=f['location'], run=f))
            else:
                rows.append(dict(plan='', tag='stocked', location=f['location'], run=f))
            continue
        if f['number'] is None:
            rows.append(dict(plan='ANALに残す：#番号なし', tag='skip', location=f['location'], run=f))
            continue
        if f['location'] == 'ANAL' and not stay:
            if f['name'].casefold() in stocked_names:
                rows.append(dict(plan='移動しない：stockedに同名あり', tag='skip', location=f['location'], run=f))
                continue
            moves += ops(f['paths'], anal / 'stocked')
            rows.append(dict(plan='退避（→ ANAL/stocked）', tag='archive', location=f['location'], run=f))
        elif f['location'] == 'ANAL/stocked' and f['number'] not in common:
            # A previous round stocked a number some target now lacks: it must be analysed again.
            if f['name'].casefold() in taken:
                rows.append(dict(plan='移動しない：ANALに同名あり', tag='skip', location=f['location'], run=f))
                continue
            moves += ops(f['paths'], anal)
            rows.append(dict(plan='復帰（→ ANAL、欠番の再解析用）', tag='restore', location=f['location'], run=f))
        else:
            rows.append(dict(plan='ANALに残す（欠番・再解析）' if stay else '', tag='stay' if stay else 'stocked',
                             location=f['location'], run=f))
    summary = dict(common=len(common), common_text=_ranges(sorted(common)) or 'なし',
                   missing=len(missing), missing_text=_ranges(missing) or 'なし',
                   archive=sum(r['tag'] == 'archive' for r in rows), restore=sum(r['tag'] == 'restore' for r in rows),
                   delete=sum(r['tag'] == 'delete' for r in rows),
                   targets=[dict(target=x['target'], exists=x['folder'] is not None, done=len(done_numbers(x)),
                                 runs=len(x['runs']), empty=sum(r['size'] == 0 for r in x['runs']),
                                 delete=sum(r['tag'] == 'delete' and r['location'] == 'BNAL@' + x['target'] for r in rows))
                            for x in result['targets']])
    from shared.t_stock import pair_up
    skipped = []
    pair_up(result.get('orphans', []), result['files'], moves, skipped, {'ANAL': anal, 'ANAL/stocked': anal / 'stocked'})
    for orphan in result.get('orphans', []):
        reason = dict(skipped).get(orphan['name'])
        paired = any(Path(o['source']).stem == orphan['stem'] for o in moves)
        rows.append(dict(plan=('移動しない：' + reason) if reason else '付属ファイルを.tdmsの側へ' if paired else '',
                         tag='skip' if reason else 'restore' if paired else 'stocked', location=orphan['location'],
                         run=dict(number=_number(orphan['name']), name=orphan['name'], size=None, paths=orphan['paths'])))
    operations = deletions + moves  # empty when every target is complete or ANAL already holds only the gaps
    plan = dict(folder=str(t), action='bnal_stock', suffixes=SUFFIXES, verify_content=False, operations=operations)
    return dict(plan=plan, rows=rows, warnings=warnings, summary=summary)


def stage(built):
    """(kind, text) of where this sample stands: todo / restore (a plan to run), wait (stocked, BNAL to run), done."""
    s = built['summary']
    stocked = sum(r['location'] == 'ANAL/stocked' for r in built['rows'])
    if built['plan']['operations']:
        if s['archive'] or s['delete']:
            return 'todo', (f"未実行 — 退避 {s['archive']}件・BNAL削除 {s['delete']}件"
                            + (f"・復帰 {s['restore']}件" if s['restore'] else '') + 'の予定があります。確認して「予定を実行」を押してください。')
        return 'restore', f"BNAL完了 — ANAL/stockedの{s['restore']}件をANALへ戻せます。「予定を実行」で復帰します。"
    if s['missing']:
        return 'wait', (f"✔ 退避済み・BNAL解析待ち — ANALには欠番 {s['missing']}件（{s['missing_text']}）だけが残り、"
                        f"ANAL/stocked {stocked}件。BNAL解析を流し、終わったら「選択サンプルのDBを更新」→ 再調査で復帰します。")
    return 'done', '✔ 完了 — CSVの全targetのBNALがそろい、ANAL/stockedもANALへ戻っています。'


def result_text(plan):
    """What a finished plan did, counted from its operations."""
    files = [o for o in plan['operations'] if not o.get('dir') and o['source'].lower().endswith('.tdms')]
    deleted = [o for o in files if HISTORY in Path(o['target']).parts]
    to_stock = [o for o in files if o not in deleted and Path(o['target']).parent.name.casefold() == 'stocked']
    back = [o for o in files if o not in deleted and o not in to_stock]
    folders = sum(bool(o.get('dir')) for o in plan['operations'])
    parts = [f'ANAL/stockedへ {len(to_stock)}件' if to_stock else '', (('ANALへ' if all(Path(o['target']).parent.name.casefold() == 'anal' for o in back) else '元の場所へ')
             + f' {len(back)}件') if back else '',
             f'削除（.整理履歴へ） {len(deleted)}件' + (f'（結果フォルダ {folders}件）' if folders else '') if deleted else '']
    return '・'.join(p for p in parts if p) or f'{len(plan["operations"])}件'


def action_label(built):
    """'BNAL用の退避' when anything goes to stocked or is deleted, otherwise 'BNAL用の復帰'."""
    s = built['summary']
    if s['archive'] or s['delete']:
        return 'BNAL用の退避' + ('・一部復帰' if s['restore'] else '')
    return 'BNAL用の復帰'


def headline(built):
    """First line of the confirmation: which direction, in words."""
    s = built['summary']
    if s['archive'] or s['delete']:
        return (f"【退避】ANALの{s['archive']}件をANAL/stockedへ移し、BNAL先行分 {s['delete']}件を削除します。"
                + (f"stockedの{s['restore']}件はANALへ戻します。" if s['restore'] else '')
                + f"ANALには欠番 {s['missing']}件が残ります。")
    return f"【復帰】ANAL/stockedの{s['restore']}件をANALへ戻します（CSVの全targetのBNALが完了）。BNALは削除しません。"


def describe(built):
    s = built['summary']
    lines = [headline(built), '', f"全targetで済んだ番号: {s['common']}件（{s['common_text']}）",
             f"ANALに残す欠番: {s['missing']}件（{s['missing_text']}）",
             f"ANAL → stocked: {s['archive']}件　stocked → ANAL: {s['restore']}件　BNAL削除: {s['delete']}件", '']
    lines += ['⚠ ' + w for w in built['warnings']] + ([''] if built['warnings'] else [])
    for t in s['targets']:
        lines.append(f"BNAL@{t['target']}: " + (f"済み {t['done']}件　削除 {t['delete']}件"
                                                + (f"（0バイト {t['empty']}件含む）" if t['empty'] else '')
                                                if t['exists'] else 'フォルダなし'))
    lines.append('')
    for o in built['plan']['operations']:
        kind = '[フォルダ] ' if o.get('dir') else ''
        lines.append(f"{kind}{o['source']}\n  → {o['target']}")
    return '\n'.join(lines)
