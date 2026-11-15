"""Chip Info analysis.vi / AXN panel_info-compatible history summaries.

Info1 limits are strict; counts include omitted measurements. STD is sample
standard deviation. FIRST/LAST use file order, not a date sort.
"""
from collections import Counter
import math
import statistics

LIMITS = {'CAL': 200., 'FC': 3., 'MA': 100., 'TA': 100.}
COLUMNS = ('ChipID', 'GAP#', 'Ex#', 'Run#', 'CAL#', 'FC#', 'MA#', 'TA#',
           'EX FIRST Date', 'EX Last Date', 'CAL Last', 'CAL Mean', 'CAL STD',
           'FC FIRST', 'FC Last', 'FC Mean', 'FC STD', 'MA Last', 'MA Mean',
           'MA STD', 'TA Last', 'TA Mean', 'TA STD', 'M/T Last', 'Machine',
           'Operator', 'FC Chip R [Ohm]', 'FC Chip R Time')
HISTOGRAMS = (('実験数', 'Ex#', None), ('CAL 最新値', 'CAL Last', 200.),
              ('M/T 最新値', 'M/T Last', 2.), ('GAP 数', 'GAP#', None),
              ('FC 初回値', 'FC FIRST', 1.5), ('MA 最新値', 'MA Last', 25.),
              ('TA 最新値', 'TA Last', 25.))


def date(text):
    if len(text) == 13 and text[8] == '_':
        return f'{text[:4]}/{text[4:6]}/{text[6:8]}_{text[9:11]}:{text[11:]}'
    return text


def summarize(rows):
    rows = [r for r in rows if r and any(r)]
    if not rows:
        raise ValueError('空のチップ履歴です')
    experiments = [r for r in rows if r[0] == 'Experiment' and len(r) > 5]
    kinds = {}
    for row in experiments:
        kinds.setdefault(row[5].split('#')[0], []).append(row)
    result = dict.fromkeys(COLUMNS, '')
    result.update(ChipID=rows[0][0], **{'GAP#': rows[0][6] if len(rows[0]) > 6 else ''})
    for column, kind in [('Ex#', 'GPSET'), ('Run#', 'EXSV')] + [(k+'#', k) for k in LIMITS]:
        result[column] = str(len(kinds.get(kind, [])))
    if experiments:
        result['EX FIRST Date'] = date(experiments[0][2])
        result['EX Last Date'] = date(experiments[-1][2])
        words = experiments[-1][4].split(' ', 1)
        result['Machine'] = words[1].split('Pex')[0][2:] if len(words) > 1 and 'Pex' in words[1] else ''
        result['Operator'] = experiments[-1][3]
    stats = {}
    for kind, limit in LIMITS.items():
        values = []
        for row in kinds.get(kind, []):
            try:
                value = float(row[7])
            except (IndexError, ValueError):
                continue
            if 0 < value < limit:
                values.append(value)
        stats[kind] = values
        if values:
            result[kind+' Last'] = f'{values[-1]:.6g}'
            result[kind+' Mean'] = f'{statistics.mean(values):.6g}'
            if len(values) > 1:
                result[kind+' STD'] = f'{statistics.stdev(values):.6g}'
            if kind == 'FC':
                result['FC FIRST'] = f'{values[0]:.6g}'
    if stats['MA'] and stats['TA']:
        result['M/T Last'] = f"{stats['MA'][-1] / stats['TA'][-1]:.6g}"
    fc = kinds.get('FC', [])
    if fc:
        result['FC Chip R [Ohm]'] = fc[-1][8] if len(fc[-1]) > 8 else ''
        result['FC Chip R Time'] = fc[-1][9] if len(fc[-1]) > 9 else ''
    return result


def histogram(records, column, maximum, bins=50):
    values = []
    for record in records:
        try:
            value = float(record[column])
        except (ValueError, KeyError):
            value = 0.  # AXN / LabVIEW string-to-number convention
        if math.isfinite(value) and (maximum is None or 0 < value <= maximum):
            values.append(value)
    if not values:
        return [], []
    low, high = min(values), max(values)
    width = (high-low) / bins or 1.
    counts = [0] * bins
    for value in values:
        counts[min(int((value-low)/width), bins-1)] += 1
    return [low + width*i for i in range(bins+1)], counts


def shares(records, column):
    return Counter(r[column] or '未記録' for r in records).most_common()
