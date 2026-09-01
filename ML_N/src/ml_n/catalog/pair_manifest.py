"""Discover RAW/ANAL pairs in the canonical source-data layout."""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re


_MEASUREMENT_PATTERN = re.compile(r"N(?P<number>\d+)$")


@dataclass(frozen=True)
class TdmsPairRecord:
    pair_id: str
    sample_name: str
    measurement_number: str
    raw_tdms_path: Path
    anal_tdms_path: Path
    status: str = "discovered"

    def to_manifest_row(
        self,
        manifest_dir: str | Path,
    ) -> dict[str, str]:
        base = Path(manifest_dir).resolve()
        row = asdict(self)
        row["raw_tdms_path"] = os.path.relpath(
            self.raw_tdms_path.resolve(),
            base,
        )
        row["anal_tdms_path"] = os.path.relpath(
            self.anal_tdms_path.resolve(),
            base,
        )
        return row


def _single_tdms(directory: Path, *, role: str) -> Path:
    files = sorted(directory.glob("*.tdms"))

    if len(files) != 1:
        raise ValueError(
            f"{directory}: expected exactly one {role} TDMS file, "
            f"found {len(files)}"
        )

    return files[0]


def discover_tdms_pairs(
    samples_root: str | Path,
) -> tuple[TdmsPairRecord, ...]:
    """Discover ``<sample>/N<number>/{raw,anal}`` folders."""

    root = Path(samples_root)

    if not root.is_dir():
        raise ValueError(
            f"samples root does not exist: {root}"
        )

    records = []

    for sample_dir in sorted(
        path for path in root.iterdir() if path.is_dir()
    ):
        for measurement_dir in sorted(
            path for path in sample_dir.iterdir() if path.is_dir()
        ):
            match = _MEASUREMENT_PATTERN.fullmatch(
                measurement_dir.name
            )
            if match is None:
                raise ValueError(
                    "measurement folder must use N<number>: "
                    f"{measurement_dir}"
                )

            number = match.group("number")
            raw_path = _single_tdms(
                measurement_dir / "raw",
                role="RAW",
            )
            anal_path = _single_tdms(
                measurement_dir / "anal",
                role="ANAL",
            )
            sample_name = sample_dir.name

            records.append(
                TdmsPairRecord(
                    pair_id=f"{sample_name}-N{number}",
                    sample_name=sample_name,
                    measurement_number=number,
                    raw_tdms_path=raw_path,
                    anal_tdms_path=anal_path,
                )
            )

    return tuple(records)


def resolve_manifest_path(
    value: str | Path,
    *,
    manifest_path: str | Path,
) -> Path:
    """Resolve absolute or manifest-relative input paths."""

    path = Path(value)
    if path.is_absolute():
        return path

    return (Path(manifest_path).resolve().parent / path).resolve()
