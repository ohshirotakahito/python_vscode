"""Read-only cross-operation history and one-representative-per-chip comparisons."""
from dataclasses import dataclass
import math
import statistics

from chipdb.axis import parse_time
from chipdb.summary import LIMITS

KINDS = ('FC', 'MA', 'TA', 'CAL')
REPRESENTATIVES = ('中央値', '平均', '最初の値', '最後の値')
UNITS = {'FC': 'mm', 'MA': 'µm', 'TA': 'µm', 'CAL': 'µm/V'}


def metric_label(kind, metric):
    return f'{kind} / Info {metric}' + (f' [{UNITS[kind]}]' if metric == 1 else '')


@dataclass(frozen=True)
class Observation:
    chip: str
    line: int
    kind: str
    value: float
    metric: int
    time: object
    time_source: str
    experiment: str
    operation: str
    gap: str
    operator: str


def observations(chip, rows, kind, metric=1, since=None, until=None, valid_only=False):
    if kind not in KINDS or metric not in range(1, 7):
        raise ValueError('操作とInfo列を選択してください。')
    output, omitted = [], 0
    for line, row in enumerate(rows, 1):
        if len(row) < 6 or row[0] != 'Experiment' or row[5].split('#')[0] != kind:
            continue
        try:
            value = float(row[6 + metric])
        except (ValueError, IndexError):
            omitted += 1
            continue
        if not math.isfinite(value) or valid_only and metric == 1 and not 0 < value < LIMITS[kind]:
            omitted += 1
            continue
        actual = parse_time(row[9]) if len(row) > 9 else None
        moment = actual or parse_time(row[2])
        if (since or until) and (moment is None or since and moment.date() < since or until and moment.date() > until):
            omitted += 1
            continue
        output.append(Observation(chip, line, kind, value, metric, moment,
            '実施時刻' if actual else '実験開始日時（代用）' if moment else '日時なし',
            row[4], row[5], row[6] if len(row) > 6 else '', row[3]))
    return output, omitted


def representative(points, method):
    if not points:
        return None
    if method == '中央値':
        return statistics.median(p.value for p in points)
    if method == '平均':
        return statistics.mean(p.value for p in points)
    # FIRST/LAST follow history record order, like Chip Atlas's summaries.
    if method == '最初の値':
        return points[0].value
    if method == '最後の値':
        return points[-1].value
    raise ValueError('代表値を選択してください。')


def chip_points(histories, x_kind, y_kind, x_metric=1, y_metric=1, method='中央値', **options):
    points, excluded = [], []
    for chip, rows in histories.items():
        xs, _ = observations(chip, rows, x_kind, x_metric, **options)
        ys, _ = observations(chip, rows, y_kind, y_metric, **options)
        if not xs or not ys:
            excluded.append(chip)
            continue
        points.append(dict(chip=chip, x=representative(xs, method), y=representative(ys, method),
                           nx=len(xs), ny=len(ys), x_records=xs, y_records=ys))
    return points, excluded


def pearson(xs, ys):
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    xmean, ymean = statistics.mean(xs), statistics.mean(ys)
    dx, dy = [x-xmean for x in xs], [y-ymean for y in ys]
    sx, sy = math.sqrt(sum(x*x for x in dx)), math.sqrt(sum(y*y for y in dy))
    if not sx or not sy:
        return None
    return max(-1., min(1., sum((x/sx)*(y/sy) for x,y in zip(dx,dy))))


def ranks(values):
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        for index, _ in indexed[start:end]:
            result[index] = (start + 1 + end) / 2
        start = end
    return result


def correlations(points):
    xs, ys = [p['x'] for p in points], [p['y'] for p in points]
    return pearson(xs, ys), pearson(ranks(xs), ranks(ys))


def count_matches(nx, ny, threshold=5, operator='>', scope='XとY両方'):
    if threshold < 0 or operator not in ('>', '>='):
        raise ValueError('件数は0以上、比較は > または >= を指定してください。')
    x = nx > threshold if operator == '>' else nx >= threshold
    y = ny > threshold if operator == '>' else ny >= threshold
    return {'XとY両方': x and y, 'Xのみ': x, 'Yのみ': y, 'XまたはY': x or y}[scope]


def representative_limits(xmin='', xmax='', ymin='', ymax=''):
    """Blank limits are open-ended; finite endpoints are inclusive."""
    result = []
    for label, raw in zip(('X下限', 'X上限', 'Y下限', 'Y上限'), (xmin, xmax, ymin, ymax)):
        if raw is None or not str(raw).strip():
            result.append(None)
            continue
        try:
            value = float(raw)
        except (ValueError, TypeError):
            raise ValueError(f'{label}には数値を入力してください。') from None
        if not math.isfinite(value):
            raise ValueError(f'{label}には有限の数値を入力してください。')
        result.append(value)
    for index, axis in ((0, 'X'), (2, 'Y')):
        if result[index] is not None and result[index+1] is not None and result[index] > result[index+1]:
            raise ValueError(f'{axis}の下限は上限以下にしてください。')
    return tuple(result)


def within_representative_limits(point, limits):
    xmin, xmax, ymin, ymax = limits
    return ((xmin is None or point['x'] >= xmin) and (xmax is None or point['x'] <= xmax) and
            (ymin is None or point['y'] >= ymin) and (ymax is None or point['y'] <= ymax))


def density_grid(points, x_range, y_range, bins=40):
    """散布図の密度: x・y の範囲を bins×bins のマスに分け、各マスの点数を数える。

    戻り値 grid[i][j] は x 方向 i 番目・y 方向 j 番目（下から）のマスの件数。範囲外の点は数えない。"""
    (xl, xh), (yl, yh) = x_range, y_range
    grid = [[0] * bins for _ in range(bins)]
    if xh <= xl or yh <= yl:
        return grid
    for point in points:
        x, y = point['x'], point['y']
        if xl <= x <= xh and yl <= y <= yh:
            i = min(int((x - xl) / (xh - xl) * bins), bins - 1)
            j = min(int((y - yl) / (yh - yl) * bins), bins - 1)
            grid[i][j] += 1
    return grid


DENSITY_STOPS = ('#1d3557', '#2b6cb0', '#2a9d8f', '#5fbf77', '#c9d65a', '#ffe36e')


def density_colour(count, maximum):
    """件数 → 色（対数スケール、1件＝暗い青 … 最大＝明るい黄）。0 件は None（塗らない）。"""
    if count <= 0 or maximum <= 0:
        return None
    t = math.log1p(count) / math.log1p(maximum) if maximum > 1 else 1.
    t = min(max(t, 0.), 1.) * (len(DENSITY_STOPS) - 1)
    k = min(int(t), len(DENSITY_STOPS) - 2)
    f = t - k
    a, b = (tuple(int(s[i:i + 2], 16) for i in (1, 3, 5)) for s in DENSITY_STOPS[k:k + 2])
    return '#%02x%02x%02x' % tuple(round(p + (q - p) * f) for p, q in zip(a, b))
