"""Current state of one sample's ANAL on the server: what is not tidy, and what can be done about it.

survey(t) reads T, T/stocked, T/ANAL and T/ANAL/stocked (it changes nothing) and returns the issues found.
Each issue says whether it can be put right here (action) or needs work by hand (manual):

    action          what it does (journaled moves; undo from the file manager's history)
    results_in_t    A@@ files sitting in T  -> T/ANAL                       (when ANAL has neither the name nor the #)
    empty_raw       0-byte raw files in T  -> T/broken                      (a copy cut short)
    restore_t       T/stocked              -> T
    restore_anal    ANAL/stocked           -> ANAL                          (numbers ANAL does not have)
    raw_copy        identical copies of one raw file: one stays, the others -> T/duplicate
    old_anal        older analyses of the same raw file, and 0-byte A@@ -> ANAL/.整理履歴 (deleted, restorable)

    manual
    renumber        one '#NNN' used by different measurements (raw files, or the raw files behind the A@@):
                    give them new numbers (file manager: rename), then analyse the sample again (redo)
    results_b       B@@ files in T (belong in T/BNAL@<target>)
    no_raw          A@@ whose raw file is not in T (an analysis copy, or raw files moved elsewhere)  (info)
    pending         raw files in T without A@@ (run the analysis)                                    (info)

redo_plan(t) is the sample-level fresh start after renumbering: every A@@ of ANAL and ANAL/stocked goes to
ANAL/.整理履歴 (restorable) so that the whole sample is analysed again.
"""
import os
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import server_anal
from first_step import long_path
from shared import file_moves, rack_store

ANAL_LOGS = server_anal.ANAL_LOGS
T_LOGS = server_anal.T_STOCK_LOGS
RESULT = ("A@@", "B@@")


def _files(folder):
    """{name: os.stat} of the direct .tdms files of a folder ({} when it does not exist)."""
    try:
        with os.scandir(long_path(folder)) as it:
            return {e.name: e.stat() for e in it if e.is_file() and e.name.lower().endswith(".tdms")}
    except OSError:
        return {}


def _issue(kind, label, files, detail, action=None, manual=""):
    return dict(kind=kind, label=label, count=len(files), files=[str(f) for f in files], detail=detail,
                action=action, manual=manual)


def _same_file(a, b):
    if os.path.getsize(long_path(a)) != os.path.getsize(long_path(b)):
        return False
    return file_moves.checksum(long_path(a)) == file_moves.checksum(long_path(b))


