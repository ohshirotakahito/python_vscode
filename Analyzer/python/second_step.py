"""Python port of "20180801 2nd Step Analysis.vi" (base calling of burst regions).

Input is a 1st step result file (A@@...tdms: raw data + S/R/B tables).
Produces the BS, LSP, LCP, LTP, LAP, LL and BR tables; saving is in
second_step_io.py.  Graphs are not ported.
"""
import numpy as np
import pandas as pd
from nptdms import TdmsFile

from first_step import long_path

# BC Table defaults: relative level of each base (B = baseline) and its STD factor
DEFAULT_BASES = {"G": 1.0, "A": 0.7, "C": 0.6, "U": 0.38, "B": 0.0}


def base_bits(bases):
    """Base Combination [n] bits: 1, 2, 4, ... in Base Table order (B excluded), e.g. G A C U M -> 1 2 4 8 16."""
    return {b: 1 << i for i, b in enumerate(b for b in bases if b != "B")}
DEFAULT_CONDITION = {
    "sample_name": "",
    "sample_sequence": "",
    "right_read_min": 5,
    "noise_max": 3,
    "filter_p": 0,
    "bases": DEFAULT_BASES,
    "fs": 10000,
}


def lv_round(x):
    """LabVIEW To I32: round half to even."""
    return int(np.rint(x))


def lv_multimode_first(x, intervals=100):
    """NI Mode.vi (Multimodal), first returned mode.

    Bin = floor((x - min) / width) as NI does (first_step.lv_mode); np.histogram's edge arithmetic puts values
    sitting exactly on a bin edge into the other bin, which changes the burst baseline."""
    mn, mx = x.min(), x.max()
    w = (mx - mn) / intervals
    if w == 0:
        return mn
    h = np.bincount(np.minimum(np.floor((x - mn) / w).astype(int), intervals - 1), minlength=intervals)
    return mn + w * (np.argmax(h) + 0.5)


def segment_means(x, div):
    """Base Level Estimation.vi: round(n/div) blocks of round(n/blocks) points (last one may be short)."""
    n = len(x)
    cut = lv_round(n / div)
    length = lv_round(n / cut)
    return np.array([x[i * length:(i + 1) * length].mean() for i in range(cut) if i * length < n])


def burst_levels(x, next_b):
    """Base Level Estimation.vi + STD recal.vi for one burst window [pA].

    `next_b` holds B BL / Region BL / B STD / Region STD of the *next* B Table
    row: LabVIEW (BSD BR all info cluster) reads them one row ahead, and gets
    0 for the last burst.
    """
    bl = min(lv_multimode_first(x),
             lv_multimode_first(segment_means(x, 5)),
             lv_multimode_first(segment_means(x, 2)),
             next_b["Region BL"], next_b["B BL"])
    mx = bl + 0.9 * (x.max() - bl)
    with np.errstate(divide="ignore"):
        sn = abs(mx / min(next_b["Region STD"], next_b["B STD"]))  # LabVIEW reports S/N as a positive value
    # per point probabilities use the next B STD, change point ones the next Region STD
    return {"BL": bl, "Mx": mx, "I": mx - bl, "sigma": next_b["B STD"] / mx, "SN": sn,
            "sigma_cp": next_b["Region STD"] / mx,
            "MAX": x.max(), "MIN": x.min()}


def base_probability(rel, bases, sigma):
    """Base Probability calculation.vi: Normal PDF of each base level, normalized per point.

    A point far from every level (e.g. a spike) underflows to 0/0 and stays NaN, as in LabVIEW."""
    levels = np.array(list(bases.values()))
    with np.errstate(divide="ignore", invalid="ignore", under="ignore"):
        pdf = np.exp(-0.5 * ((rel[:, None] - levels[None, :]) / sigma) ** 2)
        return pdf / pdf.sum(axis=1, keepdims=True)


def runs(a):
    """[(start, end)] of runs of equal values."""
    edges = np.nonzero(a[1:] != a[:-1])[0] + 1
    starts = np.concatenate([[0], edges])
    ends = np.concatenate([edges - 1, [len(a) - 1]])
    return list(zip(starts, ends))


