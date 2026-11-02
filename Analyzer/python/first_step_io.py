"""Save the 1st step results in the LabVIEW output format (A@@...tdms) and batch-run folders.

Usage:
    python first_step_io.py <raw .tdms file or folder> [-o OUTDIR] [--csv]

The output TDMS has the same layout as "20180816 1st Step Analysis.vi":
groups Data / Table Tag / AC Table / S Table / R Table / B Table / AR Table,
every table value stored as a string with LabVIEW's number of decimals.
"""
import argparse
import getpass
import os
import socket
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter, types

from first_step import DEFAULT_CONDITION, first_step, long_path, read_raw
from result_folder import write_result_folder

ANAL_VERSION = "20180816 1st Step Analysis (Python)"

# names written by LabVIEW for each selection
BL_SELECTION_NAMES = {  # condition -> (AC Table "BL Selection", S Table "RT BL [select]")
    "All Mode": ("All Value Mode BL", "All DMode"),
    "Div Mode": ("Div Value Mode BL", "Div MMode"),
    "Div Mean": ("Div mean value BL", "Div Mean"),
}
STD_SELECTION_NAMES = {  # condition -> (AC Table "STD Selection", S Table "RT STD [select]")
    "All STD": ("All STD", "All STD"),
    "Div STD Mode": ("DivSTD Mode STD", "Div STD Mode"),
    "Div STD Mean": ("DivSTD mean STD", "Div STD Mean"),
}


def parse_file_info(name):
    """file name transfer.vi: experiment info encoded in the raw file name.

    e.g. "Lys_10k_Sample#001 D_20250926_1140 UNAN#2Pex1n1_HFSP-AA-26_EXSV#10_..._SGM
    D0.540nm B100vsI_22.76_EP1_100_EP2_-100_DV_SC78-4301-18-G080.tdms"
    """
    def find(pattern):
        m = re.search(pattern, name)
        return m.group(1) if m else ""

    ex = re.search(r"D_(\d{8}_\d{4}) [^A]*(A.*?)_EX", name)
    return {
        "Ex ID": f"{ex.group(1)} {ex.group(2)}" if ex else "",
        "Chip ID": "",
        "Distance": find(r" D(-?[\d.]+)nm"),
        "Bias": find(r" B(-?[\d.]+)vsI_"),
        "EP1": find(r"_EP1_(.*?)_EP2_"),
        "EP2": find(r"_EP2_(.*?)\.t[^.]*$"),
    }


def _fmt(v, dec):
    """LabVIEW Number To Fractional String (dec=None: integer, str: as is)."""
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    v = float(v)
    if np.isnan(v):
        return "NaN"
    if np.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return f"{v:.0f}" if dec is None else f"{v:.{dec}f}"


def _table(df, spec, n):
    """spec: [(LabVIEW column, python column or constant, decimals)] -> {column: [str]}"""
    out = {}
    for lv_col, src, dec in spec:
        if isinstance(src, str) and src in df:
            out[lv_col] = [_fmt(v, dec) for v in df[src]]
        else:
            out[lv_col] = [_fmt(src, dec)] * n
    return out


