import pytest

from ml_n.io.raw_tdms_reader import infer_sampling_rate_hz


def test_infer_10k_sampling_rate():
    filename = "oxytocin_10k_Sample#065.tdms"

    result = infer_sampling_rate_hz(filename)

    assert result == 10_000.0


def test_infer_sampling_rate_is_case_insensitive():
    filename = "sample_20K_sample#001.tdms"

    result = infer_sampling_rate_hz(filename)

    assert result == 20_000.0


def test_infer_sampling_rate_returns_none_when_missing():
    result = infer_sampling_rate_hz("measurement.tdms")

    assert result is None