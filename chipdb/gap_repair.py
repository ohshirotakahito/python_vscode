"""Repair GAP# counters that jumped to date-like values (>= 10000).

Rule (user-approved): within Experiment rows, the first row whose GAP# is
>= THRESHOLD takes the previous row's (repaired) value — or 0 when no earlier
valid row exists. Each following anomalous row keeps the original increment:
new[i] = new[i-1] + (orig[i] - orig[i-1]). A value below THRESHOLD ends the
anomalous run and is kept as is. Non-Experiment rows and non-integer GAP cells
are left untouched. The header counter (row 0, column 6) continues the chain.
"""
from pathlib import Path

from chipdb.store import Document

THRESHOLD = 10000
GAP = 6


def _int(value):
    value = value.strip()
    return int(value) if value.isdigit() else None


def repair_rows(rows):
    """Return (new_rows, changes); changes = [(row_index, old, new)]."""
    rows = [list(r) for r in rows]
    changes = []
    prev_orig = prev_new = None
    anomalous = False

    def step(value):
        nonlocal prev_orig, prev_new, anomalous
        if value >= THRESHOLD:
            if anomalous:
                new = prev_new + (value - prev_orig)
            else:
                if not 20000000 <= value <= 20991231:
                    raise ValueError(f'日付型ではない異常GAP値 {value}（自動修正の対象外）')
                new = prev_new if prev_new is not None else 0
                anomalous = True
        else:
            new, anomalous = value, False
        prev_orig, prev_new = value, new
        return new

    for index, row in enumerate(rows[1:], start=1):
        if len(row) > GAP and row[0] == 'Experiment':
            value = _int(row[GAP])
            if value is None:
                continue
            new = step(value)
            if new < 0:
                raise ValueError(f'{index + 1}行目: 修正後のGAPが負になります ({row[GAP]} -> {new})')
            if new != value:
                changes.append((index, row[GAP], str(new)))
                row[GAP] = str(new)
    if rows and len(rows[0]) > GAP:
        value = _int(rows[0][GAP])
        if value is not None and value >= THRESHOLD:
            new = step(value)
            if new < 0:
                raise ValueError(f'見出し行: 修正後のGAPが負になります ({rows[0][GAP]} -> {new})')
            changes.append((0, rows[0][GAP], str(new)))
            rows[0][GAP] = str(new)
    return rows, changes


def preview(db):
    """[{file, changes, error}] for every CSV that needs a repair or failed."""
    plans = []
    for path in sorted(Path(db).glob('*.csv')):
        try:
            document = Document(path)
            _, changes = repair_rows(document.rows)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            plans.append(dict(file=str(path), changes=[], error=str(error)))
            continue
        if changes:
            plans.append(dict(file=str(path), changes=changes, error=''))
    return plans


def apply(plans):
    """Save every error-free plan via Document.save (backup + conflict check)."""
    results = []
    for plan in plans:
        if plan['error']:
            continue
        document = Document(plan['file'])
        rows, changes = repair_rows(document.rows)
        if [list(c) for c in changes] != [list(c) for c in plan['changes']]:
            results.append((plan['file'], 'プレビュー後に内容が変わったためスキップ'))
            continue
        results.append((plan['file'], str(document.save(rows))))
    return results
