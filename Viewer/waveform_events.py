"""Saved ANAL event rows and explicitly requested viewer detection previews."""
import math
from types import SimpleNamespace

import numpy as np


def saved_events(path):
    from nptdms import TdmsFile
    with TdmsFile.open(path) as tdms:
        names = {g.name for g in tdms.groups()}
        if not names & {'S Table', 'B Table'}:
            return None
        result = {'signals': [], 'bursts': [], 'skipped': 0}
        for group_name, kind in [('S Table', 'signals'), ('B Table', 'bursts')]:
            if group_name not in names:
                continue
            group = tdms[group_name]
            columns = {c.name: c[:] for c in group.channels()}
            length = max((len(v) for v in columns.values()), default=0)
            def number(column, index, default=None):
                if column not in columns or index >= len(columns[column]):
                    return default
                try:
                    value = float(columns[column][index])
                    return value if math.isfinite(value) else default
                except (ValueError, TypeError):
                    return default
            for i in range(length):
                signal = kind == 'signals'
                start = number('Signal S [s]' if signal else 'B Start [s]', i)
                duration = number('S TL [ms]' if signal else 'B time length [ms]', i)
                end = number('Signal E [s]' if signal else 'B End [s]', i,
                             start + duration / 1000 if start is not None and duration is not None else None)
                if start is None or end is None or end <= start:
                    result['skipped'] += 1
                    continue
                result[kind].append(dict(id=number('S # [n]' if signal else 'B # [n]', i, i),
                    start=start, end=end, duration_ms=(end-start)*1000,
                    peak_time=number('S Peak Position [s]', i, (start+end)/2),
                    value=number('Signal [pA]' if signal else 'B Signal Count [n]', i),
                    baseline=number('Region BL [pA]' if signal else 'B BL [pA]', i)))
        return result


def preview_signals(pulses, data, offset, dt):
    rows = []
    for i, pulse in enumerate(pulses):
        start, end = offset + pulse['start_index'], offset + pulse['end_index']
        peak = start + int(np.argmax(data[start:end]))
        rows.append(dict(id=i, start=start*dt, end=end*dt, duration_ms=(end-start)*dt*1000,
                         peak_time=peak*dt, value=float(data[peak])*1000,
                         baseline=pulse['baseline']*1000))
    return rows


def preview_bursts(signals, gap_ms=10., margin_ms=20., total_time=None):
    if not math.isfinite(gap_ms) or gap_ms <= 0 or not math.isfinite(margin_ms) or margin_ms < 0:
        raise ValueError('バースト間隔は正数、前後余白は0以上の数値で指定してください。')
    groups, current = [], []
    for row in sorted(signals, key=lambda r: r['peak_time']):
        if current and row['peak_time'] - current[-1]['peak_time'] > gap_ms / 1000:
            if len(current) >= 2:
                groups.append(current)
            current = []
        current.append(row)
    if len(current) >= 2:
        groups.append(current)
    result = []
    for rows in groups:
        start = max(0., min(rows[0]['start'], rows[0]['peak_time'] - margin_ms / 1000))
        end = max(rows[-1]['end'], rows[-1]['peak_time'] + margin_ms / 1000)
        if total_time is not None:
            end = min(end, total_time)
        result.append(dict(id=len(result), start=start, end=end, duration_ms=(end-start)*1000,
                           value=len(rows), baseline=float(np.mean([r['baseline'] for r in rows]))))
    return result


def detect_file(data, dt, settings, cancelled, progress=None):
    from tdms_signal_core import RobustTdmsMultiFolderBrowser, detect_pulses_hysteresis
    chunk = max(1, int(settings['chunk_size']))
    detector = SimpleNamespace(**settings, data=data, dt=dt, n=len(data), detection_enabled=True,
                               chunk_idx=0, _view_start_override=0)
    signals = []
    overlap = max(settings['baseline_window'], settings['sg_window'], settings['pad_for_std'],
                  int(.05 / dt))
    for start in range(0, len(data), chunk):
        if cancelled.is_set():
            return None
        end = min(start + chunk, len(data))
        lo, hi = max(0, start-overlap), min(len(data), end+overlap)
        detector._view_start_override = start
        y = np.asarray(data[lo:hi], dtype=float)
        baseline, _, _, noise = RobustTdmsMultiFolderBrowser._compute_baseline_threshold(detector, y, lo)
        pulses, _, _ = detect_pulses_hysteresis(y, baseline, noise, dt=dt,
            threshold_k1=settings['threshold_k1'], threshold_k2=settings['threshold_k2'],
            min_width_ms=settings['min_width_ms'])
        for row in preview_signals(pulses, data, lo, dt):
            peak_index = round(row['peak_time'] / dt)
            if start <= peak_index < end:
                row['id'] = len(signals)
                signals.append(row)
        if len(signals) > 200000:
            raise ValueError('検出が20万件を超えました。検出閾値を上げてください。')
        if progress:
            progress(end / max(len(data), 1))
    return signals


def jump_to_event(browser, row):
    from tdms_signal_core import MAX_VIEW_SEC
    total = browser.n * browser.dt
    start, end = max(0., row['start']), min(total, row['end'])
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        raise ValueError('この行の時間範囲は読み込んだ波形の範囲外です。')
    width = min(MAX_VIEW_SEC, total, max(.02, (end-start)*1.4, browser.dt*3))
    left = max(0., min(total-width, (start+end-width)/2))
    browser._view_start_override = int(left / browser.dt)
    browser.on_window_size_submit(str(width), preserve_selection=True)
    browser.on_select(start, end)
    browser.draw()