def format_tables(tables, raw_name, cond, an_time, anal_seconds):
    c = dict(DEFAULT_CONDITION, **(cond or {}))
    info = parse_file_info(raw_name)
    dist = float(info["Distance"]) if info["Distance"] else ""
    bias = info["Bias"]
    ac_bl, s_bl = BL_SELECTION_NAMES[c["bl_selection"]]
    ac_std, s_std = STD_SELECTION_NAMES[c["std_selection"]]
    s, r, b, ar = (tables[k] for k in ("S Table", "R Table", "B Table", "AR Table"))

    s_tab = _table(s, [
        ("S # [n]", "S # [n]", None),
        ("S Peak Position [s]", "S Peak Position [s]", 4),
        ("Signal [pA]", "Signal [pA]", 4),
        ("Region BL [pA]", "Region BL [pA]", 4),
        ("Region STD [pA]", "Region STD [pA]", 4),
        ("Threshold [pA]", "Threshold [pA]", 4),
        ("Signal S [s]", "Signal S [s]", 4),
        ("Signal E (s)", "Signal E [s]", 4),
        ("S TL [ms]", "S TL [ms]", 2),
        ("S DL [s]", "S DL [s]", 4),
        ("Bias [V]", bias, None),
        ("RT BL [pA]", "RT BL [pA]", 4),
        ("Chip ID [a]", info["Chip ID"], None),
        ("Ex ID [a]", info["Ex ID"], None),
        ("Distance [nm]", dist, 3),
        ("EP1 [mV]", info["EP1"], None),
        ("EP2  [mV]", info["EP2"], None),
        ("Read Region Point [n", c["read_region_point"], None),
        ("STD [select]", "Smooth", None),
        ("RT BL [select]", s_bl, None),
        ("RT STD [select]", s_std, None),
        ("Moving Point [n]", c["point"], None),
        ("U threshold [n]", c["u_th_xn"], 2),
        ("D thereshold [n]", c["d_th_xn"], 2),
        ("STD smooth [n]", c["std_smooth_point"], None),
        ("Signal Region Point [n]", "Signal Region Point [n]", None),
        ("H1 [n]", "H1 [n]", None),
        ("H2 [n]", "H2 [n]", None),
    ], len(s))

    r_tab = _table(r, [
        ("R # [n]", "R #", None),
        ("Distance [nm]", dist, 3),
        ("Region BL [pA]", "Region BL [pA]", 4),
        ("Region STD [pA]", "Region STD [pA]", 4),
        ("R Signal Count [n]", "R Signal Count [n]", None),
        ("Bias [mV]", bias, None),
        ("R BL All Mode [pA]", "BL All Mode [pA]", 4),
        ("R BL Div Mode [pA]", "BL Div Mode [pA]", 4),
        ("R BL Div Mean [pA]", "BL Div Mean [pA]", 4),
        ("R BL Min Mode Peak [ms]", "R BL Min Mode Peak [pA]", 4),
        ("R STD all [pA]", "STD All STD [pA]", 4),
        # LabVIEW writes these two columns swapped
        ("R STD Div mean [pA]", "STD Div STD Mode [pA]", 4),
        ("R STD Div Mode [pA]", "STD Div STD Mean [pA]", 4),
        ("R H#1 [n]", "R H#1 [n]", None),
        ("R H#2 [n]", "R H#2 [n]", None),
        ("R H#3 [n]", "R H#3 [n]", None),
        ("Ex ID", info["Ex ID"], None),
        ("Chip ID", info["Chip ID"], None),
        ("EP1 [mV]", info["EP1"], None),
        ("EP2 [mV]", info["EP2"], None),
        ("R S_SCount [n]", "R Signal Count [n]", None),
        ("R B_SCount [n]", "R B_SCount [n]", None),
        ("R B Region Count [%]", "R B Region Count [%]", 2),
        ("R B Region Count [n]", "R B Region Count [n]", None),
        ("R B Region Time [s]", "R B Region Time [s]", 4),
        ("R Region Time [%]", "R Region Time [%]", 4),
        ("B Signal Mean [pA]", "B Signal Mean [pA]", 4),
        ("B Signal STD [pA]", "B Signal STD [pA]", 3),
        ("R_SN [%]", "R_SN [%]", 3),
        ("R skewness [a]", "R skewness [a]", 4),
        ("R kutosis [a]", "R kurtosis [a]", 4),
    ], len(r))

    b_tab = _table(b, [
        ("B # [n]", "B # [n]", None),
        ("B Start [s]", "B Start [s]", 4),
        ("B End [s]", "B End [s]", 4),
        ("B time length [ms]", "B time length [ms]", 1),
        ("B BL [pA]", "B BL [pA]", 4),
        ("B STD [pA]", "B STD [pA]", 4),
        ("Region BL [pA]", "Region BL [pA]", 4),
        ("Region STD [pA]", "Region STD [pA]", 4),
        ("R # [n]", "R # [n]", None),
        ("R S_IDX [n]", "R S_IDX [n]", None),
        ("R E_IDX [n]", "R E_IDX [n]", None),
        ("B Signal Count [n]", "B Signal Count [n]", None),
        ("B Signal MAX [pA]", "B Signal MAX [pA]", 4),
        ("B Signal Average [n]", "B Signal Average [pA]", 4),
        ("S S_IDX [n]", "S S_IDX [n]", None),
        ("S E_IDX [n]", "S E_IDX [n]", None),
        ("LL BR number [n]", 0, None),
        ("LL time length [ms]", 0.0, 1),
        ("Distance [nm]", dist, 3),
        ("Ex ID", info["Ex ID"], None),
        ("Chip ID", info["Chip ID"], None),
        ("EP1 [mV]", info["EP1"], None),
        ("EP2 [mV]", info["EP2"], None),
        ("Bias [mV]", bias, None),
        ("B H1 [n]", "B H1 [n]", None),
        ("B H2 [n]", "B H2 [n]", None),
    ], len(b))

    h, rem = divmod(int(round(anal_seconds)), 3600)
    ar_tab = _table(ar, [
        ("Filename", raw_name, None),
        ("AN time", "@" + an_time.strftime("%Y_%m%d_%H%M_%S"), None),
        ("Ex ID", info["Ex ID"], None),
        ("Chip ID", info["Chip ID"], None),
        ("Distance (nm)", dist, 3),
        ("Bias Voltage (mV)", bias, None),
        ("EP1 Voltage (mV)", info["EP1"], None),
        ("EP2 Voltage (mV)", info["EP2"], None),
        ("Burst#Sum", "Burst#Sum", None),
        ("Signal#Sum", "Signal#Sum", None),
        ("R B_SCount [n] in", "R B_SCount [n] in", None),
        ("B Region Time [s]", "B Region Time [s]", 4),
        ("Signal Freq [/s]", "Signal Freq [/s]", 4),
        ("Datapoint", "Datapoint", None),
        ("Region STD Mode [pA]", "Region STD Mode [pA]", 4),
        ("Region BL Mode [pA]", "Region BL Mode [pA]", 4),
        ("Region Count Mode [a]", "Region Count Mode [a]", 4),
        ("B Duty Cycle [%]", "B Duty Cycle [%]", 6),
        ("Total Time [s]", "Total Time [s]", 4),
        ("Anal Time [xx:xx:xx]", f"{h:02d}:{rem // 60:02d}:{rem % 60:02d}", None),
        ("Data Aqusition Rate", f"{c['fs'] // 1000}kHz", None),
        ("Anal Version", ANAL_VERSION, None),
        ("Anal Update Date", "", None),
    ], 1)

    ac_tab = _table(pd.DataFrame(), [
        ("Filter Point [n]", c["point"], None),
        ("Cut Region [n]", c["cut_region"], None),
        ("BL Selection", ac_bl, None),
        ("STD Selection", ac_std, None),
        ("D Th std xn", c["d_th_xn"], 3),
        ("U Th std xn", c["u_th_xn"], 3),
        ("MAX D time (ms)", c["max_d_time_ms"], 3),
        ("MAX E_S time (ms)", c["max_es_time_ms"], 3),
        ("LL BR (n)", 0, None),
        ("LL BR T length (ms)", 0.0, 3),
        ("LL Retention (ms)", 0.0, 3),
        ("Read Region Point", c["read_region_point"], None),
        ("Filter Selection", "Smooth", None),
        ("STD smooth Point", c["std_smooth_point"], None),
    ], 1)
    return {"AC Table": ac_tab, "S Table": s_tab, "R Table": r_tab, "B Table": b_tab, "AR Table": ar_tab}


