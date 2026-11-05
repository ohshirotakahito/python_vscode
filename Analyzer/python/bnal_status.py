"""Current state of one sample's BNAL@<target> folders on the server, and what can be done about it.

survey(t) reads T/ANAL, T/ANAL/stocked and every T/BNAL@<target> (it changes nothing). Issues carry the target
they belong to (None for the sample). B@@ keeps the measurement of its A@@ ('#NNN', shared.rack_store), and its
name starts with the time it was made, so a B@@ older than the current A@@ of its number was made from an
earlier analysis.

    action          what it does (journaled in the file manager's bnal_logs; undo from the history)
    empty_b         0-byte B@@ (an interrupted write)            -> BNAL@<target>/.整理履歴 (deleted, restorable)
    old_b           older B@@ of a number that has a newer one    -> BNAL@<target>/.整理履歴
    orphan_b        B@@ of a number ANAL does not have            -> BNAL@<target>/old
    restore_anal    ANAL/stocked -> ANAL (the BNAL run analyses ANAL only; anal_status does the move)

    manual / information
    renumber        ANAL needs new numbers first (anal_status)
    stale_b         B@@ made before the current A@@ of its number (one line for the sample): the A@@ was analysed
                    again later. With the same raw file and conditions the B@@ still holds; to make them again use
                    the redo mode (再解析) of the run. Never moved from here: it can be thousands of files.
    anal_pending    raw files in T without A@@: the ANAL run comes first
    pending         A@@ without B@@ in this target (run the analysis: add the pair to ④)
    empty_folders   BNAL@ folders without any B@@ (a LabVIEW run stopped, or nothing made yet)
The B@@ result folders (B@@<stamp>@<name>) move together with their .tdms.
"""
import os
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import anal_status
from first_step import long_path
from shared import file_moves, rack_store

BNAL_LOGS = file_moves.journal_dir("BNAL")
HISTORY = ".整理履歴"


def _stamp(name):
    """'YYYY_MMDD_HHMM' of an A@@ / B@@ name ('' when it has none); minutes, as B@@ names have no seconds."""
    m = rack_store.RUN_STAMP.match(name)
    return m[0][3:3 + 14] if m and len(m[0]) >= 17 else ""


def _issue(kind, label, target, files, detail, action=None, manual=""):
    return dict(kind=kind, label=label, target=target, count=len(files), files=[str(f) for f in files],
                detail=detail, action=action, manual=manual)


def _b_files(folder):
    """{B@@ .tdms path: size} directly in BNAL@<target>."""
    try:
        with os.scandir(long_path(folder)) as it:
            return {Path(folder) / e.name: e.stat().st_size for e in it
                    if e.is_file() and e.name.upper().startswith("B@@") and e.name.lower().endswith(".tdms")}
    except OSError:
        return {}


def anal_latest(t):
    """({key: newest A@@ name} of ANAL and ANAL/stocked, keys directly in ANAL) of one T folder."""
    latest, direct = {}, set()
    for d in (Path(t) / "ANAL", Path(t) / "ANAL" / "stocked"):
        for name, st in anal_status._files(d).items():
            if name.upper().startswith("A@@") and st.st_size:
                key = rack_store.measurement_key(name)
                latest[key] = max(latest.get(key, ""), name)
                if d.name == "ANAL":
                    direct.add(key)
    return latest, direct


def target_issues(t, target, anal_latest, direct):
    """Issues of one BNAL@<target>. anal_latest: {key: newest A@@ name} of ANAL and ANAL/stocked;
    direct: keys directly in ANAL (what the BNAL run analyses)."""
    folder = Path(t) / f"BNAL@{target}"
    files = _b_files(folder)
    out = []
    empty = [p for p, size in files.items() if size == 0]
    if empty:
        out.append(_issue("empty_b", "空の B@@（0 バイト）", target, empty,
                          "書き込みが途中で止まった B@@ です。削除します（.整理履歴 に保管し、元に戻せます）。", "empty_b"))
    groups = defaultdict(list)
    for p, size in files.items():
        if size:
            groups[rack_store.measurement_key(p.name)].append(p)
    old, stale, orphan, good = [], [], [], set()
    for key, paths in groups.items():
        paths.sort(key=lambda p: p.name)  # names start with the time they were made
        old += paths[:-1]
        newest = paths[-1]
        a = anal_latest.get(key)
        if a is None:
            orphan.append(newest)
        elif _stamp(newest.name) and _stamp(a) and _stamp(newest.name) < _stamp(a):
            stale.append(newest)
            good.add(key)  # the run counts it as made (server_bnal.pending): not pending
        else:
            good.add(key)
    if old:
        out.append(_issue("old_b", "同じ番号の古い B@@", target, old,
                          "同じ番号を解析し直した古い B@@ です。新しいものを残して削除します（元に戻せます）。", "old_b"))
    if stale:  # gathered into one line for the sample by survey()
        out.append(_issue("stale_b", "A@@ より前に作った B@@", target, stale, ""))
    if orphan:
        out.append(_issue("orphan_b", "ANAL に無い番号の B@@", target, orphan,
                          "元の A@@ が ANAL（stocked 含む）にありません（ANAL のやり直し・整理のあとなど）。old へ移します。",
                          "orphan_b"))
    todo = sorted(direct - good, key=str)
    if todo:
        out.append(_issue("pending", "未解析の A@@", target, [],
                          f"B@@ の無い A@@ {len(todo)} 件（{', '.join(map(str, todo[:8]))}{'…' if len(todo) > 8 else ''}）",
                          manual="④ の組み合わせに追加して解析します"))
        out[-1]["count"] = len(todo)
    # a folder whose B@@ all went to old is being made again, not a run that never started
    return out, bool(files) or os.path.isdir(long_path(folder / "old"))


