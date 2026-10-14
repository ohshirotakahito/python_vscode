"""Estimate the lower-amplitude background mode of positive pulse data."""

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import curve_fit
from scipy.signal import find_peaks, peak_widths


def analysis_bounds(start, length, total, view_start, view_size):
    """Keep a crop's estimation context identical to its parent view."""
    end = start + length
    view_end = min(total, view_start + view_size)
    if view_start <= start and end <= view_end:
        return view_start, view_end
    return (start // view_size * view_size,
            min(total, ((end + view_size - 1) // view_size) * view_size))


def lower_mode_noise(residual):
    """Return (background offset, sigma); reject unresolvable distributions.

    Fit a Gaussian to the lowest well-supported histogram mode, rather than
    taking the most frequent mode. Input must be detrended, unsmoothed data.
    This assumes positive-going pulses and a roughly Gaussian background.
    """
    values = np.asarray(residual, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 100 or np.ptp(values) == 0:
        raise ValueError("背景成分の推定に十分な点数・変動がありません")
    lower, upper = np.quantile(values, [0.001, 0.999])
    if upper <= lower:
        raise ValueError("背景分布が退化しています")
    hist, edges = np.histogram(values, bins=192, range=(lower, upper))
    centers = (edges[:-1] + edges[1:]) / 2
    width = edges[1] - edges[0]
    smooth = gaussian_filter1d(hist.astype(float), 1.2)
    peaks, _ = find_peaks(smooth, prominence=max(2.0, smooth.max() * 0.04), distance=6)
    if not len(peaks):
        raise ValueError("背景の山を分離できません")
    for peak in peaks:  # Amplitude order, not histogram height order.
        half_width = float(peak_widths(smooth, [peak], rel_height=0.5)[0][0])
        radius = max(4, int(np.ceil(half_width)))
        left, right = max(0, peak-radius), min(len(hist), peak+radius+1)
        other = peaks[peaks < peak]
        if len(other):
            left = max(left, (int(other[-1])+int(peak))//2+1)
        other = peaks[peaks > peak]
        if len(other):
            right = min(right, (int(other[0])+int(peak))//2)
        if right-left < 7 or hist[left:right].sum() < max(50, len(values)*0.01):
            continue
        x, observed = centers[left:right], hist[left:right].astype(float)
        def gaussian(x, height, mean, sigma, floor):
            return height*np.exp(-0.5*((x-mean)/sigma)**2)+floor
        try:
            fit, _ = curve_fit(
                gaussian, x, observed,
                p0=(smooth[peak], centers[peak], max(width, half_width*width/2.355), 0),
                bounds=([0, centers[peak]-2*width, width/2, 0],
                        [np.inf, centers[peak]+2*width, (upper-lower)/2, np.inf]),
                maxfev=3000,
            )
        except (RuntimeError, ValueError):
            continue
        predicted = gaussian(x, *fit)
        variation = np.sum((observed-observed.mean())**2)
        quality = 1-np.sum((observed-predicted)**2)/variation if variation else 0
        if quality >= 0.6 and np.isfinite(fit).all():
            return float(fit[1]), float(fit[2])
    raise ValueError("低振幅側の背景成分を信頼できる精度で推定できません")
