from pathlib import Path

import numpy as np

from ml_n.io import RawMeasurement
from ml_n.processing import (
    calculate_config_hash,
    detect_measurement_events,
)
from signal_core import DetectionConfig, PipelineConfig


def make_config():
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
    )


def make_measurement():
    rng = np.random.default_rng(12345)

    current_pa = (
        10.0
        + rng.normal(0.0, 0.2, 5000)
    )
    current_pa[2000:2020] += 3.0

    return RawMeasurement(
        source_path=Path("synthetic_10k_Sample.tdms"),
        source_filename="synthetic_10k_Sample.tdms",
        file_id="synthetic-file",
        waveform_sha256="abc123",
        sampling_rate_hz=10_000.0,
        current_scale_to_pa=1000.0,
        current_pa=current_pa,
        metadata_tables={
            "Gap Table": {
                "Sample Name": "synthetic",
                "GAP_ID": "TEST-G001",
            },
        },
    )


def test_config_hash_is_stable():
    first = calculate_config_hash(make_config())
    second = calculate_config_hash(make_config())

    assert first == second
    assert len(first) == 16


def test_detect_measurement_events_adds_provenance():
    result = detect_measurement_events(
        make_measurement(),
        make_config(),
    )

    matching = [
        record
        for record in result.records
        if record.start_index <= 2000 < record.end_index
    ]

    assert len(matching) == 1

    record = matching[0]

    assert record.file_id == "synthetic-file"
    assert record.sample_name == "synthetic"
    assert record.gap_id == "TEST-G001"
    assert record.config_hash == result.config_hash
    assert record.detector_name == "positive_hysteresis"

    assert record.start_time_s == (
        record.start_index / 10_000
    )
    assert record.end_time_s == (
        record.end_index / 10_000
    )


def test_rejects_sampling_rate_mismatch():
    config = PipelineConfig(
        detection=DetectionConfig(
            sampling_rate_hz=20_000,
        )
    )

    try:
        detect_measurement_events(
            make_measurement(),
            config,
        )
    except ValueError as error:
        assert "sampling_rate_hz" in str(error)
    else:
        raise AssertionError("ValueError was not raised")