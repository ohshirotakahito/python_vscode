"""BNAL進捗 panel: expected targets from SEQlist CSVs versus saved BNAL@ folders on every analysis server.

Everything here reads the saved DB (rack_samples / rack_bnals); no server is accessed.
Progress of one BNAL@<target> = its direct .tdms count / T/ANAL direct .tdms count on the same share.
"""
from collections import defaultdict
from pathlib import Path
import csv
import os
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
try:
    from . import catalog, rackstation_view
except ImportError:
    import catalog, rackstation_view
import ui_theme  # noqa: E402  (rackstation_view puts the repository root on sys.path)

DEFAULT_TARGET_DIR = r'D:\Dropbox\ou&t\machinedb\SEQlist'
UNSTARTED_GROUP = '—'


def read_targets(path):
    """SEQlist CSVs have no header: name,sequence,length. Returns names in file order without duplicates."""
    data = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'cp932'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f'文字コードを判別できません: {path}')
    names = (line.split(',', 1)[0].strip() for line in text.splitlines())
    return list(dict.fromkeys(name for name in names if name))


def list_target_csvs(folder):
    return sorted((p.name for p in Path(folder).glob('*.csv') if p.is_file()), key=str.casefold)


def load_saved(path):
    """(samples, bnals) from the saved DB; both empty when the Analysis_server scan has never run."""
    with catalog.connect(path, readonly=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        samples = [dict(r) for r in db.execute('SELECT * FROM rack_samples')] if 'rack_samples' in tables else []
    return samples, rackstation_view.bnal_inventory(path)


def group_labels(configured):
    """root identity -> 'Server/share' label for the registered analysis groups."""
    return {catalog.root_identity(rackstation_view.default_root(configured, server, share)):
            rackstation_view.group_label(server, share) for server, share in rackstation_view.ANALYSIS_GROUPS}


def sample_choices(samples):
    return sorted({(row['experiment'], row['sample']) for row in samples}, key=lambda key: (key[0].casefold(), key[1].casefold()))


def occurrence_state(tdms, anal):
    """(進捗表示, 状態, タグ) for one BNAL@ folder against the ANAL on the same share."""
    if tdms is None:
        return '未計数', '着手（TDMS未計数）', 'unknown'
    if not anal:
        return '確認不可', 'ANALなし・要確認', 'unknown'
    rate = f'{tdms / anal * 100:.1f}%'
    if tdms > anal:
        return rate, '超過・要確認', 'excess'
    if tdms == anal:
        return rate, '完了', 'complete'
    return rate, '進行中', 'partial'


FLAG_NAMES = dict(stocked='退避', short='不足', mixed='混在')
# 差異 is information only (shares may legitimately hold different ANAL sets); the others need fixing first.
INFO_FLAGS = ('差異',)
FLAG_LEGEND = ('⚠（黄の帯）＝先に片付けが必要：退避＝ANAL/stockedに退避中　混在＝ANALの世代混在の疑い　'
               '不足＝ANALがT（元ファイル）より少ない（ANAL未完了・移動ミス）　未取得＝BNAL@名が未取得（要更新）。'
               '参考：差異＝共有間でANAL数が違う')


def _listing(items):
    return '、'.join(f'{name} {value}' for name, value in items)


def count_check(actual, expected):
    """Compare saved file counts without treating unknown counts as zero."""
    if actual is None or expected is None:
        return '未計数・確認不可'
    if actual == expected:
        return '一致'
    return f'不足 {expected - actual}件' if actual < expected else f'超過 {actual - expected}件'


def sequence_checks(target, tdms, anal, base):
    basic = parent_target(target)
    modified = basic != target
    base_tdms = base.get('tdms') if base is not None else None
    return dict(sequence_type='修飾配列' if modified else '基本配列', base_target=basic,
                base_tdms=base_tdms, anal_check=count_check(tdms, anal),
                base_check=('基本配列なし' if base is None else count_check(tdms, base_tdms))
                if modified else '対象外',
                base_ok=not modified or (tdms is not None and base_tdms is not None and tdms == base_tdms))


def build_progress(selected, expected, samples, bnals, labels, show_extra=False):
    """Rows per (sample, target, share) plus per-share and overall summaries.

    BNAL makes one file per ANAL file present on its share, so progress = BNAL files / (ANAL + ANAL/stocked)
    on the same share. Overall progress averages each target's best share ratio (capped at 1), because
    shares can legitimately hold different ANAL sets.
    selected: [(experiment, sample)], expected: target names from the chosen CSVs,
    labels: root identity -> group label (unknown roots fall back to the root itself)."""
    label = lambda root: labels.get(catalog.root_identity(root), root)  # noqa: E731
    info = {}
    for r in samples:
        stocked = r.get('anal_stocked_tdms') or 0
        anal = None if r.get('anal_tdms') is None else r['anal_tdms'] + stocked
        original = None if r.get('t_tdms') is None else r['t_tdms'] + (r.get('t_stocked_tdms') or 0)
        mixed = r['anal_tdms'] - r['anal_distinct'] if r.get('anal_distinct') is not None and r.get('anal_tdms') else 0
        info[(catalog.root_identity(r['root']), r['experiment'], r['sample'])] = dict(
            anal=anal, stocked=stocked, folders=r.get('bnal_folders') or 0, mixed=mixed,
            short=(original - anal) if anal is not None and original and original > anal else 0)
    groups_of = defaultdict(list)
    for r in samples:
        groups_of[(r['experiment'], r['sample'])].append(r['root'])
    found = defaultdict(list)
    by_share_target = {}
    for b in bnals:
        found[(b['experiment'], b['sample'], b['target'])].append(b)
        by_share_target[(catalog.root_identity(b['root']), b['experiment'], b['sample'], b['target'])] = b
    expected_set = set(expected)
    rows, warnings = [], []
    shares = defaultdict(lambda: dict(started=0, complete=0, uncounted=0, done=0, goal=0))
    total = dict(expected=0, started=0, complete=0, uncounted=0, done=0.0, goal=0)
    for experiment, sample in selected:
        roots = sorted(groups_of.get((experiment, sample), []), key=label)
        per_share = {label(root): info[(catalog.root_identity(root), experiment, sample)] for root in roots}
        prefix = f'{experiment}/{sample}: '
        counts = {name: v['anal'] for name, v in per_share.items() if v['anal']}
        flags = []  # short codes shown on the map; the warnings carry the details
        if len(set(counts.values())) > 1:
            warnings.append(prefix + f'共有ごとにANAL数が異なります（{_listing(counts.items())}）')
            flags.append('差異')
        for key, text in (('stocked', 'ANAL/stockedに退避中のファイルがあります（{}件）。進捗の分母には含めています。'),
                          ('short', 'ANAL（stocked含む）がT直下＋T/stockedより少ない共有があります（不足 {}件）。'
                                    '誤った場所への移動などを確認してください。'),
                          ('mixed', 'ANALに同じ#番号のファイルがあります（重複 {}件）。ANALの世代が混在している可能性があります。')):
            hits = [(name, v[key]) for name, v in per_share.items() if v[key]]
            if hits:
                warnings.append(prefix + text.format(_listing(hits)))
                flags.append(FLAG_NAMES[key])
        reference = max(counts.values()) if counts else None
        sample_bnals = [b for (e, s, _), items in found.items() if (e, s) == (experiment, sample) for b in items]
        sample_targets = {b['target'] for b in sample_bnals}
        # Shares scanned before BNAL@ names were saved: folder count known, names missing.
        stale = [label(root) for root in roots
                 if info[(catalog.root_identity(root), experiment, sample)]['folders']
                 > sum(catalog.root_identity(b['root']) == catalog.root_identity(root) for b in sample_bnals)]
        if stale:
            warnings.append(prefix + f'BNAL@名が未取得の共有があります（{"、".join(stale)}）。Analysis_serverで更新してください。')
            flags.append('未取得')
        flags = tuple(flags)
        first_row = len(rows)
        targets = list(expected) + (sorted(sample_targets - expected_set, key=str.casefold) if show_extra else [])
        for target in targets:
            occurrences = found.get((experiment, sample, target), [])
            listed = target in expected_set
            if not occurrences:
                state, tag = ('未確認（要更新）', 'unknown') if stale else ('未着手', 'unstarted')
                rows.append(dict(experiment=experiment, sample=sample, target=target, group=UNSTARTED_GROUP,
                                 anal_tdms=reference, bnal_tdms=None, rerun=None, progress='0%', state=state,
                                 tdms_checked=None, tag=tag,
                                 **sequence_checks(target, None, reference, None)))
            best = 0.0
            any_complete = False
            for b in sorted(occurrences, key=lambda b: label(b['root'])):
                share_anal = info.get((catalog.root_identity(b['root']), experiment, sample), {}).get('anal')
                progress, state, tag = occurrence_state(b['tdms'], share_anal)
                base = by_share_target.get((catalog.root_identity(b['root']), experiment, sample, parent_target(target)))
                checks = sequence_checks(target, b['tdms'], share_anal, base)
                complete = bool(share_anal and b['tdms'] == share_anal and checks['base_ok'])
                if tag == 'complete' and not checks['base_ok']:
                    state, tag = ('進行中（基本配列と不一致）', 'partial') if base is not None and base['tdms'] is not None else ('基本配列未確認・要確認', 'unknown')
                if not listed:
                    state, tag = '一覧外（CSVにない）', 'extra'
                elif len(occurrences) > 1:
                    state, tag = state + '／複数サーバー', 'duplicate'
                rerun = None if b.get('tdms_distinct') is None or b['tdms'] is None else b['tdms'] - b['tdms_distinct']
                rows.append(dict(experiment=experiment, sample=sample, target=target, group=label(b['root']),
                                 anal_tdms=share_anal, bnal_tdms=b['tdms'], rerun=rerun, progress=progress, state=state,
                                 tdms_checked=b['tdms_checked'], tag=tag, **checks))
                if not listed:
                    continue
                done = min(b['tdms'] or 0, share_anal or 0)
                share = shares[label(b['root'])]
                share['started'] += 1
                share['uncounted'] += b['tdms'] is None
                share['complete'] += complete
                any_complete = any_complete or complete
                share['done'] += done
                share['goal'] += share_anal or 0
                if share_anal:
                    best = max(best, done / share_anal)
            if not listed:
                continue
            # Overall counts each (sample, target) once even if it runs on several shares.
            total['expected'] += 1
            total['goal'] += 1
            if occurrences:
                total['started'] += 1
                total['uncounted'] += all(b['tdms'] is None for b in occurrences)
                total['done'] += best
                total['complete'] += any_complete
        for row in rows[first_row:]:
            row['flags'] = flags
    summary =[dict(group=name, **values) for name, values in sorted(shares.items())]
    return rows, summary, dict(group='全体', **total), warnings


SERVER_ABBR = {'rackstation': 'R', 'qtserver': 'QT', 'qdserver': 'QD', 'qkserver': 'QK'}
SHARE_ABBR = {'analysis': 'a', 'backup': 'b', 'archive': 'r'}
DONE_STATES = ('complete', 'excess')


def share_abbr(group):
    """'QTserver/analysis' -> 'QTa' (map cell text); unknown roots keep a short prefix."""
    server, _, share = group.partition('/')
    return SERVER_ABBR.get(server.casefold(), server[:3]) + SHARE_ABBR.get(share.casefold(), share[:1])


def parent_target(name):
    """Variants such as 'hsa-miR-193a-5p_M_11' / '_N_17' fold into 'hsa-miR-193a-5p'."""
    return re.sub(r'_[NM]_\d+$', '', name)


def _leaf_cell(target_rows):
    """Best share for one (sample, target) from build_progress rows."""
    started = [r for r in target_rows if r['group'] != UNSTARTED_GROUP]
    if not started:
        row = target_rows[0]
        if row['state'] == '未着手':
            return dict(status='unstarted', ratio=0.0, remaining=row['anal_tdms'] or 0, servers=())
        return dict(status='unknown', ratio=None, remaining=None, servers=())

    def ratio(r):
        return None if r['bnal_tdms'] is None or not r['anal_tdms'] else r['bnal_tdms'] / r['anal_tdms']
    ordered = sorted(started, key=lambda r: -1 if ratio(r) is None else ratio(r), reverse=True)
    best, servers = ordered[0], tuple(share_abbr(r['group']) for r in ordered)
    value = ratio(best)
    if value is None:
        return dict(status='unknown', ratio=None, remaining=None, servers=servers)
    status = 'excess' if value > 1 else 'complete' if value == 1 else 'partial'
    if status == 'complete' and not best.get('base_ok', True):
        status = 'unknown' if best.get('base_tdms') is None else 'partial'
    return dict(status=status, ratio=value, remaining=best['anal_tdms'] - min(best['bnal_tdms'], best['anal_tdms']),
                servers=servers)


def _merge_cells(cells):
    """One parent-target cell from its variants."""
    statuses = {c['status'] for c in cells}
    ratios = [min(c['ratio'], 1) for c in cells if c['ratio'] is not None]
    if statuses <= set(DONE_STATES):
        status = 'excess' if 'excess' in statuses else 'complete'
    elif statuses == {'unstarted'}:
        status = 'unstarted'
    elif statuses == {'unknown'}:
        status = 'unknown'
    else:
        status = 'partial'
    known = [c['remaining'] for c in cells if c['remaining'] is not None]
    return dict(status=status, ratio=sum(ratios) / len(cells) if ratios else None,
                remaining=sum(known) if known else None, variants=len(cells),
                servers=tuple(dict.fromkeys(s for c in cells for s in c['servers'])))


def natural_key(text):
    """'KUHCC2' < 'KUHCC10': digit runs compare as numbers, case-insensitive."""
    return [(0, int(part), '') if part.isdigit() else (1, 0, part.casefold()) for part in re.split(r'(\d+)', text)]


def server_name(group):
    """'QTserver/analysis' -> 'QTserver'."""
    return group.partition('/')[0]


def _sample_cells(by_key, key, expected, by_parent, keep=lambda r: True):
    """({column: cell}, unmerged leaves) for one sample from the rows that keep() accepts."""
    leaves = {}
    for t in expected:
        target_rows = [r for r in by_key.get(key + (t,), []) if keep(r)]
        if target_rows:
            leaves[t] = _leaf_cell(target_rows)
    if not by_parent:
        return leaves, leaves
    groups = defaultdict(list)
    for target, cell in leaves.items():
        groups[parent_target(target)].append(cell)
    return {name: _merge_cells(group) for name, group in groups.items()}, leaves


def build_map(rows, expected, by_parent=False, order='name', servers=()):
    """Sample x target matrix for the overview map.

    Each sample also carries by_server={server: {column: cell}} from that server's shares only
    (a missing column = no BNAL@ for it there).

    order='name': experiment / sample ascending (natural order).
    order='work': remaining TDMS (desc), then unstarted targets (desc), then completion (desc: nearly done first).
    Returns (columns, samples, column_rates); each sample has cells {column: cell}."""
    by_key = defaultdict(list)
    for r in rows:
        if r['tag'] != 'extra':
            by_key[(r['experiment'], r['sample'], r['target'])].append(r)
    columns = list(dict.fromkeys(parent_target(t) for t in expected)) if by_parent else list(expected)
    samples = []
    for key in dict.fromkeys((r['experiment'], r['sample']) for r in rows):
        cells, leaves = _sample_cells(by_key, key, expected, by_parent)
        if not leaves:
            continue
        by_server = {name: _sample_cells(by_key, key, expected, by_parent,
                                         lambda r, name=name: server_name(r['group']) == name)[0] for name in servers}
        flags = next((r.get('flags', ()) for r in rows if (r['experiment'], r['sample']) == key), ())
        samples.append(dict(
            experiment=key[0], sample=key[1], cells=cells, by_server=by_server, flags=flags,
            remaining=sum(c['remaining'] or 0 for c in leaves.values()),
            unstarted=sum(c['status'] == 'unstarted' for c in leaves.values()),
            unknown=sum(c['status'] == 'unknown' for c in leaves.values()),
            complete=sum(c['status'] in DONE_STATES for c in leaves.values()), targets=len(leaves),
            completion=sum(min(c['ratio'] or 0, 1) for c in leaves.values()) / len(leaves)))
    if order == 'work':
        samples.sort(key=lambda s: (-s['remaining'], -s['unstarted'], -s['completion'],
                                    natural_key(s['experiment']), natural_key(s['sample'])))
    else:
        samples.sort(key=lambda s: (natural_key(s['experiment']), natural_key(s['sample'])))
    column_rates = {}
    for column in columns:
        values = [min(s['cells'][column]['ratio'] or 0, 1) for s in samples if column in s['cells']]
        column_rates[column] = sum(values) / len(values) if values else None
    return columns, samples, column_rates


MOVE_LOG = Path(__file__).resolve().parent / 'logs' / 'anal_moves.csv'
OLD_FOLDER = 'old'
# ANAL output names: file 'A@@2023_0728_0724_10_KUHCC10_10k_Sample#001.tdms' (+ '.tdms_index' etc.)
# and its per-file result folder 'A@2023_0728_0724_10@KUHCC10_10k_Sample#001'.
ANAL_FILE = re.compile(r'^A@@(\d{4}_\d{4}_\d{4}_\d{2})_.*#(\d+)\.tdms$', re.I)
ANAL_ITEM = re.compile(r'^A@@?(\d{4}_\d{4}_\d{4}_\d{2})[_@].*#(\d+)(?:\..*)?$', re.I)


def _child(folder, name):
    """Case-insensitive direct child directory, or None."""
    try:
        with os.scandir(folder) as iterator:
            return next((Path(e.path) for e in iterator
                         if e.name.casefold() == name.casefold() and e.is_dir(follow_symlinks=False)), None)
    except (FileNotFoundError, NotADirectoryError):
        return None


def plan_restore(root, experiment, sample):
    """Files directly in T/ANAL/stocked to move back into T/ANAL (.tdms and companions such as .tdms_index).

    Names already present in ANAL are conflicts and are never overwritten. Reads the server, writes nothing."""
    t = Path(root) / experiment / sample / f'{sample}_10k_Sample' / 'T'
    anal = _child(t, 'ANAL')
    stocked = _child(anal, 'stocked') if anal else None
    plan = dict(root=root, experiment=experiment, sample=sample, t=t, anal=anal, stocked=stocked, moves=[], conflicts=[],
                operation='ANAL/stocked→ANAL', conflict_note='ANALに同名ありのため移動せず')
    if stocked is None:
        return plan
    with os.scandir(anal) as iterator:
        existing = {e.name.casefold() for e in iterator}
    with os.scandir(stocked) as iterator:
        for entry in sorted(iterator, key=lambda e: e.name):
            if not entry.is_file(follow_symlinks=False):
                continue
            target = anal / entry.name
            (plan['conflicts'] if entry.name.casefold() in existing else plan['moves']).append((Path(entry.path), target))
    return plan


def anal_generations(names):
    """Split ANAL file names into runs: sorted by the timestamp in the name, a new run starts when the
    '#NNN' number drops back to the smallest number. A resumed run (e.g. restarting at #21) is not a new run.

    Returns [dict(start, end, count, first, last, keys)] oldest first; keys are (timestamp, number)."""
    items = sorted((m[1], int(m[2])) for m in map(ANAL_FILE.match, names) if m)
    if not items:
        return []
    lowest = min(number for _, number in items)
    runs, previous = [[]], None
    for stamp, number in items:
        if previous is not None and number == lowest and number <= previous:
            runs.append([])
        runs[-1].append((stamp, number))
        previous = number
    return [dict(start=run[0][0], end=run[-1][0], count=len(run), first=min(n for _, n in run),
                 last=max(n for _, n in run), keys={key for key in run}) for run in runs]


def plan_old_generations(root, experiment, sample):
    """Move every ANAL run except the newest into ANAL/old: its .tdms, same-stem companions (.tdms_index ...)
    and per-file result folders. Existing names in ANAL/old are conflicts. Reads the server, writes nothing."""
    t = Path(root) / experiment / sample / f'{sample}_10k_Sample' / 'T'
    anal = _child(t, 'ANAL')
    plan = dict(root=root, experiment=experiment, sample=sample, t=t, anal=anal, generations=[], moves=[], conflicts=[],
                operation='ANAL旧世代→ANAL/old', conflict_note='ANAL/oldに同名ありのため移動せず')
    if anal is None:
        return plan
    with os.scandir(anal) as iterator:
        entries = [e for e in iterator if e.name.casefold() not in ('stocked', OLD_FOLDER)]
    plan['generations'] = anal_generations([e.name for e in entries if e.is_file(follow_symlinks=False)])
    old_keys = set().union(*(g['keys'] for g in plan['generations'][:-1])) if len(plan['generations']) > 1 else set()
    if not old_keys:
        return plan
    old = _child(anal, OLD_FOLDER)
    existing = set()
    if old is not None:
        with os.scandir(old) as iterator:
            existing = {e.name.casefold() for e in iterator}
    plan['make_dir'] = anal / OLD_FOLDER if old is None else None
    for entry in sorted(entries, key=lambda e: e.name):
        match = ANAL_ITEM.match(entry.name)
        if not match or (match[1], int(match[2])) not in old_keys:
            continue
        target = (old or anal / OLD_FOLDER) / entry.name
        (plan['conflicts'] if entry.name.casefold() in existing else plan['moves']).append((Path(entry.path), target))
    return plan


def restore(plan, log_path=MOVE_LOG):
    """Rename each planned item within the share; skip anything whose destination appeared meanwhile.

    Returns (moved, skipped) and appends every attempt to a local CSV log."""
    moved, skipped = 0, []
    records = []
    if plan.get('make_dir') is not None and plan['moves']:
        plan['make_dir'].mkdir(exist_ok=True)
    for source, target in plan['moves']:
        if target.exists():
            result = '既存のためスキップ'
        else:
            try:
                os.rename(source, target)  # same share: a server-side rename, never a copy
                result = '移動'
                moved += 1
            except OSError as exc:
                result = f'失敗: {exc}'
        if result != '移動':
            skipped.append((source.name, result))
        records.append((catalog.now(), plan['operation'], str(source), str(target), result))
    for source, target in plan['conflicts']:
        records.append((catalog.now(), plan['operation'], str(source), str(target), plan['conflict_note']))
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    new = not log_path.exists()
    with log_path.open('a', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(('日時 UTC', '操作', '移動元', '移動先', '結果'))
        writer.writerows(records)
    return moved, skipped


def refresh_sample_counts(path, root, experiment, sample):
    """Re-read one sample's T after a restore and update only its count columns in rack_samples."""
    t = Path(root) / experiment / sample / f'{sample}_10k_Sample' / 'T'
    counts = rackstation_view.sample_counts(t, threading.Event())
    with catalog.connect(path) as db:
        rackstation_view.ensure_tables(db)
        db.execute(f"UPDATE rack_samples SET {', '.join(f'{name}=?' for name in counts)}, last_success=? "
                   'WHERE root=? AND experiment=? AND sample=?',
                   (*counts.values(), catalog.now(), root, experiment, sample))


def rate_text(done, goal):
    return f'{done / goal * 100:.1f}%' if goal else '—'


DETAIL_COLUMNS = ('experiment', 'sample', 'target', 'group', 'anal_tdms', 'bnal_tdms', 'rerun', 'progress', 'state',
                  'sequence_type', 'base_target', 'base_tdms', 'anal_check', 'base_check', 'tdms_checked')
DETAIL_TITLES = ('実験名', 'サンプル名', 'target', 'グループ', 'ANAL数（stocked含む・同じ共有）', 'BNAL TDMS数',
                 '同じ#番号の重複', '進捗（対ANAL）', '状態', '配列種別', '基本配列名', '基本配列TDMS数（同じ共有）',
                 'ANAL数との一致', '基本配列との一致', 'TDMS確認 UTC')
SUMMARY_COLUMNS = ('group', 'expected', 'started', 'complete', 'uncounted', 'tdms', 'rate')
SUMMARY_TITLES = ('グループ', '期待target数', '着手target数', '完了target数', 'TDMS未計数', '済／期待', '進捗')


def summary_values(row):
    """Share rows count TDMS files; the overall row counts targets (each target's best share ratio)."""
    if row['group'] == '全体':
        tdms = f"{row['done']:,.1f} / {row['goal']:,} target換算"
    else:
        tdms = f"{row['done']:,} / {row['goal']:,} TDMS"
    return dict(row, expected=row.get('expected', ''), tdms=tdms, rate=rate_text(row['done'], row['goal']))


MAP_GAP = 2
TARGET_GAP = 6   # target同士の間隔（1マス内のサーバー帯より広く）
MAP_BAR_W = 120   # 残りTDMSバーの最大長（px）
MAP_ORDERS = {'サンプル名順': 'name', '次に解析すべき順': 'work'}


def map_color(cell):
    """Status fill on the dark field; partial deepens with progress (one hue, light -> strong)."""
    if cell is None:
        return ui_theme.FIELD
    status = cell['status']
    if status == 'partial':
        return ui_theme.mix(ui_theme.FIELD, ui_theme._ROW_HUES['warning'], 0.18 + 0.6 * min(cell['ratio'] or 0, 1))
    hue, strength = dict(unstarted=('danger', 0.55), complete=('success', 0.6), excess=('violet', 0.65),
                         unknown=('neutral', 0.28))[status]
    return ui_theme.mix(ui_theme.FIELD, ui_theme._ROW_HUES[hue], strength)


STATUS_TEXT = dict(unstarted='未着手', partial='進行中', complete='完了', excess='超過', unknown='未計数・未確認')
SERVER_SHORT = {'qdserver': 'QD', 'qtserver': 'QT', 'rackstation': 'RS', 'qkserver': 'QK'}
OVERALL_SHARE = 0.4   # マス幅のうち「全体」帯の割合（残りをサーバーで等分）


# Each server has its own colour so its strip is recognisable without reading text.
SERVER_COLORS = {'qdserver': '#5b9dff', 'qtserver': '#4fc1b5', 'rackstation': '#e889c0', 'qkserver': '#c9c26a'}


def server_short(name):
    return SERVER_SHORT.get(name.casefold(), name[:3])


def server_color(name):
    return SERVER_COLORS.get(name.casefold(), ui_theme._ROW_HUES['neutral'])


def server_rates(samples, servers):
    """Share of all map cells (sample x column) analysed on each server, plus the overall share.

    A cell missing on a server counts as 0, so a server that took only part of the work shows only that part."""
    cells = [(s, column) for s in samples for column in s['cells']]
    if not cells:
        return {}

    def rate(get):
        return sum(min(get(s, c)['ratio'] or 0, 1) if get(s, c) else 0 for s, c in cells) / len(cells)
    result = {name: rate(lambda s, c, name=name: s['by_server'][name].get(c)) for name in servers}
    result['全体'] = rate(lambda s, c: s['cells'][c])
    return result


def server_overview(rows, expected, servers=()):
    """Saved BNAL workload per server; shares remain separate physical copies."""
    expected = set(expected)
    result = {name: dict(server=name, targets=0, complete=0, partial=0, unknown=0,
                         tdms=0, remaining=0) for name in servers}
    for row in rows:
        if row['group'] == UNSTARTED_GROUP or row['target'] not in expected:
            continue
        name = server_name(row['group'])
        item = result.setdefault(name, dict(server=name, targets=0, complete=0, partial=0,
                                            unknown=0, tdms=0, remaining=0))
        cell = _leaf_cell([row])
        item['targets'] += 1
        item['complete'] += cell['status'] == 'complete'
        item['partial'] += cell['status'] in ('partial', 'excess')
        item['unknown'] += cell['status'] == 'unknown'
        item['tdms'] += row['bnal_tdms'] or 0
        item['remaining'] += cell['remaining'] or 0
    return list(result.values())


def create_map_view(frame, jump):
    """Overview canvas: one row per sample (name order, or most remaining work first), one column per target.

    jump(experiment, sample, target_or_None) is called on click to show the matching detail rows."""
    by_parent = tk.BooleanVar(value=False)
    legend = ttk.Frame(frame, padding=(8, 4))
    legend.pack(fill='x')
    for status in ('unstarted', 'partial', 'complete', 'excess', 'unknown'):
        swatch = tk.Canvas(legend, width=14, height=14, highlightthickness=0, bg=ui_theme.PANEL)
        swatch.create_rectangle(1, 1, 13, 13, fill=map_color(dict(status=status, ratio=0.6)), outline='')
        swatch.pack(side='left', padx=(8, 2))
        ttk.Label(legend, text=STATUS_TEXT[status] + ('（濃いほど進んでいる）' if status == 'partial' else '')).pack(side='left')
    ttk.Checkbutton(legend, text='派生（_N_ / _M_）を親targetにまとめる', variable=by_parent,
                    command=lambda: redraw()).pack(side='right')
    order = tk.StringVar(value='サンプル名順')
    order_box = ttk.Combobox(legend, textvariable=order, values=list(MAP_ORDERS), state='readonly', width=16)
    order_box.pack(side='right', padx=(4, 16))
    order_box.bind('<<ComboboxSelected>>', lambda _: redraw())
    ttk.Label(legend, text='並び').pack(side='right')
    # Key for the split cells: [全体 | QD | QT | RS], drawn like a real cell.
    key_row = ttk.Frame(frame, padding=(8, 2))
    key_row.pack(fill='x')
    ttk.Label(key_row, text='1マスの中身（左から）').pack(side='left')
    key_canvas = tk.Canvas(key_row, height=24, highlightthickness=0, bg=ui_theme.PANEL)
    key_canvas.pack(side='left', padx=8)
    ttk.Label(key_row, text='全体＝どれかのサーバーで揃えば完了　サーバー帯＝高さが解析率（空き＝BNAL@なし）',
              style='Muted.TLabel').pack(side='left')
    board = tk.Canvas(frame, height=40, highlightthickness=0, bg=ui_theme.PANEL)
    board.pack(fill='x', padx=8, pady=(4, 2))
    ttk.Label(frame, text='「次に解析すべき順」＝残りTDMSが多い順 → 未着手targetが多い順 → 完了に近い順（上ほど優先）。'
                          'マスにマウスで詳細、クリックで明細へ移動します。', style='Muted.TLabel', padding=(8, 2)).pack(fill='x')
    ttk.Label(frame, text=FLAG_LEGEND, style='Muted.TLabel', padding=(8, 0)).pack(fill='x')
    body = ttk.Frame(frame)
    body.pack(fill='both', expand=True, padx=8, pady=4)
    canvas = tk.Canvas(body, bg=ui_theme.FIELD, highlightthickness=0)
    vertical = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
    horizontal = ttk.Scrollbar(body, orient='horizontal', command=canvas.xview)
    canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    canvas.grid(row=0, column=0, sticky='nsew')
    vertical.grid(row=0, column=1, sticky='ns')
    horizontal.grid(row=1, column=0, sticky='ew')
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    state = dict(rows=[], expected=[], servers=[], hover={}, tip=None, pending=None)
    font = tkfont.Font(family=ui_theme.UI, size=11)
    small = tkfont.Font(family=ui_theme.UI, size=10)
    big = tkfont.Font(family=ui_theme.UI_BOLD, size=15)
    name_font = tkfont.Font(family=ui_theme.UI_BOLD, size=11)

    def strips(x, width, count):
        """[(left, right)] for the overall strip then one per server; a wider gap after the overall strip."""
        if not count:
            return [(x, x + width)]
        overall = width * OVERALL_SHARE
        each = (width - overall) / count
        return [(x, x + overall - 2)] + [(x + overall + i * each, x + overall + (i + 1) * each) for i in range(count)]

    def draw_key():
        key_canvas.delete('all')
        names = ['全体'] + list(state['servers'])
        width = 46 * len(names)
        key_canvas.configure(width=width + 2)
        sample = dict(status='complete', ratio=1)
        for (left, right), name in zip(strips(1, width, len(names) - 1), names):
            fill = map_color(sample) if name == '全体' else server_color(name)
            key_canvas.create_rectangle(left, 1, right - 1, 23, fill=fill, outline='')
            key_canvas.create_text((left + right) / 2, 12, text=name if name == '全体' else server_short(name),
                                   fill=ui_theme.FIELD if name != '全体' else ui_theme.TEXT, font=name_font)

    def server_strip(target, left, right, top_, bottom, cell, name):
        """Server strip: faint track in the server colour, filled from the bottom up to its analysis rate."""
        color = server_color(name)
        items = [target.create_rectangle(left, top_, right, bottom, fill=ui_theme.mix(ui_theme.FIELD, color, 0.22),
                                         outline='')]
        if cell['ratio'] is None:   # present but uncounted: outline only
            items.append(target.create_rectangle(left, top_, right - 1, bottom - 1, outline=color, dash=(2, 2)))
            return items
        filled = (bottom - top_) * min(cell['ratio'], 1)
        if filled:
            items.append(target.create_rectangle(left, bottom - filled, right, bottom, fill=color, outline=''))
        if cell['ratio'] > 1:   # 超過: white cap
            items.append(target.create_rectangle(left, top_, right, top_ + 3, fill=ui_theme.TEXT, outline=''))
        return items

    def draw_board(samples, summaries, overall):
        """Large per-server progress bars: who has analysed how much of the selected work."""
        board.delete('all')
        rates = server_rates(samples, state['servers'])
        names = ['全体'] + list(state['servers'])
        row_h, bar_x, bar_w = 30, 130, 320
        board.configure(height=row_h * len(names) + 6)
        details = {item['server']: f"完了 {item['complete']:,}/{item['targets']:,} target　保存TDMS {item['tdms']:,}"
                                   f"　残り {item['remaining']:,}" for item in summaries}
        details['全体'] = (f"完了 {sum(s['complete'] for s in overall):,}/{sum(s['targets'] for s in overall):,} target"
                         f"　未着手 {sum(s['unstarted'] for s in overall):,}　残りTDMS {sum(s['remaining'] for s in overall):,}")
        for index, name in enumerate(names):
            y = 3 + index * row_h
            color = map_color(dict(status='complete', ratio=1)) if name == '全体' else server_color(name)
            board.create_rectangle(0, y + 5, 8, y + row_h - 5, fill=color, outline='')
            board.create_text(16, y + row_h / 2, anchor='w', text=name, fill=ui_theme.TEXT, font=name_font)
            rate = rates.get(name)
            board.create_rectangle(bar_x, y + 7, bar_x + bar_w, y + row_h - 7,
                                   fill=ui_theme.mix(ui_theme.PANEL, color, 0.18), outline='')
            if rate:
                board.create_rectangle(bar_x, y + 7, bar_x + bar_w * rate, y + row_h - 7, fill=color, outline='')
            board.create_text(bar_x + bar_w + 12, y + row_h / 2, anchor='w', font=big, fill=ui_theme.TEXT,
                              text='—' if rate is None else f'{rate * 100:.1f}%')
            board.create_text(bar_x + bar_w + 110, y + row_h / 2, anchor='w', text=details.get(name, ''),
                              fill=ui_theme.MUTED, font=small)

    def redraw():
        canvas.delete('all')
        state['hover'].clear()
        state['tip'] = None
        rows = state['rows']
        summaries = server_overview(rows, state['expected'], state['servers'])
        _, overall, _ = build_map(rows, state['expected'], servers=state['servers'])
        draw_key()
        draw_board(overall, summaries, overall)
        if not rows:
            canvas.create_text(16, 16, anchor='nw', fill=ui_theme.MUTED, font=font,
                               text='表示対象がありません。サーバーの選択、サンプルの選択、保存済みの調査情報を確認してください。')
            canvas.configure(scrollregion=(0, 0, 0, 0))
            return
        servers = state['servers']
        columns, samples, rates = build_map(rows, state['expected'], by_parent.get(), MAP_ORDERS[order.get()], servers)
        # Widths follow the actual text so there is no dead space between the columns.
        label_w = 16 + max(font.measure(f"{s['experiment']} / {s['sample']}") for s in samples) + 12
        label_w = max(label_w, small.measure('target完了率（平均）') + 20)
        done_w = max(font.measure(f"{s['complete']}/{s['targets']}") for s in samples) + 14
        number_w = max(small.measure(f"{s['remaining']:,}") for s in samples) + 18
        right_w = done_w + MAP_BAR_W + number_w + small.measure('⚠ 退避・混在・不足・未取得　差異')
        width = max(canvas.winfo_width(), 900)
        parts_n = len(servers) + 1
        cell_w = max(4 * parts_n, min(22 * parts_n, (width - label_w - right_w) // max(len(columns), 1)))
        row_h = 28 if len(samples) <= 60 else 22
        header_h = 130 if cell_w >= 14 else 34
        grid_right = label_w + cell_w * len(columns)
        top = header_h
        # Column headers: rotated names when there is room, otherwise names are only in the hover text.
        if cell_w >= 14:
            for index, column in enumerate(columns):
                canvas.create_text(label_w + index * cell_w + cell_w / 2, header_h - 12, text=column, angle=60,
                                   anchor='w', fill=ui_theme.MUTED, font=small)
        else:
            canvas.create_text(label_w, header_h - 8, anchor='sw', fill=ui_theme.MUTED, font=small,
                               text=f'target {len(columns)}列（名前はマウスで表示）')
        for index in range(len(columns)):
            x = label_w + index * cell_w
            names = [None] + servers
            for (left, right), name in zip(strips(x + TARGET_GAP / 2 + 1, cell_w - TARGET_GAP - 2, len(servers)), names):
                fill = map_color(dict(status='complete', ratio=1)) if name is None else server_color(name)
                canvas.create_rectangle(left, top - 7, right - 1, top - 2, fill=fill, outline='')
        x0 = grid_right + 12
        bar_x = x0 + done_w
        flag_x = bar_x + MAP_BAR_W + number_w
        for text, x in (('完了', x0), ('残りTDMS', bar_x), ('注意', flag_x)):
            canvas.create_text(x, header_h - 6, anchor='sw', text=text, fill=ui_theme.MUTED, font=small)
        canvas.create_text(8, header_h - 6, anchor='sw', text=f'サンプル（{len(samples)}）', fill=ui_theme.MUTED, font=small)
        most = max((s['remaining'] for s in samples), default=0) or 1
        warn = ui_theme._ROW_HUES['warning']
        empty_fill = ui_theme.mix(ui_theme.FIELD, ui_theme.TEXT, 0.06)
        for index, s in enumerate(samples):
            y = top + index * row_h
            fixes = [f for f in s['flags'] if f not in INFO_FLAGS]
            notes = [f for f in s['flags'] if f in INFO_FLAGS]
            if fixes:
                canvas.create_rectangle(0, y + 1, 4, y + row_h - 1, fill=warn, outline='')
            name = canvas.create_text(10, y + row_h / 2, anchor='w', text=f"{s['experiment']} / {s['sample']}",
                                      fill=ui_theme.TEXT, font=font)
            state['hover'][name] = (s, None)
            for col_index, column in enumerate(columns):
                x = label_w + col_index * cell_w
                if column not in s['cells']:
                    continue
                parts = [s['cells'][column]] + [s['by_server'][n].get(column) for n in servers]
                # Faint frame per target so the strips of neighbouring targets do not run together.
                frame_item = canvas.create_rectangle(x + TARGET_GAP / 2, y + 1, x + cell_w - TARGET_GAP / 2, y + row_h - 1,
                                                     fill=empty_fill, outline='')
                state['hover'][frame_item] = (s, column)
                names = [None] + servers
                for (left, right), cell, name in zip(strips(x + TARGET_GAP / 2 + 1, cell_w - TARGET_GAP - 2,
                                                            len(servers)), parts, names):
                    if cell is None:
                        continue
                    if name is None:
                        items = [canvas.create_rectangle(left, y + MAP_GAP / 2, right - 1, y + row_h - MAP_GAP / 2,
                                                         fill=map_color(cell), outline='')]
                    else:
                        items = server_strip(canvas, left, right - 1, y + MAP_GAP / 2, y + row_h - MAP_GAP / 2, cell, name)
                    for item in items:
                        state['hover'][item] = (s, column)
            canvas.create_text(x0, y + row_h / 2, anchor='w', text=f"{s['complete']}/{s['targets']}",
                               fill=ui_theme.TEXT, font=font)
            bar = MAP_BAR_W * s['remaining'] / most
            if bar:
                canvas.create_rectangle(bar_x, y + 7, bar_x + bar, y + row_h - 7,
                                        fill=ui_theme.mix(ui_theme.FIELD, ui_theme.TEXT, 0.45), outline='')
            canvas.create_text(bar_x + bar + 6, y + row_h / 2, anchor='w', text=f"{s['remaining']:,}",
                               fill=ui_theme.MUTED, font=small)
            mark = flag_x
            if fixes:
                item = canvas.create_text(mark, y + row_h / 2, anchor='w', text='⚠ ' + '・'.join(fixes), fill=warn, font=small)
                mark = canvas.bbox(item)[2] + 8
            if notes:
                canvas.create_text(mark, y + row_h / 2, anchor='w', text='・'.join(notes), fill=ui_theme.DIM, font=small)
        # Bottom line: mean completion per column.
        y = top + len(samples) * row_h + 6
        canvas.create_text(10, y + row_h / 2, anchor='w', text='target完了率（平均）', fill=ui_theme.MUTED, font=small)
        for col_index, column in enumerate(columns):
            rate = rates[column]
            x = label_w + col_index * cell_w
            here = [s for s in samples if column in s['cells']]
            server_rate = {n: sum(min(s['by_server'][n][column]['ratio'] or 0, 1) for s in here
                                  if column in s['by_server'][n]) / len(here) if here else None for n in servers}
            spans = strips(x + TARGET_GAP / 2 + 1, cell_w - TARGET_GAP - 2, len(servers))
            (left, right), rest = spans[0], spans[1:]
            fill = (ui_theme.FIELD if rate is None else ui_theme.mix(ui_theme.FIELD, ui_theme._ROW_HUES['success'],
                                                                     0.15 + 0.5 * rate))
            item = canvas.create_rectangle(left, y, right - 1, y + row_h, fill=fill, outline='')
            state['hover'][item] = (None, column, rate, server_rate)
            if rate is not None and right - left >= small.measure('100') + 4:
                text = canvas.create_text((left + right) / 2, y + row_h / 2, text=f'{rate * 100:.0f}',
                                          fill=ui_theme.TEXT, font=small)
                state['hover'][text] = (None, column, rate, server_rate)
            for (left, right), name in zip(rest, servers):
                if server_rate[name] is None:
                    continue
                for item in server_strip(canvas, left, right - 1, y, y + row_h, dict(ratio=server_rate[name]), name):
                    state['hover'][item] = (None, column, rate, server_rate)
        canvas.configure(scrollregion=(0, 0, x0 + right_w + 12, y + row_h + 12))

    def describe(info):
        if info[0] is None:
            _, column, rate, server_rate = info
            text = f"{column}\n選択サンプルの平均完了率\n  全体: {'—' if rate is None else f'{rate * 100:.1f}%'}"
            for name, value in server_rate.items():
                text += f"\n  {name}: {'—' if value is None else f'{value * 100:.1f}%'}"
            return text
        s, column = info
        head = (f"{s['experiment']} / {s['sample']}\n完了 {s['complete']}/{s['targets']}　未着手 {s['unstarted']}"
                f"　残りTDMS {s['remaining']:,}　平均 {s['completion'] * 100:.1f}%")
        if s['flags']:
            head += '\n⚠ ' + '・'.join(s['flags'])
        if column is None:
            return head + '\nクリックで明細へ移動'
        cell = s['cells'].get(column)
        if cell is None:
            return head + f'\n{column}: 対象外'
        detail = f"\n{column}" + (f"（派生 {cell['variants']}）" if 'variants' in cell else '')
        detail += f"\n全体: {STATUS_TEXT[cell['status']]}"
        if cell['ratio'] is not None:
            detail += f"　{cell['ratio'] * 100:.1f}%"
        if cell['remaining'] is not None:
            detail += f"　残り {cell['remaining']:,}"
        for name in state['servers']:
            part = s['by_server'][name].get(column)
            text = 'BNAL@なし' if part is None else STATUS_TEXT[part['status']]
            if part is not None and part['ratio'] is not None:
                text += f"　{part['ratio'] * 100:.1f}%"
            detail += f"\n  {name}: {text}"
        return head + detail

    def hover(event):
        x, y = canvas.canvasx(event.x), canvas.canvasy(event.y)
        found = next((state['hover'][i] for i in reversed(canvas.find_overlapping(x, y, x, y)) if i in state['hover']), None)
        canvas.delete('tip')
        if found is None:
            return
        text = canvas.create_text(x + 16, y + 16, anchor='nw', text=describe(found), fill=ui_theme.TEXT, font=font,
                                  tags='tip')
        left, top_, right, bottom = canvas.bbox(text)
        # Keep the tooltip inside the visible area.
        view_right = canvas.canvasx(canvas.winfo_width())
        view_bottom = canvas.canvasy(canvas.winfo_height())
        dx = min(0, view_right - right - 8)
        dy = min(0, view_bottom - bottom - 8)
        canvas.move(text, dx, dy)
        left, top_, right, bottom = canvas.bbox(text)
        box = canvas.create_rectangle(left - 6, top_ - 4, right + 6, bottom + 4, fill=ui_theme.PANEL,
                                      outline=ui_theme.BORDER, tags='tip')
        canvas.tag_raise(text, box)

    def click(event):
        x, y = canvas.canvasx(event.x), canvas.canvasy(event.y)
        found = next((state['hover'][i] for i in reversed(canvas.find_overlapping(x, y, x, y)) if i in state['hover']), None)
        if found and found[0] is not None:
            s, column = found
            jump(s['experiment'], s['sample'], None if by_parent.get() else column)

    def resized(_):
        if state['pending']:
            canvas.after_cancel(state['pending'])
        state['pending'] = canvas.after(200, redraw)

    canvas.bind('<Motion>', hover)
    canvas.bind('<Leave>', lambda _: canvas.delete('tip'))
    canvas.bind('<Button-1>', click)
    canvas.bind('<Configure>', resized)
    canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
    canvas.bind('<Shift-MouseWheel>', lambda e: canvas.xview_scroll(-1 if e.delta > 0 else 1, 'units'))

    def set_data(rows, expected, servers=()):
        state['rows'], state['expected'] = rows, expected
        state['servers'] = sorted(set(servers) | {server_name(r['group']) for r in rows
                                                 if r['group'] != UNSTARTED_GROUP})
        redraw()

    redraw()
    return set_data


def run_moves(parent, window, title, targets, empty_text, planner, describe, intro, done):
    """Plan on the servers for the given rack_samples rows, confirm, move, re-read their counts, then done()."""
    if Path(parent.db_path) != Path(catalog.DEFAULT_DB) or getattr(parent, 'rack_busy', False) or parent.busy:
        messagebox.showinfo(title, 'ローカルDBを表示中で、更新処理が動いていないときに実行してください。', parent=window)
        return
    if not targets:
        messagebox.showinfo(title, empty_text + '\n（記録はAnalysis_serverの更新時点のものです）', parent=window)
        return
    window.config(cursor='watch')
    window.update_idletasks()
    try:
        plans = [planner(r['root'], r['experiment'], r['sample']) for r in targets]
    except OSError as exc:
        messagebox.showerror(title, f'サーバーを読めません: {exc}', parent=window)
        return
    finally:
        window.config(cursor='')
    labels = group_labels(parent.configured)
    lines = [f"{labels.get(catalog.root_identity(p['root']), p['root'])}  {p['experiment']}/{p['sample']}: "
             + describe(p) + f"　移動 {len(p['moves'])}件"
             + (f"　同名あり {len(p['conflicts'])}件（移動しません）" if p['conflicts'] else '') for p in plans]
    if not any(p['moves'] for p in plans):
        messagebox.showinfo(title, '移動できるものはありません。\n\n' + '\n'.join(lines), parent=window)
        return
    if not messagebox.askyesno(title, intro + '\n上書きはせず、同じ共有内での移動（名前変更）です。\n\n' + '\n'.join(lines)
                               + f'\n\n記録: {MOVE_LOG}\n実行しますか？', parent=window):
        return
    results = []
    for plan, row in zip(plans, targets):
        moved, skipped = restore(plan)
        try:
            refresh_sample_counts(catalog.DEFAULT_DB, row['root'], row['experiment'], row['sample'])
        except OSError as exc:
            skipped.append(('件数の再取得', str(exc)))
        results.append(f"{row['experiment']}/{row['sample']}: 移動 {moved}" + (f"　未移動 {len(skipped)}" if skipped else ''))
    messagebox.showinfo(title, '\n'.join(results) + f'\n\n詳細は記録CSVを確認してください。\n{MOVE_LOG}', parent=window)
    done()


def restore_wanted(row):
    return bool(row.get('anal_stocked_tdms'))


def old_wanted(row):
    return row.get('anal_distinct') is not None and row['anal_tdms'] > row['anal_distinct']


def restore_stocked(parent, window, targets, done):
    run_moves(parent, window, 'ANAL/stockedを戻す', targets,
              '選択したサンプルに、ANAL/stockedへ退避中のTDMSは記録されていません。', plan_restore,
              lambda p: '', 'ANAL/stocked直下のファイル（.tdms・.tdms_index等）をANAL直下へ移動します。', done)


def describe_generations(plan):
    runs = plan['generations']
    return '　'.join(f"{'残す' if index == len(runs) - 1 else '旧'}[{g['start'][:9]}〜 {g['count']}件 #{g['first']}-#{g['last']}]"
                    for index, g in enumerate(runs))


def move_old_generations(parent, window, targets, done):
    run_moves(parent, window, 'ANAL旧世代をANAL/oldへ', targets,
              '選択したサンプルに、ANALの#番号の重複（世代混在の疑い）は記録されていません。', plan_old_generations,
              describe_generations,
              'ANALのファイル名の日時順で#番号が最初に戻る所を世代の区切りとし、最新の世代以外を\n'
              'ANAL/oldへ移動します（.tdms・同名の.tdms_index等・対応する結果フォルダ）。BNAL@は変更しません。', done)


def create_panel(parent, window):
    top = ttk.Frame(window, padding=8)
    top.pack(fill='x')
    # 左：サンプル選択　右：targetリスト（CSV）選択
    left = ttk.LabelFrame(top, text='① 実験・サンプルを選択（10k_Sample。Ctrl / Shiftで複数可）', padding=6)
    left.pack(side='left', fill='both', expand=True)
    sample_filter = tk.StringVar()
    filter_bar = ttk.Frame(left)
    filter_bar.pack(fill='x')
    ttk.Label(filter_bar, text='絞り込み').pack(side='left')
    ttk.Entry(filter_bar, textvariable=sample_filter, width=30).pack(side='left', padx=6)
    sample_list = tk.Listbox(left, selectmode='extended', height=10, exportselection=False)
    sample_list.pack(fill='both', expand=True, pady=(4, 0))
    right = ttk.LabelFrame(top, text='② targetリスト（CSV）を選択（複数可）', padding=6)
    right.pack(side='left', fill='both', expand=True, padx=(8, 0))
    folder = tk.StringVar(value=DEFAULT_TARGET_DIR)
    folder_bar = ttk.Frame(right)
    folder_bar.pack(fill='x')
    ttk.Label(folder_bar, text='フォルダ').pack(side='left')
    ttk.Entry(folder_bar, textvariable=folder, width=45).pack(side='left', padx=6)
    csv_list = tk.Listbox(right, selectmode='extended', height=10, exportselection=False)
    csv_list.pack(fill='both', expand=True, pady=(4, 0))

    actions = ttk.Frame(window, padding=(8, 0))
    actions.pack(fill='x')
    show_extra = tk.BooleanVar(value=False)
    status = tk.StringVar(value='Analysis_serverタブで更新した保存済み情報から集計します（サーバーには接続しません）。')
    ttk.Label(window, textvariable=status, padding=(8, 4), wraplength=1300).pack(side='bottom', fill='x')
    # 下半分は「全体マップ」（どのサンプルを次に解析するかの判断用）と「明細」の2つ
    views = ttk.Notebook(window)
    views.pack(fill='both', expand=True, padx=8, pady=4)
    map_tab = ttk.Frame(views)
    detail_tab = ttk.Frame(views)
    views.add(map_tab, text='全体マップ')
    views.add(detail_tab, text='明細')
    summary_tree = parent.table(SUMMARY_COLUMNS, SUMMARY_TITLES, 6, parent=detail_tab)
    summary_tree.tag_configure('total', background=ui_theme.row('violet', 0.2))
    # 警告は件数が多くなるので高さ固定・スクロール（明細の表を押し出さない）
    warning_frame = ttk.Frame(detail_tab, padding=(8, 2))
    warning_frame.pack(fill='x')
    warning_box = tk.Text(warning_frame, height=5, wrap='word', bg=ui_theme.FIELD, fg=ui_theme.MUTED,
                          relief='flat', font=(ui_theme.UI, 9))
    warning_scroll = ttk.Scrollbar(warning_frame, command=warning_box.yview)
    warning_box.configure(yscrollcommand=warning_scroll.set, state='disabled')
    warning_scroll.pack(side='right', fill='y')
    warning_box.pack(fill='x', expand=True)

    def set_warnings(lines):
        warning_box.config(state='normal')
        warning_box.delete('1.0', 'end')
        warning_box.insert('1.0', '\n'.join(lines) if lines else '警告はありません。')
        warning_box.config(state='disabled')
    detail_tree = parent.table(DETAIL_COLUMNS, DETAIL_TITLES, 18, True, parent=detail_tab)
    for col in DETAIL_COLUMNS:
        detail_tree.column(col, width=240 if col in ('target', 'base_target', 'state', 'tdms_checked') else 140)
    for tag, color in [('unstarted', ui_theme.row('danger')), ('partial', ui_theme.row('warning')),
                       ('complete', ui_theme.row('success')), ('excess', ui_theme.row('violet')),
                       ('duplicate', ui_theme.row('info')), ('extra', ui_theme.row('neutral', 0.3)),
                       ('unknown', ui_theme.row('neutral', 0.16))]:
        detail_tree.tag_configure(tag, background=color)
    detail_tree.bind('<Double-1>', lambda event: parent.show_row_details(detail_tree))
    summary_tree.bind('<Double-1>', lambda event: parent.show_row_details(summary_tree))
    ttk.Label(detail_tab, text='進捗＝BNAL@直下TDMS数÷同じ共有のANAL（直下＋stocked）TDMS数。全体は各targetの最も進んだ共有の割合（最大100%）の平均。'
                               '赤：未着手　黄：進行中　緑：完了　紫：超過　青：複数サーバーで重複　灰：未計数・一覧外。'
                               'TDMS数はAnalysis_serverの「BNAL内訳のTDMS数も取得」で更新します。',
              padding=(8, 2), wraplength=1300).pack(fill='x')

    def jump(experiment, sample, target):
        """From the map: open the detail tab on the first matching row."""
        views.select(detail_tab)
        for item in detail_tree.get_children():
            values = detail_tree.item(item, 'values')
            if values[0] == experiment and values[1] == sample and (target is None or values[2] == target):
                detail_tree.selection_set(item)
                detail_tree.see(item)
                detail_tree.focus(item)
                return

    set_map = create_map_view(map_tab, jump)
    choices = []
    saved = dict(samples=[], bnals=[])

    def fill_samples(*_):
        query = sample_filter.get().strip().casefold()
        chosen = {choices[i] for i in sample_list.curselection()}
        choices[:] = [key for key in sample_choices(saved['samples']) if query in f'{key[0]}/{key[1]}'.casefold()]
        sample_list.delete(0, 'end')
        aside, mixed = defaultdict(int), defaultdict(int)
        for r in saved['samples']:
            aside[(r['experiment'], r['sample'])] += r.get('anal_stocked_tdms') or 0
            if r.get('anal_distinct') is not None and r.get('anal_tdms'):
                mixed[(r['experiment'], r['sample'])] += r['anal_tdms'] - r['anal_distinct']
        for index, (experiment, sample) in enumerate(choices):
            key = (experiment, sample)
            mark = (f"　［ANAL/stocked 退避中 {aside[key]}］" if aside[key] else '') + \
                   (f"　［ANAL #番号重複 {mixed[key]}（世代混在?）］" if mixed[key] else '')
            sample_list.insert('end', f'{experiment} / {sample}{mark}')
            if mark:
                sample_list.itemconfig(index, foreground=ui_theme.row('warning', 1.0))
            if (experiment, sample) in chosen:
                sample_list.selection_set(index)

    def fill_csvs():
        csv_list.delete(0, 'end')
        try:
            names = list_target_csvs(folder.get())
        except OSError as exc:
            status.set(f'CSVフォルダを読めません: {exc}')
            return
        for name in names:
            csv_list.insert('end', name)

    def reload_db():
        try:
            saved['samples'], saved['bnals'] = load_saved(parent.db_path)
        except Exception as exc:
            status.set(f'DBを読めません: {exc}')
            return
        fill_samples()
        status.set(f"保存済み {len(choices)}サンプル・BNAL@ {len(saved['bnals']):,}件を読み込みました（{parent.db_path}）。")

    def browse():
        chosen = filedialog.askdirectory(parent=window, initialdir=folder.get() or None)
        if chosen:
            folder.set(chosen)
            fill_csvs()

    def show():
        selected = [choices[i] for i in sample_list.curselection()]
        csvs = [csv_list.get(i) for i in csv_list.curselection()]
        if not selected or not csvs:
            messagebox.showinfo('BNAL進捗', '実験・サンプルとtargetリスト（CSV）をそれぞれ1つ以上選択してください。', parent=window)
            return
        expected = []
        try:
            for name in csvs:
                expected.extend(read_targets(Path(folder.get()) / name))
        except (OSError, ValueError) as exc:
            messagebox.showerror('CSVの読み込み', str(exc), parent=window)
            return
        expected = list(dict.fromkeys(expected))
        # Analysis_server can update the DB while this panel remains open.
        try:
            saved['samples'], saved['bnals'] = load_saved(parent.db_path)
        except Exception as exc:
            status.set(f'DB??????: {exc}')
            return
        rows, shares, total, warnings = build_progress(selected, expected, saved['samples'], saved['bnals'],
                                                       group_labels(parent.configured), show_extra.get())
        summary_tree.delete(*summary_tree.get_children())
        for row in shares:
            summary_tree.insert('', 'end', values=[parent.cell(summary_values(row), c) for c in SUMMARY_COLUMNS])
        summary_tree.insert('', 'end', values=[parent.cell(summary_values(total), c) for c in SUMMARY_COLUMNS],
                            tags=('total',))
        detail_tree.delete(*detail_tree.get_children())
        for row in rows:
            values = dict(row)
            for key in ('anal_tdms', 'bnal_tdms', 'base_tdms'):
                values[key] = '—' if row[key] is None else row[key]
            values['rerun'] = row['rerun'] or ''
            values['tdms_checked'] = row['tdms_checked'] or '—'
            detail_tree.insert('', 'end', values=[parent.cell(values, c) for c in DETAIL_COLUMNS], tags=(row['tag'],))
        labels = group_labels(parent.configured)
        set_map(rows, expected, {labels.get(catalog.root_identity(r['root']), r['root']).partition('/')[0]
                               for r in saved['samples']})
        notes = list(warnings)
        if total['uncounted']:
            notes.append(f"TDMS未計数のtarget {total['uncounted']}件は進捗0として数えています。")
        set_warnings([f'警告 {len(notes)}件（スクロールで確認できます）'] + notes if notes else [])
        status.set(f"{len(selected)}サンプル × {len(expected)}target（CSV {len(csvs)}件）: "
                   f"着手 {total['started']} / 完了 {total['complete']} / 期待 {total['expected']}　"
                   f"全体の進捗 {rate_text(total['done'], total['goal'])}")

    def after_moves():
        reload_db()
        if csv_list.curselection():
            show()

    def selected_rows(wanted):
        selected = {choices[i] for i in sample_list.curselection()}
        return [r for r in saved['samples'] if (r['experiment'], r['sample']) in selected and wanted(r)]

    def restore_selected():
        restore_stocked(parent, window, selected_rows(restore_wanted), after_moves)

    def move_old_selected():
        move_old_generations(parent, window, selected_rows(old_wanted), after_moves)

    ttk.Button(folder_bar, text='参照', command=browse).pack(side='left')
    ttk.Button(folder_bar, text='再読込', command=fill_csvs).pack(side='left', padx=4)
    ttk.Button(actions, text='進捗を表示', command=show).pack(side='left')
    ttk.Button(actions, text='DBを再読込', command=reload_db).pack(side='left', padx=6)
    ttk.Checkbutton(actions, text='CSVにないBNAL@（一覧外）も表示', variable=show_extra).pack(side='left', padx=6)
    ttk.Button(actions, text='選択サンプルのANAL/stockedをANALへ戻す…', command=restore_selected).pack(side='left', padx=(24, 0))
    ttk.Button(actions, text='選択サンプルのANAL旧世代をANAL/oldへ…', command=move_old_selected).pack(side='left', padx=6)
    sample_filter.trace_add('write', fill_samples)
    window.after(0, fill_csvs)
    window.after(0, reload_db)
