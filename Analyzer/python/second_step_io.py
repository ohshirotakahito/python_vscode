"""Save the 2nd step results in the LabVIEW format (BNAL@<sample>/B@@...tdms) and batch-run.

Usage:
    python second_step_io.py <A@@ file or folder> --sequence UGUUGUACUUUUUUUUUUGUUC --name hsa-miR-3613-5p
    python second_step_io.py <A@@ file or folder> --settings-from <B@@ file>   (reuse its BC Table)

Options: --right-read-min (5), --noise-max (3), -o OUTDIR (default: <A@@ folder>/BNAL@<name>),
         --csv, --from-raw (path is raw data: run the 1st step first)
"""
import argparse
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from nptdms import ChannelObject, GroupObject, RootObject, TdmsFile, TdmsWriter

import first_step_io
from first_step_io import StringChannel
from first_step import long_path
from second_step import DEFAULT_CONDITION, condition_from_bc_table, read_first_step, second_step

BNAL_VERSION = "20180801 2nd Step Analysis (Python)"

# column -> decimals LabVIEW writes (None: integer or string as is)
_LEVELS = {"G_AL": 4, "A_AL": 4, "C_AL": 4, "U_AL": 4, "BL Level [pA]": 4,
           "Region MAX [pA]": 4, "Region MIN [pA]": 4, "S/N ratio": 4,
           "Signal Intensity [pA]": 4, "BLX Level [pA]": 4, "Mx Intensity level [pA]": 4}
_SIGNAL = {"Signal Length [ms]": 1, "Signal Start [s]": 4, "Signal End [s]": 4,
           "Probability [a]": 4, "Q value [x]": 2, "BL_AL": 4, **_LEVELS}
DECIMALS = {
    "BS Table": _SIGNAL,
    "LSP Table": _SIGNAL,
    "LCP Table": {"Relative Averaged Data": 6, "Relative Assigned Level": 6, "STD [a]": 4,
                  "CP Length [ms]": 2, "Q value [x]": 4, "Probability [a]": 4,
                  "CP S [ms]": 4, "CP E [ms]": 4, "G_probability": 6, "A_probability": 6,
                  "C_probability": 6, "U_probability": 6, "B_probability": 6, "B_AL": 4, **_LEVELS},
    "LTP Table": {"Time [s]": 4, "Raw data [pA]": 8, "Relative data [a]": 8,
                  "Relative Assigned [a]": 8, "Probability [a]": 4, "Q value [x]": 4,
                  "G_probability": 6, "A_probability": 6, "C_probability": 6,
                  "U_probability": 6, "B_probability": 6, "B_AL": 4, **_LEVELS},
    # B rows (flanking baseline) are written with fewer decimals
    "LAP Table": {"Relative Averaged Data": 4, "Relative Averaged Level": 3,
                  "Base Time length [ms]": ("B", 2, 4), "Base Time [s]": ("B", 4, 6),
                  "Signal Intensity [pA]": 4},
    "LL Table": {},
    "BR Table": {"Coverage [%]": 3, "Long Read Rate [kbp/day]": 1,
                 "Long Right Read Rate [kbp/day]": 1, "5’→3' direction [%]": 3,
                 "Av Read # [a]": 3, "Av Right Read # [a]": 3,
                 "Assembly Ratio 1 [%]": 3, "Assembly Ratio 2 [%]": 3, "RR Ratio [%]": 3},
}


# number of data channels LabVIEW writes for a table without rows (Table Tag still lists every column)
EMPTY_DATA_COLUMNS = {"BS Table": 14, "LSP Table": 1, "LCP Table": 0, "LTP Table": 0, "LAP Table": 0}


def fmt(v, dec):
    """LabVIEW number to string (dec None: integer; strings unchanged)."""
    if isinstance(v, str):
        return v
    v = float(v)
    if np.isnan(v):
        return "NaN"
    if np.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return f"{v:.0f}" if dec is None else f"{v:.{dec}f}"


def format_table(name, df):
    """DataFrame -> {column: [str]} with the LabVIEW decimals."""
    spec = DECIMALS.get(name, {})
    out = {}
    for col in df.columns:
        dec = spec.get(col)
        if dec is None and col.endswith("_AL"):
            dec = 4  # assigned level of any base in the Base Table
        elif dec is None and col.endswith("_probability"):
            dec = 6
        if isinstance(dec, tuple):  # per row decimals depending on the base
            base_col = "Assigned Base"
            out[col] = [fmt(v, dec[1] if b == dec[0] else dec[2]) for v, b in zip(df[col], df[base_col])]
        else:
            out[col] = [fmt(v, dec) for v in df[col]]
    return out


def bc_table(c):
    row = {"Sample name": c["sample_name"], "Sample Sequence": c["sample_sequence"],
           "STD Type": "Region", "Right Read MIN [n]": str(c["right_read_min"]),
           "noise MAX [n]": str(c["noise_max"]), "Filter P [n]": str(c["filter_p"])}
    row.update({f"{b}_Relative G": f"{v:.3f}" for b, v in c["bases"].items()})
    stds = c.get("base_stds") or {}
    row.update({f"{b}_STD": f"{stds.get(b, 2.0):.3f}" for b in c["bases"]})
    return {k: [v] for k, v in row.items()}


def output_path(a_path, c, out_dir, an_time):
    """BNAL@<sample name>/B@@<yyyy_mmdd_HHMM>_<A@@ name without its time stamp>."""
    a_name = Path(a_path).name
    parts = a_name[3:].split("_", 4)  # A@@yyyy_mmdd_HHMM_SS_<rest>
    rest = parts[4] if len(parts) == 5 else a_name
    folder = Path(out_dir) if out_dir else Path(a_path).parent / f"BNAL@{c['sample_name']}"
    return folder / f"B@@{an_time.strftime('%Y_%m%d_%H%M')}_{rest}"


