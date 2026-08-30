"""Connect RawMeasurement with the shared signal-core pipeline."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from signal_core import (
    DetectionResult,
    PipelineConfig,
    detect_events_from_waveform,
)

from ml_n.catalog import build_catalog_record
from ml_n.io import RawMeasurement


@dataclass(frozen=True)
class EventRecord:
    file_id: str
    event_id: str

    start_index: int
    end_index: int
    peak_index: int

    start_time_s: float
    end_time_s: float
    peak_time_s: float
    duration_ms: float

    baseline_pa: float
    noise_pa: float
    peak_current_pa: float
    relative_peak_pa: float

    detector_name: str
    detector_version: str
    config_hash: str

    sample_name: str | None
    gap_id: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MeasurementDetectionResult:
    records: tuple[EventRecord, ...]
    detection: DetectionResult
    config_hash: str


def calculate_config_hash(config: PipelineConfig) -> str:
    """検出設定から再現用の安定したハッシュを作る。"""
    serialized = json.dumps(
        asdict(config),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    return sha256(
        serialized.encode("utf-8")
    ).hexdigest()[:16]


def _make_event_id(
    *,
    file_id: str,
    config_hash: str,
    start_index: int,
    peak_index: int,
    end_index: int,
) -> str:
    source = (
        f"{file_id}|{config_hash}|"
        f"{start_index}|{peak_index}|{end_index}"
    )

    return sha256(
        source.encode("utf-8")
    ).hexdigest()[:20]


def detect_measurement_events(
    measurement: RawMeasurement,
    config: PipelineConfig,
) -> MeasurementDetectionResult:
    """1つの元TDMS波形から検索可能なイベント表を作る。"""
    if (
        config.detection.sampling_rate_hz
        != measurement.sampling_rate_hz
    ):
        raise ValueError(
            "DetectionConfig sampling_rate_hz does not match "
            "RawMeasurement sampling_rate_hz"
        )

    detection = detect_events_from_waveform(
        measurement.current_pa,
        config,
    )

    catalog = build_catalog_record(measurement)
    config_hash = calculate_config_hash(config)
    sampling_rate_hz = measurement.sampling_rate_hz

    records = []

    for event in detection.events:
        event_id = _make_event_id(
            file_id=measurement.file_id,
            config_hash=config_hash,
            start_index=event.start_index,
            peak_index=event.peak_index,
            end_index=event.end_index,
        )

        records.append(
            EventRecord(
                file_id=measurement.file_id,
                event_id=event_id,
                start_index=event.start_index,
                end_index=event.end_index,
                peak_index=event.peak_index,
                start_time_s=(
                    event.start_index / sampling_rate_hz
                ),
                end_time_s=(
                    event.end_index / sampling_rate_hz
                ),
                peak_time_s=(
                    event.peak_index / sampling_rate_hz
                ),
                duration_ms=event.duration_ms(
                    sampling_rate_hz
                ),
                baseline_pa=event.baseline_pa,
                noise_pa=event.noise_pa,
                peak_current_pa=event.peak_current_pa,
                relative_peak_pa=event.relative_peak_pa,
                detector_name=event.detector_name,
                detector_version=event.detector_version,
                config_hash=config_hash,
                sample_name=catalog.sample_name,
                gap_id=catalog.gap_id,
            )
        )

    return MeasurementDetectionResult(
        records=tuple(records),
        detection=detection,
        config_hash=config_hash,
    )