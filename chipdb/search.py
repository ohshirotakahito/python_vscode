"""Single-field name or actual experiment-date search."""
import calendar
from datetime import date
import re
import unicodedata


def period(text):
    text = text.strip().replace('年', '-').replace('月', '-').replace('日', '').rstrip('-')
    match = re.fullmatch(r'(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?|(\d{2})(\d{2})?)?', text)
    if not match:
        return None
    year = int(match[1])
    month = int(match[2] or match[4] or 1)
    day = int(match[3] or match[5] or 1)
    has_month = bool(match[2] or match[4])
    has_day = bool(match[3] or match[5])
    try:
        start = date(year, month, day)
        end_month = month if has_month else 12
        end = date(year, end_month, day if has_day else calendar.monthrange(year, end_month)[1])
    except ValueError:
        raise ValueError('日付を確認してください（例: 2026-11）') from None
    return start, end


def date_query(text):
    text = unicodedata.normalize('NFKC', text).strip()
    pieces = re.split(r'\s*(?:〜|～|~|\.\.|から)\s*', text)
    if len(pieces) == 1:
        return period(text)
    if len(pieces) != 2:
        raise ValueError('期間は「2026-10〜2026-11」の形で入力してください')
    first, last = period(pieces[0]), period(pieces[1])
    if not first or not last or first[0] > last[1]:
        raise ValueError('期間の開始・終了を確認してください')
    return first[0], last[1]


def usage_dates(rows):
    """Actual Experiment date entries, never filesystem timestamps or gaps between uses."""
    dates = set()
    for row in rows:
        if len(row) <= 2 or row[0] != 'Experiment':
            continue
        text = row[2].split('_', 1)[0].split(' ', 1)[0]
        try:
            span = period(text)
        except ValueError:
            continue
        if span and span[0] == span[1]:
            dates.add(span[0])
    return dates


def matches(name, dates, query, span):
    if span is None:
        return query.strip().casefold() in name.casefold()
    return any(span[0] <= day <= span[1] for day in dates)
