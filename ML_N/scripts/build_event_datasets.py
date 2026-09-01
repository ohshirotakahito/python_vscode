"""Build searchable event datasets from a RAW/ANAL pair manifest."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from ml_n.catalog import resolve_manifest_path
from ml_n.dataset import write_event_dataset
from ml_n.io import read_anal_tdms, read_raw_tdms
from ml_n.processing import detect_measurement_events
from signal_core import DetectionConfig, PipelineConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Detect RAW TDMS events and write searchable waveform datasets."
        )
    )
    parser.add_argument("pairs_csv")
    parser.add_argument(
        "--output-dir",
        default="data/events",
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
        "--pre-context-ms",
        type=float,
        default=1.0,
    )
    parser.add_argument(
        "--post-context-ms",
        type=float,
        default=1.0,
    )
    parser.add_argument(
        "--verify-anal",
        action="store_true",
        help="Read ANAL files and require matching waveform hashes.",
    )
    args = parser.parse_args()

    manifest_path = Path(args.pairs_csv).resolve()
    pairs = pd.read_csv(manifest_path)
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

    output_root = Path(args.output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    batch_start = perf_counter()
    index_rows = []

    for row in pairs.itertuples(index=False):
        pair_start = perf_counter()
        pair_id = str(row.pair_id)
        raw_path = resolve_manifest_path(
            row.raw_tdms_path,
            manifest_path=manifest_path,
        )
        print(f"Processing: {pair_id}")
        measurement = read_raw_tdms(raw_path)

        if args.verify_anal:
            anal_path = resolve_manifest_path(
                row.anal_tdms_path,
                manifest_path=manifest_path,
            )
            anal = read_anal_tdms(anal_path)
            if measurement.file_id != anal.file_id:
                raise ValueError(
                    f"{pair_id}: RAW and ANAL hashes do not match"
                )

        config = PipelineConfig(
            detection=DetectionConfig(
                sampling_rate_hz=measurement.sampling_rate_hz,
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
        detection = detect_measurement_events(
            measurement,
            config,
        )
        dataset_dir = (
            output_root
            / measurement.file_id
            / detection.config_hash
        )
        dataset = write_event_dataset(
            measurement,
            detection,
            dataset_dir,
            pre_context_ms=args.pre_context_ms,
            post_context_ms=args.post_context_ms,
        )

        index_rows.append(
            {
                "pair_id": pair_id,
                "sample_name": getattr(row, "sample_name", None),
                "measurement_number": getattr(
                    row,
                    "measurement_number",
                    None,
                ),
                "file_id": measurement.file_id,
                "config_hash": detection.config_hash,
                "event_count": dataset.event_count,
                "event_table_path": dataset.event_table_path.relative_to(
                    output_root
                ).as_posix(),
                "dataset_dir": dataset_dir.relative_to(
                    output_root
                ).as_posix(),
                "processing_seconds": perf_counter() - pair_start,
            }
        )

    index_path = output_root / "dataset_index.csv"
    summary_path = output_root / "dataset_summary.json"
    pd.DataFrame(index_rows).to_csv(
        index_path,
        index=False,
        encoding="utf-8-sig",
    )
    summary = {
        "status": "experimental_event_dataset",
        "pair_count": len(index_rows),
        "event_count": sum(
            row["event_count"] for row in index_rows
        ),
        "settings": {
            "noise_method": args.noise_method,
            "noise_block_points": args.noise_block_points,
            "threshold_k1": args.threshold_k1,
            "threshold_k2": args.threshold_k2,
            "pre_context_ms": args.pre_context_ms,
            "post_context_ms": args.post_context_ms,
            "verify_anal": args.verify_anal,
        },
        "processing_seconds": perf_counter() - batch_start,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved dataset index: {index_path}")
    print(f"Saved dataset summary: {summary_path}")


if __name__ == "__main__":
    main()
