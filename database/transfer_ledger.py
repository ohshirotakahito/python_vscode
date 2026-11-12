"""SQserver の Recent_Data にある実験が、解析サーバーへ転送されたか・ANAL がどこまで進んだかの台帳。

計測PC（AXN）の Upload Check は、測定PC → SQserver（Tmp_backup）のアップロードを確かめる。
この台帳はその続きで、SQserver → 解析サーバーの転送と ANAL の進捗を、装置＋元の実験フォルダ名ごとに示す。

    実験の一覧   : 保存済みDBの SQserver Recent_Data（sq_experiments.csv と同じもの）。
                   装置は Machine_N.csv の Tmp_backup で決める（backup_06 → AXN2）
    転送         : 転送履歴（LabVIEW版 Uploader.csv ＋ Python版 Uploader_python.csv）に
                   (装置, 元の名前) があれば転送済み。転送先の実験名・サンプル名もここから取る
    ANAL進捗     : 転送先の (実験名, サンプル名) を、保存済みの解析サーバーの全グループから探し、
                   最も進んだ共有で判定する（shared.progress.assess_sample。ANAL進捗タブと同じ）

1 つの実験を複数のサンプルに分けて転送したときは、サンプルごとに 1 行。転送していない実験も 1 行出す。
"""
from shared import progress

COLUMNS = ('machine', 'name', 'experiment_at', 'transfer', 'first_transfer', 'last_transfer',
           'dest_experiment', 'dest_sample', 'locations', 'anal_progress', 'anal_state', 'flags')
TITLES = ('装置', '元の名前', '実験日時', '転送', '初回転送日時', '最終転送日時',
          '転送先の実験名', '転送先のサンプル名', '解析サーバー', 'ANAL進捗', 'ANAL状態', '注意')

TRANSFERRED = '転送済み'
NOT_TRANSFERRED = '未転送'
NOT_FOUND = '解析サーバーに見当たらない'


def _date(text):
    """'2026/10/31_14:14' -> '2026-10-31 14:14'."""
    return text.replace('/', '-').replace('_', ' ')


def machine_by_backup(machines):
    """{'backup_06': 'AXN2'} from Machine_N.csv rows (Tmp_backup column)."""
    out = {}
    for code, info in machines.items():
        # \\SQserver\backup_06 is a UNC share root: PureWindowsPath(...).name is empty there
        backup = (info.get('Tmp_backup') or '').replace('/', '\\').rstrip('\\').rsplit('\\', 1)[-1].casefold()
        if backup.startswith('backup_'):
            out[backup] = code
    return out


def anal_index(rows_by_group):
    """{(experiment, sample): [(group label, rack_samples row), ...]}."""
    index = {}
    for label, rows in rows_by_group.items():
        for row in rows:
            index.setdefault((row['experiment'], row['sample']), []).append((label, row))
    return index


def anal_summary(found):
    """(locations, progress text, state, flags) of one destination sample over every share holding it."""
    if not found:
        return '', '', NOT_FOUND, ''
    cells = []
    for label, row in found:
        cell = progress.anal_share(row)
        cell['group'] = label
        cells.append(cell)
    best, flags = progress.assess_sample(cells)
    locations = '・'.join(dict.fromkeys(c['group'] for c in cells))
    if best['ratio'] is None:
        text = ''
    else:
        text = f"{best['ratio'] * 100:.1f}%（{best['analysed'] or 0}/{best['total']}）"
    return locations, text, best['state'], '・'.join(flags)


def build_rows(entries, machines, history, rows_by_group):
    """Rows (dicts with COLUMNS) for every Recent_Data experiment folder of a machine in Machine_N.csv."""
    backups = machine_by_backup(machines)
    transfers = {}
    for record in history:
        uploaded_at, code, source, destination, sample = record[0], record[2], record[6], record[7], record[8]
        if not (code and source):
            continue
        key = (code, source)
        target = (destination, sample)
        dates = transfers.setdefault(key, {}).setdefault(target, [])
        dates.append(uploaded_at)
    index = anal_index(rows_by_group)
    out, seen = [], set()
    for entry in entries:
        if entry.get('group') != 'Recent_Data' or entry.get('entry_type', 'フォルダ') != 'フォルダ':
            continue
        code = backups.get(str(entry.get('container', '')).casefold())
        if code is None or (code, entry['name']) in seen:
            continue
        seen.add((code, entry['name']))
        base = dict(machine=code, name=entry['name'], experiment_at=entry.get('experiment_at') or '')
        targets = transfers.get((code, entry['name']))
        if not targets:
            out.append(dict(base, transfer=NOT_TRANSFERRED, first_transfer='', last_transfer='', dest_experiment='',
                            dest_sample='', locations='', anal_progress='', anal_state='', flags=''))
            continue
        for (destination, sample), dates in sorted(targets.items(), key=lambda item: min(item[1])):
            locations, text, state, flags = anal_summary(index.get((destination, sample), []))
            out.append(dict(base, transfer=TRANSFERRED, first_transfer=_date(min(dates)),
                            last_transfer=_date(max(dates)), dest_experiment=destination, dest_sample=sample,
                            locations=locations, anal_progress=text, anal_state=state, flags=flags))
    out.sort(key=lambda r: (r['machine'], r['name'], r['first_transfer']))
    return out