def q_value(p):
    """Phred quality, capped at 100 (P = 1)."""
    with np.errstate(divide="ignore"):
        return np.minimum(-10 * np.log10(1 - p), 100.0)


def ascii_q(q):
    """FASTQ style quality characters (Q capped at 40 -> 'I'; NaN counts as 0 -> '!', as in LabVIEW)."""
    return "".join(chr(min(lv_round(v) if np.isfinite(v) else 0, 40) + 33) for v in q)


def collapse(seq):
    """Merge runs of the same base ("UUUACU" -> "UAC U")."""
    return "".join(c for i, c in enumerate(seq) if i == 0 or seq[i - 1] != c)


DIR_53, DIR_35 = "5’→3'", "3'→5'"


def _longest_match(seq, ref):
    """(length, start in ref, start in seq) of the longest substring of seq found in ref.

    The first one in seq wins ties.
    """
    best = (0, 0, 0)
    for i in range(len(seq)):
        for j in range(i + best[0] + 1, len(seq) + 1):
            p = ref.find(seq[i:j])
            if p < 0:
                break
            best = (j - i, p, i)
    return best


def right_read(seq, sample):
    """Assembly / Right Read: longest stretch of the signal matching the sample.

    The match is searched in the homopolymer-collapsed sample, forward and in
    the reversed sample (3'->5'), the forward one winning ties.  It is mapped
    back to the full sample: it starts at the last base of the run of its first
    base and is extended to the end of the run of its last base.
    """
    cut = collapse(sample)
    fl, fp, fi = _longest_match(seq, cut)
    rl, rp, ri = _longest_match(seq, cut[::-1])
    if fl >= rl:
        length, s, at, direction = fl, fp, fi, DIR_53
    else:
        # LabVIEW looks the reversed match up in the sample from its 5' end: of several places it takes the first
        length, s, at, direction = rl, cut.find(seq[ri:ri + rl][::-1]), ri, DIR_35
    e = s + length - 1  # 0-based positions in cut
    # run boundaries of the full sample for every base of cut
    run_s = [i for i in range(len(sample)) if i == 0 or sample[i - 1] != sample[i]]
    run_e = [i - 1 for i in run_s[1:]] + [len(sample) - 1]
    ms, me = (run_e[s], run_e[e]) if length else (0, -1)
    return {
        "Mx Right Read Seq": cut[s:e + 1],
        "Mx Right No": length,
        "Direction": direction,
        "Front Seq": cut[:s],
        "Seq": cut[s:e + 1],
        "Later Seq": cut[e + 1:],
        "Seq SNo": s + 1,
        "Seq ENo": e + 1,
        "MSeq_SNo": ms + 1,
        "MSeq_ENo": me + 1,
        "M_RRead_Seq": sample[ms:me + 1],
        "M_MxRight_No": me - ms + 1,
        "_match_at": at,  # start of the match in the signal sequence
    }


def analyse_burst(x, next_b, bases):
    """One burst: levels, per point assignment and the signals (runs of non-B points).

    Points whose base probability is NaN (0/0 far from every level, or every point of the last burst, which
    has no next B Table row) are removed before the assignment, as LabVIEW does: the indices and times of the
    following points move up. The levels, Region MAX / MIN and Region Point use the whole window.
    Returns the kept points as x. LabVIEW still reads the probabilities and Q values from the arrays of the
    whole window, at the indices of the kept points: prob_full / pmax_full keep every point (NaN rows included)
    for that."""
    lv = burst_levels(x, next_b)
    # LabVIEW scales the relative level so that Mx sits at the highest base level (e.g. 1.2 with M = 5mC),
    # while the assigned levels (_AL) stay BL + level * I
    rel = (x - lv["BL"]) / lv["I"] * max(bases.values())
    names = np.array(list(bases))
    prob_full = base_probability(rel, bases, lv["sigma"])
    pmax_full = prob_full.max(axis=1) if len(x) else np.array([])
    keep = np.isfinite(prob_full).all(axis=1)
    x, rel, prob = x[keep], rel[keep], prob_full[keep]
    assigned = names[prob.argmax(axis=1)] if len(x) else np.array([], dtype=names.dtype)
    pmax = prob.max(axis=1) if len(x) else np.array([])
    # change point number of each point: starts at 1 and counts base changes,
    # with the base before the window taken as "B"
    prev = np.concatenate([["B"], assigned[:-1]])[:len(assigned)]
    cp_no = 1 + np.cumsum(assigned != prev)

    signals = []
    n, i = len(x), 0
    while i < n:
        if assigned[i] == "B":
            i += 1
            continue
        j = i
        while j < n and assigned[j] != "B":
            j += 1
        if j < n:  # a signal still open at the window end is dropped
            signals.append((i, j - 1))
        i = j
    return lv, x, rel, prob, assigned, pmax, cp_no, signals, prob_full, pmax_full


