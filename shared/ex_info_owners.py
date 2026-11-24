"""Read project Owners from Ex Info; match samples by their registered locations.

No name-only guesses or writes to either ledger. A row spanning several samples
with the same folder names is explicitly ambiguous.
"""
import json
from collections import defaultdict

from shared import ex_info_links


def load(path=None):
    path = ex_info_links.LEDGER if path is None else path
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return dict(projects=[], samples=[], locations=[])
    if not isinstance(data, dict) or any(not isinstance(data.get(k), list)
                                         for k in ('projects', 'samples', 'locations')):
        raise ValueError('Ex Infoの形式が不正です')
    for group in ('projects', 'samples', 'locations'):
        if any(not isinstance(row, dict) for row in data[group]):
            raise ValueError('Ex Infoの行が不正です')
    for project in data['projects']:
        owners = project.get('owner_names', [])
        if not isinstance(owners, list) or any(not isinstance(n, str) for n in owners):
            raise ValueError('Ex Infoの責任者が不正です')
    return data


def attach(rows, samples, data):
    projects = {p['key']: p for p in data['projects'] if p.get('key')}
    definitions = {s['key']: s for s in data['samples'] if s.get('key')}
    locations = defaultdict(set)
    for loc in data['locations']:
        sample = definitions.get(loc.get('sample_key'))
        if (sample is None or sample.get('project_key') not in projects
                or sample.get('project_key') != loc.get('project_key')):
            continue
        if all(loc.get(k) for k in ('root_path', 'experiment_folder', 'sample_folder')):
            key = (ex_info_links.path_key(loc['root_path']), loc['experiment_folder'].casefold(),
                   loc['sample_folder'].casefold())
            locations[key].add(sample['key'])
    roots = defaultdict(set)
    for sample in samples:
        roots[(sample['experiment'], sample['sample'])].add(ex_info_links.path_key(sample['root']))
    result = []
    for row in rows:
        candidates = set()
        for root in roots[(row['experiment'], row['sample'])]:
            candidates.update(locations.get((root, row['experiment'].casefold(), row['sample'].casefold()), ()))
        state, owners = 'unmatched', []
        if len(candidates) > 1:
            state = 'ambiguous'
        elif len(candidates) == 1:
            sample = definitions[next(iter(candidates))]
            owners = sorted(set(n.strip() for n in projects[sample['project_key']].get('owner_names', []) if n.strip()),
                            key=str.casefold)
            state = 'matched' if owners else 'unset'
        result.append(dict(row, owner_names=owners, owner_state=state))
    return result


def member_names():
    """メンバー表（machinedb の SEQ/Operator.csv）の名前。読めなければ空。"""
    from shared import machinedb, seq_records
    try:
        rows = seq_records.rows(machinedb.SEQ / 'Operator.csv')
    except (OSError, ValueError):
        return []
    return [r[0].strip() for r in rows if r and r[0].strip()]


def candidates(data, members=None):
    """責任者の候補。AXN の owner_picker.candidates と同じ決まり：メンバー表の名前・覚えている責任者
    （ex_info.json の owner_names）・各プロジェクトの責任者。Other は除く。"""
    members = member_names() if members is None else members
    names = (set(members) | set(data.get('owner_names', []))
             | {n for p in data.get('projects', []) for n in p.get('owner_names', [])})
    return sorted({n.strip() for n in names if isinstance(n, str) and n.strip()} - {'Other'}, key=str.casefold)
