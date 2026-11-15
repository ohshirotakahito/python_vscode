"""Experimental ID（測定フォルダ名）1 つで、計画 → 計測 → 転送 → 解析結果 → スコアをつなぐ台帳。

1 行 = 1 実験 × 転送先の 1 サンプル（OUNmain03 が毎時書く `D:\\Qstatus\\database\\sq_transfers.csv` と同じ単位。
転送していない・一覧にだけある実験も 1 行）。

- 計画: `shared.ex_info_links.load_plans`（AXN の `upload_check.read_plans` と同じ規則。サンプルは `sample_key` で同一と見る）
- 転送・ANAL: `sq_transfers.csv`。そこに無い実験（Archived など）は `machinedb\\SEQ\\Transfers.jsonl`（`load_transfers`）
- スコア: score_store の 3 種類。再構成の行があればそれを、なければ計測時の行を使い、どちらを使ったかを出す
- 実験の一覧: SQserver の一覧（Upload）と、スコアの行にある Experimental ID

書き出し: `D:\\Qstatus\\database\\Scores\\experiment_scores.csv`（UTF-8 BOM 付き、Python_VScode の他の台帳と同じ日本語の列名）。
"""
import csv
from pathlib import Path
import statistics

from shared import ex_info_links, machinedb

from . import machine_data, score_store
from .machine_data import STAGES
from .score_store import MEASURED, RECONSTRUCTED

PUBLISH_DIR = machinedb.ROOT.parent / 'database'
TRANSFERS_CSV = PUBLISH_DIR / 'sq_transfers.csv'

COLUMNS = ('machine', 'name', 'experiment_at', 'era', 'plan', 'plan_destination', 'transfer', 'first_transfer',
           'destination', 'as_planned', 'anal_state', 'anal_progress', 'exsv_count', 'exsv_mean', 'exsv_max',
           'ac_count', 'ac_last', 'gg_score', 'gg_missing', 'score_source', 'criteria', 'location')
TITLES = ('装置', 'Experimental ID', '実験日時', '時代', '計画（研究 / サンプル）', '計画の送り先', '転送', '初回転送日時',
          '転送先', '計画どおり', 'ANAL状態', 'ANAL進捗', 'EXSV 回数', 'EXSV 平均', 'EXSV 最高',
          'AC 回数', 'AC 最終', 'GG スコア', 'GG 未実施工程', 'スコアの元', '基準', 'SQserver')
TITLE_OF = dict(zip(COLUMNS, TITLES))


def read_transfers_csv(path=None):
    """sq_transfers.csv の行（{日本語の列名: 値}）。無ければ []。"""
    path = TRANSFERS_CSV if path is None else Path(path)
    if not path.exists():
        return []
    with open(path, encoding='utf-8-sig', newline='') as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _mean(values):
    return f'{statistics.fmean(values):.1f}' if values else ''


def summarize(scores):
    """{Experimental ID: スコアのまとめ}。再構成の行があればそれを、なければ計測時の行を使う。

    scores: {kind: score_store の行}。"""
    by_name = {}
    for kind, rows in scores.items():
        for row in rows:
            entry = by_name.setdefault(row.get('Experimental ID', ''), {})
            entry.setdefault(kind, {}).setdefault(row.get('Source', ''), []).append(row)
    out = {}
    for name, kinds in by_name.items():
        summary = {'machine': next((r.get('Machine', '') for k in kinds.values() for rows in k.values() for r in rows
                                    if r.get('Machine')), ''), 'era': '', 'criteria': ''}
        sources = set()
        for kind, by_source in kinds.items():
            source = RECONSTRUCTED if RECONSTRUCTED in by_source else MEASURED if MEASURED in by_source else None
            if source is None:
                continue
            rows = by_source[source]
            sources.add(source)
            summary['era'] = summary['era'] or next((r.get('Era', '') for r in rows if r.get('Era')), '')
            if source == RECONSTRUCTED:
                summary['criteria'] = next((r.get('Criteria', '') for r in rows if r.get('Criteria')), '')
            values = [score_store.number(r.get(score_store.SCORE_COLUMN[kind])) for r in rows]
            values = [v for v in values if v is not None]
            if kind == 'EXSV':
                summary.update(exsv_count=str(len(rows)), exsv_mean=_mean(values),
                               exsv_max=f'{max(values):.1f}' if values else '')
            elif kind == 'AC':
                last = max(rows, key=lambda r: (r.get('AC Time', ''), r.get('AC#', '')))
                summary.update(ac_count=str(len(rows)), ac_last=last.get('AC Score', ''))
            else:
                latest = max(rows, key=lambda r: r.get('Round start', ''))
                summary.update(gg_score=latest.get('AutoGG Score', ''),
                               gg_missing=latest.get('Stages missing', '').replace(';', '・') or 'なし')
        summary['score_source'] = ' + '.join(sorted(sources, key=(RECONSTRUCTED, MEASURED).index))
        out[name] = summary
    return out


