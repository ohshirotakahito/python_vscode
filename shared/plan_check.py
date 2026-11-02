"""サンプル台帳と実験ファイルDB（保存済みの調査結果）を突き合わせ、サンプルごとの確認事項を出す。

サーバーにはアクセスしない。確認事項は3段階:
    矛盾（CONFLICT）  : 台帳と実際が食い違う・台帳の参照先が無い。放置すると誤った解析になりうる
    設定不足（MISSING）: BNAL に回すか・種類・SEQlist・BC が決まっていない
    作業待ち（WAITING）: BNAL 対象で ANAL は終わっているのに BNAL が始まっていない
"""
from collections import defaultdict

from shared import bnal_lists, catalog, progress, sample_plan

CONFLICT, MISSING, WAITING = '矛盾', '設定不足', '作業待ち'
ISSUES = {
    '台帳のみ（DBになし）': CONFLICT, 'SEQlistファイルなし': CONFLICT, 'BCファイルなし': CONFLICT,
    'SEQlistの種類違い': CONFLICT, 'BCの種類違い': CONFLICT, '対象外なのにBNALあり': CONFLICT,
    '未分類': MISSING, '種類未設定': MISSING, 'SEQlist未設定': MISSING, 'BC未設定': MISSING,
    'BNAL未着手': WAITING,
}
LEVELS = (CONFLICT, MISSING, WAITING)
KIND_OF_SEQ = {'Nucleotide': '核酸', 'Peptide': 'アミノ酸'}
KIND_OF_BC = {'Base': '核酸', 'Amino acid': 'アミノ酸'}


def issues(entry, in_db, anal_status, bnal_started, seq_kinds, bc_kinds):
    """Issue names of one sample, worst first.

    entry: its ledger entry ({} if none); anal_status: best-share status (shared.progress) or None;
    bnal_started: BNAL@ targets holding files on any share; seq_kinds / bc_kinds: {csv name: kind} in machinedb."""
    found = []
    if entry and not in_db:
        found.append('台帳のみ（DBになし）')
    bnal, kind = entry.get('bnal'), entry.get('kind')
    for name in entry.get('seqlists', []):
        if name not in seq_kinds:
            found.append('SEQlistファイルなし')
        elif kind and KIND_OF_SEQ.get(seq_kinds[name]) != kind:
            found.append('SEQlistの種類違い')
    bc = entry.get('bc')
    if bc and bc not in bc_kinds:
        found.append('BCファイルなし')
    elif bc and kind and KIND_OF_BC.get(bc_kinds[bc]) != kind:
        found.append('BCの種類違い')
    if bnal == 'excluded' and bnal_started:
        found.append('対象外なのにBNALあり')
    if in_db and not bnal:
        found.append('未分類')
    if bnal == 'target':
        if not kind:
            found.append('種類未設定')
        if not entry.get('seqlists'):
            found.append('SEQlist未設定')
        if not bc:
            found.append('BC未設定')
        if anal_status in progress.DONE_STATES and not bnal_started:
            found.append('BNAL未着手')
    found = list(dict.fromkeys(found))
    return sorted(found, key=lambda name: LEVELS.index(ISSUES[name]))


def level(found):
    """The worst level among the issues, or '' when there is none."""
    return next((lv for lv in LEVELS if any(ISSUES[name] == lv for name in found)), '')


def sample_rows(samples, bnals, plan, labels=None, seq_kinds=None, bc_kinds=None):
    """One row per (experiment, sample) in the DB or the ledger, with its ledger entry and issues.

    samples / bnals: rack_samples / rack_bnals rows; plan: sample_plan.load(); labels: root identity -> group."""
    labels = labels or {}
    seq_kinds = bnal_lists.seq_files() if seq_kinds is None else seq_kinds
    bc_kinds = bnal_lists.bc_files() if bc_kinds is None else bc_kinds
    cells, shares = defaultdict(list), defaultdict(list)
    for r in samples:
        k = sample_plan.key(r['experiment'], r['sample'])
        cells[k].append(progress.anal_share(r))
        shares[k].append(labels.get(catalog.root_identity(r['root']), r['root']))
    started = defaultdict(set)
    for b in bnals:
        if b.get('tdms'):  # an empty BNAL@ folder is not started (shared.progress.bnal_state)
            started[sample_plan.key(b['experiment'], b['sample'])].add(b['target'])
    rows = []
    for k in sorted(set(cells) | set(plan), key=str.casefold):
        experiment, sample = k.split('|', 1)
        entry = plan.get(k, {})
        in_db = k in cells  # before cells[k] below (a defaultdict) adds the key
        best = progress.assess_sample(cells[k])[0] if in_db else None
        found = issues(entry, in_db, best and best['status'], started[k], seq_kinds, bc_kinds)
        rows.append(dict(key=k, experiment=experiment, sample=sample, shares=sorted(set(shares[k])),
                         anal=best['state'] if best else '', anal_status=best and best['status'],
                         bnal_started=len(started[k]), kind=entry.get('kind', ''), bnal=entry.get('bnal', ''),
                         seqlists=entry.get('seqlists', []), bc=entry.get('bc', ''),
                         updated=(entry.get('updated_at') or '')[:16].replace('T', ' '),
                         updated_by=entry.get('updated_by', ''), issues=found, level=level(found)))
    return rows
