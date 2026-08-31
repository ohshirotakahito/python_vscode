"""Utilities for aggregating validation results across files."""

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class FileValidationMetrics:
    """1組のRAW/ANAL比較結果。"""

    file_id: str
    python_event_count: int
    anal_event_count: int
    matched_count: int
    python_only_count: int
    anal_only_count: int

    def __post_init__(self) -> None:
        counts = (
            self.python_event_count,
            self.anal_event_count,
            self.matched_count,
            self.python_only_count,
            self.anal_only_count,
        )

        if any(count < 0 for count in counts):
            raise ValueError(
                "event counts must be non-negative"
            )

        if (
            self.python_event_count
            != self.matched_count + self.python_only_count
        ):
            raise ValueError(
                "python_event_count must equal "
                "matched_count + python_only_count"
            )

        if (
            self.anal_event_count
            != self.matched_count + self.anal_only_count
        ):
            raise ValueError(
                "anal_event_count must equal "
                "matched_count + anal_only_count"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AggregateValidationMetrics:
    """複数ファイルを合算した検証結果。"""

    file_count: int
    python_event_count: int
    anal_event_count: int
    matched_count: int
    python_only_count: int
    anal_only_count: int
    precision: float
    recall: float
    f1: float

    def to_dict(self) -> dict:
        return asdict(self)


def aggregate_file_metrics(
    metrics: Iterable[FileValidationMetrics],
) -> AggregateValidationMetrics:
    """ファイル別イベント数を合算して指標を計算する。"""

    rows = tuple(metrics)

    if not rows:
        raise ValueError(
            "at least one file metric is required"
        )

    python_event_count = sum(
        row.python_event_count for row in rows
    )
    anal_event_count = sum(
        row.anal_event_count for row in rows
    )
    matched_count = sum(
        row.matched_count for row in rows
    )
    python_only_count = sum(
        row.python_only_count for row in rows
    )
    anal_only_count = sum(
        row.anal_only_count for row in rows
    )

    precision_denominator = (
        matched_count + python_only_count
    )
    recall_denominator = (
        matched_count + anal_only_count
    )

    precision = (
        matched_count / precision_denominator
        if precision_denominator
        else 0.0
    )
    recall = (
        matched_count / recall_denominator
        if recall_denominator
        else 0.0
    )

    f1 = (
        2.0 * precision * recall
        / (precision + recall)
        if precision + recall
        else 0.0
    )

    return AggregateValidationMetrics(
        file_count=len(rows),
        python_event_count=python_event_count,
        anal_event_count=anal_event_count,
        matched_count=matched_count,
        python_only_count=python_only_count,
        anal_only_count=anal_only_count,
        precision=precision,
        recall=recall,
        f1=f1,
    )