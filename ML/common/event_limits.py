# -*- coding: utf-8 -*-
"""学習に使うイベントの範囲条件（全パイプラインで共通）。

rmc（tsfresh）と rmb（traditional）で同じ条件にして、パイプラインを公平に比較するため。
境界の扱いも共通: 継続時間・ベースラインは両端を含まない整数範囲、信号強度は両端を含まない。
"""
import numpy as np
import pandas as pd

DURATION_LIMIT = (5, 1000)
BASELINE_LIMIT = (-300, 1000)
SIGNAL_LIMIT = (0, 1000)


def event_mask(duration, baseline, signal, duration_limit=None, baseline_limit=None, signal_limit=None):
    """範囲条件を満たすイベントのマスク（pandas Series）。"""
    duration_limit = duration_limit or DURATION_LIMIT
    baseline_limit = baseline_limit or BASELINE_LIMIT
    signal_limit = signal_limit or SIGNAL_LIMIT
    duration = pd.to_numeric(duration, errors='coerce')
    baseline = pd.to_numeric(baseline, errors='coerce')
    signal = pd.to_numeric(signal, errors='coerce')
    return (duration.between(duration_limit[0] + 1, duration_limit[1] - 1)
            & baseline.between(baseline_limit[0] + 1, baseline_limit[1] - 1)
            & signal.between(signal_limit[0] + 1e-12, signal_limit[1] - 1e-12)
            & np.isfinite(duration) & np.isfinite(baseline) & np.isfinite(signal))
