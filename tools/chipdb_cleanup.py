"""Preview the user-approved ChipDB identity rules; apply only with --apply."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chipdb.cleanup import preview, apply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    plans = preview(args.db)
    Path(args.report).write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')
    for plan in plans:
        print(json.dumps(dict(code=plan['code'], files=len(plan['sources']),
            remove=len(plan.get('remove', [])), rows=plan.get('output_rows'), error=plan['error'][:500], notes=plan['notes']), ensure_ascii=True))
    if args.apply:
        print('Backup: ' + str(apply(args.db, plans)))


if __name__ == '__main__':
    main()