def read_first_step(path):
    """Raw data [nA] and the 1st step tables (as strings) of an A@@ file."""
    t = TdmsFile.read(long_path(path))
    tables = {}
    for g in ("S Table", "R Table", "B Table", "AR Table", "AC Table"):
        channels = t[g].channels() if g in t else []
        if channels:
            tables[g] = pd.DataFrame({c.name: c[:] for c in channels})
        else:
            # LabVIEW writes no channels for a table without rows (e.g. 0 bursts); the names stay in Table Tag
            names = list(t["Table Tag"][g][:]) if "Table Tag" in t and g in t["Table Tag"] else []
            tables[g] = pd.DataFrame(columns=names)
    return t["Data"]["Ch1"][:], tables


def condition_from_bc_table(bc):
    """Settings of a LabVIEW BC Table (B@@ file) -> condition dict."""
    row = bc.iloc[0]
    bases = {col.split("_")[0]: float(row[col]) for col in bc.columns if col.endswith("_Relative G")}
    return {
        "sample_name": row["Sample name"],
        "sample_sequence": row["Sample Sequence"],
        "right_read_min": int(row["Right Read MIN [n]"]),
        "noise_max": int(row["noise MAX [n]"]),
        "filter_p": int(row["Filter P [n]"]),
        "bases": bases,
    }


