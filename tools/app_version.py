"""List application versions or record one independent release."""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.app_release import MANIFEST, load_releases


def bump(data, app_id, part, note, date):
    if not note.strip():
        raise ValueError('Release note must not be empty')
    app = data['apps'][app_id]
    numbers = list(map(int, app['version'].split('.')))
    index = ('major', 'minor', 'patch').index(part)
    numbers[index] += 1
    numbers[index + 1:] = [0] * (2 - index)
    app['version'] = '.'.join(map(str, numbers))
    app['releases'].insert(0, dict(version=app['version'], date=date, notes=[note.strip()]))
    return app['version']


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', nargs='?', help='Application ID; omit to list versions')
    parser.add_argument('--bump', choices=['major', 'minor', 'patch'])
    parser.add_argument('--note', help='Required release note when bumping')
    args = parser.parse_args()
    data = load_releases()
    if args.bump:
        if args.app not in data['apps'] or not args.note or not args.note.strip():
            parser.error('--bump requires a valid app ID and non-empty --note')
        updated = bump(data, args.app, args.bump, args.note,
                       datetime.now(timezone(timedelta(hours=9))).date().isoformat())
        temporary = MANIFEST.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(MANIFEST)
        print(f'{args.app}: {updated}')
    else:
        if args.note:
            parser.error('--note requires --bump')
        if args.app and args.app not in data['apps']:
            parser.error('Unknown app ID')
        for app_id, app in data['apps'].items():
            if not args.app or app_id == args.app:
                print(f"{app_id:12} {app['version']:8} {app['name']}")


if __name__ == '__main__':
    main()
