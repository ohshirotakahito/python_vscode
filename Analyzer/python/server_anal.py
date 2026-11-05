"""1st Step (ANAL) on the analysis servers.

Samples and their ANAL progress come from the saved inventory of the experiment file database
(database/inventory.sqlite3 = shared.catalog.DEFAULT_DB, table rack_samples), read-only: listing never touches
the servers. Only the samples chosen for a run are listed on the server, right before the analysis, so a stale
inventory can not cause double analysis.

Layout:  <root>/<experiment>/<sample>/<sample>_10k_Sample/T/*.tdms   ->   .../T/ANAL/A@@<time>_<sample>#NNN.tdms
ANAL keeps the original '#NNN' number; ANAL/stocked holds analysed files moved aside for BNAL, so they count
as analysed.
"""
import csv
import os
import re
import socket
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from first_step import long_path

# Python_VScode のリポジトリ直下（shared/ を読むため）。DB・移動の記録・測定番号の規則は shared が持つ。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from shared import catalog, file_moves, progress, rack_store, t_stock  # noqa: E402

DEFAULT_DB = catalog.DEFAULT_DB
LOG_CSV = Path(__file__).resolve().parent.parent / "logs" / "anal_runs.csv"

# the measurement of a T / ANAL / BNAL file is read exactly as the database / file manager do
# (shared.rack_store.measurement_key), so the progress here and there always agrees
SAMPLE_NUMBER, NUMBER = rack_store.SAMPLE_NUMBER, rack_store.NUMBER

TODO, RUNNING, DONE, COPY, EMPTY = "未着手", "進行中", "完了", "解析用コピー", "対象なし"
EXCESS, UNKNOWN = "超過", "確認不可"  # ANAL が元より多い（要確認）/ T が未調査（DB の更新が必要）
# shared.progress の状態 -> この画面の表示（unknown は T 未調査なら確認不可、T も ANAL も 0 なら対象なし）
STATES = dict(unstarted=TODO, partial=RUNNING, complete=DONE, excess=EXCESS, copy=COPY)
# 自動着手で外す注意（先に片付けが必要なもの＋再開地点の未取得）。差異・コピー不足は表示だけ
AUTO_BLOCKING = (*progress.ANAL_BLOCKING, "要更新")


def identity(root):
    return catalog.root_identity(root)


def load_samples(db_path=DEFAULT_DB):
    """Saved samples of every share with status, progress and warnings (no server access)."""
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        roots = {identity(r["root"]): dict(r) for r in db.execute("SELECT * FROM rack_roots")}
        rows = [dict(r) for r in db.execute("SELECT * FROM rack_samples ORDER BY root, experiment, sample")]
    finally:
        db.close()
    by_sample = defaultdict(list)
    for r in rows:
        info = roots.get(identity(r["root"]), {})
        share = re.split(r"[\\/]", identity(r["root"]))[-1]
        r["group"] = f"{info.get('server') or r['root']}/{share}"
        r["reachable"] = not info.get("error")
        # the same judgment as the database's ANAL進捗 and the file manager (shared/progress.py)
        cell = progress.anal_share(r)
        r["total"], r["analysed"] = cell["total"], cell["analysed"]
        r["state"] = STATES.get(cell["status"]) or (UNKNOWN if cell["total"] is None else EMPTY)
        r["progress"] = 100.0 * (cell["analysed"] or 0) / cell["total"] if cell["total"] else None
        r["flags"] = progress.share_flags(cell)
        by_sample[(r["experiment"], r["sample"])].append((r, cell))
    for shares in by_sample.values():
        # over every share holding the sample: 差異 (T counts differ) on each of its rows,
        # コピー不足 only on the analysis copy that holds fewer ANAL than the sample's best share
        _, sample_flags = progress.assess_sample([cell for _, cell in shares])
        for r, cell in shares:
            if "差異" in sample_flags:
                r["flags"].append("差異")
            if cell.get("coverage") is not None and cell["coverage"] < 1:
                r["flags"].append("コピー不足")
    return rows


def auto_pick(r):
    """Automatic selection: unfinished, reachable, nothing to tidy first (差異・コピー不足 are shown only)."""
    return r["state"] in (TODO, RUNNING) and r["reachable"] and not blocking(r)


def blocking(r):
    return [f for f in r["flags"] if f in AUTO_BLOCKING]


