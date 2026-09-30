"""Redraw confusion-matrix PNGs from an existing training run.

Usage:
    python ML/redraw_confusion_matrix.py path/to/run_directory
"""

import argparse
from pathlib import Path

from common.eval_viz import redraw_confusion_matrices


def main():
    parser = argparse.ArgumentParser(
        description='Redraw confusion-matrix PNGs without retraining the model.'
    )
    parser.add_argument(
        'run_dir', type=Path, help='Directory containing confusion_matrix*.csv'
    )
    args = parser.parse_args()

    redraw_confusion_matrices(args.run_dir)
    print(f"Redrew confusion-matrix PNGs in: {args.run_dir.resolve()}")


if __name__ == '__main__':
    main()
