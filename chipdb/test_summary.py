import math

import pytest

from chipdb.summary import summarize, histogram, shares, COLUMNS


def operation(kind, value='', resistance='50', when='20261113_1234'):
    return ['Experiment', '○', when, '担当', '20261113_1234 UNAN#2Pex0n1', kind+'#1', '4', str(value), resistance, when]


def test_axn_summary_limits_counts_and_sample_std():
    rows = [['chip', '', '', '', '', '', '4'], operation('GPSET'), operation('EXSV')]
    rows += [operation('FC', v) for v in [1, 2, 3, 0, -1, 'nan', 'inf', 'bad']]
    rows += [operation('CAL', 200), operation('CAL', 100), operation('MA', 12), operation('TA', 4)]
    summary = summarize(rows)
    assert tuple(summary) == COLUMNS
    assert summary['Ex#'] == '1'
    assert summary['Run#'] == '1'
    assert summary['FC#'] == '8'
    assert summary['FC FIRST'] == '1'
    assert summary['FC Last'] == '2'
    assert summary['FC Mean'] == '1.5'
    assert float(summary['FC STD']) == pytest.approx(math.sqrt(.5), rel=1e-6)
    assert summary['CAL Last'] == '100'
    assert summary['CAL STD'] == ''
    assert summary['M/T Last'] == '3'
    assert summary['Machine'] == 'AN#2'
    assert summary['Operator'] == '担当'
    assert summary['EX FIRST Date'] == '2026/11/13_12:34'
    assert summary['FC Chip R [Ohm]'] == '50'


def test_missing_and_empty_history():
    summary = summarize([['chip']])
    assert summary['FC Last'] == ''
    assert summary['Ex#'] == '0'
    with pytest.raises(ValueError):
        summarize([])


def test_histogram_axn_boundary_and_shares():
    records = [{'FC FIRST': value, 'Machine': machine} for value, machine in [('1.5', 'AXN1'), ('0', 'AXN1'), ('nan', ''), ('2', '')]]
    edges, counts = histogram(records, 'FC FIRST', 1.5)
    assert sum(counts) == 1
    assert edges[0] == 1.5
    assert shares(records, 'Machine') == [('AXN1', 2), ('未記録', 2)]
