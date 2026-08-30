"""Reader for LabVIEW-generated ANAL TDMS files."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

import numpy as np
from nptdms import TdmsFile

from .raw_tdms_reader import infer_sampling_rate_hz


@dataclass(frozen=True)
class AnalEvent:
    signal_number: int

    start_index: int
    end_index: int
    peak_index: int

    start_time_s: float
    end_time_s: float
    peak_time_s: float

    duration_ms: float
    dead_time_s: float

    relative_signal_pa: float
    region_baseline_pa: float
    region_std_pa: float
    threshold_pa: float
    rt_baseline_pa: float

    @property
    def n_points(self) -> int:
        return self.end_index - self.start_index

    @property
    def absolute_peak_pa(self) -> float:
        return self.region_baseline_pa + self.relative_signal_pa


@dataclass
class AnalMeasurement:
    source_path: Path
    source_filename: str
    raw_source_filename: str | None

    file_id: str
    waveform_sha256: str
    sampling_rate_hz: float
    current_scale_to_pa: float
    current_pa: np.ndarray

    events: tuple[AnalEvent, ...]
    analysis_config: dict[str, Any]
    analysis_summary: dict[str, Any]

    @property
    def n_points(self) -> int:
        return int(self.current_pa.size)

    @property
    def duration_s(self) -> float:
        return self.n_points / self.sampling_rate_hz


def parse_sampling_rate_hz(value: Any) -> float | None:
    """`10kHz`、`20000 Hz`などをHzへ変換する。"""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        result = float(value)
        return result if result > 0 else None

    text = str(value).strip()

    match = re.search(
        r"([-+]?\d+(?:\.\d+)?)\s*([kKmM]?)\s*[hH][zZ]",
        text,
    )

    if match is None:
        return None

    number = float(match.group(1))
    prefix = match.group(2).lower()

    multiplier = {
        "": 1.0,
        "k": 1000.0,
        "m": 1_000_000.0,
    }[prefix]

    result = number * multiplier
    return result if result > 0 else None


def _python_value(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()

    return value


def _read_group(tdms: TdmsFile, group_name: str) -> dict[str, Any]:
    if group_name not in tdms:
        return {}

    result = {}

    for channel in tdms[group_name].channels():
        values = channel[:]

        if len(values) == 0:
            value = None
        elif len(values) == 1:
            value = _python_value(values[0])
        else:
            value = [
                _python_value(item)
                for item in values
            ]

        result[channel.name] = value

    return result


def _required_float_column(group, channel_name: str) -> np.ndarray:
    if channel_name not in group:
        raise KeyError(
            f"ANAL channel was not found: "
            f"S Table/{channel_name}"
        )

    try:
        return np.asarray(
            group[channel_name][:],
            dtype=float,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"ANAL channel could not be converted to float: "
            f"S Table/{channel_name}"
        ) from error


def read_anal_tdms(
    path: str | Path,
    *,
    sampling_rate_hz: float | None = None,
    current_scale_to_pa: float = 1000.0,
) -> AnalMeasurement:
    """ANAL TDMSの波形、設定、S Tableイベントを取得する。"""
    source_path = Path(path).expanduser().resolve()

    if not source_path.is_file():
        raise FileNotFoundError(
            f"ANAL TDMS file was not found: {source_path}"
        )

    if current_scale_to_pa <= 0:
        raise ValueError("current_scale_to_pa must be positive")

    with TdmsFile.read(source_path) as tdms:
        if "Data" not in tdms or "Ch1" not in tdms["Data"]:
            raise KeyError(
                "ANAL waveform was not found: Data/Ch1"
            )

        if "S Table" not in tdms:
            raise KeyError(
                "ANAL group was not found: S Table"
            )

        current_raw = np.asarray(
            tdms["Data"]["Ch1"][:],
            dtype=np.float64,
        )

        if current_raw.ndim != 1 or current_raw.size == 0:
            raise ValueError(
                "ANAL waveform must be a non-empty "
                "one-dimensional array"
            )

        if not np.all(np.isfinite(current_raw)):
            raise ValueError(
                "ANAL waveform contains non-finite values"
            )

        analysis_config = _read_group(tdms, "AC Table")
        analysis_summary = _read_group(tdms, "AR Table")

        raw_source_filename_value = analysis_summary.get(
            "Filename"
        )
        raw_source_filename = (
            str(raw_source_filename_value).strip()
            if raw_source_filename_value is not None
            else None
        )

        rate_from_ar = parse_sampling_rate_hz(
            analysis_summary.get("Data Aqusition Rate")
        )
        rate_from_filename = (
            infer_sampling_rate_hz(raw_source_filename)
            if raw_source_filename
            else None
        )

        effective_rate = (
            sampling_rate_hz
            if sampling_rate_hz is not None
            else rate_from_ar or rate_from_filename
        )

        if effective_rate is None or effective_rate <= 0:
            raise ValueError(
                "sampling_rate_hz could not be determined"
            )

        group = tdms["S Table"]

        signal_number = _required_float_column(
            group, "S # [n]"
        )
        peak_time = _required_float_column(
            group, "S Peak Position [s]"
        )
        relative_signal = _required_float_column(
            group, "Signal [pA]"
        )
        region_baseline = _required_float_column(
            group, "Region BL [pA]"
        )
        region_std = _required_float_column(
            group, "Region STD [pA]"
        )
        threshold = _required_float_column(
            group, "Threshold [pA]"
        )
        start_time = _required_float_column(
            group, "Signal S [s]"
        )
        end_time = _required_float_column(
            group, "Signal E (s)"
        )
        duration = _required_float_column(
            group, "S TL [ms]"
        )
        dead_time = _required_float_column(
            group, "S DL [s]"
        )
        rt_baseline = _required_float_column(
            group, "RT BL [pA]"
        )

        columns = (
            signal_number,
            peak_time,
            relative_signal,
            region_baseline,
            region_std,
            threshold,
            start_time,
            end_time,
            duration,
            dead_time,
            rt_baseline,
        )

        lengths = {len(column) for column in columns}

        if len(lengths) != 1:
            raise ValueError(
                "ANAL S Table columns have different lengths"
            )

        events = []

        for index in range(len(signal_number)):
            start_index = int(
                round(start_time[index] * effective_rate)
            )
            end_index = int(
                round(end_time[index] * effective_rate)
            )
            peak_index = int(
                round(peak_time[index] * effective_rate)
            )

            if end_index <= start_index:
                raise ValueError(
                    f"Invalid ANAL event interval at row {index}"
                )

            if not start_index <= peak_index < end_index:
                raise ValueError(
                    f"Invalid ANAL peak position at row {index}"
                )

            events.append(
                AnalEvent(
                    signal_number=int(signal_number[index]),
                    start_index=start_index,
                    end_index=end_index,
                    peak_index=peak_index,
                    start_time_s=float(start_time[index]),
                    end_time_s=float(end_time[index]),
                    peak_time_s=float(peak_time[index]),
                    duration_ms=float(duration[index]),
                    dead_time_s=float(dead_time[index]),
                    relative_signal_pa=float(
                        relative_signal[index]
                    ),
                    region_baseline_pa=float(
                        region_baseline[index]
                    ),
                    region_std_pa=float(region_std[index]),
                    threshold_pa=float(threshold[index]),
                    rt_baseline_pa=float(rt_baseline[index]),
                )
            )

    waveform_hash = sha256(
        current_raw.tobytes()
    ).hexdigest()

    return AnalMeasurement(
        source_path=source_path,
        source_filename=source_path.name,
        raw_source_filename=raw_source_filename,
        file_id=waveform_hash[:16],
        waveform_sha256=waveform_hash,
        sampling_rate_hz=float(effective_rate),
        current_scale_to_pa=float(current_scale_to_pa),
        current_pa=current_raw * current_scale_to_pa,
        events=tuple(events),
        analysis_config=analysis_config,
        analysis_summary=analysis_summary,
    )