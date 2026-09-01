import numpy as np
import pytest

from ml_n.processing import detect_measurement_events
from ml_n.processing.event_segments import (
    extract_event_segments,
)
from signal_core import DetectionConfig, PipelineConfig

from test_event_detection import make_measurement


def make_config(*, noise_method="global"):
    return PipelineConfig(
        detection=DetectionConfig(
            sampling_rate_hz=10_000,
            polarity="positive",
            threshold_k1=3.0,
            threshold_k2=6.0,
            minimum_duration_ms=0.2,
        ),
        smoothing_window_points=51,
        smoothing_polynomial_order=3,
        baseline_window_points=501,
        baseline_percentile=50.0,
        noise_method=noise_method,
        noise_block_points=1000,
    )


@pytest.mark.parametrize(
    "noise_method",
    ["global", "blockwise"],
)
def test_extracts_aligned_event_arrays(noise_method):
    measurement = make_measurement()
    detection = detect_measurement_events(
        measurement,
        make_config(noise_method=noise_method),
    )

    segments = extract_event_segments(
        measurement,
        detection,
        pre_context_ms=1.0,
        post_context_ms=2.0,
    )

    target = next(
        segment
        for segment in segments
        if segment.window_start_index <= 2000
        < segment.window_end_index
    )

    assert target.n_points == target.current_pa.size
    assert target.smoothed_current_pa.shape == (
        target.current_pa.shape
    )
    assert target.baseline_pa.shape == target.current_pa.shape
    assert target.noise_pa.shape == target.current_pa.shape
    np.testing.assert_allclose(
        target.relative_current_pa,
        target.current_pa - target.baseline_pa,
    )

    record = next(
        record
        for record in detection.records
        if record.event_id == target.event_id
    )
    absolute_peak_index = (
        target.window_start_index + target.peak_offset
    )
    assert absolute_peak_index == record.peak_index


def test_rejects_negative_context():
    measurement = make_measurement()
    detection = detect_measurement_events(
        measurement,
        make_config(),
    )

    with pytest.raises(ValueError, match="non-negative"):
        extract_event_segments(
            measurement,
            detection,
            pre_context_ms=-1.0,
        )
