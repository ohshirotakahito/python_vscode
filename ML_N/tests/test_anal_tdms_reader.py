import pytest

from ml_n.io import AnalEvent, parse_sampling_rate_hz


def test_parse_sampling_rate_khz():
    assert parse_sampling_rate_hz("10kHz") == 10_000.0


def test_parse_sampling_rate_hz():
    assert parse_sampling_rate_hz("20000 Hz") == 20_000.0


def test_parse_sampling_rate_returns_none_for_invalid_value():
    assert parse_sampling_rate_hz("unknown") is None


def test_anal_event_properties():
    event = AnalEvent(
        signal_number=0,
        start_index=396,
        end_index=398,
        peak_index=397,
        start_time_s=0.0396,
        end_time_s=0.0398,
        peak_time_s=0.0397,
        duration_ms=0.2,
        dead_time_s=0.0397,
        relative_signal_pa=17.8342,
        region_baseline_pa=11.4331,
        region_std_pa=2.8714,
        threshold_pa=17.2283,
        rt_baseline_pa=13.7388,
    )

    assert event.n_points == 2
    assert event.absolute_peak_pa == pytest.approx(29.2673)