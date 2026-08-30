"""One-to-one matching between Python and LabVIEW ANAL events."""

from dataclasses import asdict, dataclass

from ml_n.io import AnalEvent
from ml_n.processing import EventRecord


@dataclass(frozen=True)
class EventMatch:
    python_event_id: str
    anal_signal_number: int

    python_peak_index: int
    anal_peak_index: int

    peak_error_ms: float
    start_error_ms: float
    end_error_ms: float
    duration_error_ms: float

    interval_iou: float
    baseline_error_pa: float
    relative_signal_error_pa: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EventMatchResult:
    matches: tuple[EventMatch, ...]
    python_only: tuple[EventRecord, ...]
    anal_only: tuple[AnalEvent, ...]

    @property
    def true_positive(self) -> int:
        return len(self.matches)

    @property
    def false_positive(self) -> int:
        return len(self.python_only)

    @property
    def false_negative(self) -> int:
        return len(self.anal_only)

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return (
            self.true_positive / denominator
            if denominator
            else 0.0
        )

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return (
            self.true_positive / denominator
            if denominator
            else 0.0
        )

    @property
    def f1(self) -> float:
        denominator = self.precision + self.recall
        return (
            2 * self.precision * self.recall / denominator
            if denominator
            else 0.0
        )


def interval_iou(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> float:
    intersection = max(
        0,
        min(first_end, second_end)
        - max(first_start, second_start),
    )

    if intersection == 0:
        return 0.0

    union = (
        max(first_end, second_end)
        - min(first_start, second_start)
    )

    return intersection / union if union > 0 else 0.0


def match_events(
    python_events: tuple[EventRecord, ...],
    anal_events: tuple[AnalEvent, ...],
    *,
    sampling_rate_hz: float,
    max_peak_error_ms: float = 1.0,
) -> EventMatchResult:
    """ピーク差と区間IoUを使ってイベントを一対一対応させる。"""
    if sampling_rate_hz <= 0:
        raise ValueError("sampling_rate_hz must be positive")

    if max_peak_error_ms < 0:
        raise ValueError("max_peak_error_ms must be non-negative")

    max_peak_error_points = int(
        round(
            max_peak_error_ms
            * sampling_rate_hz
            / 1000.0
        )
    )

    candidates = []

    for python_index, python_event in enumerate(python_events):
        for anal_index, anal_event in enumerate(anal_events):
            peak_distance = abs(
                python_event.peak_index
                - anal_event.peak_index
            )

            if peak_distance > max_peak_error_points:
                continue

            iou = interval_iou(
                python_event.start_index,
                python_event.end_index,
                anal_event.start_index,
                anal_event.end_index,
            )

            # 区間が重なる候補を優先し、次にIoU、
            # 最後にピーク距離で評価する。
            candidates.append(
                (
                    0 if iou > 0 else 1,
                    -iou,
                    peak_distance,
                    python_index,
                    anal_index,
                )
            )

    candidates.sort()

    matched_python = set()
    matched_anal = set()
    matches = []

    milliseconds_per_point = 1000.0 / sampling_rate_hz

    for (
        _no_overlap,
        negative_iou,
        _peak_distance,
        python_index,
        anal_index,
    ) in candidates:
        if python_index in matched_python:
            continue

        if anal_index in matched_anal:
            continue

        python_event = python_events[python_index]
        anal_event = anal_events[anal_index]

        matched_python.add(python_index)
        matched_anal.add(anal_index)

        matches.append(
            EventMatch(
                python_event_id=python_event.event_id,
                anal_signal_number=anal_event.signal_number,
                python_peak_index=python_event.peak_index,
                anal_peak_index=anal_event.peak_index,
                peak_error_ms=(
                    python_event.peak_index
                    - anal_event.peak_index
                ) * milliseconds_per_point,
                start_error_ms=(
                    python_event.start_index
                    - anal_event.start_index
                ) * milliseconds_per_point,
                end_error_ms=(
                    python_event.end_index
                    - anal_event.end_index
                ) * milliseconds_per_point,
                duration_error_ms=(
                    python_event.duration_ms
                    - anal_event.duration_ms
                ),
                interval_iou=-negative_iou,
                baseline_error_pa=(
                    python_event.baseline_pa
                    - anal_event.region_baseline_pa
                ),
                relative_signal_error_pa=(
                    python_event.relative_peak_pa
                    - anal_event.relative_signal_pa
                ),
            )
        )

    python_only = tuple(
        event
        for index, event in enumerate(python_events)
        if index not in matched_python
    )
    anal_only = tuple(
        event
        for index, event in enumerate(anal_events)
        if index not in matched_anal
    )

    return EventMatchResult(
        matches=tuple(matches),
        python_only=python_only,
        anal_only=anal_only,
    )