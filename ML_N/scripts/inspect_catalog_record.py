import argparse
import json

from ml_n.catalog import build_catalog_record
from ml_n.io import read_raw_tdms


def main():
    parser = argparse.ArgumentParser(
        description="Inspect a safe normalized TDMS catalog record."
    )
    parser.add_argument("tdms_path")
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

    measurement = read_raw_tdms(
        args.tdms_path,
        sampling_rate_hz=args.sampling_rate_hz,
        current_scale_to_pa=args.current_scale_to_pa,
    )

    record = build_catalog_record(measurement)

    print(
        json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()