import argparse
from dataclasses import fields
from datetime import datetime
from pathlib import Path

import pandas as pd

from ml_n.catalog import (
    FileCatalogRecord,
    build_catalog,
    discover_tdms_files,
)


def main():
    parser = argparse.ArgumentParser(
        description="Build a safe searchable catalog from raw TDMS files."
    )
    parser.add_argument(
        "roots",
        nargs="+",
        help="TDMS file or directory paths",
    )
    parser.add_argument(
        "--output-dir",
        default="data/catalogs",
    )
    parser.add_argument(
        "--sampling-rate-hz",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--current-scale-to-pa",
        type=float,
        default=1000.0,
    )
    args = parser.parse_args()

    tdms_paths = discover_tdms_files(args.roots)

    print(f"Discovered TDMS files: {len(tdms_paths)}")

    result = build_catalog(
        tdms_paths,
        sampling_rate_hz=args.sampling_rate_hz,
        current_scale_to_pa=args.current_scale_to_pa,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    catalog_path = output_dir / f"{timestamp}_file_catalog.csv"
    latest_path = output_dir / "latest.csv"
    errors_path = output_dir / f"{timestamp}_errors.csv"

    catalog_columns = [
        field.name
        for field in fields(FileCatalogRecord)
    ]

    catalog_df = pd.DataFrame(
        [record.to_dict() for record in result.records],
        columns=catalog_columns,
    )

    catalog_df.to_csv(
        catalog_path,
        index=False,
        encoding="utf-8-sig",
    )
    catalog_df.to_csv(
        latest_path,
        index=False,
        encoding="utf-8-sig",
    )

    if result.errors:
        error_df = pd.DataFrame(
            [error.to_dict() for error in result.errors]
        )
        error_df.to_csv(
            errors_path,
            index=False,
            encoding="utf-8-sig",
        )

    print(f"Catalog records: {len(result.records)}")
    print(f"Skipped non-raw TDMS: {result.skipped_non_raw}")
    print(f"Skipped duplicate waveforms: {result.skipped_duplicates}")
    print(f"Errors: {len(result.errors)}")
    print(f"Saved catalog: {catalog_path}")
    print(f"Updated latest: {latest_path}")

    if result.errors:
        print(f"Saved local errors: {errors_path}")


if __name__ == "__main__":
    main()