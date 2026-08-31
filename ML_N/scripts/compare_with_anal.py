import argparse
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from ml_n.io import read_anal_tdms, read_raw_tdms
from ml_n.processing import detect_measurement_events
from ml_n.validation import match_events
from signal_core import DetectionConfig, PipelineConfig


def main():
    parser = argparse.ArgumentParser(
        description="Compare Python detection with LabVIEW ANAL events."
    )
    parser.add_argument("raw_tdms_path")
    parser.add_argument("anal_tdms_path")
    parser.add_argument(
        "--output-dir",
        default="data/validation",
    )
    parser.add_argument(
        "--max-peak-error-ms",
        type=float,
        default=1.0,
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
        "--baseline-percentile",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--noise-method",
        choices=("global", "blockwise"),
        default="global",
    )
    parser.add_argument(
        "--noise-block-points",
        type=int,
        default=10_000,
    )
    args = parser.parse_args()

    timer_start = perf_counter()

    raw = read_raw_tdms(args.raw_tdms_path)
    anal = read_anal_tdms(args.anal_tdms_path)

    if raw.file_id != anal.file_id:
        raise ValueError(
            "Raw and ANAL waveform hashes do not match"
        )

    config = PipelineConfig(
        detection=DetectionConfig(
            sampling_rate_hz=raw.sampling_rate_hz,
            polarity="positive",
            threshold_k1=args.threshold_k1,
            threshold_k2=args.threshold_k2,
            minimum_duration_ms=args.minimum_duration_ms,
        ),
        smoothing_window_points=51,
        smoothing_polynomial_order=3,
        baseline_window_points=501,
        baseline_percentile=args.baseline_percentile,
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

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{raw.file_id}_{python_result.config_hash}"

    matches_path = output_dir / f"{stem}_matches.csv"
    python_only_path = output_dir / f"{stem}_python_only.csv"
    anal_only_path = output_dir / f"{stem}_anal_only.csv"
    summary_path = output_dir / f"{stem}_summary.json"

    pd.DataFrame(
        [match.to_dict() for match in match_result.matches]
    ).to_csv(matches_path, index=False, encoding="utf-8-sig")

    pd.DataFrame(
        [event.to_dict() for event in match_result.python_only]
    ).to_csv(
        python_only_path,
        index=False,
        encoding="utf-8-sig",
    )

    pd.DataFrame(
        [asdict(event) for event in match_result.anal_only]
    ).to_csv(
        anal_only_path,
        index=False,
        encoding="utf-8-sig",
    )

    summary = {
        "status": "experimental_validation",
        "file_id": raw.file_id,
        "python_event_count": len(python_result.records),
        "anal_event_count": len(anal.events),
        "matched_count": match_result.true_positive,
        "python_only_count": match_result.false_positive,
        "anal_only_count": match_result.false_negative,
        "precision": match_result.precision,
        "recall": match_result.recall,
        "f1": match_result.f1,
        "max_peak_error_ms": args.max_peak_error_ms,
        "config_hash": python_result.config_hash,
        "pipeline_config": asdict(config),
        "processing_seconds": perf_counter() - timer_start,
    }

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved matches: {matches_path}")
    print(f"Saved Python-only: {python_only_path}")
    print(f"Saved ANAL-only: {anal_only_path}")
    print(f"Saved summary: {summary_path}")


if __name__ == "__main__":
    main()