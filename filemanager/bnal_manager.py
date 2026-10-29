"""BNAL duplicate management using the same saved counts as BNAL progress."""
from pathlib import Path

from database import catalog
from filemanager.anal_manager import AnalPanel


def load_inventory(path):
    with catalog.connect(path, readonly=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'rack_bnals' not in tables:
            raise ValueError('BNALの保存済み調査がありません。実験データベースでBNAL内訳を更新してください。')
        rows = [dict(r) for r in db.execute('SELECT * FROM rack_bnals ORDER BY root,experiment,sample,target')]
        samples = [dict(r) for r in db.execute('SELECT * FROM rack_samples')] if 'rack_samples' in tables else []
    lookup = {(catalog.root_identity(r['root']), r['experiment'], r['sample']): r for r in samples}
    for row in rows:
        sample = lookup.get((catalog.root_identity(row['root']), row['experiment'], row['sample']), {})
        anal = sample.get('anal_tdms')
        expected = anal + (sample.get('anal_stocked_tdms') or 0) if anal is not None else None
        count, distinct = row.get('tdms'), row.get('tdms_distinct')
        mixed = max(0, count - distinct) if count is not None and distinct is not None else None
        excess = max(0, count - expected) if count is not None and expected else 0
        target = 'BNAL@' + row['target']
        row.update(anal_tdms=count, mixed=mixed, expected=expected, excess=excess,
                   sample_stocked=sample.get('anal_stocked_tdms'),
                   anal_stocked_tdms=None, last_success=row.get('tdms_checked'),
                   state='未計数・要DB更新' if count is None else
                         ('同番号候補あり' if mixed else '番号未調査・要DB更新' if distinct is None else '同番号候補なし')
                         + (f'／超過 {excess}件' if excess else '')
                         + ('／基準ANAL未取得' if expected is None else ''),
                   anal_path=str(Path(row['root']) / row['experiment'] / row['sample'] /
                                 (row['sample'] + '_10k_Sample') / 'T' / target),
                   target=target)
    return rows


class BnalPanel(AnalPanel):
    def __init__(self, owner):
        super().__init__(owner, entity='BNAL', loader=load_inventory)
        self.notice.set('BNAL進捗と同じ保存済み調査を参照します。対象BNAL@フォルダを選択して再調査してください。'
                        '未計数の対象を見る場合は「重複候補・超過・退避ありのみ」を外してください。')
