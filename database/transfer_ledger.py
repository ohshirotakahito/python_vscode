"""SQserver の Recent_Data にある実験が、解析サーバーへ転送されたか・ANAL がどこまで進んだかの台帳。

計測PC（AXN）の Upload Check は、測定PC → SQserver（Tmp_backup）のアップロードを確かめる。
この台帳はその続きで、SQserver → 解析サーバーの転送と ANAL の進捗を、装置＋元の実験フォルダ名ごとに示す。

    実験の一覧   : 保存済みDBの SQserver Recent_Data（sq_experiments.csv と同じもの）。
                   装置は Machine_N.csv の Tmp_backup で決める（backup_06 → AXN2）
    転送         : 転送履歴（LabVIEW版 Uploader.csv ＋ Python版 Uploader_python.csv）に
                   (装置, 元の名前) があれば転送済み。転送先の実験名・サンプル名もここから取る
    ANAL進捗     : 転送先の (実験名, サンプル名) を、保存済みの解析サーバーの全グループから探し、
                   最も進んだ共有で判定する（shared.progress.assess_sample。ANAL進捗タブと同じ）

    転送先の共有 : exmanager が転送ごとに書く machinedb\\SEQ\\Transfers.jsonl（shared.ex_info_links）に記録が
                   あれば、ANAL はその共有だけで判定する（同じ名前の実験・サンプルが別の共有にあっても混ぜない）。
                   記録の共有で見つからず別の共有にあれば、そこで判定して注意「記録と別の共有」を付ける。
                   記録のない転送（LabVIEW 版・この記録より前の Python 版）は、これまでどおり全グループから探す

1 つの実験を複数のサンプルに分けて転送したときは、サンプルごとに 1 行。転送していない実験も 1 行出す。
"""
from shared import progress, destination_history
from shared.ex_info_links import split_root

# 2026-11-13: 共有・計画キーの3列と、現在の保存先・追跡状態の2列を追加。AXNも列名で読む。
COLUMNS = ('machine', 'name', 'experiment_at', 'transfer', 'first_transfer', 'last_transfer',
           'dest_experiment', 'dest_sample', 'locations', 'anal_progress', 'anal_state', 'flags',
           'dest_shares', 'as_planned', 'sample_key', 'current_destinations', 'path_tracking')
TITLES = ('装置', '元の名前', '実験日時', '転送', '初回転送日時', '最終転送日時',
          '転送先の実験名', '転送先のサンプル名', '解析サーバー', 'ANAL進捗', 'ANAL状態', '注意',
          '転送先の共有（記録）', '計画どおり', 'Ex Info サンプルキー',
          '現在の保存先（履歴）', '保存先の追跡')

TRANSFERRED = '転送済み'
NOT_TRANSFERRED = '未転送'
NOT_FOUND = '解析サーバーに見当たらない'
OTHER_SHARE = '記録と別の共有'
TRACKING_UNCERTAIN = '保存先の追跡要確認'


def root_label(root):
    r"""'\\QDserver\analysis' -> 'QDserver/analysis' (rack_store.group_label), or '' when it is not \\server\share."""
    text = str(root or '').replace('/', '\\')
    parts = [p for p in text.split('\\') if p]
    return f'{parts[0]}/{parts[1]}' if text.startswith('\\\\') and len(parts) == 2 else ''


def recorded_targets(records):
    """{(装置, 元の名前, 実験名, サンプル名) casefolded: dict(shares=[...], planned=[...], sample_keys=[...])}."""
    out = {}
    for r in records:
        key = (r.machine, r.source, r.experiment.casefold(), r.sample.casefold())
        entry = out.setdefault(key, dict(shares=[], planned=[], sample_keys=[], records=[]))
        entry['records'].append(r)
        label = root_label(r.root)
        if label and label not in entry['shares']:
            entry['shares'].append(label)
        entry['planned'].append(r.planned)
        if r.sample_key and r.sample_key not in entry['sample_keys']:
            entry['sample_keys'].append(r.sample_key)
    return out


def planned_text(values):
    """'はい' / 'いいえ' / '一部'（記録ごとの planned）、計画のない転送だけなら ''."""
    known = [v for v in values if v is not None]
    if not known:
        return ''
    return 'はい' if all(known) else 'いいえ' if not any(known) else '一部'


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


def build_rows(entries, machines, history, rows_by_group, records=(), moves=()):
    """Rows (dicts with COLUMNS) for every Recent_Data experiment folder of a machine in Machine_N.csv.

    records: the Transfers.jsonl records (ex_info_links.Transfer) that tell each transfer's share.
    moves: confirmed/uncertain directory operations read by shared.destination_history."""
    recorded = recorded_targets(records)
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
    current_index = {}
    for label, rows in rows_by_group.items():
        for row in rows:
            current_index.setdefault((label.casefold(), row['experiment'].casefold(), row['sample'].casefold()),
                                     []).append((label, row))
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
                            dest_sample='', locations='', anal_progress='', anal_state='', flags='',
                            dest_shares='', as_planned='', sample_key='',
                            current_destinations='', path_tracking=''))
            continue
        for (destination, sample), dates in sorted(targets.items(), key=lambda item: min(item[1])):
            found = index.get((destination, sample), [])
            rec = recorded.get((code, entry['name'], destination.casefold(), sample.casefold()))
            other_share = False
            current, changed, uncertain = [], False, False
            mapped = []
            for record in rec['records'] if rec else ():
                path, moved, unsure = destination_history.trace(record.destination, record.transferred_at, moves)
                changed, uncertain = changed or moved, uncertain or unsure
                if path not in current:
                    current.append(path)
                parts = split_root(path)
                if moved and parts is None:
                    uncertain = True
                if parts is not None:
                    root, experiment_now, sample_now = parts
                    mapped.extend(current_index.get((root_label(root).casefold(), experiment_now.casefold(),
                                                     sample_now.casefold()), []))
            if uncertain:
                found = []  # do not claim completion for a same-named, possibly unrelated sample
            elif changed:
                found = list({(label, row['experiment'], row['sample']): (label, row)
                              for label, row in mapped}.values())
            elif rec and rec['shares']:
                shares = {label.casefold() for label in rec['shares']}
                there = [f for f in found if f[0].casefold() in shares]
                other_share = bool(found) and not there
                found = there or found
            locations, text, state, flags = anal_summary(found)
            if other_share:
                flags = '・'.join(f for f in (OTHER_SHARE, flags) if f)
            if uncertain:
                flags = '・'.join(f for f in (TRACKING_UNCERTAIN, flags) if f)
            out.append(dict(base, transfer=TRANSFERRED, first_transfer=_date(min(dates)),
                            last_transfer=_date(max(dates)), dest_experiment=destination, dest_sample=sample,
                            locations=locations, anal_progress=text, anal_state=state, flags=flags,
                            dest_shares='・'.join(rec['shares']) if rec else '',
                            as_planned=planned_text(rec['planned']) if rec else '',
                            sample_key='・'.join(rec['sample_keys']) if rec else '',
                            current_destinations='・'.join(current) if not uncertain else '',
                            path_tracking=('要確認' if uncertain else '履歴で追跡' if changed else '履歴なし')
                                          if rec else ''))
    out.sort(key=lambda r: (r['machine'], r['name'], r['first_transfer']))
    return out
