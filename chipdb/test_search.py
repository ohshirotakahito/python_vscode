from datetime import date

import pytest

from chipdb.search import date_query, usage_dates, matches


@pytest.mark.parametrize('query', ['2026-11', '2026/11', '202611', '2026年11月', '２０２６－１１'])
def test_month_formats(query):
    assert date_query(query) == (date(2026, 11, 1), date(2026, 11, 30))


def test_year_day_and_ranges():
    assert date_query('2026') == (date(2026, 1, 1), date(2026, 12, 31))
    assert date_query('20240229') == (date(2024, 2, 29), date(2024, 2, 29))
    assert date_query('2026-10〜2026-11') == (date(2026, 10, 1), date(2026, 11, 30))
    assert date_query('2026/10/5..2026/11/2') == (date(2026, 10, 5), date(2026, 11, 2))
    assert date_query('SC25-2115-37') is None


@pytest.mark.parametrize('query', ['2026-02-29', '2026-13', '2026-11〜2026-10'])
def test_invalid_dates(query):
    with pytest.raises(ValueError):
        date_query(query)


def test_search_actual_uses_not_first_last_span():
    dates = usage_dates([['Experiment', '○', '20260101_1000'],
                         ['Experiment', '○', '2026/03/01_10:00'],
                         ['Experiment', '○', 'bad'], ['Arrived', '', '20260201']])
    assert matches('chip', dates, '2026-01', date_query('2026-01'))
    assert not matches('chip', dates, '2026-02', date_query('2026-02'))
    assert matches('chip', dates, '2026-03-01', date_query('2026-03-01'))
    assert matches('SC25-37', dates, 'sc25', None)
    assert not matches('202611-chip', set(), '202611', date_query('202611'))
