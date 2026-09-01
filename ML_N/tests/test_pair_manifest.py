from pathlib import Path

import pytest

from ml_n.catalog import (
    discover_tdms_pairs,
    resolve_manifest_path,
)


def make_pair(root: Path, sample: str, number: str) -> None:
    pair = root / sample / f"N{number}"
    (pair / "raw").mkdir(parents=True)
    (pair / "anal").mkdir(parents=True)
    (pair / "raw" / "raw.tdms").touch()
    (pair / "anal" / "anal.tdms").touch()


def test_discovers_canonical_pair_layout(tmp_path):
    samples = tmp_path / "source_tdms" / "samples"
    make_pair(samples, "oxytocin", "001")
    make_pair(samples, "AOMe", "002")

    records = discover_tdms_pairs(samples)

    assert [record.pair_id for record in records] == [
        "AOMe-N002",
        "oxytocin-N001",
    ]
    assert records[1].measurement_number == "001"


def test_manifest_rows_use_relative_paths(tmp_path):
    samples = tmp_path / "data" / "source_tdms" / "samples"
    make_pair(samples, "oxytocin", "001")
    record = discover_tdms_pairs(samples)[0]
    manifest = tmp_path / "data" / "manifests" / "pairs.csv"
    manifest.parent.mkdir(parents=True)

    row = record.to_manifest_row(manifest.parent)
    resolved = resolve_manifest_path(
        row["raw_tdms_path"],
        manifest_path=manifest,
    )

    assert not Path(row["raw_tdms_path"]).is_absolute()
    assert resolved == record.raw_tdms_path.resolve()


def test_rejects_missing_anal_file(tmp_path):
    samples = tmp_path / "samples"
    pair = samples / "oxytocin" / "N001"
    (pair / "raw").mkdir(parents=True)
    (pair / "anal").mkdir(parents=True)
    (pair / "raw" / "raw.tdms").touch()

    with pytest.raises(ValueError, match="ANAL"):
        discover_tdms_pairs(samples)


def test_rejects_noncanonical_measurement_folder(tmp_path):
    samples = tmp_path / "samples"
    (samples / "oxytocin" / "sample001").mkdir(parents=True)

    with pytest.raises(ValueError, match="N<number>"):
        discover_tdms_pairs(samples)
