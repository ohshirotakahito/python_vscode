"""Configure this PC independently of the published application versions."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.terminal_profile import PROFILE, load, save
from shared.app_release import load_releases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=['developer', 'user'])
    parser.add_argument('--apps', nargs='*', choices=sorted(set(load_releases()['apps']) - {'workbench'}))
    args = parser.parse_args()
    if args.apps is not None and args.role is None:
        parser.error('--apps requires --role')
    apps = args.apps if args.apps is not None else (['exmanager'] if args.role == 'user' else [])
    data = save(args.role, apps) if args.role else load()
    print(str(PROFILE))
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
