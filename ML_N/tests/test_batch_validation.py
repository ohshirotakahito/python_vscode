import pytest

from ml_n.validation.batch_validation import (
    FileValidationMetrics,
    aggregate_file_metrics,
)


def test_aggregate_file_metrics():
    rows = [
        FileValidationMetrics(
            file_id="sample065",
            python_event_count=984,
            anal_event_count=864,
            matched_count=631,
            python_only_count=353,
            anal_only_count=233,
        ),
        FileValidationMetrics(
            file_id="sample001",
            python_event_count=156,
            anal_event_count=81,
            matched_count=50,
            python_only_count=106,
            anal_only_count=31,
        ),
    ]

    result = aggregate_file_metrics(rows)

    assert result.file_count == 2
    assert result.python_event_count == 1140
    assert result.anal_event_count == 945
    assert result.matched_count == 681
    assert result.python_only_count == 459
    assert result.anal_only_count == 264
    assert result.precision == pytest.approx(681 / 1140)
    assert result.recall == pytest.approx(681 / 945)
    assert result.f1 == pytest.approx(
        2 * 681 / (2 * 681 + 459 + 264)
    )


def test_file_metrics_reject_inconsistent_python_count():
    with pytest.raises(
        ValueError,
        match="python_event_count",
    ):
        FileValidationMetrics(
            file_id="invalid",
            python_event_count=10,
            anal_event_count=8,
            matched_count=5,
            python_only_count=4,
            anal_only_count=3,
        )


def test_file_metrics_reject_negative_count():
    with pytest.raises(
        ValueError,
        match="non-negative",
    ):
        FileValidationMetrics(
            file_id="invalid",
            python_event_count=-1,
            anal_event_count=0,
            matched_count=0,
            python_only_count=0,
            anal_only_count=0,
        )


def test_aggregate_rejects_empty_input():
    with pytest.raises(
        ValueError,
        match="at least one",
    ):
        aggregate_file_metrics([])