"""Safe searchable file catalog schema."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FileCatalogRecord:
    # ファイル・波形情報
    file_id: str
    source_filename: str
    waveform_sha256: str
    sampling_rate_hz: float
    n_points: int
    duration_s: float

    # 実験・試料情報
    experimental_id: str | None = None
    experiment_date: str | None = None
    sample_name: str | None = None
    solution_medium: str | None = None

    # デバイス情報
    device_code: str | None = None
    device_wafer: str | None = None
    device_lot: str | None = None
    device_condition: str | None = None
    cover: str | None = None
    gap_id: str | None = None
    gap_number: str | None = None

    # 基本測定条件
    recipe_name: str | None = None
    distance_nm: float | None = None
    calibration_pm_per_v: float | None = None
    target_current_pa: float | None = None
    bias_mv: float | None = None
    ep1_mv: float | None = None
    ep2_mv: float | None = None
    configured_experiment_min: float | None = None

    # EXSV測定条件
    exsv_number: int | None = None
    exsv_time: str | None = None
    exsv_elapsed_s: float | None = None
    exsv_current_pa: float | None = None
    exsv_rms_pa: float | None = None
    exsv_set_distance_nm: float | None = None
    exsv_set_tunnel_current_pa: float | None = None
    exsv_target_baseline_pa: float | None = None
    exsv_total_signal_count: int | None = None
    exsv_signal_rate_per_s: float | None = None
    exsv_run_time_min: float | None = None

    # 非機密の装置識別情報
    machine_code: str | None = None
    machine_selection: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)