import argparse

import numpy as np

from ml_n.io import read_raw_tdms


def main():
    parser = argparse.ArgumentParser(
        description="Inspect an original measurement TDMS file."
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

    print("source_path:", measurement.source_path)
    print("file_id:", measurement.file_id)
    print("waveform_sha256:", measurement.waveform_sha256)
    print("sampling_rate_hz:", measurement.sampling_rate_hz)
    print("n_points:", measurement.n_points)
    print("duration_s:", measurement.duration_s)
    print("current_pa_min:", float(np.min(measurement.current_pa)))
    print("current_pa_max:", float(np.max(measurement.current_pa)))
    print("current_pa_mean:", float(np.mean(measurement.current_pa)))
    print("current_pa_std:", float(np.std(measurement.current_pa)))

    print("\nMetadata groups:")
    for group_name, fields in measurement.metadata_tables.items():
        print(f"\n[{group_name}] ({len(fields)} fields)")
        for name, value in fields.items():
            print(f"  {name}: {value}")


if __name__ == "__main__":
    main()