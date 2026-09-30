# -*- coding: utf-8 -*-
"""Compare RMC meta features by distance and/or sample_name, per sample.

Examples
--------
python stat_features_rmc_by_group.py oxytocin vasopressin
python stat_features_rmc_by_group.py oxytocin --group-by distance
"""

from __future__ import annotations

import argparse
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

import common.paths as paths
from common.ml_analysis import data_stat


WAVE_COLUMNS = [f"wave_{i}" for i in range(12)]
FEATURE_COLUMNS = ["absolute_signal", "relative_signal", "duration", *WAVE_COLUMNS]
GROUP_COLUMNS = ("distance", "sample_name")
#DEFAULT_SAMPLES = ["oxytocin", "vasopressin"]
DEFAULT_SAMPLES = ["AA33LTyr"]

DURATION_LIMIT = (5, 200)
BASELINE_LIMIT = (-300, 200)
SIGNAL_LIMIT = (0, 100)

DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "hist_var_resulsts"
DEFAULT_UPPER_LIMITS = {"relative_signal": 80.0, "duration": 150.0}
DEFAULT_MAX_GROUPS_PER_PLOT = 12


def find_meta_path(data_root: Path, sample: str) -> Path:
    stem = f"{sample}_10k_Sample_ANAL_meta"
    for suffix in (".parquet", ".csv"):
        candidate = data_root / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"meta file not found for {sample!r} under {data_root}")


def load_features(sample: str, data_root: Path, group_by: str) -> pd.DataFrame:
    path = find_meta_path(data_root, sample)
    frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    required = {group_by, "signal", "baseline", "duration", *WAVE_COLUMNS}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{path.name} is missing columns: {missing}")

    duration = pd.to_numeric(frame["duration"], errors="coerce")
    baseline = pd.to_numeric(frame["baseline"], errors="coerce")
    signal = pd.to_numeric(frame["signal"], errors="coerce")
    mask = (
        duration.between(DURATION_LIMIT[0] + 1, DURATION_LIMIT[1] - 1)
        & baseline.between(BASELINE_LIMIT[0] + 1, BASELINE_LIMIT[1] - 1)
        & signal.between(SIGNAL_LIMIT[0] + 1e-12, SIGNAL_LIMIT[1] - 1e-12)
        & frame[group_by].notna()
    )
    result = frame.loc[mask, [group_by, *WAVE_COLUMNS]].copy()
    result["duration"] = duration.loc[mask]
    result["relative_signal"] = signal.loc[mask]
    result["absolute_signal"] = signal.loc[mask] + baseline.loc[mask]
    print(f"[{sample}] using {len(result):,}/{len(frame):,} rows from {path.name}")
    return result[[group_by, *FEATURE_COLUMNS]]


def pairwise_effects(frame: pd.DataFrame, group_by: str) -> pd.DataFrame:
    """Return distribution difference (KS) and standardized mean difference."""
    records: list[dict[str, object]] = []
    for feature in FEATURE_COLUMNS:
        values = pd.to_numeric(frame[feature], errors="coerce")
        groups = {
            str(name): values.loc[index].dropna().to_numpy()
            for name, index in frame.groupby(group_by, sort=True).groups.items()
        }
        for (name_a, a), (name_b, b) in combinations(groups.items(), 2):
            if len(a) < 2 or len(b) < 2:
                continue
            pooled_sd = np.sqrt((np.var(a, ddof=1) + np.var(b, ddof=1)) / 2)
            smd = (np.mean(a) - np.mean(b)) / pooled_sd if pooled_sd > 0 else np.nan
            ks = ks_2samp(a, b, method="auto")
            records.append({
                "Feature": feature, "GroupA": name_a, "GroupB": name_b,
                "N_A": len(a), "N_B": len(b),
                "MeanDifference_A_minus_B": np.mean(a) - np.mean(b),
                "StandardizedMeanDifference": smd,
                "KSStatistic": ks.statistic, "KSPValue": ks.pvalue,
            })
    return pd.DataFrame.from_records(records)


def descriptive_stats_by_group(frame: pd.DataFrame, group_by: str) -> pd.DataFrame:
    """Return one row per group with wide descriptive-statistic columns.

    ``N`` is the number of filtered events in the group. Statistics ignore missing
    values and use population standard deviation (ddof=0), matching data_stat().
    """
    numeric = frame[[group_by]].copy()
    numeric[FEATURE_COLUMNS] = frame[FEATURE_COLUMNS].apply(
        pd.to_numeric, errors="coerce"
    )

    records: list[dict[str, object]] = []
    for group_name, group in numeric.groupby(group_by, sort=True, dropna=False):
        record: dict[str, object] = {group_by: group_name, "N": len(group)}
        for feature in FEATURE_COLUMNS:
            values = group[feature].dropna()
            prefix = f"{feature}_"
            record.update({
                f"{prefix}mean": values.mean(),
                f"{prefix}median": values.median(),
                f"{prefix}std": values.std(ddof=0),
                f"{prefix}q1": values.quantile(0.25),
                f"{prefix}q3": values.quantile(0.75),
            })
        records.append(record)
    return pd.DataFrame.from_records(records)


