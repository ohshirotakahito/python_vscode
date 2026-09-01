"""Build a manifest from the canonical RAW/ANAL directory layout."""

import argparse
from pathlib import Path

import pandas as pd

from ml_n.catalog import discover_tdms_pairs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a relative-path RAW/ANAL pair manifest."
    )
    parser.add_argument("samples_root")
    parser.add_argument("output_csv")
    args = parser.parse_args()

    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = discover_tdms_pairs(args.samples_root)

    rows = [
        record.to_manifest_row(output_path.parent)
        for record in records
    ]
    pd.DataFrame(rows).to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Discovered pairs: {len(records)}")
    print(f"Saved manifest: {output_path}")


if __name__ == "__main__":
    main()