def t_dir(r):
    return Path(r["root"]) / r["experiment"] / r["sample"] / f"{r['sample']}_10k_Sample" / "T"


def original_key(name):
    """Measurement of a T file: '#NNN' (int), or '<first word>' such as 'ABex1T01#001' (str)."""
    return rack_store.measurement_key(name)


def anal_key(name):
    """Measurement of an A@@ / B@@ file, the same key as its original in T."""
    return rack_store.measurement_key(name)


def _tdms(folder):
    try:
        with os.scandir(long_path(folder)) as it:
            return [e for e in it if e.is_file() and e.name.lower().endswith(".tdms")]
    except FileNotFoundError:
        return []


def analysed_keys(t):
    """'#NNN' numbers already in T/ANAL and T/ANAL/stocked."""
    return {anal_key(e.name) for d in (Path(t) / "ANAL", Path(t) / "ANAL" / "stocked") for e in _tdms(d)}


def pending_files(t, settle_min=5.0):
    """(files to analyse, files still being written) directly under one T folder, listed on the server now.

    T/stocked holds set-aside originals: they count in the progress (as in the database) but are not analysed."""
    t = Path(t)
    if not os.path.isdir(long_path(t)):
        raise FileNotFoundError(f"T フォルダが見つかりません: {t}")
    done = analysed_keys(t)
    todo, busy = [], []
    now = time.time()
    for e in _tdms(t):
        # A@@/B@@ copied into T are analysis results, not measurements
        if e.name.upper().startswith(("A@@", "B@@")) or original_key(e.name) in done:
            continue
        (busy if now - e.stat().st_mtime < settle_min * 60 else todo).append(t / e.name)
    key = lambda p: (isinstance(original_key(p.name), str), str(original_key(p.name)).zfill(6))
    return sorted(todo, key=key), sorted(busy, key=key)


def refresh_db(db_path, r):
    """Re-count one sample on the server and save it to the inventory, with the database app's own
    refresh_sample (the same counts as a full Analysis_server scan, for that sample only)."""
    return rack_store.refresh_sample(db_path, r["root"], r["experiment"], r["sample"])


def log_run(path=None, **row):
    """Append one analysed file to logs/anal_runs.csv (or `path`); columns are the keyword names
    (plus who ran it). A file with other columns is kept as *_<time>.csv and a new one is started."""
    import first_step_io
    path = Path(path or LOG_CSV)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {**row, "operator": first_step_io.OPERATOR, "pc": socket.gethostname()}
    fields = ["time", *row]
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:
            header = f.readline().strip().split(",")
        if header != fields:
            path.rename(path.with_name(f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}.csv"))
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fields)
        if new:
            w.writeheader()
        w.writerow({"time": datetime.now().isoformat(timespec="seconds"), **row})


# ---------------------------------------------------------------------------
# T/stocked -> T (with the file manager app's planner and journal, so moves show in its history)
# ---------------------------------------------------------------------------
T_STOCK_LOGS = file_moves.journal_dir("T_stock")


def restore_plan(r):
    """(plan, skipped [(name, reason)], description) for moving every T/stocked file of one sample back to T."""
    plan, skipped = t_stock.plan_moves(t_stock.scan_t(t_dir(r)), restore=True)
    return plan, skipped, t_stock.describe(plan)


def execute_restore(plan, log_dir=T_STOCK_LOGS):
    """Move the files; the journal is written to the file manager's t_stock_logs (undo from its history)."""
    return file_moves.execute_moves(plan, log_dir)


# ANAL/stocked -> ANAL (journal in the file manager's anal_logs, as its ANAL tab writes)
ANAL_LOGS = file_moves.journal_dir("ANAL")