def survey(t, settle_min=5.0, sources=None):
    """Issues of one T folder (list of dicts, see the module docstring). sources: {A@@ path: raw name} cache."""
    t = Path(t)
    if not os.path.isdir(long_path(t)):
        raise FileNotFoundError(f"T フォルダが見つかりません: {t}")
    issues = []
    in_t, in_ts = _files(t), _files(t / "stocked")
    anal, anal_st = _files(t / "ANAL"), _files(t / "ANAL" / "stocked")

    # results in T
    a_in_t = [t / n for n in in_t if n.upper().startswith("A@@")]
    b_in_t = [t / n for n in in_t if n.upper().startswith("B@@")]
    if a_in_t:
        issues.append(_issue("results_in_t", "T に A@@ がある", a_in_t,
                             "解析結果が T 直下にあります（T の件数・進捗が多く見えます）。ANAL へ移します。", "results_in_t"))
    if b_in_t:
        issues.append(_issue("results_b", "T に B@@ がある", b_in_t, "BNAL の結果が T 直下にあります。",
                             manual="該当する T\\BNAL@<target> へ手で移してください"))

    raw = {t / n: st for n, st in in_t.items() if not n.upper().startswith(RESULT)}
    raw_st = {t / "stocked" / n: st for n, st in in_ts.items() if not n.upper().startswith(RESULT)}
    empty = [p for p, st in raw.items() if st.st_size == 0]
    if empty:
        issues.append(_issue("empty_raw", "空の生データ（0 バイト）", empty,
                             "コピーが途中で止まった残りです。T/broken へ移します（T の件数から外れます）。", "empty_raw"))
    empty_st = [p for p, st in raw_st.items() if st.st_size == 0]
    if empty_st:
        issues.append(_issue("empty_raw_stocked", "空の生データ（T/stocked、0 バイト）", empty_st,
                             "T/stocked に 0 バイトの生データがあります。", manual="内容を確かめてから手で片付けてください"))
    if raw_st:
        issues.append(_issue("t_stocked", "T/stocked に退避中", list(raw_st),
                             "解析済みとして T/stocked に退避した生データです。T に戻します（同名があるものは戻しません）。",
                             "restore_t"))

    # raw numbers used by more than one file (T and T/stocked)
    by_key = defaultdict(list)
    for p, st in {**raw, **raw_st}.items():
        if st.st_size:
            by_key[rack_store.measurement_key(p.name)].append(p)
    copies, renumber_raw = [], []
    for key, paths in by_key.items():
        if len(paths) < 2:
            continue
        paths.sort(key=lambda p: (p.parent != t, p.name))  # a copy directly in T stays
        keep = paths[0]
        for p in paths[1:]:
            (copies if _same_file(p, keep) else renumber_raw).append((p, keep, key))
    if copies:
        issues.append(_issue("raw_copy", "同じ生データのコピー", [p for p, _, _ in copies],
                             "内容が同じ生データが複数あります。1 つ残して T/duplicate へ移します。", "raw_copy"))
    if renumber_raw:
        keys = sorted({str(k) for *_, k in renumber_raw})
        issues.append(_issue("renumber", "同じ #番号の別の測定（生データ）",
                             sorted({p for p, _, _ in renumber_raw} | {k for _, k, _ in renumber_raw}),
                             f"内容の違う生データが同じ番号を使っています: {', '.join(keys[:10])}",
                             manual="ファイル管理のリネームで番号を付け直し、そのあと「ANAL をやり直す」で解析し直してください"))

    # ANAL: 0-byte files, then numbers with more than one A@@ (ANAL and ANAL/stocked)
    a_all = {**{t / "ANAL" / n: st for n, st in anal.items() if n.upper().startswith("A@@")},
             **{t / "ANAL" / "stocked" / n: st for n, st in anal_st.items() if n.upper().startswith("A@@")}}
    zero = [p for p, st in a_all.items() if st.st_size == 0]
    groups = defaultdict(list)
    for p, st in a_all.items():
        if st.st_size:
            groups[rack_store.measurement_key(p.name)].append(p)
    sources = {} if sources is None else sources
    old, renumber_anal = list(zero), []
    for key, paths in groups.items():
        if len(paths) < 2:
            continue
        for p in paths:
            if str(p) not in sources:
                sources[str(p)] = server_anal.anal_source(p)
        src = {sources[str(p)] for p in paths}
        if len(src) == 1 and None not in src:
            paths.sort(key=lambda p: Path(p).name)  # names start with the analysis time
            old += paths[:-1]
        else:
            renumber_anal.append(key)
    if old:
        issues.append(_issue("old_anal", "古い解析・空の A@@", old,
                             "同じ生データを解析し直した古い A@@ と、書き込みが途中で止まった 0 バイトの A@@ です。"
                             "新しいものを残して削除します（ANAL/.整理履歴 に保管し、元に戻せます）。", "old_anal"))
    if renumber_anal:
        keys = sorted(map(str, renumber_anal))
        issues.append(_issue("renumber_anal", "同じ #番号の別の測定（ANAL）",
                             [p for k in renumber_anal for p in groups[k]],
                             f"同じ番号の A@@ が別の生データ（または読めない生データ）から作られています: {', '.join(keys[:10])}",
                             manual="生データの番号を付け直し、そのあと「ANAL をやり直す」で解析し直してください"))

    # ANAL/stocked whose number ANAL does not have
    in_anal = {rack_store.measurement_key(n) for n in anal if n.upper().startswith("A@@")}
    back = [t / "ANAL" / "stocked" / n for n, st in anal_st.items()
            if n.upper().startswith("A@@") and st.st_size and rack_store.measurement_key(n) not in in_anal]
    if back:
        issues.append(_issue("anal_stocked", "ANAL/stocked に退避中", back,
                             "BNAL のために退避した A@@ です。ANAL に無い番号を ANAL へ戻します。", "restore_anal"))

    # A@@ whose raw file is not in T (compared per side as the database does)
    t_num = rack_store.as_numbers(rack_store.measurement_key(p.name) for p in {**raw, **raw_st})
    a_num = rack_store.as_numbers(rack_store.measurement_key(p.name) for p in a_all)
    orphans = sorted({a_num[rack_store.measurement_key(p.name)] for p in a_all} - set(t_num.values()), key=str)
    if orphans:
        issues.append(_issue("no_raw", "元の生データが T に無い A@@", [],
                             f"{len(orphans)} 番号（{', '.join(map(str, orphans[:8]))}{'…' if len(orphans) > 8 else ''}）。"
                             + ("T に生データが無いので解析用コピーです。"
                                if not any(st.st_size for st in {**raw, **raw_st}.values()) else
                                "生データが別の場所へ移された可能性があります。"),
                             manual="生データの場所を確かめてください（情報）"))
        issues[-1]["count"] = len(orphans)

    todo, busy = server_anal.pending_files(t, settle_min)
    if todo or busy:
        issues.append(_issue("pending", "未解析の生データ", todo,
                             f"A@@ の無い生データ {len(todo)} 件" + (f"、書き込み中 {len(busy)} 件" if busy else "")
                             + ("（同じ番号の別の測定があるので、番号を付け直すまで解析しないでください）"
                                if renumber_raw or renumber_anal else ""),
                             manual="「解析を実行」で解析します"))
    return issues


