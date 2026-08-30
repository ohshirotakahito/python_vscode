import argparse
from dataclasses import asdict, fields
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from ml_n.io import read_raw_tdms
from ml_n.processing import (
    EventRecord,
    detect_measurement_events,
)
from signal_core import DetectionConfig, PipelineConfig


def main():
    parser = argparse.ArgumentParser(
        description="Detect experimental events from one original TDMS file."
    )
    parser.add_argument("tdms_path")
    parser.add_argument(
        "--output-dir",
        default="data/events",
    )
    parser.add_argument(
        "--sampling-rate-hz",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--current-scale-to-pa",
        type=float,
        default=1000.0,
    )
    parser.add_argument(
        "--threshold-k1",
        type=float,
        default=3.0,
    )
    parser.add_argument(
        "--threshold-k2",
        type=float,
        default=6.0,
    )
    parser.add_argument(
        "--minimum-duration-ms",
        type=float,
        default=0.2,
    )
    parser.add_argument(
        "--smoothing-window-points",
        type=int,
        default=51,
    )
    parser.add_argument(
        "--smoothing-polynomial-order",
        type=int,
        default=3,
    )
    parser.add_argument(
        "--baseline-window-points",
        type=int,
        default=501,
    )
    parser.add_argument(
        "--baseline-percentile",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--noise-iterations",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--noise-clip-sigma",
        type=float,
        default=4.0,
    )
    args = parser.parse_args()

    started_at = datetime.now().astimezone()
    timer_start = perf_counter()

    print("Reading raw TDMS...")

    measurement = read_raw_tdms(
        args.tdms_path,
        sampling_rate_hz=args.sampling_rate_hz,
        current_scale_to_pa=args.current_scale_to_pa,
    )

    config = PipelineConfig(
        detection=DetectionConfig(
            sampling_rate_hz=measurement.sampling_rate_hz,
            polarity="positive",
            threshold_k1=args.threshold_k1,
            threshold_k2=args.threshold_k2,
            minimum_duration_ms=args.minimum_duration_ms,
        ),
        smoothing_window_points=args.smoothing_window_points,
        smoothing_polynomial_order=args.smoothing_polynomial_order,
        baseline_window_points=args.baseline_window_points,
        baseline_percentile=args.baseline_percentile,
        noise_iterations=args.noise_iterations,
        noise_clip_sigma=args.noise_clip_sigma,
    )

    print(
        f"Detecting events: points={measurement.n_points}, "
        f"duration={measurement.duration_s:.3f}s"
    )

    result = detect_measurement_events(
        measurement,
        config,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_stem = (
        f"{measurement.file_id}_{result.config_hash}"
    )
    events_path = output_dir / f"{output_stem}_events.csv"
    summary_path = output_dir / f"{output_stem}_summary.json"

    event_columns = [
        field.name
        for field in fields(EventRecord)
    ]

    event_df = pd.DataFrame(
        [record.to_dict() for record in result.records],
        columns=event_columns,
    )
    event_df.to_csv(
        events_path,
        index=False,
        encoding="utf-8-sig",
    )

    elapsed_seconds = perf_counter() - timer_start

    summary = {
        "status": "experimental",
        "message": (
            "These events have not yet been validated against "
            "the LabVIEW ANAL S Table."
        ),
        "created_at": started_at.isoformat(),
        "source_filename": measurement.source_filename,
        "file_id": measurement.file_id,
        "waveform_sha256": measurement.waveform_sha256,
        "sampling_rate_hz": measurement.sampling_rate_hz,
        "n_points": measurement.n_points,
        "duration_s": measurement.duration_s,
        "event_count": len(result.records),
        "config_hash": result.config_hash,
        "pipeline_config": asdict(config),
        "processing_seconds": elapsed_seconds,
        "events_path": str(events_path),
    }

    summary_path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Detected events: {len(result.records)}")
    print(f"Config hash: {result.config_hash}")
    print(f"Processing time: {elapsed_seconds:.2f}s")
    print(f"Saved events: {events_path}")
    print(f"Saved summary: {summary_path}")
    print("Status: experimental / not validated against ANAL")


if __name__ == "__main__":
    main()