"""Convert raw TDMS metadata into a safe searchable catalog."""

import math
import re
from typing import Any

from ml_n.io import RawMeasurement

from .schema import FileCatalogRecord


_LEADING_NUMBER = re.compile(
    r"^[\s]*([-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?)"
)


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    if text.lower() in {"nan", "none", "null"}:
        return None

    return text


def normalize_float(value: Any) -> float | None:
    """文字列の先頭にある数値をfloatへ変換する。

    例:
        "-100_DV_SC91..." -> -100.0
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _LEADING_NUMBER.match(str(value))

        if match is None:
            return None

        number = float(match.group(1))

    if not math.isfinite(number):
        return None

    return number


def normalize_int(value: Any) -> int | None:
    number = normalize_float(value)

    if number is None:
        return None

    return int(number)


def _get(
    measurement: RawMeasurement,
    group_name: str,
    channel_name: str,
) -> Any:
    return measurement.metadata_tables.get(
        group_name,
        {},
    ).get(channel_name)


def build_catalog_record(
    measurement: RawMeasurement,
) -> FileCatalogRecord:
    """RawMeasurementから許可済みフィールドだけを抽出する。"""
    return FileCatalogRecord(
        file_id=measurement.file_id,
        source_filename=measurement.source_filename,
        waveform_sha256=measurement.waveform_sha256,
        sampling_rate_hz=measurement.sampling_rate_hz,
        n_points=measurement.n_points,
        duration_s=measurement.duration_s,

        experimental_id=normalize_text(
            _get(measurement, "Gap Table", "Experimental ID")
        ),
        experiment_date=normalize_text(
            _get(measurement, "Gap Table", "Experiment Date")
        ),
        sample_name=normalize_text(
            _get(measurement, "Gap Table", "Sample Name")
        ),
        solution_medium=normalize_text(
            _get(measurement, "Gap Table", "Solution Medium")
        ),

        device_code=normalize_text(
            _get(measurement, "Gap Table", "Device Code")
        ),
        device_wafer=normalize_text(
            _get(measurement, "Gap Table", "Device Wafer")
        ),
        device_lot=normalize_text(
            _get(measurement, "Gap Table", "Device Lot")
        ),
        device_condition=normalize_text(
            _get(measurement, "Gap Table", "Device Condition")
        ),
        cover=normalize_text(
            _get(measurement, "Gap Table", "Cover")
        ),
        gap_id=normalize_text(
            _get(measurement, "Gap Table", "GAP_ID")
        ),
        gap_number=normalize_text(
            _get(measurement, "Gap Table", "GAP_#")
        ),

        recipe_name=normalize_text(
            _get(measurement, "Try Table", "RecipeName")
        ),
        distance_nm=normalize_float(
            _get(measurement, "Try Table", "D")
        ),
        calibration_pm_per_v=normalize_float(
            _get(measurement, "Try Table", "CAL")
        ),
        target_current_pa=normalize_float(
            _get(measurement, "Try Table", "I")
        ),
        bias_mv=normalize_float(
            _get(measurement, "Try Table", "Bias")
        ),
        ep1_mv=normalize_float(
            _get(measurement, "Try Table", "EP1")
        ),
        ep2_mv=normalize_float(
            _get(measurement, "Try Table", "EP2")
        ),
        configured_experiment_min=normalize_float(
            _get(measurement, "Try Table", "Ex min")
        ),

        exsv_number=normalize_int(
            _get(measurement, "Servo Table", "EXSV#")
        ),
        exsv_time=normalize_text(
            _get(measurement, "Servo Table", "EXSV Time")
        ),
        exsv_elapsed_s=normalize_float(
            _get(measurement, "Servo Table", "EXSV ex Time [s]")
        ),
        exsv_current_pa=normalize_float(
            _get(measurement, "Servo Table", "EXSV Current [pA]")
        ),
        exsv_rms_pa=normalize_float(
            _get(measurement, "Servo Table", "EXSV RMS [pA]")
        ),
        exsv_set_distance_nm=normalize_float(
            _get(
                measurement,
                "Servo Table",
                "EXSV Set Distance [nm] ",
            )
        ),
        exsv_set_tunnel_current_pa=normalize_float(
            _get(
                measurement,
                "Servo Table",
                "EXSV Set Tunnel Current [pA] ",
            )
        ),
        exsv_target_baseline_pa=normalize_float(
            _get(
                measurement,
                "Servo Table",
                "EXSV Target BL Current [pA] ",
            )
        ),
        exsv_total_signal_count=normalize_int(
            _get(
                measurement,
                "Servo Table",
                "EXSV Total Signal # [n] ",
            )
        ),
        exsv_signal_rate_per_s=normalize_float(
            _get(
                measurement,
                "Servo Table",
                "EXSV Signal Rate [/s]",
            )
        ),
        exsv_run_time_min=normalize_float(
            _get(
                measurement,
                "Servo Table",
                "EXSV Run Time [min] ",
            )
        ),

        machine_code=normalize_text(
            _get(measurement, "Machine Table", "Machine Code")
        ),
        machine_selection=normalize_text(
            _get(
                measurement,
                "Machine Table",
                "Machine selection",
            )
        ),
    )