def anal_restore_plan(r):
    """(plan, skipped [(name, reason)], description) for moving the ANAL/stocked A@@ files of one sample back to ANAL.
    A file stays when ANAL already has its name or its '#NNN' number; of one number in stocked only the newest
    moves. The .tdms_index next to a moved file goes with it."""
    folder = t_dir(r) / "ANAL"
    files = file_moves.scan_anal(folder)
    in_anal = [f for f in files if f["location"] != "stocked"]
    names, numbers = {Path(f["path"]).name for f in in_anal}, {f["number"] for f in in_anal}
    take, skipped, newest = [], [], {}
    for f in sorted((f for f in files if f["location"] == "stocked"), key=lambda f: Path(f["path"]).name):
        if f["number"] is not None:
            newest[f["number"]] = f  # names start with the analysis time, so the last one is the newest
    for f in (f for f in files if f["location"] == "stocked"):
        name = Path(f["path"]).name
        if name in names:
            skipped.append((name, "ANAL に同名あり"))
        elif f["number"] is not None and f["number"] in numbers:
            skipped.append((name, f"#{f['number']:03d} は ANAL にある"))
        elif f["number"] is not None and newest[f["number"]] is not f:
            skipped.append((name, f"#{f['number']:03d} は新しい方だけ戻す"))
        else:
            take.append(f)
    if not take:
        return {"folder": str(folder), "operations": []}, skipped, ""
    plan = file_moves.move_plan(folder, take, restore=True)
    for op in list(plan["operations"]):
        src, dst = Path(op["source"] + "_index"), Path(op["target"] + "_index")
        if src.is_file() and not os.path.lexists(dst):
            st = src.stat()
            plan["operations"].append(dict(source=str(src), target=str(dst), size=st.st_size,
                                           modified_ns=st.st_mtime_ns, status="予定"))
    plan.update(suffixes=[".tdms", ".tdms_index"], action="extract")
    text = f"{r['experiment']} / {r['sample']}: ANAL/stocked → ANAL {len(take)} 件\n" + \
        "\n".join(f"  {Path(f['path']).name}" for f in take)
    return plan, skipped, text


# ---------------------------------------------------------------------------
# automatic start (全自動着手): duplicates, state for resuming, report
# ---------------------------------------------------------------------------
AUTO_DIR = Path(__file__).resolve().parent.parent / "logs" / "anal_auto"
AUTO_STATE = AUTO_DIR / "state.json"


def duplicate_anal(t):
    """Older A@@ files of '#NNN' numbers that appear more than once directly in T/ANAL (the timestamp in the name
    decides; the newest stays). Returns [(older path, kept path)]."""
    by_key = {}
    for e in sorted(_tdms(Path(t) / "ANAL"), key=lambda e: e.name):
        if e.name.startswith("A@@"):
            by_key.setdefault(anal_key(e.name), []).append(Path(t) / "ANAL" / e.name)
    return [(old, paths[-1]) for paths in by_key.values() if len(paths) > 1 for old in paths[:-1]]


def delete_anal(path):
    """Delete one A@@ .tdms and its .tdms_index (result folders A@...@ are left as they are)."""
    removed = []
    for p in (Path(path), Path(str(path) + "_index")):
        if os.path.exists(long_path(p)):
            os.remove(long_path(p))
            removed.append(p.name)
    return removed


def save_state(state):
    import json
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AUTO_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    for attempt in range(20):  # Windows refuses the replace while another thread is reading the file
        try:
            os.replace(tmp, AUTO_STATE)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


