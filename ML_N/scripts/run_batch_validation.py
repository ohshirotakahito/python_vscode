"""Run validation for multiple RAW/ANAL TDMS pairs."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from ml_n.io import read_anal_tdms, read_raw_tdms
from ml_n.processing import detect_measurement_events
from ml_n.validation import (
    FileValidationMetrics,
    aggregate_file_metrics,
    match_events,
)
from signal_core import DetectionConfig, PipelineConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate multiple RAW/ANAL TDMS pairs."
    )
    parser.add_argument("pairs_csv")
    parser.add_argument(
        "--output-dir",
        default="data/validation/batch",
    )
    parser.add_argument(
        "--noise-method",
        choices=("global", "blockwise"),
        default="blockwise",
    )
    parser.add_argument(
        "--noise-block-points",
        type=int,
        default=10_000,
    )
    parser.add_argument(
        "--threshold-k1",
        type=float,
        default=3.0,
    )
    parser.add_argument(
        "--threshold-k2",
        type=float,
        default=5.5,
    )
    parser.add_argument(
        "--max-peak-error-ms",
        type=float,
        default=1.0,
    )
    args = parser.parse_args()

    pairs = pd.read_csv(args.pairs_csv)

    required_columns = {
        "pair_id",
        "raw_tdms_path",
        "anal_tdms_path",
    }
    missing_columns = required_columns - set(pairs.columns)

    if missing_columns:
        raise ValueError(
            "pairs CSV is missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_metrics = []
    output_rows = []
    batch_start = perf_counter()

    for row in pairs.itertuples(index=False):
        pair_start = perf_counter()
        pair_id = str(row.pair_id)

        print(f"Processing: {pair_id}")

        raw = read_raw_tdms(row.raw_tdms_path)
        anal = read_anal_tdms(row.anal_tdms_path)

        if raw.file_id != anal.file_id:
            raise ValueError(
                f"{pair_id}: RAW and ANAL hashes do not match"
            )

        config = PipelineConfig(
            detection=DetectionConfig(
                sampling_rate_hz=raw.sampling_rate_hz,
                polarity="positive",
                threshold_k1=args.threshold_k1,
                threshold_k2=args.threshold_k2,
                minimum_duration_ms=0.2,
            ),
            smoothing_window_points=51,
            smoothing_polynomial_order=3,
            baseline_window_points=501,
            baseline_percentile=10.0,
            noise_iterations=5,
            noise_clip_sigma=4.0,
            noise_method=args.noise_method,
            noise_block_points=args.noise_block_points,
        )

        python_result = detect_measurement_events(
            raw,
            config,
        )

        match_result = match_events(
            python_result.records,
            anal.events,
            sampling_rate_hz=raw.sampling_rate_hz,
            max_peak_error_ms=args.max_peak_error_ms,
        )

        metrics = FileValidationMetrics(
            file_id=raw.file_id,
            python_event_count=len(python_result.records),
            anal_event_count=len(anal.events),
            matched_count=match_result.true_positive,
            python_only_count=match_result.false_positive,
            anal_only_count=match_result.false_negative,
        )
        file_metrics.append(metrics)

        output_rows.append(
            {
                "pair_id": pair_id,
                **metrics.to_dict(),
                "precision": match_result.precision,
                "recall": match_result.recall,
                "f1": match_result.f1,
                "config_hash": python_result.config_hash,
                "processing_seconds": (
                    perf_counter() - pair_start
                ),
            }
        )

    aggregate = aggregate_file_metrics(file_metrics)

    results_csv = output_dir / "file_metrics.csv"
    summary_json = output_dir / "summary.json"

    pd.DataFrame(output_rows).to_csv(
        results_csv,
        index=False,
        encoding="utf-8-sig",
    )

    summary = {
        "status": "experimental_batch_validation",
        "settings": {
            "noise_method": args.noise_method,
            "noise_block_points": args.noise_block_points,
            "threshold_k1": args.threshold_k1,
            "threshold_k2": args.threshold_k2,
            "max_peak_error_ms": args.max_peak_error_ms,
        },
        "aggregate": aggregate.to_dict(),
        "processing_seconds": perf_counter() - batch_start,
    }

    summary_json.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved file metrics: {results_csv}")
    print(f"Saved summary: {summary_json}")


if __name__ == "__main__":
    main()