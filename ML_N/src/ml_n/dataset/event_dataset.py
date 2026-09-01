"""Write searchable event metadata and aligned waveform arrays."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ml_n.catalog import build_catalog_record
from ml_n.io import RawMeasurement
from ml_n.processing import (
    MeasurementDetectionResult,
    extract_event_segments,
)


@dataclass(frozen=True)
class EventDatasetResult:
    event_count: int
    event_table_path: Path
    segments_dir: Path


def write_event_dataset(
    measurement: RawMeasurement,
    detection: MeasurementDetectionResult,
    output_dir: str | Path,
    *,
    pre_context_ms: float = 1.0,
    post_context_ms: float = 1.0,
) -> EventDatasetResult:
    """Write one metadata row and one compressed array file per event."""

    destination = Path(output_dir)
    segments_dir = destination / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    catalog = build_catalog_record(measurement)
    segments = extract_event_segments(
        measurement,
        detection,
        pre_context_ms=pre_context_ms,
        post_context_ms=post_context_ms,
    )
    records_by_id = {
        record.event_id: record
        for record in detection.records
    }
    rows = []

    for segment in segments:
        record = records_by_id[segment.event_id]
        relative_path = Path("segments") / (
            f"{segment.event_id}.npz"
        )
        segment_path = destination / relative_path

        np.savez_compressed(
            segment_path,
            current_pa=segment.current_pa,
            smoothed_current_pa=segment.smoothed_current_pa,
            baseline_pa=segment.baseline_pa,
            relative_current_pa=segment.relative_current_pa,
            noise_pa=segment.noise_pa,
        )

        rows.append(
            {
                **catalog.to_dict(),
                **record.to_dict(),
                "window_start_index": (
                    segment.window_start_index
                ),
                "window_end_index": segment.window_end_index,
                "event_start_offset": (
                    segment.event_start_offset
                ),
                "event_end_offset": segment.event_end_offset,
                "peak_offset": segment.peak_offset,
                "segment_n_points": segment.n_points,
                "pre_context_ms": pre_context_ms,
                "post_context_ms": post_context_ms,
                "segment_path": relative_path.as_posix(),
            }
        )

    event_table_path = destination / "events.csv"
    pd.DataFrame(rows).to_csv(
        event_table_path,
        index=False,
        encoding="utf-8-sig",
    )

    return EventDatasetResult(
        event_count=len(rows),
        event_table_path=event_table_path,
        segments_dir=segments_dir,
    )
