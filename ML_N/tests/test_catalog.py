from pathlib import Path

import numpy as np

from ml_n.catalog import (
    build_catalog_record,
    normalize_float,
)
from ml_n.io import RawMeasurement


def make_measurement():
    return RawMeasurement(
        source_path=Path("example.tdms"),
        source_filename="sample_10k_Sample#001.tdms",
        file_id="file123",
        waveform_sha256="abc123",
        sampling_rate_hz=10_000.0,
        current_scale_to_pa=1000.0,
        current_pa=np.zeros(20_000),
        metadata_tables={
            "Gap Table": {
                "Experimental ID": "EX001",
                "Sample Name": "oxytocin",
                "Operator": "Private Person",
                "GAP_ID": "DEVICE-G001",
            },
            "Try Table": {
                "D": "0.5400",
                "Bias": "100.0",
                "EP2": "-100_DV_DEVICE-G001",
            },
            "Servo Table": {
                "EXSV#": "14",
                "EXSV Current [pA]": "14.7175",
                "EXSV Target BL Current [pA] ": "13.183300",
            },
            "Machine Table": {
                "Machine Code": "AN#2",
                "IP": "10.0.0.1",
                "MAC_Address": "00-00-00-00-00-00",
            },
        },
    )


def test_normalize_float_accepts_prefixed_metadata():
    assert normalize_float("-100_DV_DEVICE") == -100.0


def test_build_catalog_record_converts_values():
    record = build_catalog_record(make_measurement())

    assert record.sample_name == "oxytocin"
    assert record.distance_nm == 0.54
    assert record.ep2_mv == -100.0
    assert record.exsv_number == 14
    assert record.exsv_current_pa == 14.7175
    assert record.duration_s == 2.0


def test_catalog_does_not_include_sensitive_fields():
    output = build_catalog_record(
        make_measurement()
    ).to_dict()

    assert "operator" not in output
    assert "ip" not in output
    assert "mac_address" not in output
    assert "source_path" not in output