"""Python port of "20180816 1st Step Analysis.vi".

Reproduces the R Table (per-region baseline / STD), the S Table (detected
signals), the B Table (bursts) and the AR Table summary of the LabVIEW
version, including the NI Peak Detector columns (R H#1-3, R BL Min Mode Peak,
S/B H1/H2, see ni_peak.py) and R skewness/kurtosis.  Not ported yet: LL BR.
"""
import os

import numpy as np
import pandas as pd
from nptdms import TdmsFile

from ni_peak import region_h_peaks, signal_h1_h2, skewness_kurtosis


# ---------------------------------------------------------------------------
# LabVIEW building blocks
# ---------------------------------------------------------------------------
def lv_mode(x, intervals=100):
    """NI Mode.vi (Unimodal): center of the most populated of `intervals` bins.

    Bin = floor((x - min) / width) as NI does; np.histogram's edge arithmetic puts the quantized raw values
    that sit exactly on a bin edge into the other bin, which can move the mode by several bins."""
    mn, mx = x.min(), x.max()
    w = (mx - mn) / intervals
    if w == 0:
        return mn
    k = np.minimum(np.floor((x - mn) / w).astype(int), intervals - 1)
    return mn + w * (np.argmax(np.bincount(k, minlength=intervals)) + 0.5)


def smoothing_pp(y, point):
    """Smoothing++.vi: centered moving average of +/-point.

    For i <= point the window is y[0 .. 2i] (Array Subset, cut at the array end);
    otherwise y[i-h .. i+h] with h = min(point, n-1-i)."""
    n = len(y)
    c = np.concatenate([[0.0], np.cumsum(y)])
    i = np.arange(n)
    lo = np.where(i <= point, 0, i - np.minimum(point, n - 1 - i))
    hi = np.where(i <= point, np.minimum(2 * i + 1, n), i + np.minimum(point, n - 1 - i) + 1)
    return (c[hi] - c[lo]) / (hi - lo)


def bl_estimation(region_raw, cut_region):
    """ASD BL estimation VI.vi -> BL cluster [pA].

    The region is split into `cut_region` blocks of len/cut_region points.
    """
    seg_mean = region_raw.reshape(cut_region, -1).mean(axis=1)
    return {
        "All Mode": lv_mode(region_raw) * 1000,
        "Div Mode": lv_mode(seg_mean) * 1000,
        "Div Mean": seg_mean.mean() * 1000,
    }


def std_estimation(sub_raw, cut_region):
    """ASD STD estimation VI.vi -> STD cluster [pA] (sample STD, n-1)."""
    seg_std = np.std(sub_raw.reshape(cut_region, -1), axis=1, ddof=1)
    return {
        "All STD": np.std(sub_raw, ddof=1) * 1000,
        "Div STD Mode": lv_mode(seg_std) * 1000,
        "Div STD Mean": seg_std.mean() * 1000,
    }


def std_for_threshold(std_history, smooth_point):
    """ASD STD for Threshold VI.vi: Smoothing++ of the STD history, last element.

    Smoothing++ uses mean(x[0..2i]) while i <= point, so the first regions
    average all STDs so far; afterwards the window collapses to the current value.
    """
    i = len(std_history) - 1
    if i <= smooth_point:
        return float(np.mean(std_history))
    return float(std_history[-1])


def trigger_detection(x, u_thr, d_thr):
    """2Triger Peak detectionM VI.vi (hysteresis trigger).

    A signal starts when x rises above `u_thr` (start index is moved back to
    the last sample at or below `d_thr`) and ends at the first sample below `d_thr`.
    Returns a list of (start, end, peak) indices.
    """
    events = []
    state = x[0] > u_thr
    start = None
    for k in range(len(x)):
        if not state and x[k] > u_thr:
            state = True
            j = k
            while j > 0 and x[j - 1] > d_thr:
                j -= 1
            start = max(j - 1, 0)  # last sample at or below d_thr
        elif state and x[k] < d_thr:
            state = False
            if start is not None:
                peak = start + int(np.argmax(x[start:k + 1]))
                events.append((start, k, peak))
    return events


def lv_multimode_first(x, intervals=100):
    """NI Mode.vi (Multimodal), first returned mode."""
    h, e = np.histogram(x, bins=intervals, range=(x.min(), x.max()))
    k = np.nonzero(h == h.max())[0][0]
    return (e[k] + e[k + 1]) / 2


def burst_signal_analysis(x):
    """ASD Signal Analysis VI.vi -> (BL, Omit STD) [pA] of the burst window."""
    bl = lv_multimode_first(x)
    sd = np.std(x, ddof=1)
    kept = x[x < bl + 0.5 * sd]
    return bl * 1000, np.std(kept, ddof=1) * 1000


def burst_candidates(sig_s, sig_e, max_es):
    """region analysis.vi: signal i is a candidate when E[i+1] - S[i] < max_es.

    LabVIEW's Index Array returns 0 past the end, so the last signal of a
    region always qualifies (its span is coerced to 0).
    """
    n = len(sig_s)
    cand = []
    for i in range(n):
        e_next = sig_e[i + 1] if i + 1 < n else 0.0
        span = min(max(e_next - sig_s[i], 0.0), 1.0)
        if span < max_es:
            cand.append(i)
    return cand


