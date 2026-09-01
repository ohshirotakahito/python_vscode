import numpy as np
import pandas as pd

from ml_n.dataset import write_event_dataset
from ml_n.processing import detect_measurement_events
from signal_core import DetectionConfig, PipelineConfig

from test_event_detection import make_measurement


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
        noise_method="blockwise",
        noise_block_points=1000,
    )


def test_writes_searchable_metadata_and_waveforms(tmp_path):
    measurement = make_measurement()
    detection = detect_measurement_events(
        measurement,
        make_config(),
    )

    result = write_event_dataset(
        measurement,
        detection,
        tmp_path / "dataset",
        pre_context_ms=1.0,
        post_context_ms=2.0,
    )

    assert result.event_count == len(detection.records)
    table = pd.read_csv(result.event_table_path)
    assert len(table) == result.event_count
    assert set(table["event_id"]) == {
        record.event_id for record in detection.records
    }

    row = table.iloc[0]
    assert row["source_filename"] == measurement.source_filename
    assert row["sample_name"] == "synthetic"
    assert row["gap_id"] == "TEST-G001"
    assert "source_path" not in table.columns

    segment_path = result.event_table_path.parent / row["segment_path"]
    with np.load(segment_path, allow_pickle=False) as arrays:
        np.testing.assert_allclose(
            arrays["current_pa"],
            arrays["baseline_pa"]
            + arrays["relative_current_pa"],
        )
        assert arrays["noise_pa"].shape == arrays["current_pa"].shape
