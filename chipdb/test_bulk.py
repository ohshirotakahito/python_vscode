import pytest

from chipdb.bulk import Draft


def rows():
    return [['chip', 'Status', 'Date', 'Operator', 'EXID', 'Operation', '20190422'],
            ['Experiment', '○', '20190402_1359', 'User', 'EX1', 'TA#2', '20190422', '7.16'],
            ['Experiment', '○', '20190402_1359', 'User', 'EX1', 'AC#2', '20190423', '0'],
            ['Break'], ['END']]


def test_replace_header_and_history_with_undo():
    draft = Draft(rows())
    draft.replace(range(5), 6, '20190422', '24')
    assert draft.rows[0][6] == draft.rows[1][6] == '24'
    assert draft.rows[2][6] == '20190423'
    assert len(draft.changes()) == 2
    draft.undo()
    assert draft.rows == rows()


def test_paste_column_and_rectangle():
    draft = Draft(rows())
    draft.paste(1, 6, '24\n25\n')
    assert [r[6] for r in draft.rows[1:3]] == ['24', '25']
    draft.paste(1, 6, '26\t8.5\r\n27\t0.1\r\n')
    assert draft.rows[1][6:] == ['26', '8.5']


def test_atomic_rejection_of_partial_paste():
    draft = Draft(rows())
    for content in ['24\nnot-number', '24\n25\n26']:
        with pytest.raises(ValueError):
            draft.paste(1, 6, content)
        assert draft.rows == rows()
        assert not draft.undo_stack


def test_protection_and_fill_scope():
    draft = Draft(rows())
    with pytest.raises(ValueError):
        draft.apply([((1, 0), 'Break')])
    with pytest.raises(ValueError):
        draft.apply([((0, 2), 'changed')])
    draft.replace([1, 2], 6, '', '10', fill=True)
    assert draft.rows[0][6] == '20190422'
    assert draft.rows[1][6] == draft.rows[2][6] == '10'
