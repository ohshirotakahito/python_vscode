"""Bulk construction of safe TDMS file catalogs."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ml_n.io import RawMeasurement, read_raw_tdms

from .normalize import build_catalog_record
from .schema import FileCatalogRecord


@dataclass(frozen=True)
class CatalogBuildError:
    source_path: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_path": self.source_path,
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass(frozen=True)
class CatalogBuildResult:
    records: tuple[FileCatalogRecord, ...]
    errors: tuple[CatalogBuildError, ...]
    skipped_non_raw: int
    skipped_duplicates: int


def discover_tdms_files(
    roots: Iterable[str | Path],
) -> tuple[Path, ...]:
    """ファイルまたはフォルダからTDMSを再帰検索する。"""
    discovered: dict[str, Path] = {}

    for root in roots:
        path = Path(root).expanduser()

        if path.is_file():
            candidates = [path] if path.suffix.lower() == ".tdms" else []
        elif path.is_dir():
            candidates = [
                candidate
                for candidate in path.rglob("*")
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() == ".tdms"
                )
            ]
        else:
            continue

        for candidate in candidates:
            resolved = candidate.resolve()
            key = str(resolved).casefold()
            discovered[key] = resolved

    return tuple(
        sorted(
            discovered.values(),
            key=lambda item: str(item).casefold(),
        )
    )


def _is_non_raw_tdms_error(error: Exception) -> bool:
    """ANAL等、元TDMS構造でないことを示すKeyErrorか判定する。"""
    if not isinstance(error, KeyError):
        return False

    message = str(error)

    return (
        "Example" in message
        or "Example/Ch1" in message
        or "Ch1" in message
    )


def build_catalog(
    tdms_paths: Iterable[str | Path],
    *,
    reader: Callable[..., RawMeasurement] = read_raw_tdms,
    sampling_rate_hz: float | None = None,
    current_scale_to_pa: float = 1000.0,
) -> CatalogBuildResult:
    """複数TDMSを処理し、安全なカタログレコードを作る。"""
    records: list[FileCatalogRecord] = []
    errors: list[CatalogBuildError] = []

    known_file_ids: set[str] = set()
    skipped_non_raw = 0
    skipped_duplicates = 0

    for item in tdms_paths:
        path = Path(item).expanduser().resolve()

        try:
            measurement = reader(
                path,
                sampling_rate_hz=sampling_rate_hz,
                current_scale_to_pa=current_scale_to_pa,
            )
        except Exception as error:
            if _is_non_raw_tdms_error(error):
                skipped_non_raw += 1
                continue

            errors.append(
                CatalogBuildError(
                    source_path=str(path),
                    error_type=type(error).__name__,
                    message=str(error),
                )
            )
            continue

        if measurement.file_id in known_file_ids:
            skipped_duplicates += 1
            continue

        known_file_ids.add(measurement.file_id)
        records.append(build_catalog_record(measurement))

    return CatalogBuildResult(
        records=tuple(records),
        errors=tuple(errors),
        skipped_non_raw=skipped_non_raw,
        skipped_duplicates=skipped_duplicates,
    )