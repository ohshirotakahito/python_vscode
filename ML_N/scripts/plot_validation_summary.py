"""Plot a concise Python-detector versus ANAL validation summary."""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ml_n.validation import (
    FileValidationMetrics,
    aggregate_file_metrics,
)


def _annotate_segments(ax, bars, values) -> None:
    for bar, value in zip(bars, values):
        if value <= 0:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_y() + bar.get_height() / 2,
            str(int(value)),
            ha="center",
            va="center",
            fontsize=9,
            color="black",
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot batch validation metrics against ANAL."
    )
    parser.add_argument("file_metrics_csv")
    parser.add_argument("--output-path")
    parser.add_argument(
        "--title",
        default="Python detector vs LabVIEW ANAL",
    )
    args = parser.parse_args()

    metrics_path = Path(args.file_metrics_csv)
    table = pd.read_csv(metrics_path)
    required = {
        "file_id",
        "pair_id",
        "python_event_count",
        "anal_event_count",
        "matched_count",
        "python_only_count",
        "anal_only_count",
        "precision",
        "recall",
        "f1",
    }
    missing = required - set(table.columns)
    if missing:
        raise ValueError(
            "metrics CSV is missing columns: "
            + ", ".join(sorted(missing))
        )
    if table.empty:
        raise ValueError("metrics CSV must contain at least one row")

    file_metrics = [
        FileValidationMetrics(
            file_id=str(row.file_id),
            python_event_count=int(row.python_event_count),
            anal_event_count=int(row.anal_event_count),
            matched_count=int(row.matched_count),
            python_only_count=int(row.python_only_count),
            anal_only_count=int(row.anal_only_count),
        )
        for row in table.itertuples(index=False)
    ]
    aggregate = aggregate_file_metrics(file_metrics)

    labels = [*table["pair_id"].astype(str), "Aggregate"]
    matched = np.append(
        table["matched_count"].to_numpy(dtype=float),
        aggregate.matched_count,
    )
    python_only = np.append(
        table["python_only_count"].to_numpy(dtype=float),
        aggregate.python_only_count,
    )
    anal_only = np.append(
        table["anal_only_count"].to_numpy(dtype=float),
        aggregate.anal_only_count,
    )
    precision = np.append(
        table["precision"].to_numpy(dtype=float),
        aggregate.precision,
    )
    recall = np.append(
        table["recall"].to_numpy(dtype=float),
        aggregate.recall,
    )
    f1 = np.append(
        table["f1"].to_numpy(dtype=float),
        aggregate.f1,
    )

    x = np.arange(len(labels))
    figure_width = max(11.0, 2.2 * len(labels))
    fig, (count_ax, metric_ax) = plt.subplots(
        1,
        2,
        figsize=(figure_width, 5.5),
        gridspec_kw={"width_ratios": (1.15, 1.0)},
    )

    matched_bars = count_ax.bar(
        x,
        matched,
        color="#4C9F70",
        label="Matched",
    )
    python_only_bars = count_ax.bar(
        x,
        python_only,
        bottom=matched,
        color="#F2A541",
        label="Python only",
    )
    anal_only_bars = count_ax.bar(
        x,
        anal_only,
        bottom=matched + python_only,
        color="#D95D5D",
        label="ANAL only",
    )
    _annotate_segments(count_ax, matched_bars, matched)
    _annotate_segments(count_ax, python_only_bars, python_only)
    _annotate_segments(count_ax, anal_only_bars, anal_only)
    count_ax.set_title("Event matching")
    count_ax.set_ylabel("Event count")
    count_ax.set_xticks(x, labels, rotation=20, ha="right")
    count_ax.legend(frameon=False)
    count_ax.grid(axis="y", alpha=0.2)

    width = 0.24
    metric_ax.bar(
        x - width,
        precision,
        width,
        color="#4C78A8",
        label="Precision",
    )
    metric_ax.bar(
        x,
        recall,
        width,
        color="#72B7B2",
        label="Recall",
    )
    f1_bars = metric_ax.bar(
        x + width,
        f1,
        width,
        color="#B279A2",
        label="F1",
    )
    for bar, value in zip(f1_bars, f1):
        metric_ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.025,
            f"{value:.3f}",
            ha="center",
            va="bottom",
            fontsize=8,
            rotation=90,
        )
    metric_ax.set_title("Detection metrics")
    metric_ax.set_ylabel("Score")
    metric_ax.set_ylim(0.0, 1.05)
    metric_ax.set_xticks(x, labels, rotation=20, ha="right")
    metric_ax.legend(frameon=False)
    metric_ax.grid(axis="y", alpha=0.2)

    fig.suptitle(args.title)
    fig.tight_layout()

    output_path = (
        Path(args.output_path)
        if args.output_path
        else metrics_path.with_name("validation_summary.png")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved plot: {output_path}")


if __name__ == "__main__":
    main()