def second_step(raw, tables, cond=None, file_name="", progress=None):
    """`file_name`: name of the A@@ file, copied into the tables;
    `progress(done, total)` is called for every burst."""
    c = dict(DEFAULT_CONDITION, **(cond or {}))
    fs, bases = c["fs"], c["bases"]
    bt = tables["B Table"]
    f = lambda col: bt[col].astype(float).to_numpy()
    b_start, b_end = f("B Start [s]"), f("B End [s]")
    nxt = {"B BL": f("B BL [pA]"), "Region BL": f("Region BL [pA]"),
           "B STD": f("B STD [pA]"), "Region STD": f("Region STD [pA]")}

    bs_rows, bursts = [], []
    for k in range(len(bt)):
        if progress:
            progress(k, len(bt))
        s0, e0 = lv_round(b_start[k] * fs), lv_round(b_end[k] * fs)
        # LabVIEW Array Subset: a negative start (a burst starting before 0 s) reads from 0 with the same length;
        # times still count from the burst start
        x = raw[max(s0, 0):max(s0, 0) + e0 - s0 + 1] * 1000
        next_b = {key: v[k + 1] if k + 1 < len(bt) else 0.0 for key, v in nxt.items()}
        region_points = len(x)
        lv, x, rel, prob, assigned, pmax, cp_no, signals, prob_full, pmax_full = analyse_burst(x, next_b, bases)
        # probabilities and Q values are read from the whole-window arrays at the kept-point indices (LabVIEW)
        q = q_value(pmax_full)
        bits = base_bits(bases)
        levels = {b: lv["BL"] + v * lv["I"] for b, v in bases.items()}
        bursts.append(dict(x=x, lv=lv, rel=rel, prob=prob_full, assigned=assigned, pmax=pmax_full,
                           cp_no=cp_no, q=q, levels=levels, start=b_start[k]))
        for idx, (s, e) in enumerate(signals):
            per_point = "".join(assigned[s:e + 1])
            seq = collapse(per_point)
            # LabVIEW takes the probability of the point after the signal (index off by one)
            p = pmax_full[e + 1] if e + 1 < len(pmax_full) else np.nan
            if not np.isfinite(p):  # the point after the signal was removed (NaN): LabVIEW writes 0
                p = 0.0
            present = [b for b in bits if b in seq]
            bs_rows.append({
                "S#": len(bs_rows),
                "S IDX": idx + 1,
                "Assinged Sequence": seq,
                "Assigned Time": per_point,
                "Base Number": len(seq),
                "Signal Length [ms]": (e - s + 1) / fs * 1000,
                "Signal Start [s]": b_start[k] + s / fs,
                "Signal End [s]": b_start[k] + (e + 1) / fs,
                "Probability [a]": p,
                "Q value [x]": q_value(p),
                "Base Speices Number [n]": len(present),
                "Base Combination [n]": sum(bits[b] for b in present),
                "S T IDX Start [n]": s + 1,
                "S T IDX End [n]": e + 1,
                "S CP IDX Start [n]": cp_no[s],
                "S CP IDX End [n]": cp_no[e],
                "S ASCII_Q": ascii_q(q[s:e + 1]),
                **{f"{b}_n": seq.count(b) for b in bits},
                **{f"{b}_AL" if b != "B" else "BL_AL": levels[b] for b in bases},
                "BL Level [pA]": lv["BL"],
                "Region MAX [pA]": lv["MAX"],
                "Region MIN [pA]": lv["MIN"],
                "Region Point": region_points,
                "File name": file_name,
                "S/N ratio": lv["SN"],
                "Signal Intensity [pA]": lv["I"],
                "BLX Level [pA]": lv["BL"],
                "Mx Intensity level [pA]": lv["Mx"],
                **right_read(seq, c["sample_sequence"]),
                "B #": k,
            })
    bs = pd.DataFrame(bs_rows) if bs_rows else pd.DataFrame(columns=bs_columns(bases) + ["B #", "_match_at"])
    # long signals: right read of at least Right Read MIN bases get an LSP number.
    # Inside a burst they are numbered by right read length, longest first; ties
    # newest first (LabVIEW sorts ascending and reverses).
    long_sig = bs[bs["Mx Right No"] >= c["right_read_min"]]
    # LabVIEW orders the long signals of a burst by where their right read starts in the sample
    order = long_sig.sort_values(["B #", "Seq SNo", "S#"], kind="mergesort").index
    bs["LM#"] = -1
    bs.loc[order, "LM#"] = np.arange(len(order))

    lsp = bs.loc[order].drop(columns="LM#")
    lsp.insert(0, "L#", np.arange(len(order)))
    lcp, ltp, lap = long_signal_tables(lsp, bursts, c)
    drop = ["B #", "_match_at"]
    return {"BS Table": bs.drop(columns=drop), "LSP Table": lsp.drop(columns=drop),
            "LCP Table": lcp, "LTP Table": ltp, "LAP Table": lap,
            "LL Table": ll_table(lsp, bases),
            "BR Table": br_table(bs, lsp, c, np.float64(b_end[-1] if len(b_end) else 0.0))}


def _kmers(bases):
    """LL Table k-mers: single bases, then 2-mers and 3-mers without repeated neighbours."""
    nb = [b for b in bases if b != "B"]
    two = sorted(a + b for a in nb for b in nb if a != b)
    three = sorted((a + b + d for a in nb for b in nb for d in nb if a != b and b != d),
                   key=lambda k: (nb.index(k[0]), k[1:]))
    return nb + two + three


def _count(seqs, kmer):
    """Overlapping occurrences of kmer in all seqs."""
    k = len(kmer)
    return sum(sum(s[i:i + k] == kmer for i in range(len(s) - k + 1)) for s in seqs)


def ll_table(lsp, bases):
    """LL Table: k-mer counts in the long signals (all_) and in their right reads (Mx_)."""
    kmers = _kmers(bases)
    row = {f"all_{k}": _count(lsp["Assinged Sequence"], k) for k in kmers}
    row.update({f"Mx_{k}": _count(lsp["Mx Right Read Seq"], k) for k in kmers})
    return pd.DataFrame([row])


