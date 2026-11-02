"""LabVIEW 1st Step result folder next to the A@@ file (optional).

    ANAL/A@<yyyy_mmdd_HHMM_SS>@<sample>/
        A@...@<sample> In S Table.csv / In R Table.csv / In B Table.csv   same strings as the A@@ tables
        A@...@<sample> In FP.Jpg       summary figure (Python version of the LabVIEW front-panel snapshot)
        PS/PS_#NNNN_<head>##<region> (<peak>)_I_<signal>_T_<TL>.txt          ±100 points per 10 ms of signal
        BS/BS_#NNNN_<head>##<R#> (S_<start>-E_<end>)_TL_<TL>.txt            each burst, B Start .. B End

<head> is the raw file name up to its third part ('NBB_10k_Sample#001 D_20250401_1040 UNAN#2Pex1n1').
PS time column reproduces LabVIEW: region number + (index inside the region) * 0.001 (LabVIEW uses 1 ms instead
of 0.1 ms per point there; the current values themselves are the right raw points).
"""
import os
from pathlib import Path

import numpy as np

from first_step import DEFAULT_CONDITION, long_path

PS_HALF = 100


def head_name(raw_name):
    parts = Path(raw_name).stem.split(" ")
    return " ".join(parts[:2] + [parts[2].split("_")[0]]) if len(parts) > 2 else Path(raw_name).stem


def folder_for(a_path, an_time):
    sample = Path(a_path).stem[len("A@@yyyy_mmdd_HHMM_SS_"):]
    return Path(a_path).parent / f"A@{an_time.strftime('%Y_%m%d_%H%M_%S')}@{sample}"


def _write(path, text):
    with open(long_path(path), "w", encoding="cp932", errors="replace", newline="") as f:
        f.write(text)


def _table_csv(tab):
    cols = list(tab)
    n = max((len(v) for v in tab.values()), default=0)
    lines = [",".join(cols)]
    lines += [",".join(str(tab[c][i]) if i < len(tab[c]) else "" for c in cols) for i in range(n)]
    return "\r\n".join(lines) + "\r\n"


