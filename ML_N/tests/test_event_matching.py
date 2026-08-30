from ml_n.io import AnalEvent
from ml_n.processing import EventRecord
from ml_n.validation import interval_iou, match_events


def make_python_event(
    event_id="python-1",
    start=100,
    peak=105,
    end=110,
):
    return EventRecord(
        file_id="file-1",
        event_id=event_id,
        start_index=start,
        end_index=end,
        peak_index=peak,
        start_time_s=start / 10_000,
        end_time_s=end / 10_000,
        peak_time_s=peak / 10_000,
        duration_ms=(end - start) / 10,
        baseline_pa=10.0,
        noise_pa=1.0,
        peak_current_pa=25.0,
        relative_peak_pa=15.0,
        detector_name="test",
        detector_version="0.1.0",
        config_hash="config",
        sample_name="sample",
        gap_id="gap",
    )


def make_anal_event(
    signal_number=1,
    start=101,
    peak=105,
    end=111,
):
    return AnalEvent(
        signal_number=signal_number,
        start_index=start,
        end_index=end,
        peak_index=peak,
        start_time_s=start / 10_000,
        end_time_s=end / 10_000,
        peak_time_s=peak / 10_000,
        duration_ms=(end - start) / 10,
        dead_time_s=0.0,
        relative_signal_pa=14.0,
        region_baseline_pa=9.5,
        region_std_pa=1.0,
        threshold_pa=12.0,
        rt_baseline_pa=9.5,
    )


def test_interval_iou():
    result = interval_iou(100, 110, 105, 115)

    assert result == 5 / 15


def test_exact_event_match():
    result = match_events(
        (make_python_event(),),
        (make_anal_event(),),
        sampling_rate_hz=10_000,
        max_peak_error_ms=1.0,
    )

    assert result.true_positive == 1
    assert result.false_positive == 0
    assert result.false_negative == 0
    assert result.precision == 1.0
    assert result.recall == 1.0

    match = result.matches[0]

    assert match.peak_error_ms == 0.0
    assert match.start_error_ms == -0.1
    assert match.end_error_ms == -0.1
    assert match.baseline_error_pa == 0.5
    assert match.relative_signal_error_pa == 1.0


def test_one_to_one_matching():
    python_events = (
        make_python_event(
            event_id="best",
            start=100,
            peak=105,
            end=110,
        ),
        make_python_event(
            event_id="other",
            start=102,
            peak=106,
            end=108,
        ),
    )

    result = match_events(
        python_events,
        (make_anal_event(),),
        sampling_rate_hz=10_000,
        max_peak_error_ms=1.0,
    )

    assert result.true_positive == 1
    assert result.false_positive == 1
    assert result.false_negative == 0
    assert result.matches[0].python_event_id == "best"