def group_bursts(peaks, cand, max_d):
    """ASD Burst Reigon VI.vi: consecutive candidates whose peaks are < max_d apart
    form one burst (>= 2 signals). Returns lists of positions in `cand`."""
    groups, cur = [], []
    for k, i in enumerate(cand):
        if cur and peaks[i] - peaks[cand[cur[-1]]] < max_d:
            cur.append(k)
            continue
        if len(cur) >= 2:
            groups.append(cur)
        cur = [k]
    if len(cur) >= 2:
        groups.append(cur)
    return groups


# ---------------------------------------------------------------------------
# 1st step
# ---------------------------------------------------------------------------
DEFAULT_CONDITION = {
    "point": 2000,             # +/- Point (real-time BL smoothing)
    "cut_region": 500,         # Cut Region
    "bl_selection": "Div Mode",
    "std_selection": "Div STD Mode",
    "d_th_xn": 1.0,            # D Th std xn
    "u_th_xn": 6.0,            # U Th std xn
    "read_region_point": 10000,
    "std_smooth_point": 10,
    "max_d_time_ms": 10.0,     # MAX D time (ms): max peak interval inside a burst
    "max_es_time_ms": 10.0,    # MAX E_S time (ms): max span E[i+1]-S[i] of a candidate
    "burst_margin_point": 200, # burst window = first peak - 200 .. last peak + 200
    "fs": 10000,               # 1s datapoint (10kHz)
}