def br_table(bs, lsp, c, total_time):
    """BR Table: read statistics of the whole file.

    `total_time` is the B End of the last burst.  Several LabVIEW labels are
    swapped (Av Read / Av Right Read and the two rates); the values follow
    LabVIEW.  LabVIEW reads "RR MIN Over S#" before computing it, i.e. it
    reports the value of the previously analysed file; here the value of this
    file is used (also for Assembly Ratio 2 and RR Ratio).
    """
    noise = c["noise_max"]
    bn, mx = bs["Base Number"], bs["Mx Right No"]
    long_read, right_read = bn[bn >= noise], mx[mx >= noise]
    covered = set()
    for _, r in lsp.iterrows():
        covered.update(range(r["Seq SNo"], r["Seq ENo"] + 1))
    total_cp = len(collapse(c["sample_sequence"])) if len(bs) else 0  # LabVIEW writes 0 without signals
    rr_min_over = int((bn >= c["right_read_min"]).sum())
    n_long = len(lsp)
    with np.errstate(divide="ignore", invalid="ignore"):
        return pd.DataFrame([{
            "Signal Number # [n]": len(bs),
            "Long Signal Number # [n]": n_long,
            "MAX Right Read # [n]": int(mx.max()) if len(mx) else 0,
            " Right Read Base # [n]": len(covered),
            "Total CP Base [n]": total_cp,
            "Coverage [%]": np.float64(len(covered)) / total_cp * 100,
            "Long Read [bp]": int(long_read.sum()),
            # LabVIEW writes 0 (not NaN) for these three when there is nothing to divide by
            "Long Read Rate [kbp/day]": right_read.sum() / total_time * 86.4 if total_time else 0.0,
            "Long Right Read [bp]": int(right_read.sum()),
            "Long Right Read Rate [kbp/day]": long_read.sum() / total_time * 86.4 if total_time else 0.0,
            f"{DIR_53} direction [%]": np.float64((lsp["Direction"] == DIR_35).sum()) / n_long,
            "Av Read # [a]": right_read.mean(),
            "Av Right Read # [a]": long_read.mean(),
            "RR MIN Over S# [n]": rr_min_over,
            "Assembly Ratio 1 [%]": np.float64(n_long) / len(bs) * 100 if len(bs) else 0.0,
            "Assembly Ratio 2 [%]": np.float64(n_long) / rr_min_over * 100,
            "RR Ratio [%]": np.float64(mx.max()) / rr_min_over * 100,
        }])


# columns copied from the long signal to every LCP / LTP row
_LONG_INFO = ["Region MAX [pA]", "Region MIN [pA]", "Region Point", "File name", "S/N ratio",
              "Signal Intensity [pA]", "BLX Level [pA]", "Mx Intensity level [pA]"]
_TAIL = ["Assinged Sequence", "Mx Right Read Seq", "Mx Right No", "M_RRead_Seq", "M_MxRight_No"]


# full column lists, used for tables without rows (LabVIEW still lists every column in Table Tag)
def bs_columns(bases):
    nb = [b for b in bases if b != "B"]
    return (["S#", "S IDX", "Assinged Sequence", "Assigned Time", "Base Number", "Signal Length [ms]",
             "Signal Start [s]", "Signal End [s]", "Probability [a]", "Q value [x]", "Base Speices Number [n]",
             "Base Combination [n]", "S T IDX Start [n]", "S T IDX End [n]", "S CP IDX Start [n]",
             "S CP IDX End [n]", "S ASCII_Q"] + [f"{b}_n" for b in nb] + [f"{b}_AL" for b in nb]
            + ["BL_AL", "BL Level [pA]", *_LONG_INFO, "Mx Right Read Seq", "Mx Right No", "Direction",
               "Front Seq", "Seq", "Later Seq", "Seq SNo", "Seq ENo", "MSeq_SNo", "MSeq_ENo", "M_RRead_Seq",
               "M_MxRight_No"])


def _point_columns(bases, head):
    return (head + [f"{b}_probability" for b in bases] + [f"{b}_AL" for b in bases]
            + ["BL Level [pA]", *_LONG_INFO, "MS_IDX", "Direction", *_TAIL])


def lcp_columns(bases):
    return _point_columns(bases, ["L#", "S#", "AS IDX", "CP IDX [n]", "Assinged BaseS", "Relative Averaged Data",
                                  "Relative Assigned Level", "STD [a]", "CP Length [ms]", "Q value [x]",
                                  "Probability [a]", "CP S IDX [n]", "CP T IDX Start [n]", "CP T IDX End [n]",
                                  "CP S [ms]", "CP E [ms]", "CP ASCII_Q"])