def write_result_folder(a_path, raw, str_tables, raw_name, an_time, cond=None, jpg=True):
    """Create the result folder of one A@@ file; returns its path."""
    c = dict(DEFAULT_CONDITION, **(cond or {}))
    rrp, fs = c["read_region_point"], c["fs"]
    folder = folder_for(a_path, an_time)
    for sub in ("", "PS", "BS"):
        os.makedirs(long_path(folder / sub), exist_ok=True)
    base = folder.name
    for name in ("S", "R", "B"):
        _write(folder / f"{base} In {name} Table.csv", _table_csv(str_tables[f"{name} Table"]))

    head = head_name(raw_name)
    s = str_tables["S Table"]
    for k, (peak, sig, tl) in enumerate(zip(s["S Peak Position [s]"], s["Signal [pA]"], s["S TL [ms]"])):
        p = int(round(float(peak) * fs))
        region = p // rrp
        lo = region * rrp
        seg = raw[lo:lo + rrp]
        # half width = 100 points per started 10 ms of signal (as "Signal Region Point" in the S Table)
        half = -(-int(round(float(tl) * fs / 1000)) // PS_HALF) * PS_HALF
        start = max(p - lo - half, 0)  # Array Subset: a negative index starts at 0, length kept
        vals = seg[start:start + 2 * half]
        lines = "".join(f"{region + (start + i) * 0.001:.8f},{v:.8f}\r\n" for i, v in enumerate(vals))
        _write(folder / "PS" / f"PS_#{k:04d}_{head}##{region} ({peak})_I_{sig}_T_{float(tl):.1f}.txt", lines)

    b = str_tables["B Table"]
    for k, (bs, be, tl, r) in enumerate(zip(b["B Start [s]"], b["B End [s]"], b["B time length [ms]"],
                                            b["R # [n]"])):
        i0, i1 = int(round(float(bs) * fs)), int(round(float(be) * fs))
        lines = "".join(f"{i / fs:.6f},{raw[i]:.6f}\r\n" for i in range(i0, min(i1, len(raw))))
        _write(folder / "BS" / f"BS_#{k:04d}_{head}##{r} (S_{bs}-E_{be})_TL_{tl}.txt", lines)

    if jpg:
        summary_jpg(folder / f"{base} In FP.Jpg", raw, str_tables, raw_name, base, c)
    return folder


def summary_jpg(path, raw, str_tables, raw_name, title, c):
    """Raw / region / STD / count / BL graphs and the AR values (in place of the LabVIEW panel snapshot)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fs, rrp = c["fs"], c["read_region_point"]
    num = lambda v: np.array([float(x) if x not in ("", "NaN") else np.nan for x in v])
    r, ar = str_tables["R Table"], str_tables["AR Table"]
    rt = np.arange(len(r["R # [n]"])) * rrp / fs
    def smooth(y, n=10):
        ok = np.isfinite(y)
        k = np.ones(n)
        return np.convolve(np.where(ok, y, 0), k, "same") / np.maximum(np.convolve(ok, k, "same"), 1)

    fig = Figure(figsize=(19.2, 10.8), dpi=100, facecolor="white")
    FigureCanvasAgg(fig)
    fig.suptitle(f"{title}\n{raw_name}", fontsize=10)
    gs = fig.add_gridspec(3, 3, hspace=0.45, wspace=0.25)

    ax = fig.add_subplot(gs[0, :2])
    step = max(len(raw) // 4000, 1)
    m = len(raw) // step * step
    blocks = raw[:m].reshape(-1, step)
    tt = np.arange(blocks.shape[0]) * step / fs
    ax.fill_between(tt, blocks.min(1), blocks.max(1), color="k", lw=0)
    for bs, be in zip(str_tables["B Table"]["B Start [s]"], str_tables["B Table"]["B End [s]"]):
        ax.axvspan(float(bs), float(be), color="red", alpha=0.3)
    ax.set(title="Raw data Graph", xlabel="Time (s)", ylabel="Current (nA)")

    ax = fig.add_subplot(gs[1, :2])
    seg = raw[:rrp]
    ax.plot(np.arange(len(seg)) / fs, seg, color="tab:red", lw=0.5)
    ax.axhline(float(r["Region BL [pA]"][0]) / 1000, color="k", lw=1)
    ax.set(title="Region graph (first region)", xlabel="Time (s)", ylabel="Current (nA)")

    for pos, col, name, color in [(gs[2, 0], "Region STD [pA]", "STD Graph", "gold"),
                                  (gs[2, 1], "Region BL [pA]", "BL Graph", "mediumseagreen")]:
        ax = fig.add_subplot(pos)
        y = num(r[col])
        ax.plot(rt, y, "o", ms=2.5, color=color)
        ax.plot(rt, smooth(y), color="tab:red", lw=1)
        ax.set(title=name, xlabel="Ex Time (s)", ylabel=col)

    ax = fig.add_subplot(gs[2, 2])
    ax.plot(rt, num(r["R Signal Count [n]"]), "o", ms=2.5, color="tab:red")
    ax.set(title="Count Graph", xlabel="Ex Time (s)", ylabel="Counted Signal")

    ax = fig.add_subplot(gs[:2, 2])
    ax.axis("off")
    keys = ["Signal#Sum", "Burst#Sum", "Signal Freq [/s]", "B Duty Cycle [%]", "Region BL Mode [pA]",
            "Region STD Mode [pA]", "Total Time [s]", "Anal Time", "Anal Version"]
    lines = [f"{k}: {ar[k][0]}" for k in keys if k in ar and len(ar[k])]
    lines += ["", "Set Condition"] + [f"{k}: {c[k]}" for k in
                                       ("point", "cut_region", "bl_selection", "std_selection", "d_th_xn",
                                        "u_th_xn", "read_region_point", "std_smooth_point", "max_d_time_ms",
                                        "max_es_time_ms")]
    ax.text(0, 1, "\n".join(lines), va="top", family="monospace", fontsize=10)
    fig.savefig(long_path(path), format="jpg", dpi=100)