# who ran the Python analysis (written into the A@@ / B@@ root properties; the GUI sets it)
OPERATOR = getpass.getuser()
PROGRAM = "Total Analysis (Python)"


def operator_properties(prefix=""):
    return {f"{prefix}Operator": OPERATOR, f"{prefix}PC": socket.gethostname(), f"{prefix}Program": PROGRAM}


def output_name(raw_name, an_time):
    """A@@<yyyy_mmdd_HHMM_SS>_<sample name>.tdms (sample name = file name up to the first space)."""
    sample = Path(raw_name).stem.split(" ")[0]
    return f"A@@{an_time.strftime('%Y_%m%d_%H%M_%S')}_{sample}.tdms"


class StringChannel(ChannelObject):
    """String channel that can also be empty (a table without rows, e.g. 0 bursts):
    npTDMS infers the type from the first value and fails on an empty column."""

    def __init__(self, group, channel, values, properties=None):
        super().__init__(group, channel, np.array(list(values), dtype=object), properties)

    @property
    def data_type(self):
        return types.String


def write_tdms(path, raw, str_tables, an_time):
    root = RootObject({"name": an_time.strftime("%Y_%m%d_%H_%M_%S.") + f"{an_time.microsecond // 1000:03d}",
                       **operator_properties("ANAL ")})
    objs = [root, GroupObject("Data"), ChannelObject("Data", "Ch1", np.asarray(raw, dtype=np.float64)),
            GroupObject("Table Tag")]
    order = ["S Table", "R Table", "B Table", "AR Table", "AC Table"]
    objs += [StringChannel("Table Tag", name, str_tables[name]) for name in order]
    for name in ["AC Table", "S Table", "R Table", "B Table", "AR Table"]:
        objs.append(GroupObject(name))
        if not any(len(v) for v in str_tables[name].values()):
            continue  # LabVIEW writes no channels for a table without rows (the names stay in Table Tag)
        for k, (col, values) in enumerate(str_tables[name].items()):
            objs.append(StringChannel(name, col, values, properties={"NI_ArrayColumn": k}))
    with TdmsWriter(long_path(path)) as w:
        w.write_segment(objs)


