"""Python ports of the NI analysis VIs used for the histogram peak columns.

Peak Detector.vi was reproduced from lvanlys.dll (PeakD2014) and checked bit for bit against
LabVIEW 2018 (via ActiveX) on random inputs; Histogram.vi and Skewness and Kurtosis.vi were
checked the same way.  ni_peak_filters.npz holds LabVIEW's own quadratic-fit filters
(Real PseudoInverse Matrix.vi of the centred Vandermonde matrix) for widths 3..400 so that exact
ties between neighbouring fits break the same way as in LabVIEW; wider windows fall back to numpy.
"""
import os

import numpy as np

_FILTERS = None
_EPS = 2.220446049250313e-16


def _filter(w):
    global _FILTERS
    if _FILTERS is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ni_peak_filters.npz")
        try:
            with np.load(path) as z:
                _FILTERS = {int(k[1:]): z[k] for k in z.files}
        except OSError:
            _FILTERS = {}
    if w in _FILTERS:
        return _FILTERS[w]
    t = np.arange(w) - (w - 1) / 2
    return np.linalg.pinv(np.vstack([np.ones(w), t, t * t]).T)


def histogram(x, intervals):
    """Histogram.vi: counts and bin centres (bin = floor((x - min) * (m / (max - min))))."""
    x = np.asarray(x, float)
    m = int(intervals)
    if m < 1 or len(x) == 0:
        return np.zeros(0), np.zeros(0)
    mn, mx = x.min(), x.max()
    if mx == mn:
        k = np.full(len(x), m - 1)          # LabVIEW puts a constant array in the last bin
    else:
        k = np.minimum(np.floor((x - mn) * (m / (mx - mn))).astype(int), m - 1)
    centres = mn + (mx - mn) / m * (np.arange(m) + 0.5)
    return np.bincount(k, minlength=m).astype(float), centres


def peak_detector(x, threshold, width, valleys=False):
    """Peak Detector.vi with initialize = end of data = True.

    Returns (# found, locations, amplitudes, 2nd derivatives).
    A quadratic is fitted to every window of `width` points.  Width <= 4: every window with the
    right curvature is a candidate; otherwise only windows where the slope sign flips.  A vertex
    left of the window centre by more than 0.5 is compared with the previous window and the
    vertex closer to its own centre is kept; it must lie within (width-1)/4 of the centre and its
    amplitude must pass the threshold.  Peaks closer than 1.0 are merged (larger one kept).
    """
    x = np.asarray(x, float)
    w = max(int(width), 3)
    n = len(x)
    empty = (0, np.zeros(0), np.zeros(0), np.zeros(0))
    if n < w:
        return empty
    F = _filter(w)
    offset = 0.0
    win = np.lib.stride_tricks.sliding_window_view(x, w)
    scale = np.ones(len(win))
    if x.max() - x.min() < 1e-8:
        # nearly flat input: the DLL removes the mean and normalises every window by |max|
        offset = x.sum() / n
        win = np.lib.stride_tricks.sliding_window_view(x - offset, w)
        scale = np.maximum(np.abs(win.min(axis=1) if valleys else win.max(axis=1)), _EPS)
        win = win / scale[:, None]
    C = np.zeros((len(win), 3))
    for j in range(w):                      # sequential sums, same rounding as the DLL
        C += win[:, j:j + 1] * F[:, j]
    Cp = C * scale[:, None]                 # coefficients kept as "previous window"

    rising = np.where(np.abs(C[:, 1]) > 1e-10, C[:, 1] > 0, C[:, 2] > 0)
    if w <= 4:
        cand = (C[:, 2] > 0) if valleys else (C[:, 2] < 0)
    else:
        cand = np.zeros(len(C), bool)
        cand[1:] = rising[1:] != rising[:-1]
    hw = float(w - 1) * 0.5
    lim = 0.5 * hw + 2.220446049250313e-15
    thr = threshold - offset
    locs, amps, d2s = [], [], []
    for i in np.flatnonzero(cand):
        c0, c1, c2 = C[i]
        dd = c2 + c2
        if abs(dd) < 1e-10:
            continue
        v = -(c1 / dd)
        amp = (c2 * v + c1) * v + c0
        k, f = i, scale[i]
        if i > 0 and v < -0.5:
            p0, p1, p2 = Cp[i - 1]
            pd = p2 + p2
            if pd == 0:
                continue
            pv = -(p1 / pd)
            if abs(v) > abs(pv):
                v, amp, dd, k, f = pv, (p2 * pv + p1) * pv + p0, pd, i - 1, 1.0
        if not abs(v) <= lim:
            continue
        if not ((dd > 0 and thr > f * amp) if valleys else (dd < 0 and f * amp > thr)):
            continue
        loc = (k + hw) + v
        amp, d2 = f * amp + offset, f * dd
        if locs and abs(locs[-1] - loc) < 1.0:
            if (amp < amps[-1]) if valleys else (amp > amps[-1]):
                locs[-1], amps[-1], d2s[-1] = loc, amp, d2
        else:
            locs.append(loc)
            amps.append(amp)
            d2s.append(d2)
    return len(locs), np.array(locs), np.array(amps), np.array(d2s)


