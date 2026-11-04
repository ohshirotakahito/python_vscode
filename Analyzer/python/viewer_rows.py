"""Per-file rows of the result viewers: the summary values of every A@@ (T\\ANAL) or B@@ (T\\BNAL@<target>) file.

Reading a file takes little data but much CPU (nptdms parses the metadata of every segment in Python, and a
B@@ written by LabVIEW has hundreds of segments), so the files are read in worker processes and the values are
kept in a cache (Analyzer/logs/viewer_rows.pkl) by path, size and modification time: an unchanged folder opens
at once the next time.
"""
import io
import os
import pickle
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from nptdms import TdmsFile

from first_step import long_path

CACHE = Path(__file__).resolve().parent.parent / "logs" / "viewer_rows.pkl"
CACHE_VERSION = 1

# AR Table value -> column of the ANAL folder file list
A_COLS = [("信号数", "Signal#Sum"), ("バースト数", "Burst#Sum"), ("測定時間 [s]", "Total Time [s]"),
          ("バースト時間 [s]", "B Region Time [s]"), ("信号頻度 [/s]", "Signal Freq [/s]"),
          ("BL [pA]", "Region BL Mode [pA]"), ("STD [pA]", "Region STD Mode [pA]")]
# BR Table value -> column of the BNAL@ folder file list
B_COLS = [("信号数", "Signal Number # [n]"), ("長い信号数", "Long Signal Number # [n]"),
          ("MAX Right Read", "MAX Right Read # [n]"), ("Coverage [%]", "Coverage [%]"),
          ("Long Read [bp]", "Long Read [bp]"), ("Long Right Read [bp]", "Long Right Read [bp]")]


def number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


SMALL = 8 << 20  # read a file this small in one go: nptdms seeks to every segment, a round trip on a share each


def tdms(path):
    p = long_path(path)
    if os.path.getsize(p) <= SMALL:
        with open(p, "rb") as f:
            return TdmsFile.open(io.BytesIO(f.read()))
    return TdmsFile.open(p)


def first_row(t, group):
    return {c.name: c[0] for c in t[group].channels() if len(c)} if group in t else {}


def a_values(path):
    """AR Table of an A@@ file (the waveform is not read)."""
    with tdms(path) as t:
        ar = first_row(t, "AR Table")
    return {**{col: number(ar.get(key)) for col, key in A_COLS}, "元データ": ar.get("Filename", "")}


def b_values(path):
    """BR / BC / AR tables of a B@@ file and the sample positions its long signals cover."""
    with tdms(path) as t:
        if "BR Table" not in t:
            raise ValueError("BR Table がありません（書き込みが途中で終わったファイル）")
        br, bc = first_row(t, "BR Table"), first_row(t, "BC Table")
        ar = first_row(t, "AR Table")
        covered = set()
        lsp = t["LSP Table"] if "LSP Table" in t else {}  # LabVIEW leaves it out without long signals
        if "Seq SNo" in lsp and "Seq ENo" in lsp:
            for s, e in zip(lsp["Seq SNo"][:], lsp["Seq ENo"][:]):
                covered.update(range(int(number(s)), int(number(e)) + 1))
    return {**{col: number(br.get(key)) for col, key in B_COLS},
            "測定時間 [s]": number(ar.get("Total Time [s]")), "Total CP": number(br.get("Total CP Base [n]")),
            "bc": {k: bc.get(k, "") for k in ("Sample name", "Sample Sequence", "Right Read MIN [n]")},
            "covered": covered}


READERS = {"A@@": (a_values, [c for c, _ in A_COLS]), "B@@": (b_values, [c for c, _ in B_COLS] + ["測定時間 [s]"])}


def _read(prefix, path):
    """(worker) -> (values, error text)"""
    try:
        return READERS[prefix][0](path), ""
    except Exception as e:
        return None, str(e)


_pool, _cache, _lock = None, None, threading.Lock()


WORKERS = max(1, min(8, (os.cpu_count() or 2) - 1))


def _workers():
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=WORKERS)
        return _pool


def _ready():
    time.sleep(0.3)  # long enough that every worker gets one, so all of them start


def warm_up():
    """Start the worker processes in the background (each one imports the app: seconds on Windows), so the
    first folder opened does not wait for them."""
    def run():
        try:
            for f in [_workers().submit(_ready) for _ in range(WORKERS)]:
                f.result()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def _load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE, "rb") as f:
                data = pickle.load(f)
            _cache = data["rows"] if data.get("version") == CACHE_VERSION else {}
        except Exception:
            _cache = {}
    return _cache


def _save_cache():
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump({"version": CACHE_VERSION, "rows": _cache}, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def list_files(folder, prefix):
    """[(path, (size, mtime_ns))] of <prefix>*.tdms in a folder (the stat comes with the listing on Windows)."""
    with os.scandir(long_path(folder)) as it:
        found = [(Path(folder) / e.name, e.stat()) for e in it
                 if e.is_file() and e.name.startswith(prefix) and e.name.lower().endswith(".tdms")]
    return sorted(((p, (st.st_size, st.st_mtime_ns)) for p, st in found), key=lambda x: x[0].name)


def read_rows(folder, prefix, progress=lambda *a: None):
    """[(path, values or None, error)] of every <prefix>*.tdms file of the folder, from the cache when unchanged."""
    files = list_files(folder, prefix)
    with _lock:
        cache = _load_cache()
        out = {}
        for p, sig in files:
            hit = cache.get(str(p))
            if hit and hit[0] == sig:
                out[str(p)] = hit[1]
    todo = [(p, sig) for p, sig in files if str(p) not in out]
    errors = {}
    done = len(files) - len(todo)
    progress(int(100 * done / max(len(files), 1)), f"読み込み {done}/{len(files)}")
    if todo:
        if len(todo) <= 2:  # a worker process would take longer to start than these
            results = (_read(prefix, p) for p, _ in todo)
        else:
            try:
                results = _workers().map(_read, [prefix] * len(todo), [p for p, _ in todo])
            except (BrokenProcessPool, RuntimeError):
                results = (_read(prefix, p) for p, _ in todo)
        fresh = {}
        try:
            for (p, sig), (values, err) in zip(todo, results):
                done += 1
                progress(int(100 * done / len(files)), f"読み込み {done}/{len(files)}")
                if err:
                    errors[str(p)] = err  # not cached: the file may still be being written
                else:
                    out[str(p)] = values
                    fresh[str(p)] = (sig, values)
        except BrokenProcessPool:
            global _pool
            _pool = None
            for p, sig in todo:
                if str(p) not in out and str(p) not in errors:
                    values, err = _read(prefix, p)
                    out[str(p)] = values if not err else None
                    if err:
                        errors[str(p)] = err
                    else:
                        fresh[str(p)] = (sig, values)
        if fresh:
            with _lock:
                _cache.update(fresh)
                _save_cache()
    return [(p, out.get(str(p)), errors.get(str(p), "")) for p, _ in files]
