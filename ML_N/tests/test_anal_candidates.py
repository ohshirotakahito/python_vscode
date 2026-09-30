from pathlib import Path

import pytest

from ml_n.selection import (
    AnalCountCandidate,
    select_frequency_strata,
)


def candidate(
    sample: str,
    number: int,
    frequency: float,
    *,
    raw_exists: bool = True,
) -> AnalCountCandidate:
    return AnalCountCandidate(
        sample_name=sample,
        measurement_number=f"{number:03d}",
        raw_source_filename=f"{sample}_Sample#{number:03d}.tdms",
        raw_tdms_path=Path(f"raw-{sample}-{number}.tdms"),
        anal_tdms_path=Path(f"anal-{sample}-{number}.tdms"),
        region_count=1000,
        signal_count=int(frequency),
        frequency_per_1000_regions=frequency,
        raw_exists=raw_exists,
    )


def test_selects_low_middle_high_per_sample():
    candidates = [
        candidate("A", number, float(number))
        for number in range(1, 10)
    ]

    selected = select_frequency_strata(
        candidates,
        per_sample=3,
        random_seed=42,
    )

    assert [item.selection_stratum for item in selected] == [
        "low",
        "middle",
        "high",
    ]
    assert selected[0].frequency_per_1000_regions <= 3
    assert 4 <= selected[1].frequency_per_1000_regions <= 6
    assert selected[2].frequency_per_1000_regions >= 7


def test_selection_is_reproducible():
    candidates = [
        candidate("A", number, float(number))
        for number in range(1, 20)
    ]

    first = select_frequency_strata(
        candidates,
        random_seed=123,
    )
    second = select_frequency_strata(
        candidates,
        random_seed=123,
    )

    assert first == second


def test_ignores_candidates_without_raw_file():
    selected = select_frequency_strata(
        [candidate("A", 1, 1.0, raw_exists=False)]
    )

    assert selected == ()


def test_rejects_more_than_three_per_sample():
    with pytest.raises(ValueError, match="between 1 and 3"):
        select_frequency_strata([], per_sample=4)