def skewness_kurtosis(x):
    """Skewness and Kurtosis.vi, Weighting = Sample (G1 and G2 + 3).

    With 2 or 3 points the formulas divide by zero; LabVIEW then returns NaN or +/-Inf depending
    on rounding noise, which is not reproduced exactly."""
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return np.nan, np.nan
    d = x - x.sum() / n
    m2 = (d * d).sum() / n
    with np.errstate(divide="ignore", invalid="ignore"):
        g1 = ((d * d * d).sum() / n) / (m2 * np.sqrt(m2))
        b2 = ((d * d * d * d).sum() / n) / (m2 * m2)
        skew = g1 * np.sqrt(n * (n - 1.0)) / (n - 2.0)
        kurt = ((n + 1.0) * b2 - 3.0 * (n - 1.0)) * (n - 1.0) / ((n - 2.0) * (n - 3.0)) + 3.0
    return float(skew), float(kurt)


def lv_round(v):
    """LabVIEW DBL -> I32 conversion (round half to even)."""
    return int(np.round(v))


def histogram_peak_count(x, intervals, threshold, width):
    """# found of Peak Detector on Histogram(x, intervals) counts."""
    h, _ = histogram(x, intervals)
    return peak_detector(h, threshold, width)[0]


def signal_h1_h2(x):
    """ASD Signal Analysis VI.vi: (H1, H2) of a signal / burst window of N points.

    H1 = peaks of Histogram(N/5) with width N/25, threshold 5;
    H2 = peaks of Histogram(N/2) with width N/25, threshold N/25."""
    n = len(x)
    if n == 0:
        return 0, 0
    w = lv_round(n / 25)
    return (histogram_peak_count(x, lv_round(n / 5), 5.0, w),
            histogram_peak_count(x, lv_round(n / 2), n / 25, w))


def region_h_peaks(x):
    """ASD BL estimation VI.vi HPeak cluster + Potential Baseline.

    Returns (H#1, H#2, H#3, min mode peak [same unit as x]):
    H#1 = Histogram(100) peaks, threshold 20, width 10 (its first location gives the baseline);
    H#2 / H#3 = Histogram(1000) peaks with threshold 20 width 50 / threshold 10 width 20."""
    h100, centres = histogram(x, 100)
    n1, loc1, _, _ = peak_detector(h100, 20.0, 10)
    h1000, _ = histogram(x, 1000)
    n2 = peak_detector(h1000, 20.0, 50)[0]
    n3 = peak_detector(h1000, 10.0, 20)[0]
    # Index Array of an empty array gives 0; the location is rounded to an integer index
    idx = lv_round(loc1[0]) if n1 else 0
    base = centres[idx] if 0 <= idx < len(centres) else 0.0
    return n1, n2, n3, base
