import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from ml_n.io import read_anal_tdms, read_raw_tdms
from ml_n.processing import detect_measurement_events
from ml_n.validation import match_events
from signal_core import DetectionConfig, PipelineConfig


def overlaps_view(start_s, end_s, view_start_s, view_end_s):
    return end_s > view_start_s and start_s < view_end_s


def main():
    parser = argparse.ArgumentParser(
        description="Plot Python and ANAL detection results."
    )
    parser.add_argument("raw_tdms_path")
    parser.add_argument("anal_tdms_path")
    parser.add_argument(
        "--start-time-s",
        type=float,
        default=56.5,
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=2.0,
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
        "--max-peak-error-ms",
        type=float,
        default=1.0,
    )
    parser.add_argument(
        "--output-dir",
        default="data/validation/plots",
    )
    args = parser.parse_args()

    if args.start_time_s < 0:
        raise ValueError("start-time-s must be non-negative")

    if args.duration_s <= 0:
        raise ValueError("duration-s must be positive")

    print("Reading TDMS files...")
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
    )

    print("Running Python detection...")
    python_result = detect_measurement_events(raw, config)

    matching = match_events(
        python_result.records,
        anal.events,
        sampling_rate_hz=raw.sampling_rate_hz,
        max_peak_error_ms=args.max_peak_error_ms,
    )

    matched_python_ids = {
        match.python_event_id
        for match in matching.matches
    }
    matched_anal_numbers = {
        match.anal_signal_number
        for match in matching.matches
    }

    view_start_s = args.start_time_s
    view_end_s = min(
        args.start_time_s + args.duration_s,
        raw.duration_s,
    )

    sampling_rate_hz = raw.sampling_rate_hz

    start_index = max(
        0,
        int(np.floor(view_start_s * sampling_rate_hz)),
    )
    end_index = min(
        raw.n_points,
        int(np.ceil(view_end_s * sampling_rate_hz)),
    )

    indices = np.arange(start_index, end_index)
    time_s = indices / sampling_rate_hz

    detection = python_result.detection

    threshold_t1 = (
        detection.baseline_curve_pa
        + config.detection.threshold_k1
        * detection.noise_pa
    )
    threshold_t2 = (
        detection.baseline_curve_pa
        + config.detection.threshold_k2
        * detection.noise_pa
    )

    fig, ax = plt.subplots(figsize=(16, 8))

    ax.plot(
        time_s,
        raw.current_pa[start_index:end_index],
        color="0.45",
        linewidth=0.7,
        label="Raw current",
    )
    ax.plot(
        time_s,
        detection.smoothed_current_pa[start_index:end_index],
        color="tab:blue",
        linewidth=1.0,
        label="Python smoothed",
    )
    ax.plot(
        time_s,
        detection.baseline_curve_pa[start_index:end_index],
        color="tab:green",
        linewidth=1.3,
        label="Python baseline",
    )
    ax.plot(
        time_s,
        threshold_t1[start_index:end_index],
        color="tab:orange",
        linestyle="--",
        linewidth=1.0,
        label="Python t1",
    )
    ax.plot(
        time_s,
        threshold_t2[start_index:end_index],
        color="tab:red",
        linestyle="--",
        linewidth=1.0,
        label="Python t2",
    )

    label_used = set()

    def span(start, end, color, label, alpha=0.15):
        visible_label = (
            label if label not in label_used else None
        )
        ax.axvspan(
            start,
            end,
            color=color,
            alpha=alpha,
            label=visible_label,
        )
        label_used.add(label)

    for event in python_result.records:
        if not overlaps_view(
            event.start_time_s,
            event.end_time_s,
            view_start_s,
            view_end_s,
        ):
            continue

        if event.event_id in matched_python_ids:
            color = "tab:green"
            label = "Matched event"
        else:
            color = "tab:orange"
            label = "Python only"

        span(
            event.start_time_s,
            event.end_time_s,
            color,
            label,
        )

        ax.scatter(
            event.peak_time_s,
            event.peak_current_pa,
            color=color,
            marker="o",
            s=35,
            zorder=6,
        )

    for event in anal.events:
        if not overlaps_view(
            event.start_time_s,
            event.end_time_s,
            view_start_s,
            view_end_s,
        ):
            continue

        if event.signal_number not in matched_anal_numbers:
            span(
                event.start_time_s,
                event.end_time_s,
                "tab:red",
                "ANAL only",
                alpha=0.22,
            )
            marker_color = "tab:red"
        else:
            marker_color = "darkgreen"

        peak_current = raw.current_pa[event.peak_index]

        ax.scatter(
            event.peak_time_s,
            peak_current,
            color=marker_color,
            marker="x",
            s=55,
            linewidth=1.5,
            zorder=7,
        )

        ax.scatter(
            event.peak_time_s,
            event.region_baseline_pa,
            color="purple",
            marker="_",
            s=80,
            zorder=7,
            label=(
                "ANAL region baseline"
                if "ANAL region baseline" not in label_used
                else None
            ),
        )
        label_used.add("ANAL region baseline")

        ax.scatter(
            event.peak_time_s,
            event.threshold_pa,
            color="black",
            marker="_",
            s=80,
            zorder=7,
            label=(
                "ANAL threshold"
                if "ANAL threshold" not in label_used
                else None
            ),
        )
        label_used.add("ANAL threshold")

    python_in_view = [
        event
        for event in python_result.records
        if overlaps_view(
            event.start_time_s,
            event.end_time_s,
            view_start_s,
            view_end_s,
        )
    ]
    anal_in_view = [
        event
        for event in anal.events
        if overlaps_view(
            event.start_time_s,
            event.end_time_s,
            view_start_s,
            view_end_s,
        )
    ]

    ax.set_xlim(view_start_s, view_end_s)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Current [pA]")
    ax.set_title(
        f"Python vs ANAL detection\n"
        f"file_id={raw.file_id}, "
        f"view={view_start_s:.3f}-{view_end_s:.3f}s, "
        f"Python={len(python_in_view)}, "
        f"ANAL={len(anal_in_view)}"
    )
    ax.grid(alpha=0.2)
    ax.legend(loc="upper right", ncol=2)

    fig.tight_layout()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    start_tag = f"{view_start_s:.3f}".replace(".", "p")
    end_tag = f"{view_end_s:.3f}".replace(".", "p")

    output_path = (
        output_dir
        / f"{raw.file_id}_{start_tag}-{end_tag}s.png"
    )

    fig.savefig(output_path, dpi=160)
    print(f"Saved: {output_path.resolve()}")

    plt.show()


if __name__ == "__main__":
    main()