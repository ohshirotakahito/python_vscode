"""Select representative server TDMS pairs and optionally copy them."""

from pathlib import Path

import pandas as pd

from ml_n.catalog import discover_tdms_pairs
from ml_n.selection import (
    copy_selected_candidates,
    discover_anal_candidates,
    select_frequency_strata,
)


def print_scan_progress(
    sample_name: str,
    candidate_count: int,
    raw_exists_count: int,
) -> None:
    print(
        f"Scanned {sample_name}: "
        f"ANAL={candidate_count}, RAW matched={raw_exists_count}",
        flush=True,
    )


def main(
    *,
    server: str,
    keyfolder: str,
    ex: str,
    sample: str,
    copy_files: bool,
    per_sample: int = 3,
    random_seed: int = 42,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    data_root = project_root / "data"
    destination_root = data_root / "source_tdms" / "samples"
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    candidates = discover_anal_candidates(
        server,
        keyfolder,
        ex,
        sample,
        progress=print_scan_progress,
    )
    selected = select_frequency_strata(
        candidates,
        per_sample=per_sample,
        random_seed=random_seed,
    )

    manifest_stem = f"{ex}_{sample}"
    candidate_path = (
        manifest_dir / f"{manifest_stem}_anal_candidates.csv"
    )
    selection_path = (
        manifest_dir / f"{manifest_stem}_selection_plan.csv"
    )
    candidate_frame = pd.DataFrame(
        [candidate.to_dict() for candidate in candidates]
    )
    candidate_frame.to_csv(
        candidate_path,
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(
        [candidate.to_dict() for candidate in selected],
        columns=candidate_frame.columns,
    ).to_csv(selection_path, index=False, encoding="utf-8-sig")

    print(f"ANAL candidates: {len(candidates)}")
    print(
        "Candidates with RAW: "
        f"{sum(item.raw_exists for item in candidates)}"
    )
    print(f"Selected pairs: {len(selected)}")
    print(f"Saved candidates: {candidate_path}")
    print(f"Saved selection plan: {selection_path}")

    if not copy_files:
        print("Preview only: set copy_files=True after reviewing the plan.")
        return

    if not selected:
        raise RuntimeError(
            "No RAW/ANAL pairs were selected; no files were copied."
        )

    copied_rows = copy_selected_candidates(
        selected,
        destination_root,
    )
    copy_report_path = (
        manifest_dir / f"{manifest_stem}_copy_report.csv"
    )
    pd.DataFrame(copied_rows).to_csv(
        copy_report_path,
        index=False,
        encoding="utf-8-sig",
    )

    pair_records = discover_tdms_pairs(destination_root)
    pair_manifest_path = manifest_dir / "validation_pairs.csv"
    pd.DataFrame(
        [
            record.to_manifest_row(pair_manifest_path.parent)
            for record in pair_records
        ]
    ).to_csv(
        pair_manifest_path,
        index=False,
        encoding="utf-8-sig",
    )
    print(f"Saved copy report: {copy_report_path}")
    print(f"Saved pair manifest: {pair_manifest_path}")


if __name__ == "__main__":
    # データ元のターゲットサーバー名
    server = "QTserver"
    # データ元のターゲットサーバー内の元フォルダの場所
    keyfolder = "analysis"
    # 元フォルダ内の対象実験フォルダ
    ex = "Chirality_NL"
    # 対象実験フォルダ直下の対象サンプル
    sample = "LH-10"

    # 初回はFalseで選択計画だけ確認し、確認後にTrueへ変更する。
    copy_files = False

    main(
        server=server,
        keyfolder=keyfolder,
        ex=ex,
        sample=sample,
        copy_files=copy_files,
        per_sample=3,
        random_seed=42,
    )