def create_group_histograms(
    frame: pd.DataFrame,
    group_by: str,
    output_dir: Path,
    bins: str | int,
    max_groups_per_plot: int,
) -> None:
    """Create readable histograms, splitting a large number of groups into batches."""
    groups = sorted(frame[group_by].astype(str).unique())
    batches = [
        groups[start:start + max_groups_per_plot]
        for start in range(0, len(groups), max_groups_per_plot)
    ]
    all_hist_stats: list[pd.DataFrame] = []
    all_summary_stats: list[pd.DataFrame] = []

    for batch_number, batch in enumerate(batches, start=1):
        batch_frame = frame.loc[frame[group_by].astype(str).isin(batch)]
        if len(batches) == 1:
            plot_dir = output_dir
        else:
            plot_dir = output_dir / f"histograms_{batch_number:03d}"
            plot_dir.mkdir(parents=True, exist_ok=False)

        hist_stats, summary_stats = data_stat(
            batch_frame,
            FEATURE_COLUMNS,
            bins=bins,
            save_dir=plot_dir,
            feature_upper_limits=DEFAULT_UPPER_LIMITS,
            group_col=group_by,
            split_group_prefix=False,
        )
        hist_stats["PlotBatch"] = batch_number
        summary_stats["PlotBatch"] = batch_number
        all_hist_stats.append(hist_stats)
        all_summary_stats.append(summary_stats)

    # Keep consolidated tables at the comparison root even when images are split.
    if len(batches) > 1:
        pd.concat(all_hist_stats, ignore_index=True).to_csv(
            output_dir / "hist_stats.csv", index=False, encoding="utf-8-sig"
        )
        pd.concat(all_summary_stats, ignore_index=True).to_csv(
            output_dir / "summary_stats.csv", index=False, encoding="utf-8-sig"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare RMC features between distance or sample_name groups.")
    parser.add_argument(
        "samples",
        nargs="*",
        default=DEFAULT_SAMPLES,
        help="Sample IDs (default: oxytocin vasopressin)",
    )
    parser.add_argument(
        "--group-by", choices=(*GROUP_COLUMNS, "both"), default="both",
        help="Comparison unit. The default 'both' creates distance and sample_name results.",
    )
    parser.add_argument("--data-root", type=Path, default=paths.feature_dir("rmc"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--bins", default="auto")
    parser.add_argument(
        "--max-groups-per-plot",
        type=int,
        default=DEFAULT_MAX_GROUPS_PER_PLOT,
        help="Maximum groups overlaid in one histogram image (default: 12)",
    )
    args = parser.parse_args()
    if args.bins != "auto":
        try:
            args.bins = int(args.bins)
        except ValueError:
            parser.error("--bins must be a positive integer or 'auto'")
        if args.bins <= 0:
            parser.error("--bins must be positive")
    if args.max_groups_per_plot <= 0:
        parser.error("--max-groups-per-plot must be positive")
    return args


def main() -> None:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    group_columns = GROUP_COLUMNS if args.group_by == "both" else (args.group_by,)

    for sample in args.samples:
        sample_output_dir = args.output_root / f"{stamp}_{sample}"
        for group_by in group_columns:
            frame = load_features(sample, args.data_root, group_by)
            if frame.empty:
                print(f"[{sample}/{group_by}] skipped: no rows remain after filtering")
                continue
            if group_by == "distance":
                frame[group_by] = frame[group_by].map(lambda x: f"{float(x):g}")

            output_dir = sample_output_dir / group_by
            output_dir.mkdir(parents=True, exist_ok=False)
            create_group_histograms(
                frame,
                group_by,
                output_dir,
                args.bins,
                args.max_groups_per_plot,
            )
            pairwise_effects(frame, group_by).to_csv(
                output_dir / "pairwise_group_differences.csv",
                index=False,
                encoding="utf-8-sig",
            )
            frame.groupby(group_by).size().rename("N").to_csv(
                output_dir / "group_counts.csv", encoding="utf-8-sig"
            )
            if group_by == "sample_name":
                descriptive_stats_by_group(frame, group_by).to_csv(
                    output_dir / "sample_name_descriptive_stats.csv",
                    index=False,
                    encoding="utf-8-sig",
                )
            print(f"Saved {sample}/{group_by} results: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