def load_state():
    import json
    try:
        return json.loads(AUTO_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear_state():
    if AUTO_STATE.exists():
        AUTO_STATE.unlink()


class Report:
    """Plain-text report of one automatic run, written line by line (an interrupted run keeps what was done)."""

    def __init__(self, path=None):
        AUTO_DIR.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else AUTO_DIR / f"自動着手_{datetime.now():%Y%m%d_%H%M%S}.txt"

    def write(self, text=""):
        with open(self.path, "a", encoding="utf-8-sig" if not self.path.exists() else "utf-8") as f:
            f.write(text + "\n")


# ---------------------------------------------------------------------------
# broken raw files -> T/broken (outside T and T/stocked, so the sample can reach 100 %)
# ---------------------------------------------------------------------------
def raw_problem(path):
    """Why a raw TDMS cannot be analysed (None if its segment headers read fine), e.g. a copy cut short
    and padded with zeros ('Segment does not start with TDSm')."""
    from nptdms import TdmsFile
    try:
        meta = TdmsFile.read_metadata(long_path(path))
        if not any(len(g.channels()) for g in meta.groups()):
            return "チャンネルがありません"
    except Exception as e:
        return str(e) or type(e).__name__
    return None


def move_broken(t, paths):
    """Move broken raw files (and their .tdms_index) from T to T/broken (journaled; undo from the file manager)."""
    return move_aside(t, paths, "broken")


# ---------------------------------------------------------------------------
# suspicious T contents and who made the ANAL files
# ---------------------------------------------------------------------------
EX_ID = re.compile(r" D_(\d{8}_\d{4}) ([^_ ]+)")


def t_anomalies(t):
    """Raw files of T and T/stocked: '#NNN' numbers used by more than one file, and the experiment IDs
    ('D_<date>_<time> <AN id>' in the name) present. A second experiment ID can be a file from another run."""
    t = Path(t)
    names = [e.name for e in _tdms(t)] + [e.name for e in _tdms(t / "stocked")]
    by_number = {}
    for n in names:
        k = original_key(n)
        if isinstance(k, int):
            by_number.setdefault(k, []).append(n)
    ids = {}
    for n in names:
        m = EX_ID.search(n)
        key = f"D_{m[1]} {m[2]}" if m else "（実験IDなし）"
        ids.setdefault(key, []).append(n)
    return {"duplicates": {k: v for k, v in by_number.items() if len(v) > 1}, "ex_ids": ids}


def anomaly_text(a):
    """'' when nothing is suspicious."""
    parts = []
    if a["duplicates"]:
        parts.append("T番号重複 " + ", ".join(f"#{k:03d}×{len(v)}" for k, v in sorted(a["duplicates"].items())))
    if len(a["ex_ids"]) > 1:
        ids = sorted(a["ex_ids"].items(), key=lambda kv: -len(kv[1]))
        parts.append(f"実験ID {len(ids)}種（" + "、".join(f"{k} {len(v)}件" for k, v in ids[:4])
                     + ("…" if len(ids) > 4 else "") + "）")
    return " / ".join(parts)


def anal_operators(t):
    """{who: count} of the A@@ files in T/ANAL: the Python operator from the file properties,
    otherwise 'LabVIEW など（記録なし）'."""
    from nptdms import TdmsFile
    out = {}
    for e in _tdms(Path(t) / "ANAL"):
        if not e.name.startswith("A@@"):
            continue
        try:
            props = TdmsFile.read_metadata(long_path(Path(t) / "ANAL" / e.name)).properties
            who = props.get("ANAL Operator") or "LabVIEW など（記録なし）"
        except Exception:
            who = "読めないファイル"
        out[who] = out.get(who, 0) + 1
    return out


# ---------------------------------------------------------------------------
# duplicated raw numbers in T: identical copies keep one, otherwise the newest stays (others -> T/duplicate)
# ---------------------------------------------------------------------------
MEASURED = re.compile(r"_(\d{8}_\d{4})_SGM")  # measurement time in the raw file name


def raw_time(path):
    """Sort key for 'older': the measurement time in the name, else the D_ time, else the file time."""
    name = Path(path).name
    m = MEASURED.search(name) or EX_ID.search(name)
    return (m[1] if m else "", os.stat(long_path(path)).st_mtime)


def _same_content(a, b):
    import hashlib
    if os.path.getsize(long_path(a)) != os.path.getsize(long_path(b)):
        return False
    digest = lambda p: hashlib.sha256(open(long_path(p), "rb").read()).hexdigest()
    return digest(a) == digest(b)


def raw_duplicates(t):
    """[(path to move, kept path, reason)] for raw '#NNN' numbers used by more than one file directly in T."""
    t = Path(t)
    groups = {}
    for e in _tdms(t):
        k = original_key(e.name)
        if isinstance(k, int):
            groups.setdefault(k, []).append(t / e.name)
    out = []
    for paths in groups.values():
        if len(paths) < 2:
            continue
        paths.sort(key=raw_time)
        kept = paths[-1]
        for p in paths[:-1]:
            out.append((p, kept, "同じ内容" if _same_content(p, kept) else "古い方"))
    return out


def anal_source(path):
    """Raw file name an A@@ was made from (AR Table 'Filename', written by LabVIEW and Python), or None."""
    from nptdms import TdmsFile
    try:
        with TdmsFile.open(long_path(path)) as f:
            return str(f["AR Table"]["Filename"][0])
    except Exception:
        return None


def measured(name):
    """Measurement time in a raw file name ('' when absent), for 'newer measurement'."""
    m = MEASURED.search(name or "") or EX_ID.search(name or "")
    return m[1] if m else ""


def duplicate_plan(t):
    """What 重複の整理 does in one T, without changing anything:
    raw   [(path, kept path, '同じ内容' | '古い方')]   raw files to move to T/duplicate (raw_duplicates)
    anal  [(path, reason, 'delete' | 'aside')]       A@@ files to delete, or to move to ANAL/duplicate
    redo  [number]                                   numbers left without ANAL (analysed again from the kept raw)
    The raw file of each A@@ comes from its AR Table 'Filename'.
    - Re-analyses of the same raw file: the newest A@@ stays, the older ones are deleted.
    - A@@ of different measurements with one '#NNN' (experiment duplicate): the one from the kept raw file
      stays (when T has the raw files), else the newest measurement; the others move to ANAL/duplicate."""
    t = Path(t)
    raw = raw_duplicates(t)
    kept_raw = {original_key(p.name): kept.name for p, kept, why in raw if why == "古い方"}
    by_key = {}
    for e in sorted(_tdms(t / "ANAL"), key=lambda e: e.name):
        if e.name.startswith("A@@"):
            by_key.setdefault(anal_key(e.name), []).append(t / "ANAL" / e.name)
    anal, redo = [], []
    for key, paths in sorted(by_key.items(), key=lambda kv: str(kv[0])):
        if len(paths) < 2 and key not in kept_raw:
            continue
        src = {p: anal_source(p) for p in paths}
        if key in kept_raw:
            own = [p for p in paths if src[p] == kept_raw[key]]
            keep = own[-1] if own else None
            if keep is None:
                redo.append(key)
        else:  # newest measurement, then newest analysis (names start with the analysis time)
            keep = max(paths, key=lambda p: (measured(src[p]), p.name))
        for p in paths:
            if p == keep:
                continue
            if src[p] and keep is not None and src[p] == src[keep]:
                anal.append((p, f"同じ生データの古い ANAL（残すもの {keep.name}）", "delete"))
            else:
                anal.append((p, f"別の測定の ANAL（{src[p] or '元の生データ不明'}）", "aside"))
    return {"raw": raw, "anal": anal, "redo": redo}


def apply_duplicate_plan(t, plan):
    """Carry out duplicate_plan; returns report lines."""
    lines = []
    if plan["raw"]:
        journal, moved, skipped = move_aside(t, [p for p, _, _ in plan["raw"]], "duplicate")
        for p, kept, why in plan["raw"]:
            lines.append(f"T の番号重複（{why}）: {p.name} を T/duplicate へ移動（残したもの {kept.name}）"
                         if p.name in moved else f"T の番号重複（{why}）: {p.name} は移動できません")
        if journal:
            lines.append(f"  移動の記録 {journal}（ファイル管理アプリの履歴から元に戻せます）")
    for p, why, action in plan["anal"]:
        if action == "delete":
            removed = delete_anal(p)
            if removed:
                lines.append(f"ANAL を削除（{why}）: {', '.join(removed)}")
    aside = [p for p, _, action in plan["anal"] if action == "aside"]
    if aside:
        journal, moved, skipped = move_aside(Path(t) / "ANAL", aside, "duplicate")
        for p, why, action in plan["anal"]:
            if action == "aside":
                lines.append(f"ANAL を ANAL/duplicate へ移動（{why}）: {p.name}" if p.name in moved
                             else f"ANAL {p.name} は移動できません（ANAL/duplicate に同名あり）")
        if journal:
            lines.append(f"  移動の記録 {journal}（ファイル管理アプリの履歴から元に戻せます）")
    if plan["redo"]:
        lines.append("再解析が必要な番号: " + ", ".join(f"#{k:03d}" for k in plan["redo"]))
    return lines


def move_aside(t, paths, folder):
    """Move raw files (+ .tdms_index) from T to T/<folder>, journaled like the T/stocked moves (undo from the
    file manager's history). Returns (journal path, moved names, skipped [(name, reason)])."""
    t = Path(t)
    ops, moved, skipped = [], [], []
    for p in map(Path, paths):
        group = [q for q in (p, Path(str(p) + "_index")) if os.path.exists(long_path(q))]
        if any(os.path.exists(long_path(t / folder / q.name)) for q in group):
            skipped.append((p.name, f"T/{folder} に同名あり"))
            continue
        for q in group:
            st = os.stat(long_path(q))
            ops.append(dict(source=str(q), target=str(t / folder / q.name), size=st.st_size,
                            modified_ns=st.st_mtime_ns, status="予定"))
        moved.append(p.name)
    if not ops:
        return None, moved, skipped
    plan = dict(folder=str(t), action=folder, suffixes=(".tdms", ".tdms_index"), verify_content=False, operations=ops)
    return file_moves.execute_moves(plan, T_STOCK_LOGS), moved, skipped
