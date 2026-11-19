"""チップIDの修正（docs/chip_id_cleanup.md の段階 2〜5 をまとめて）。

    python tools/chip_id_rename.py "理由" 旧=新 [旧=新 ...]            # 名前を直す（新名のチップがまだない）
    python tools/chip_id_rename.py "理由" 旧=新 --merge                # 既にある新名のチップに統合する

統合の前に、両方の履歴の実験の日時・装置を突き合わせ、同じチップかを人が判断すること（6 分違いで別の装置、などは統合しない）。

対応表に登録 → 履歴ファイル・台帳（旧名は aliases に）→ 計測記録（直前 1 時間にほかから書き込みがあれば止める）→ 確認 → 段階を記録。
"""
import collections
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import chip_aliases as aliases  # noqa: E402
from chipdb import cleanup, record_ids  # noqa: E402
from chipdb.registry import Store  # noqa: E402

from shared import machinedb  # noqa: E402

DB = machinedb.ROOT.parent / 'ChipDB' / 'DB'
MDB = machinedb.ROOT
BK = machinedb.ROOT.parent / 'ChipDB' / 'WorkbenchBackups'
args = [a for a in sys.argv[1:] if not a.startswith('--')]
merge = '--merge' in sys.argv
reason, pairs = args[0], args[1:]
mapping = dict(p.split('=', 1) for p in pairs)
path = aliases.table_path(DB)
for old, new in mapping.items():
    assert (DB / f'{old}.csv').exists(), f'no history file: {old}'
    assert merge or not (DB / f'{new}.csv').exists(), f'new name exists (use --merge): {new}'
aliases.add_entries(path, [(o, n, reason, merge, '予定') for o, n in mapping.items()])
cleanup.ALIASES.update({o.casefold(): n for o, n in mapping.items()})
plans = [p for p in cleanup.preview(DB) if p['code'] in mapping.values()]
problems = [(p['code'], p['error']) for p in plans if p['error']]
if not merge:
    problems += [(p['code'], 'content changes') for p in plans
                 if Path(p['sources'][0]).read_bytes().decode('cp932').splitlines()[1:] != p['body'].splitlines()[1:]]
assert len(plans) == len(mapping) and not problems, (len(plans), problems)
recs = record_ids.plan(MDB, mapping)
mine = {f.get('hash_after') for j in BK.glob('chipid_records_*/journal.json')
        for f in json.loads(j.read_text(encoding='utf-8'))['files']}
recent = [r.path for r in recs if time.time() - os.stat(r.path).st_mtime < 3600 and r.hash not in mine]
print('records', [(Path(r.path).parent.name + '/' + Path(r.path).name, r.group, r.rows) for r in recs])
assert not recent, f'written by others within 1 h: {recent}'
session = cleanup.apply(DB, plans)
s2, results = record_ids.apply(recs, BK, groups=('retired', 'active'))
chips = {c['code']: c for c in Store(DB).load()['chips']}
ok = all((DB / f'{n}.csv').exists() and not (DB / f'{o}.csv').exists() and o in chips[n].get('aliases', [])
         for o, n in mapping.items()) and not record_ids.plan(MDB, mapping) and all(r['status'] == 'done' for r in results)
print('chipdb', session.name, '| records', s2.name, 'done', sum(r['status'] == 'done' for r in results), '/', len(results),
      '| verified', ok)
if ok:
    steps = aliases.STEPS if merge else ('chipdb', 'machinedb_retired', 'machinedb_active')
    for step in steps:
        t = aliases.mark_step(path, list(mapping), step)
    print('table revision', t['revision'], collections.Counter(e['status'] for e in t['entries']))
for f in [Path(r['path']) for r in results][:2]:
    lines = f.read_bytes().decode('cp932').splitlines()
    h = [x.strip() for x in lines[0].split(',')]
    idx = [h.index(c) for c in ('Device Code', 'Device Wafer', 'Device Lot') if c in h]
    row = next((l.split(',') for l in lines if any(n in l for n in mapping.values())), None)
    if row:
        print(' ', f.parent.name + '/' + f.name, [row[i] for i in idx])
