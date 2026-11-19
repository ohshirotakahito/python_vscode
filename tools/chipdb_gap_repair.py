"""Preview GAP# repairs (values >= 10000) for all ChipDB CSVs; save only with --apply."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chipdb.gap_repair import preview, apply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--report', required=True, help='JSON report path')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    plans = preview(args.db)
    Path(args.report).write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')
    errors = [p for p in plans if p['error']]
    print(f'files to repair: {len(plans) - len(errors)}, rows: {sum(len(p["changes"]) for p in plans)}, errors: {len(errors)}')
    if args.apply:
        for file, result in apply(plans):
            print(json.dumps(dict(file=file, result=result), ensure_ascii=True))


if __name__ == '__main__':
    main()