def process_file(raw_path, out_dir=None, cond=None, csv=False, progress=None, folder=False):
    """`progress(done, total)` is called during the analysis.
    folder=True also writes the LabVIEW result folder A@<time>@<sample> (CSV, PS/BS waveforms, summary JPG)."""
    raw_path = Path(raw_path)
    out_dir = Path(out_dir) if out_dir else raw_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    an_time = datetime.now()
    t0 = time.perf_counter()
    raw = read_raw(raw_path)
    tables = first_step(raw, cond, progress)
    str_tables = format_tables(tables, raw_path.name, cond, an_time, time.perf_counter() - t0)
    out = out_dir / output_name(raw_path.name, an_time)
    # write under a temporary name so an interrupted run never leaves a half-written A@@ .tdms
    part = out.with_name(out.name + ".part")
    try:
        write_tdms(part, raw, str_tables, an_time)
    except BaseException:
        if os.path.exists(long_path(part)):
            os.remove(long_path(part))
        raise
    os.replace(long_path(part), long_path(out))
    if folder:
        write_result_folder(out, raw, str_tables, raw_path.name, an_time, cond)
    if csv:
        for name, tab in str_tables.items():
            pd.DataFrame(tab).to_csv(long_path(out.with_name(f"{out.stem}_{name.replace(' ', '_')}.csv")),
                                     index=False, encoding="utf-8-sig")
    return out, tables


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="raw .tdms file or folder of raw .tdms files")
    ap.add_argument("-o", "--out-dir", help="output folder (default: next to the raw file)")
    ap.add_argument("--csv", action="store_true", help="also write each table as CSV")
    ap.add_argument("--folder", action="store_true",
                    help="also write the LabVIEW result folder A@<time>@<sample> (CSV, PS/BS txt, JPG)")
    args = ap.parse_args()

    p = Path(args.path)
    # skip previous results (A@@...) and their index files
    files = sorted(f for f in p.glob("*.tdms") if not f.name.startswith("A@@")) if p.is_dir() else [p]
    for f in files:
        try:
            out, tables = process_file(f, args.out_dir, csv=args.csv, folder=args.folder)
            ar = tables["AR Table"].iloc[0]
            print(f"{f.name}\n  -> {out.name}  signals={int(ar['Signal#Sum'])} bursts={int(ar['Burst#Sum'])}")
        except Exception as e:  # keep going through the folder
            print(f"{f.name}\n  !! failed: {e}")


if __name__ == "__main__":
    main()