def survey(t, settle_min=5.0):
    """Issues of one T folder: the sample's ANAL first (what BNAL needs), then every BNAL@<target>."""
    t = Path(t)
    anal = anal_status.survey(t, settle_min)
    issues = []
    for i in anal:
        if i["kind"] in ("renumber", "renumber_anal"):
            issues.append(dict(i, target=None, manual="ANAL の番号を付け直してから BNAL を解析してください（ANAL（サーバー））"))
        elif i["kind"] == "anal_stocked":
            issues.append(dict(i, target=None, detail="BNAL（サーバー）は ANAL 直下だけを解析するので、ANAL/stocked の A@@ は"
                                                         "解析されません。ANAL に無い番号を ANAL へ戻します。"))
        elif i["kind"] == "pending":
            issues.append(dict(i, kind="anal_pending", target=None, label="ANAL が未解析",
                               manual="先に ANAL（サーバー）で解析してください（BNAL はそのあと）"))
    latest, direct = anal_latest(t)
    try:
        with os.scandir(long_path(t)) as it:
            targets = sorted(e.name[len("BNAL@"):] for e in it if e.is_dir() and e.name.startswith("BNAL@"))
    except OSError:
        targets = []
    empty_folders, stale = [], {}
    for target in targets:
        found, has_files = target_issues(t, target, latest, direct)
        if not has_files:
            empty_folders.append(t / f"BNAL@{target}")
            continue
        stale.update({target: i for i in found if i["kind"] == "stale_b"})
        issues += [i for i in found if i["kind"] != "stale_b"]
    if stale:
        files = [f for i in stale.values() for f in i["files"]]
        issues.append(_issue("stale_b", "A@@ より前に作った B@@", None, files,
                             f"{len(stale)} target・{len(files)} 件の B@@ が、その番号の今の A@@ より前に作られています"
                             f"（A@@ が後から解析し直された: {', '.join(list(stale)[:3])}{'…' if len(stale) > 3 else ''}）。"
                             "同じ生データ・同じ条件の解析し直しなら B@@ はそのまま使えます。",
                             manual="作り直す場合は ④ で「再解析」を選んで解析します（情報）"))
    if empty_folders:
        issues.append(_issue("empty_folders", "B@@ の無い BNAL@ フォルダ", None, empty_folders,
                             f"{len(empty_folders)} フォルダ（{', '.join(p.name for p in empty_folders[:5])}"
                             f"{'…' if len(empty_folders) > 5 else ''}）", manual="解析する target なら ④ に追加します（情報）"))
    return issues


def blocking(issues):
    return [i for i in issues if i["kind"] in ("renumber", "renumber_anal")]


# ---------------------------------------------------------------------------
# carrying out an action (from a fresh survey of that target)
# ---------------------------------------------------------------------------
def _ops(folder, paths, parent):
    """Moves of B@@ .tdms (+ .tdms_index and the result folder) into parent."""
    ops = []
    for p in map(Path, paths):
        for q in (p, Path(str(p) + "_index")):
            if os.path.exists(long_path(q)):
                st = os.stat(long_path(q))
                ops.append(dict(source=str(q), target=str(parent / q.name), size=st.st_size,
                                modified_ns=st.st_mtime_ns, status="予定"))
        results = file_moves.result_folder(p)
        if results is not None:
            ops.append(dict(source=str(results), target=str(parent / results.name), size=0,
                            modified_ns=results.stat().st_mtime_ns, dir=True, status="予定"))
    return ops


def apply(t, target, kind, settle_min=5.0):
    """Carry out one action for one target (target None: the sample's ANAL/stocked); returns report lines."""
    t = Path(t)
    if kind not in ("restore_anal", "empty_b", "old_b", "orphan_b"):
        raise ValueError(f"この対処はできません: {kind}")
    if kind == "restore_anal":
        return anal_status.apply(t, "restore_anal", settle_min)
    issue = next((i for i in target_issues(t, target, *anal_latest(t))[0] if i["action"] == kind), None)
    if issue is None:
        return [f"BNAL@{target}: 対処することはありません（調べ直した結果）"]
    folder = t / f"BNAL@{target}"
    if kind in ("empty_b", "old_b"):
        parent = folder / HISTORY / f"{datetime.now():%Y%m%d_%H%M%S}_{kind}_{uuid.uuid4().hex[:8]}"
        what = "削除（.整理履歴 に保管）"
    elif kind == "orphan_b":
        parent, what = folder / "old", "old へ移動"
    else:
        raise ValueError(f"この対処はできません: {kind}")
    ops = [o for o in _ops(folder, issue["files"], parent) if not os.path.lexists(long_path(o["target"]))]
    if not ops:
        return [f"BNAL@{target}: 移せるものがありません（移動先に同名あり）"]
    plan = dict(folder=str(folder), action="delete" if parent.parent.name == HISTORY else "old",
                suffixes=(".tdms", ".tdms_index"), verify_content=False, operations=ops)
    journal = file_moves.execute_moves(plan, BNAL_LOGS)
    return [f"BNAL@{target}: {issue['label']} {issue['count']} 件を{what}（記録 {journal}）"]


def sequence_of(t, target):
    """Sample Sequence of a target from the BC Table of one of its B@@ (None when there is none to read)."""
    from nptdms import TdmsFile
    for p, size in sorted(_b_files(Path(t) / f"BNAL@{target}").items()):
        if not size:
            continue
        try:
            with TdmsFile.open(long_path(p)) as f:
                return str(f["BC Table"]["Sample Sequence"][0])
        except Exception:
            continue
    return None
