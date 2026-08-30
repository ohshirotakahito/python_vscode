import argparse

import numpy as np

from ml_n.io import read_anal_tdms, read_raw_tdms


def main():
    parser = argparse.ArgumentParser(
        description="Inspect a LabVIEW ANAL TDMS file."
    )
    parser.add_argument("anal_tdms_path")
    parser.add_argument(
        "--raw-tdms",
        default=None,
    )
    args = parser.parse_args()

    anal = read_anal_tdms(args.anal_tdms_path)

    print("ANAL file:", anal.source_filename)
    print("Raw source filename:", anal.raw_source_filename)
    print("File ID:", anal.file_id)
    print("Sampling rate [Hz]:", anal.sampling_rate_hz)
    print("Points:", anal.n_points)
    print("Duration [s]:", anal.duration_s)
    print("Events:", len(anal.events))
    print("Current mean [pA]:", float(np.mean(anal.current_pa)))

    print("\nAC Table:")
    for name, value in anal.analysis_config.items():
        print(f"  {name}: {value}")

    print("\nFirst 5 events:")
    for event in anal.events[:5]:
        print(event)

    if args.raw_tdms:
        raw = read_raw_tdms(args.raw_tdms)

        print("\nRaw/ANAL comparison:")
        print("  raw file_id:", raw.file_id)
        print("  ANAL file_id:", anal.file_id)
        print("  hash match:", raw.file_id == anal.file_id)
        print(
            "  waveform exact match:",
            np.array_equal(raw.current_pa, anal.current_pa),
        )


if __name__ == "__main__":
    main()