def first_step(raw, cond=None, progress=None):
    """Run the 1st step on raw current data [nA].

    Returns a dict of DataFrames: "R Table", "S Table", "B Table", "AR Table".
    """
    c = dict(DEFAULT_CONDITION, **(cond or {}))
    rrp, fs = c["read_region_point"], c["fs"]
    margin = c["burst_margin_point"]
    std_hist, r_rows, s_rows, b_rows = [], [], [], []
    n_cand_total = 0

    n_regions = len(raw) // rrp
    for r in range(n_regions):
        if progress:
            progress(r, n_regions)
        x = raw[r * rrp:(r + 1) * rrp]
        est_bl = smoothing_pp(x, c["point"])
        sub = x - est_bl
        bl = bl_estimation(x, c["cut_region"])
        sd = std_estimation(sub, c["cut_region"])
        region_bl = bl[c["bl_selection"]]
        region_std = sd[c["std_selection"]]
        std_hist.append(region_std)

        thd = std_for_threshold(std_hist, c["std_smooth_point"])
        u_thr, d_thr = thd * c["u_th_xn"], thd * c["d_th_xn"]
        events = trigger_detection(sub * 1000, u_thr, d_thr)

        n_before = len(s_rows)
        sig_s = np.array([(r * rrp + s) / fs for s, _, _ in events])
        sig_e = np.array([(r * rrp + e) / fs for _, e, _ in events])
        peak_idx = np.array([r * rrp + p for _, _, p in events], dtype=int)
        cand = burst_candidates(sig_s, sig_e, c["max_es_time_ms"] * 0.001)
        n_cand_total += len(cand)
        groups = group_bursts(peak_idx / fs, cand, c["max_d_time_ms"] * 0.001)
        r_b_time = sum((peak_idx[cand[g[-1]]] - peak_idx[cand[g[0]]]) / fs for g in groups)
        # region analysis.vi: statistics of the candidate signal intensities
        cand_sig = np.array([sub[events[i][2]] * 1000 for i in cand])
        if len(cand_sig) == 0:
            b_mean, b_std = np.nan, -0.0  # LabVIEW Std Deviation of an empty array
        else:
            b_mean = cand_sig.mean()
            b_std = np.std(cand_sig, ddof=1) if len(cand_sig) > 1 else np.nan
        # Skewness and Kurtosis (Sample) of the Smoothing++'d intensities of all signals
        all_sig = np.array([sub[p] * 1000 for _, _, p in events])
        skew, kurt = skewness_kurtosis(smoothing_pp(all_sig, c["point"]))
        h1, h2, h3, min_peak = region_h_peaks(x)
        r_rows.append({"R #": r, "Region BL [pA]": region_bl, "Region STD [pA]": region_std,
                       "R Signal Count [n]": len(events),
                       **{f"BL {k} [pA]": v for k, v in bl.items()},
                       **{f"STD {k} [pA]": v for k, v in sd.items()},
                       "R B_SCount [n]": len(cand),
                       "R B Region Count [%]": len(cand) / len(events) * 100 if events else 0.0,
                       "R B Region Count [n]": len(groups),
                       "R B Region Time [s]": r_b_time,
                       "R Region Time [%]": r_b_time / (rrp / fs) * 100,
                       "B Signal Mean [pA]": b_mean,
                       "B Signal STD [pA]": b_std,
                       "R_SN [%]": b_std / b_mean * 100,
                       "R BL Min Mode Peak [pA]": min_peak * 1000,
                       "R H#1 [n]": h1, "R H#2 [n]": h2, "R H#3 [n]": h3,
                       "R skewness [a]": skew, "R kurtosis [a]": kurt})
        for grp in groups:
            sig = [cand[k] for k in grp]
            a, z = peak_idx[sig[0]] - margin, peak_idx[sig[-1]] + margin
            # LabVIEW Array Subset on the region data: negative index -> 0 with the
            # length kept, and the window is truncated at the region end
            lo = max(a - r * rrp, 0)
            b_win = x[lo:lo + (z - a)]
            b_bl, b_std = burst_signal_analysis(b_win)
            b_h1, b_h2 = signal_h1_h2(b_win)
            peak_val = raw[peak_idx[sig]] * 1000
            b_rows.append({
                "B Start [s]": a / fs,
                "B End [s]": z / fs,
                "B time length [ms]": (z - a) / fs * 1000,
                "B BL [pA]": b_bl,
                "B STD [pA]": b_std,
                "Region BL [pA]": region_bl,
                "Region STD [pA]": region_std,
                "R # [n]": r,
                "R S_IDX [n]": grp[0],
                "R E_IDX [n]": grp[-1],
                "B Signal Count [n]": len(grp),
                "B Signal MAX [pA]": peak_val.max(),
                "B Signal Average [pA]": peak_val.mean(),
                # LabVIEW offsets the candidate index by the number of signals before this region
                "S S_IDX [n]": n_before + grp[0],
                "S E_IDX [n]": n_before + grp[-1],
                "B Peak Span [s]": (peak_idx[sig[-1]] - peak_idx[sig[0]]) / fs,
                "B H1 [n]": b_h1,
                "B H2 [n]": b_h2,
            })

        for s, e, p in events:
            # S_ASD Each Signal SAVE: window of +/- ceil(TL/10ms)*100 points around
            # the peak, clipped to the region (its size is "Signal Region Point")
            half = int(np.ceil((e - s) / fs * 1000 * 10 / 100)) * 100
            lo = max(p - half, 0)
            s_h1, s_h2 = signal_h1_h2(x[lo:lo + 2 * half])
            s_rows.append({
                "S Peak Position [s]": (r * rrp + p) / fs,
                "Signal [pA]": sub[p] * 1000,
                "Region BL [pA]": region_bl,
                "Region STD [pA]": region_std,
                "Threshold [pA]": u_thr,
                "Signal S [s]": (r * rrp + s) / fs,
                "Signal E [s]": (r * rrp + e) / fs,
                "S TL [ms]": (e - s) / fs * 1000,
                "RT BL [pA]": est_bl[p] * 1000,
                "Signal Region Point [n]": min(lo + 2 * half, rrp) - lo,
                "H1 [n]": s_h1,
                "H2 [n]": s_h2,
            })

    r_table = pd.DataFrame(r_rows)
    s_table = pd.DataFrame(s_rows)
    if len(s_table):
        s_table.insert(0, "S # [n]", range(len(s_table)))
        s_table["S DL [s]"] = s_table["S Peak Position [s]"].diff().fillna(s_table["S Peak Position [s]"])
    b_table = pd.DataFrame(b_rows)
    if len(b_table):
        b_table.insert(0, "B # [n]", range(len(b_table)))

    # ASD total S analysis.vi
    # numpy float: a single-region file divides by 0 -> Inf/NaN like LabVIEW instead of raising
    total_time = np.float64((len(r_table) - 1) * rrp / fs)
    b_region_time = b_table["B Peak Span [s]"].sum() if len(b_table) else 0.0
    ar_table = pd.DataFrame([{
        "Burst#Sum": len(b_table),
        "Signal#Sum": len(s_table),
        "R B_SCount [n] in": n_cand_total,
        "B Region Time [s]": b_region_time,
        "Signal Freq [/s]": len(s_table) / total_time,
        "Datapoint": len(raw),
        "Region STD Mode [pA]": lv_mode(r_table["Region STD [pA]"].to_numpy()),
        "Region BL Mode [pA]": lv_mode(r_table["Region BL [pA]"].to_numpy()),
        "Region Count Mode [a]": lv_mode(r_table["R Signal Count [n]"].to_numpy(dtype=float)),
        "B Duty Cycle [%]": b_region_time / total_time * 100,
        "Total Time [s]": total_time,
    }])
    return {"R Table": r_table, "S Table": s_table, "B Table": b_table, "AR Table": ar_table}


def long_path(path):
    """Windows: allow paths over 260 characters (long raw file names)."""
    p = os.path.abspath(path)
    if os.name == "nt" and len(p) >= 248 and not p.startswith("\\\\?\\"):
        p = "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p
    return p


def read_raw(path, group=None, channel="Ch1"):
    """Read the raw current channel [nA] from a TDMS file (first group by default)."""
    t = TdmsFile.read(long_path(path))
    g = t[group] if group else t.groups()[0]
    return g[channel][:]


if __name__ == "__main__":
    import sys
    tables = first_step(read_raw(sys.argv[1]))
    print(tables["AR Table"].T)