def ltp_columns(bases):
    return _point_columns(bases, ["L#", "S#", "AS IDX", "T IDX [n]", "Assinged Base", "Time [s]", "Raw data [pA]",
                                  "Relative data [a]", "Relative Assigned [a]", "Probability [a]", "Q value [x]",
                                  "T CP IDX [n]", "T S IDX [n]", "T ASCII_Q"])


LAP_COLUMNS = ["L#", "S#", "Assigned Base", "MS_IDX", "Relative Averaged Data", "Relative Averaged Level",
               "Direction", "Base Time length [ms]", "Base Time [s]", "Signal Intensity [pA]"]


def long_signal_tables(lsp, bursts, c):
    """LCP (change points), LTP (time points) and LAP (assigned bases) of the long signals.

    Each long signal contributes the B run before and after it and the change
    points of its right read part.
    """
    fs, bases, sample = c["fs"], c["bases"], c["sample_sequence"]
    cut = collapse(sample)
    run_s = [i for i in range(len(sample)) if i == 0 or sample[i - 1] != sample[i]]
    run_e = [i - 1 for i in run_s[1:]] + [len(sample) - 1]
    names = list(bases)
    lcp, ltp, lap = [], [], []
    for _, r in lsp.iterrows():
        b = bursts[r["B #"]]
        lv, cp_no = b["lv"], b["cp_no"]
        forward = r["Direction"] == DIR_53
        cs = r["S CP IDX Start [n]"]
        first = cs + r["_match_at"]
        # (cp number, AS IDX, MS_IDX, homopolymer length in the sample, CP S IDX)
        cps = [(cs - 1, 0, 0, 1, -1), (r["S CP IDX End [n]"] + 1, len(cut) + 1, len(sample) + 1, 1, -1)]
        for j in range(r["Mx Right No"]):
            pos = r["Seq SNo"] - 1 + j if forward else r["Seq ENo"] - 1 - j  # 0-based in cut
            ms = run_s[pos] if forward else run_e[pos]
            # LAP splits a change point over its homopolymer, except the last one of the read
            homo = run_e[pos] - run_s[pos] + 1 if j < r["Mx Right No"] - 1 else 1
            cps.append((first + j, pos + 1, ms + 1, homo, r["S IDX"]))
        common = {
            **{f"{n}_AL": b["levels"][n] for n in bases},
            "BL Level [pA]": lv["BL"],
            **{col: r[col] for col in _LONG_INFO},
        }
        tail = {"Direction": r["Direction"], "Assinged Sequence": r["Assinged Sequence"],
                # LabVIEW writes the right read length in the full sample here
                "Mx Right Read Seq": r["Mx Right Read Seq"], "Mx Right No": r["M_MxRight_No"],
                "M_RRead_Seq": r["M_RRead_Seq"], "M_MxRight_No": r["M_MxRight_No"]}
        def ltp_row(t, base, as_idx, cp_idx, s_idx, ms_idx):
            return {
                "L#": r["L#"], "S#": r["S#"], "AS IDX": as_idx, "T IDX [n]": t + 1,
                "Assinged Base": base, "Time [s]": b["start"] + t / fs,
                "Raw data [pA]": b["x"][t] / 1000, "Relative data [a]": b["rel"][t],
                "Relative Assigned [a]": bases[base], "Probability [a]": b["pmax"][t],
                "Q value [x]": b["q"][t], "T CP IDX [n]": cp_idx, "T S IDX [n]": s_idx,
                "T ASCII_Q": ascii_q([b["q"][t]]),
                **{f"{n}_probability": b["prob"][t, i] for i, n in enumerate(names)},
                **common, "MS_IDX": ms_idx, **tail,
            }

        for cp, as_idx, ms_idx, homo, s_idx in cps:
            pts = np.nonzero(cp_no == cp)[0]
            if as_idx == len(cut) + 1 and len(pts) and pts[-1] == len(b["x"]) - 1:
                # the B run after the signal reaches the window end: LabVIEW finds no closing change point,
                # writes an empty LCP row, the first point of the window in LTP and a zero LAP row
                lcp.append({"L#": r["L#"], "S#": r["S#"], "AS IDX": as_idx, "MS_IDX": ms_idx, **tail, _EMPTY: 1})
                first_base = b["assigned"][0]
                row = ltp_row(0, first_base, as_idx, cp_no[0] - 1, 1 if first_base != "B" else -1, ms_idx)
                row.update({k: "" for k in ("S/N ratio", "Signal Intensity [pA]", "BLX Level [pA]",
                                            "Mx Intensity level [pA]")})
                ltp.append(row)
                lap.append({"L#": r["L#"], "S#": r["S#"], "MS_IDX": ms_idx, "Relative Averaged Data": 0.0,
                            "Relative Averaged Level": 0.0, "Direction": r["Direction"], _EMPTY: 1})
                continue
            if len(pts):
                s, e = pts[0], pts[-1]
                base = b["assigned"][s]
                rel = b["rel"][s:e + 1]
                mean = rel.mean()
                prob = base_probability(np.array([mean]), bases, lv["sigma_cp"])[0]
                p = prob[names.index(base)]
                if not np.isfinite(p):  # every base probability NaN: LabVIEW writes 0 (and Q -0)
                    p = 0.0
                q = q_value(p)
                std = np.std(rel, ddof=1) if len(rel) > 1 else np.nan
            else:
                # the signal starts at the window start: LabVIEW writes an empty B run
                s, e, base, rel, mean = 0, -1, "B", np.array([]), np.nan
                prob, p, q, std, s_idx = np.full(len(names), np.nan), 0.0, -0.0, -0.0, 0
            t_s, t_e = b["start"] + s / fs, b["start"] + (e + 1) / fs
            length = (e - s + 1) / fs * 1000
            lcp.append({
                "L#": r["L#"], "S#": r["S#"], "AS IDX": as_idx, "CP IDX [n]": cp,
                "Assinged BaseS": base, "Relative Averaged Data": mean,
                "Relative Assigned Level": bases[base],
                "STD [a]": std,
                "CP Length [ms]": length, "Q value [x]": q, "Probability [a]": p,
                "CP S IDX [n]": s_idx, "CP T IDX Start [n]": s + 1, "CP T IDX End [n]": e + 1,
                "CP S [ms]": t_s, "CP E [ms]": t_e, "CP ASCII_Q": ascii_q([q]),
                **{f"{n}_probability": prob[i] for i, n in enumerate(names)},
                **common, "MS_IDX": ms_idx, **tail,
            })
            for t in range(s, e + 1):
                ltp.append(ltp_row(t, base, as_idx, cp - 1, s_idx, ms_idx))
            # LAP: a change point covering a homopolymer of the sample is split evenly
            for h in range(homo):
                lap.append({
                    "L#": r["L#"], "S#": r["S#"], "Assigned Base": base,
                    "MS_IDX": ms_idx + (h if forward else -h) if s_idx >= 0 else ms_idx,
                    "Relative Averaged Data": mean, "Relative Averaged Level": bases[base],
                    "Direction": r["Direction"],
                    "Base Time length [ms]": length / homo,
                    "Base Time [s]": t_s + (h + 1) * (t_e - t_s) / homo if s_idx >= 0 else t_s,
                    "Signal Intensity [pA]": lv["I"],
                })
    if not len(lsp):
        return (pd.DataFrame(columns=lcp_columns(bases)), pd.DataFrame(columns=ltp_columns(bases)),
                pd.DataFrame(columns=LAP_COLUMNS))
    return _frame(lcp), pd.DataFrame(ltp), _frame(lap)


_EMPTY = "_empty"


def _frame(rows):
    """Rows -> DataFrame in the column order of a full row; cells missing from an empty row are ''."""
    full = next((r for r in rows if _EMPTY not in r), rows[0] if rows else {})
    df = pd.DataFrame([{k: r.get(k, "" if _EMPTY in r else np.nan) for k in full} for r in rows],
                      columns=list(full))
    return df


if __name__ == "__main__":
    import sys
    raw, tables = read_first_step(sys.argv[1])
    out = second_step(raw, tables)
    print(out["BS Table"].head())