def _plan_cells(plan):
    if plan is None:
        return '', ''
    text = f'{plan.project} / {plan.sample}' + (f' [{plan.kind}]' if plan.kind else '')
    return text, plan.destination or '（保存先未定）'


def _row(machine, name, summary, plan, era_table, inventory, transfer=None, record=None):
    when = machine_data.experiment_time(name)
    plan_text, plan_destination = _plan_cells(plan)
    row = {column: '' for column in COLUMNS}
    row.update(machine=machine, name=name, experiment_at=when.strftime('%Y-%m-%d %H:%M') if when else '',
               era=summary.get('era') or score_store.era_of(machine, when, era_table), plan=plan_text,
               plan_destination=plan_destination, location=inventory.get(name, ''))
    if transfer is not None:
        destination = ' / '.join(t for t in (transfer.get('転送先の実験名', ''), transfer.get('転送先のサンプル名', '')) if t)
        share = transfer.get('転送先の共有（記録）', '') or transfer.get('解析サーバー', '')
        row.update(transfer=transfer.get('転送', ''), first_transfer=transfer.get('初回転送日時', ''),
                   destination=f'{share}: {destination}' if share and destination else destination,
                   as_planned=transfer.get('計画どおり', ''), anal_state=transfer.get('ANAL状態', ''),
                   anal_progress=transfer.get('ANAL進捗', ''))
    elif record is not None:
        planned = {True: 'はい', False: 'いいえ'}.get(record.planned, '')
        row.update(transfer='転送済み（記録）', first_transfer=record.transferred_at[:16].replace('T', ' '),
                   destination=f'{record.root}\\{record.experiment}\\{record.sample}', as_planned=planned)
    for key in ('exsv_count', 'exsv_mean', 'exsv_max', 'ac_count', 'ac_last', 'gg_score', 'gg_missing', 'score_source',
                'criteria'):
        row[key] = summary.get(key, '')
    return row


def build(scores, transfers=None, plans=None, records=None, inventories=None, era_table=None, machines=None):
    """台帳の行。scores は {kind: score_store の行}。ほかは省くと共有フォルダから読む。"""
    transfers = read_transfers_csv() if transfers is None else transfers
    plans = ex_info_links.load_plans() if plans is None else plans
    records = ex_info_links.load_transfers() if records is None else records
    era_table = score_store.era_table() if era_table is None else era_table
    machines = machine_data.measurement_machines() if machines is None else machines
    inventories = ({m: machine_data.inventory(m) for m in machines} if inventories is None else inventories)
    summaries = summarize(scores)
    by_record = {}
    for record in records:
        by_record.setdefault((record.machine, record.source), []).append(record)
    rows, covered = [], set()
    for transfer in transfers:
        machine, name = transfer.get('装置', ''), transfer.get('元の名前', '')
        if not name:
            continue
        covered.add((machine, name))
        rows.append(_row(machine, name, summaries.get(name, {}), plans.get(name), era_table,
                         inventories.get(machine, {}), transfer=transfer))
    names = {(m, n) for m, inv in inventories.items() for n in inv}
    names |= {(s.get('machine') or machine_data.machine_of(n) or '', n) for n, s in summaries.items()}
    for machine, name in sorted(names - covered):
        matched = by_record.get((machine, name)) or []
        if matched:
            for record in matched:
                rows.append(_row(machine, name, summaries.get(name, {}), plans.get(name), era_table,
                                 inventories.get(machine, {}), record=record))
        else:
            rows.append(_row(machine, name, summaries.get(name, {}), plans.get(name), era_table,
                             inventories.get(machine, {})))
    rows.sort(key=lambda r: (r['name'], r['destination']), reverse=True)
    return rows


def ledger_path(folder=None):
    folder = score_store.default_dir() if folder is None else Path(folder)
    return folder / score_store.LEDGER_NAME


def write(rows, folder=None):
    """experiment_scores.csv（日本語の列名）に書く。戻り値はそのパス。"""
    target = ledger_path(folder)
    titled = [{TITLE_OF[k]: v for k, v in row.items()} for row in rows]
    score_store.write_csv(target, TITLES, titled)
    return target


def load_scores(folder=None):
    return {kind: score_store.read(kind, folder) for kind in ('AC', 'EXSV', 'AutoGG')}