def blocking(issues):
    """Issues that must be put right by hand before the sample is analysed (a run would mix measurements)."""
    return [i for i in issues if i["kind"] in ("renumber", "renumber_anal")]


# ---------------------------------------------------------------------------
# carrying out an action (always from a fresh survey; every move is journaled)
# ---------------------------------------------------------------------------
def _ops(paths, target_of):
    """Move operations for .tdms files and their .tdms_index."""
    ops = []
    for p in map(Path, paths):
        for q in (p, Path(str(p) + "_index")):
            if os.path.exists(long_path(q)):
                st = os.stat(long_path(q))
                ops.append(dict(source=str(q), target=str(target_of(q)), size=st.st_size,
                                modified_ns=st.st_mtime_ns, status="予定"))
    return ops


def _execute(folder, ops, action, log_dir, verify=True):
    if not ops:
        return None
    plan = dict(folder=str(folder), action=action, suffixes=(".tdms", ".tdms_index"), verify_content=verify,
                operations=ops)
    return file_moves.execute_moves(plan, log_dir)


def _trash(t, paths, action):
    """Delete A@@ (restorable): ANAL/.整理履歴/<action>/<location>/<name>."""
    anal = Path(t) / "ANAL"
    batch = f"{datetime.now():%Y%m%d_%H%M%S}_{action}_{uuid.uuid4().hex[:8]}"
    where = lambda q: anal / ".整理履歴" / batch / ("stocked" if q.parent.name == "stocked" else "ANAL") / q.name
    return _execute(anal, _ops(paths, where), "delete", ANAL_LOGS, verify=False)


