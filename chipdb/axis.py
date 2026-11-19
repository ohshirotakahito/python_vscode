"""グラフの目盛り: 切りのよい数値の目盛りと、日時の目盛り（解析・トレンド）。"""
from datetime import datetime, timedelta
import math

STEPS = (1, 2, 2.5, 5, 10)


def nice_ticks(low, high, max_intervals=6):
    """low〜high を含む、切りのよい間隔（1・2・2.5・5 × 10^n）の目盛り。(下端, 上端, 間隔, 目盛りの一覧)。

    区間が max_intervals 以内に収まる中で最も細かい間隔を選ぶ。範囲が大きければ整数（0, 20, 40 …）、
    小さければ 0.2・0.25 などになる。両端も目盛りに合わせて広げる。"""
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError('有限の値を渡してください')
    if high < low:
        low, high = high, low
    if high == low:                                   # 1 点だけ・すべて同じ値: 前後に幅を持たせる
        pad = abs(low) * 0.1 or 1.
        low, high = low - pad, high + pad
    power = 10 ** math.floor(math.log10((high - low) / max_intervals))
    for step in (s * power * 10 ** k for k in range(3) for s in STEPS):
        start = math.floor(low / step + 1e-9) * step
        end = math.ceil(high / step - 1e-9) * step
        if round((end - start) / step) <= max_intervals:
            break
    n = int(round((end - start) / step))
    ticks = [round(start + i * step, 10) for i in range(n + 1)]
    return start, end, step, ticks


def tick_label(value, step):
    """間隔に合わせた小数の桁で（整数の間隔なら小数なし）。"""
    if step >= 1 and float(value).is_integer():
        return f'{value:.0f}'
    decimals = max(0, -math.floor(math.log10(step) + 1e-9)) + (1 if round(step / 10 ** math.floor(math.log10(step)), 6) == 2.5 else 0)
    return f'{value:.{decimals}f}'


def parse_time(text):
    """履歴の日時 '20240220_0838'（Experiment 行の 3 列目）→ datetime。読めなければ None。"""
    text = str(text).strip().replace(':', '')
    try:
        return datetime.strptime(text[:13], '%Y%m%d_%H%M')
    except ValueError:
        return None


def _add_months(moment, months):
    total = moment.year * 12 + moment.month - 1 + months
    return moment.replace(year=total // 12, month=total % 12 + 1, day=1, hour=0, minute=0)


def time_ticks(first, last, count=6):
    """first〜last の日時の目盛り: 範囲に応じて 年・月・日・時 の切りのよい区切り。(目盛りの一覧, 書式)。"""
    if last < first:
        first, last = last, first
    span = last - first
    if span >= timedelta(days=365 * 2):
        years = next(s for s in (1, 2, 5, 10, 20) if span.days / 365 / s <= count)
        start = first.replace(month=1, day=1, hour=0, minute=0)
        start = start.replace(year=start.year - start.year % years)
        ticks, moment = [], start
        while moment <= last:
            if moment >= first:
                ticks.append(moment)
            moment = moment.replace(year=moment.year + years)
        return ticks, '%Y'
    if span >= timedelta(days=60):
        months = next(s for s in (1, 2, 3, 6, 12) if span.days / 30.4 / s <= count)
        moment = _add_months(first, 0)
        moment = _add_months(moment, -((moment.month - 1) % months))
        ticks = []
        while moment <= last:
            if moment >= first:
                ticks.append(moment)
            moment = _add_months(moment, months)
        return ticks, '%Y-%m'
    if span >= timedelta(days=2):
        days = next(s for s in (1, 2, 5, 7, 14, 30) if span.days / s <= count)
        moment = first.replace(hour=0, minute=0) + timedelta(days=1)
        ticks = []
        while moment <= last:
            ticks.append(moment)
            moment += timedelta(days=days)
        return ticks, '%m-%d'
    hours = next(s for s in (1, 2, 3, 6, 12, 24) if span.total_seconds() / 3600 / s <= count)
    moment = first.replace(minute=0) + timedelta(hours=1)
    moment -= timedelta(hours=moment.hour % hours)
    ticks = []
    while moment <= last:
        if moment >= first:
            ticks.append(moment)
        moment += timedelta(hours=hours)
    return ticks, '%m-%d %H:%M'