def write_b_file(path, a_path, results, c, anal_seconds):
    a = TdmsFile.read(long_path(a_path))
    first = {g: {ch.name: list(ch[:]) for ch in a[g].channels()}
             for g in ("AC Table", "S Table", "R Table", "B Table", "AR Table")}
    h, rem = divmod(int(round(anal_seconds)), 3600)
    tables = {
        "BC Table": bc_table(c),
        **{n: format_table(n, results[n]) for n in
           ("LSP Table", "LCP Table", "LTP Table", "BS Table", "LAP Table", "LL Table", "BR Table")},
    }
    tables["BR Table"].update({
        "BNAL analysis time [xx:xx:xx]": [f"{h:02d}:{rem // 60:02d}:{rem % 60:02d}"],
        "BNAL Programe version": [BNAL_VERSION],
        "BNAL update Date": [""],
    })
    tag_order = ["S Table", "R Table", "B Table", "AR Table", "AC Table", "BC Table", "LSP Table",
                 "LCP Table", "LTP Table", "BS Table", "LAP Table", "LL Table", "BR Table"]
    all_tables = {**first, **tables}
    objs = [RootObject({**dict(a.properties), **first_step_io.operator_properties("BNAL ")}), GroupObject("Data"),
            ChannelObject("Data", "Ch1", np.array([], dtype=np.float64)), GroupObject("Table Tag")]
    # column names of the copied 1st step tables come from the A@@ Table Tag (a table without rows has no channels)
    a_tags = {c.name: list(c[:]) for c in a["Table Tag"].channels()} if "Table Tag" in a else {}
    objs += [StringChannel("Table Tag", n, all_tables[n] or a_tags.get(n, [])) for n in tag_order]
    for n in ["AC Table", "S Table", "R Table", "B Table", "AR Table", "BC Table", "LSP Table",
              "LCP Table", "LTP Table", "BS Table", "LAP Table", "LL Table", "BR Table"]:
        objs.append(GroupObject(n))
        items = list(all_tables[n].items())
        if n in EMPTY_DATA_COLUMNS and not any(len(v) for _, v in items):
            items = items[:EMPTY_DATA_COLUMNS[n]]  # LabVIEW writes only these channels of a table without rows
        for k, (col, values) in enumerate(items):
            objs.append(StringChannel(n, col, values, properties={"NI_ArrayColumn": k}))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with TdmsWriter(long_path(path)) as w:
        w.write_segment(objs)


def process_file(a_path, cond, out_dir=None, csv=False, progress=None):
    an_time = datetime.now()
    t0 = time.perf_counter()
    c = dict(DEFAULT_CONDITION, **cond)
    raw, tables = read_first_step(a_path)
    results = second_step(raw, tables, c, file_name=Path(a_path).name, progress=progress)
    out = output_path(a_path, c, out_dir, an_time)
    # write under a temporary name so an interrupted run never leaves a half-written B@@ .tdms
    part = out.with_name(out.name + ".part")
    try:
        write_b_file(part, a_path, results, c, time.perf_counter() - t0)
    except BaseException:
        if os.path.exists(long_path(part)):
            os.remove(long_path(part))
        raise
    os.replace(long_path(part), long_path(out))
    if csv:
        for name in ("BS Table", "LSP Table", "LCP Table", "LTP Table", "LAP Table", "LL Table", "BR Table"):
            pd.DataFrame(format_table(name, results[name])).to_csv(
                long_path(out.with_name(f"{out.stem}_{name.replace(' ', '_')}.csv")),
                index=False, encoding="utf-8-sig")
    return out, results


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="A@@ file (1st step result) or a folder of them")
    ap.add_argument("--sequence", help="Sample Sequence")
    ap.add_argument("--name", default="", help="Sample name")
    ap.add_argument("--settings-from", help="take the settings from the BC Table of this B@@ file")
    ap.add_argument("--right-read-min", type=int)
    ap.add_argument("--noise-max", type=int)
    ap.add_argument("-o", "--out-dir")
    ap.add_argument("--csv", action="store_true", help="also write each table as CSV")
    ap.add_argument("--from-raw", action="store_true",
                    help="path is raw data: run the 1st step first (A@@ next to the raw file)")
    args = ap.parse_args()

    cond = {}
    if args.settings_from:
        bc = TdmsFile.read(long_path(args.settings_from))["BC Table"]
        cond = condition_from_bc_table(pd.DataFrame({ch.name: ch[:] for ch in bc.channels()}))
    if args.sequence:
        cond.update(sample_sequence=args.sequence, sample_name=args.name or cond.get("sample_name", ""))
    if args.right_read_min is not None:
        cond["right_read_min"] = args.right_read_min
    if args.noise_max is not None:
        cond["noise_max"] = args.noise_max
    if not cond.get("sample_sequence"):
        ap.error("give --sequence or --settings-from")

    p = Path(args.path)
    if args.from_raw:
        files = sorted(f for f in p.glob("*.tdms") if not f.name[1:3] == "@@") if p.is_dir() else [p]
    else:
        files = sorted(p.glob("A@@*.tdms")) if p.is_dir() else [p]
    for f in files:
        try:
            if args.from_raw:
                f, _ = first_step_io.process_file(f, csv=args.csv)
                print(f"1st step -> {f}")
            out, res = process_file(f, cond, args.out_dir, args.csv)
            br = res["BR Table"].iloc[0]
            print(f"{f.name}\n  -> {out}  signals={int(br['Signal Number # [n]'])} "
                  f"long={int(br['Long Signal Number # [n]'])}")
        except Exception as e:  # keep going through the folder
            print(f"{f.name}\n  !! failed: {e}")


if __name__ == "__main__":
    main()