def apply(t, kind, settle_min=5.0):
    """Carry out one action on one T folder from a fresh survey; returns report lines."""
    t = Path(t)
    issue = next((i for i in survey(t, settle_min) if i["action"] == kind), None)
    if issue is None:
        return ["対処することはありません（調べ直した結果）"]
    paths = [Path(p) for p in issue["files"]]
    lines = []
    if kind == "results_in_t":
        names = set(_files(t / "ANAL"))
        numbers = {rack_store.measurement_key(n) for n in names}
        move, keep = [], []
        for p in paths:
            (keep if p.name in names or rack_store.measurement_key(p.name) in numbers else move).append(p)
        journal = _execute(t, _ops(move, lambda q: t / "ANAL" / q.name), "results_to_anal", ANAL_LOGS, verify=False)
        lines.append(f"T の A@@ → ANAL: {len(move)} 件" + (f"（記録 {journal}）" if journal else ""))
        lines += [f"  移さなかったもの（ANAL に同名・同じ番号あり）: {p.name}" for p in keep]
    elif kind == "empty_raw":
        journal, moved, skipped = server_anal.move_aside(t, paths, "broken")
        lines.append(f"空の生データ → T/broken: {len(moved)} 件" + (f"（記録 {journal}）" if journal else ""))
        lines += [f"  移さなかったもの: {n}（{why}）" for n, why in skipped]
    elif kind == "restore_t":
        plan, skipped, _ = server_anal.restore_plan(_row(t))
        journal = server_anal.execute_restore(plan) if plan["operations"] else None
        lines.append(f"T/stocked → T: {len({o['source'].rsplit('.', 1)[0] for o in plan['operations']})} 件"
                     + (f"（記録 {journal}）" if journal else ""))
        lines += [f"  T/stocked に残したもの: {n}（{why}）" for n, why in skipped]
    elif kind == "restore_anal":
        plan, skipped, _ = server_anal.anal_restore_plan(_row(t))
        journal = file_moves.execute_moves(plan, ANAL_LOGS) if plan["operations"] else None
        lines.append(f"ANAL/stocked → ANAL: {sum(o['source'].endswith('.tdms') for o in plan['operations'])} 件"
                     + (f"（記録 {journal}）" if journal else ""))
        lines += [f"  ANAL/stocked に残したもの: {n}（{why}）" for n, why in skipped]
    elif kind == "raw_copy":
        journal, moved, skipped = server_anal.move_aside(t, paths, "duplicate")
        lines.append(f"同じ生データのコピー → T/duplicate: {len(moved)} 件" + (f"（記録 {journal}）" if journal else ""))
        lines += [f"  移さなかったもの: {n}（{why}）" for n, why in skipped]
    elif kind == "old_anal":
        journal = _trash(t, paths, "old_anal")
        lines.append(f"古い解析・空の A@@ を削除（ANAL/.整理履歴 に保管）: {len(paths)} 件" + (f"（記録 {journal}）" if journal else ""))
    else:
        raise ValueError(f"この対処はできません: {kind}")
    return lines


def _row(t):
    """The row fields server_anal.t_dir needs, from a T path (<root>/<experiment>/<sample>/<sample>_10k_Sample/T)."""
    t = Path(t)
    return {"root": str(t.parents[3]), "experiment": t.parents[2].name, "sample": t.parents[1].name}


def redo_files(t):
    """Every A@@ of ANAL and ANAL/stocked (what 'ANAL をやり直す' sets aside)."""
    t = Path(t)
    return [d / n for d in (t / "ANAL", t / "ANAL" / "stocked") for n in _files(d) if n.upper().startswith("A@@")]


def redo(t):
    """Set every A@@ aside (ANAL/.整理履歴, restorable) so the whole sample is analysed again."""
    paths = redo_files(t)
    if not paths:
        return ["ANAL に A@@ はありません"]
    journal = _trash(t, paths, "redo")
    return [f"ANAL をやり直すため A@@ {len(paths)} 件を ANAL/.整理履歴 へ移しました（記録 {journal}、元に戻せます）"]
