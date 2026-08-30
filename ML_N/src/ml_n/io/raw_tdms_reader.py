"""Reader for original measurement TDMS files."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

import numpy as np
from nptdms import TdmsFile


WAVEFORM_GROUP = "Example"
WAVEFORM_CHANNEL = "Ch1"

METADATA_GROUPS = (
    "Machine Table",
    "Gap Table",
    "Try Table",
    "Servo Table",
    "SRZ Table",
)


@dataclass
class RawMeasurement:
    """元TDMSから取得した波形と測定情報。"""

    source_path: Path
    source_filename: str
    file_id: str
    waveform_sha256: str

    sampling_rate_hz: float
    current_scale_to_pa: float
    current_pa: np.ndarray

    metadata_tables: dict[str, dict[str, Any]]

    @property
    def n_points(self) -> int:
        return int(self.current_pa.size)

    @property
    def duration_s(self) -> float:
        return self.n_points / self.sampling_rate_hz


def infer_sampling_rate_hz(filename: str) -> float | None:
    """`*_10k_Sample*` などのファイル名から周波数を推定する。"""
    match = re.search(
        r"_(\d+(?:\.\d+)?)k_Sample",
        filename,
        flags=re.IGNORECASE,
    )

    if match is None:
        return None

    return float(match.group(1)) * 1000.0


def _to_python_value(value: Any) -> Any:
    """NumPy scalarを通常のPython型へ変換する。"""
    if isinstance(value, np.generic):
        return value.item()

    return value


def _read_metadata_group(tdms: TdmsFile, group_name: str) -> dict[str, Any]:
    """指定groupの全channelを辞書として取得する。"""
    if group_name not in tdms:
        return {}

    result: dict[str, Any] = {}
    group = tdms[group_name]

    for channel in group.channels():
        values = channel[:]

        if len(values) == 0:
            value = None
        elif len(values) == 1:
            value = _to_python_value(values[0])
        else:
            value = [
                _to_python_value(item)
                for item in values
            ]

        result[channel.name] = value

    return result


def read_raw_tdms(
    path: str | Path,
    *,
    sampling_rate_hz: float | None = None,
    current_scale_to_pa: float = 1000.0,
) -> RawMeasurement:
    """元TDMSを読み、pA波形と測定メタデータを返す。"""
    source_path = Path(path).expanduser().resolve()

    if not source_path.is_file():
        raise FileNotFoundError(
            f"TDMS file was not found: {source_path}"
        )

    if current_scale_to_pa <= 0:
        raise ValueError("current_scale_to_pa must be positive")

    inferred_rate = infer_sampling_rate_hz(source_path.name)
    effective_rate = (
        sampling_rate_hz
        if sampling_rate_hz is not None
        else inferred_rate
    )

    if effective_rate is None:
        raise ValueError(
            "sampling_rate_hz could not be inferred from the filename; "
            "specify it explicitly"
        )

    if effective_rate <= 0:
        raise ValueError("sampling_rate_hz must be positive")

    with TdmsFile.read(source_path) as tdms:
        if WAVEFORM_GROUP not in tdms:
            raise KeyError(
                f"TDMS group was not found: {WAVEFORM_GROUP}"
            )

        group = tdms[WAVEFORM_GROUP]

        if WAVEFORM_CHANNEL not in group:
            raise KeyError(
                f"TDMS channel was not found: "
                f"{WAVEFORM_GROUP}/{WAVEFORM_CHANNEL}"
            )

        current_raw = np.asarray(
            group[WAVEFORM_CHANNEL][:],
            dtype=np.float64,
        )

        if current_raw.ndim != 1:
            raise ValueError("TDMS waveform must be one-dimensional")

        if current_raw.size == 0:
            raise ValueError("TDMS waveform must not be empty")

        if not np.all(np.isfinite(current_raw)):
            raise ValueError(
                "TDMS waveform contains non-finite values"
            )

        metadata_tables = {
            group_name: _read_metadata_group(tdms, group_name)
            for group_name in METADATA_GROUPS
        }

    waveform_hash = sha256(
        current_raw.tobytes()
    ).hexdigest()

    file_id = waveform_hash[:16]

    current_pa = current_raw * current_scale_to_pa

    return RawMeasurement(
        source_path=source_path,
        source_filename=source_path.name,
        file_id=file_id,
        waveform_sha256=waveform_hash,
        sampling_rate_hz=float(effective_rate),
        current_scale_to_pa=float(current_scale_to_pa),
        current_pa=current_pa,
        metadata_tables=metadata_tables,
    )