from pathlib import Path

import numpy as np

from ml_n.catalog import (
    build_catalog,
    discover_tdms_files,
)
from ml_n.io import RawMeasurement


def make_measurement(path: Path, file_id: str):
    return RawMeasurement(
        source_path=path,
        source_filename=path.name,
        file_id=file_id,
        waveform_sha256=file_id * 4,
        sampling_rate_hz=10_000.0,
        current_scale_to_pa=1000.0,
        current_pa=np.zeros(10_000),
        metadata_tables={
            "Gap Table": {
                "Sample Name": "test_sample",
            },
        },
    )


def test_discover_tdms_files_recursively(tmp_path):
    first = tmp_path / "first.tdms"
    second_dir = tmp_path / "nested"
    second_dir.mkdir()
    second = second_dir / "second.TDMS"
    ignored = tmp_path / "ignored.txt"

    first.touch()
    second.touch()
    ignored.touch()

    result = discover_tdms_files([tmp_path])

    assert set(result) == {
        first.resolve(),
        second.resolve(),
    }


def test_build_catalog_skips_non_raw_and_duplicates(tmp_path):
    raw1 = tmp_path / "raw1.tdms"
    duplicate = tmp_path / "duplicate.tdms"
    anal = tmp_path / "anal.tdms"
    broken = tmp_path / "broken.tdms"

    for path in (raw1, duplicate, anal, broken):
        path.touch()

    def fake_reader(path, **kwargs):
        if path.name == "anal.tdms":
            raise KeyError(
                "TDMS group was not found: Example"
            )

        if path.name == "broken.tdms":
            raise OSError("damaged TDMS")

        # raw1とduplicateは同じ波形ID
        return make_measurement(path, file_id="same-file-id")

    result = build_catalog(
        [raw1, duplicate, anal, broken],
        reader=fake_reader,
    )

    assert len(result.records) == 1
    assert result.skipped_non_raw == 1
    assert result.skipped_duplicates == 1

    assert len(result.errors) == 1
    assert result.errors[0].error_type == "OSError"
    assert "damaged TDMS" in result.errors[0].message