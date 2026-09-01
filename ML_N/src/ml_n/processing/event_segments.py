"""Extract aligned waveform regions around detected events."""

from dataclasses import dataclass

import numpy as np

from ml_n.io import RawMeasurement

from .event_detection import MeasurementDetectionResult


@dataclass(frozen=True)
class EventSegment:
    """Waveform arrays and index metadata for one detected event."""

    file_id: str
    event_id: str
    sampling_rate_hz: float

    window_start_index: int
    window_end_index: int
    event_start_offset: int
    event_end_offset: int
    peak_offset: int

    current_pa: np.ndarray
    smoothed_current_pa: np.ndarray
    baseline_pa: np.ndarray
    relative_current_pa: np.ndarray
    noise_pa: np.ndarray

    @property
    def n_points(self) -> int:
        return self.window_end_index - self.window_start_index


def _noise_curve(
    noise_pa: float | np.ndarray,
    *,
    n_points: int,
) -> np.ndarray:
    noise = np.asarray(noise_pa, dtype=float)

    if noise.ndim == 0:
        return np.full(n_points, float(noise), dtype=float)

    if noise.ndim != 1 or noise.shape[0] != n_points:
        raise ValueError(
            "noise_pa must be scalar or match the waveform length"
        )

    return noise


def extract_event_segments(
    measurement: RawMeasurement,
    result: MeasurementDetectionResult,
    *,
    pre_context_ms: float = 1.0,
    post_context_ms: float = 1.0,
) -> tuple[EventSegment, ...]:
    """Extract event regions with optional context before and after."""

    if pre_context_ms < 0 or post_context_ms < 0:
        raise ValueError("context durations must be non-negative")

    if any(
        record.file_id != measurement.file_id
        for record in result.records
    ):
        raise ValueError(
            "event records do not belong to the measurement"
        )

    current = np.asarray(measurement.current_pa, dtype=float)
    smoothed = np.asarray(
        result.detection.smoothed_current_pa,
        dtype=float,
    )
    baseline = np.asarray(
        result.detection.baseline_curve_pa,
        dtype=float,
    )

    if (
        current.ndim != 1
        or smoothed.shape != current.shape
        or baseline.shape != current.shape
    ):
        raise ValueError(
            "measurement and detection curves must have equal lengths"
        )

    noise = _noise_curve(
        result.detection.noise_pa,
        n_points=current.size,
    )
    sampling_rate_hz = measurement.sampling_rate_hz
    pre_points = round(
        pre_context_ms * sampling_rate_hz / 1000.0
    )
    post_points = round(
        post_context_ms * sampling_rate_hz / 1000.0
    )

    segments = []

    for record in result.records:
        window_start = max(
            0,
            record.start_index - pre_points,
        )
        window_end = min(
            current.size,
            record.end_index + post_points,
        )
        selection = slice(window_start, window_end)
        segment_current = current[selection].copy()
        segment_baseline = baseline[selection].copy()

        segments.append(
            EventSegment(
                file_id=record.file_id,
                event_id=record.event_id,
                sampling_rate_hz=sampling_rate_hz,
                window_start_index=window_start,
                window_end_index=window_end,
                event_start_offset=(
                    record.start_index - window_start
                ),
                event_end_offset=(
                    record.end_index - window_start
                ),
                peak_offset=(
                    record.peak_index - window_start
                ),
                current_pa=segment_current,
                smoothed_current_pa=(
                    smoothed[selection].copy()
                ),
                baseline_pa=segment_baseline,
                relative_current_pa=(
                    segment_current - segment_baseline
                ),
                noise_pa=noise[selection].copy(),
            )
        )

    return tuple(segments)
