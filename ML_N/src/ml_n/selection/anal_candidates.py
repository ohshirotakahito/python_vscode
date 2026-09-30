"""Discover, stratify, and copy representative ANAL/RAW TDMS pairs."""

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import random
import re
import shutil
from typing import Callable, Iterable

from nptdms import TdmsFile

from ml_n.io import read_anal_tdms, read_raw_tdms


_MEASUREMENT_PATTERN = re.compile(
    r"Sample#(?P<number>\d+)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class AnalCountCandidate:
    sample_name: str
    measurement_number: str
    raw_source_filename: str
    raw_tdms_path: Path
    anal_tdms_path: Path
    region_count: int
    signal_count: int
    frequency_per_1000_regions: float
    raw_exists: bool
    selection_stratum: str | None = None

    @property
    def pair_id(self) -> str:
        return (
            f"{self.sample_name}-N{self.measurement_number}"
        )

    def to_dict(self) -> dict:
        row = asdict(self)
        row["pair_id"] = self.pair_id
        row["raw_tdms_path"] = str(self.raw_tdms_path)
        row["anal_tdms_path"] = str(self.anal_tdms_path)
        return row


def _first_value(channel) -> object:
    values = channel[:1]
    if len(values) == 0:
        raise ValueError(
            f"TDMS channel is empty: {channel.path}"
        )
    return values[0]


def read_anal_count_metadata(
    path: str | Path,
) -> tuple[str, int, int]:
    """Read RAW filename, region count, and signal count only."""

    # Open lazily: catalog generation needs only two scalar values and
    # the length of one channel, not every waveform sample in the file.
    with TdmsFile.open(path) as tdms:
        for group_name in ("AR Table", "R Table"):
            if group_name not in tdms:
                raise KeyError(
                    f"ANAL group was not found: {group_name}"
                )

        ar_group = tdms["AR Table"]
        r_group = tdms["R Table"]
        raw_filename = str(
            _first_value(ar_group["Filename"])
        ).strip()
        signal_count = int(
            _first_value(ar_group["Signal#Sum"])
        )
        region_count = len(r_group["R # [n]"])

    if not raw_filename:
        raise ValueError("ANAL RAW filename is empty")
    if region_count <= 0:
        raise ValueError("ANAL region count must be positive")
    if signal_count < 0:
        raise ValueError("ANAL signal count must be non-negative")

    return raw_filename, region_count, signal_count


def _measurement_number(filename: str) -> str:
    match = _MEASUREMENT_PATTERN.search(filename)
    if match is None:
        raise ValueError(
            f"Sample number was not found in filename: {filename}"
        )
    return match.group("number")


def discover_anal_candidates(
    server: str,
    keyfolder: str,
    ex: str,
    sample: str,
    *,
    metadata_reader: Callable[
        [str | Path], tuple[str, int, int]
    ] = read_anal_count_metadata,
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[AnalCountCandidate, ...]:
    """Scan ``T/ANAL`` and resolve RAW files in the parent ``T``."""

    experiment_root = Path(
        rf"\\{server}\{keyfolder}\{ex}"
    )
    if not experiment_root.is_dir():
        raise ValueError(
            f"experiment folder does not exist: {experiment_root}"
        )

    deduplicated: dict[
        tuple[str, str], AnalCountCandidate
    ] = {}

    sample_dir = experiment_root / sample
    if not sample_dir.is_dir():
        raise ValueError(
            f"sample folder does not exist: {sample_dir}"
        )

    for sample_dir in (sample_dir,):
        sample_name = sample_dir.name
        t_dir = (
            sample_dir
            / f"{sample_name}_10k_Sample"
            / "T"
        )
        anal_dir = t_dir / "ANAL"
        if not anal_dir.is_dir():
            continue

        for anal_path in sorted(anal_dir.glob("*.tdms")):
            try:
                (
                    raw_source_filename,
                    region_count,
                    signal_count,
                ) = metadata_reader(anal_path)
                raw_name = Path(raw_source_filename).name
                measurement_number = _measurement_number(
                    raw_name
                )
            except (KeyError, TypeError, ValueError):
                continue

            raw_path = t_dir / raw_name
            candidate = AnalCountCandidate(
                sample_name=sample_name,
                measurement_number=measurement_number,
                raw_source_filename=raw_name,
                raw_tdms_path=raw_path,
                anal_tdms_path=anal_path,
                region_count=region_count,
                signal_count=signal_count,
                frequency_per_1000_regions=(
                    signal_count / region_count * 1000.0
                ),
                raw_exists=raw_path.is_file(),
            )
            key = (sample_name, raw_name.casefold())
            previous = deduplicated.get(key)
            if (
                previous is None
                or anal_path.stat().st_mtime
                > previous.anal_tdms_path.stat().st_mtime
            ):
                deduplicated[key] = candidate

        if progress is not None:
            sample_candidates = tuple(
                item
                for item in deduplicated.values()
                if item.sample_name == sample_name
            )
            progress(
                sample_name,
                len(sample_candidates),
                sum(
                    item.raw_exists
                    for item in sample_candidates
                ),
            )

    return tuple(
        sorted(
            deduplicated.values(),
            key=lambda item: (
                item.sample_name.casefold(),
                int(item.measurement_number),
            ),
        )
    )


def _stratum_names(count: int) -> tuple[str, ...]:
    if count == 1:
        return ("middle",)
    if count == 2:
        return ("low", "high")
    return ("low", "middle", "high")


def select_frequency_strata(
    candidates: Iterable[AnalCountCandidate],
    *,
    per_sample: int = 3,
    random_seed: int = 42,
) -> tuple[AnalCountCandidate, ...]:
    """Select low/middle/high frequency pairs for every sample."""

    if per_sample < 1 or per_sample > 3:
        raise ValueError("per_sample must be between 1 and 3")

    grouped: dict[str, list[AnalCountCandidate]] = {}
    for candidate in candidates:
        if candidate.raw_exists:
            grouped.setdefault(
                candidate.sample_name,
                [],
            ).append(candidate)

    rng = random.Random(random_seed)
    selected = []

    for sample_name in sorted(grouped, key=str.casefold):
        ordered = sorted(
            grouped[sample_name],
            key=lambda item: (
                item.frequency_per_1000_regions,
                int(item.measurement_number),
            ),
        )
        group_count = min(per_sample, len(ordered))
        names = _stratum_names(group_count)
        chunks = [
            ordered[
                index * len(ordered) // group_count:
                (index + 1) * len(ordered) // group_count
            ]
            for index in range(group_count)
        ]

        for name, chunk in zip(names, chunks):
            choice = rng.choice(chunk)
            selected.append(
                replace(
                    choice,
                    selection_stratum=name,
                )
            )

    return tuple(selected)


def _copy_without_overwrite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.stat().st_size != source.stat().st_size:
            raise FileExistsError(
                f"destination exists with a different size: {destination}"
            )
        return
    shutil.copy2(source, destination)


def copy_selected_candidates(
    candidates: Iterable[AnalCountCandidate],
    destination_root: str | Path,
) -> tuple[dict, ...]:
    """Verify source waveform hashes and copy canonical pairs."""

    root = Path(destination_root)
    copied_rows = []

    for candidate in candidates:
        raw = read_raw_tdms(candidate.raw_tdms_path)
        anal = read_anal_tdms(candidate.anal_tdms_path)
        if raw.file_id != anal.file_id:
            raise ValueError(
                f"{candidate.pair_id}: RAW and ANAL hashes do not match"
            )

        pair_dir = (
            root
            / candidate.sample_name
            / f"N{candidate.measurement_number}"
        )
        raw_destination = (
            pair_dir / "raw" / candidate.raw_tdms_path.name
        )
        anal_destination = (
            pair_dir / "anal" / candidate.anal_tdms_path.name
        )
        _copy_without_overwrite(
            candidate.raw_tdms_path,
            raw_destination,
        )
        _copy_without_overwrite(
            candidate.anal_tdms_path,
            anal_destination,
        )
        copied_rows.append(
            {
                **candidate.to_dict(),
                "file_id": raw.file_id,
                "hash_match": True,
                "copied_raw_tdms_path": str(raw_destination),
                "copied_anal_tdms_path": str(anal_destination),
                "copy_status": "copied_or_reused",
            }
        )

    return tuple(copied_rows)
