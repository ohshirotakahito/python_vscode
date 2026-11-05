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
# automatic start (全自動着手): state for resuming, report
# ---------------------------------------------------------------------------
AUTO_DIR = Path(__file__).resolve().parent.parent / "logs" / "anal_auto"
AUTO_STATE = AUTO_DIR / "state.json"


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


def anal_source(path):
    """Raw file name an A@@ was made from (AR Table 'Filename', written by LabVIEW and Python), or None."""
    from nptdms import TdmsFile
    try:
        with TdmsFile.open(long_path(path)) as f:
            return str(f["AR Table"]["Filename"][0])
    except Exception:
        return None


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
