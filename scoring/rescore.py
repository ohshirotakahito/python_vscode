"""後からのスコア計算（再構成）と、計測時の Score.csv の取り込み。

- AC: 結果 R_AC（G0 current・nG0 → 系抵抗）と 50 ms ログ PM_AC から `axn.ac.score`
- EXSV: 結果 R_EXSV と 0.5 s ログ PML（ブロック RMS）・50 ms ログ PM（ピエゾ）から `axn.exsv_score.score`
- Gap Generation（Auto GG の 1 回分 = FC・MA・TA・AC・CAL）: 手動の GG には「1 回分」の区切りが無いので、
  その実験の EXSV の時刻で区切り、区間ごとに各工程の最後の試行を 1 回分とする（最初の区間が「最初の EXSV より前の、
  各工程の最後の試行」）。工程点は `axn.gg_review.evaluate`、総合点は `axn.gg_review.aggregate`
  （= ある工程の平均。欠けている工程は 0 点にしないで `Stages missing` に必ず出す）。AC の工程点は上の AC スコア。

計算は AXN の関数（axn_bridge）。SQserver 上のファイルは読むだけ。結果は score_store（D:\\Qstatus\\database\\Scores）へ。

    python -m scoring.rescore --machine AXN2 --experiment "20240220_0833 ANAN#2Pex1n1_LE"
    python -m scoring.rescore --machine AXN2 --all            # 一覧（Upload）にある実験で、まだ再構成していないもの
    python -m scoring.rescore --machine AXN2 --measured       # 計測時の Score.csv を取り込む
"""
import argparse
from collections import OrderedDict
from dataclasses import dataclass, field
import sys

from . import axn_bridge, experiment_files, machine_data, score_store
from .machine_data import STAGES
from .score_store import MEASURED, RECONSTRUCTED

TIME_FORMAT = '%Y%m%d_%H:%M'        # AXN の「<工程> Time」と同じ書き方
MEASURED_CRITERIA = 'AXN Score.csv'


@dataclass
class Outcome:
    """1 実験の再構成の結果。rows は score_store の列で、kind ごと。"""
    name: str
    machine: str
    location: str = ''
    era: str = ''
    rows: dict = field(default_factory=lambda: {'AC': [], 'EXSV': [], 'AutoGG': []})
    skipped: list = field(default_factory=list)

    @property
    def found(self):
        return bool(self.location)

    def summary(self):
        counts = ', '.join(f'{kind} {len(rows)}' for kind, rows in self.rows.items())
        where = self.location or '見当たらない'
        return f'{self.name}: {where} [{self.era or "-"}] {counts}' + (f'; 飛ばした {len(self.skipped)}' if self.skipped else '')


def _number(value):
    return '' if value is None else f'{value:.1f}'


def _column(rows, index):
    out = []
    for row in rows:
        try:
            out.append(float(row[index]))
        except (ValueError, IndexError):
            continue
    return out


# --- AC ---
def ac_score_of(mods, result, log):
    """R_AC の値と 50 ms ログの行から (AcScore | None, 計算できない理由)。AXN の backfill と同じ手順。"""
    readings = []
    for row in log:
        if len(row) > 2 and row[0].strip():
            try:
                readings.append((float(row[1]), float(row[2])))
            except ValueError:
                continue
    if not readings:
        return None, '50 ms ログが空'
    bias_v = readings[0][0] / 1000
    sr = mods.ac.sr_from_result(result.fields, bias_v)
    if sr is None:
        return None, 'G0 current / nG0 がなく系抵抗が決まらない'
    s = mods.ac.score([i * 1e-6 if abs(i) < 1e30 else 1. for _, i in readings], bias_v, sr)
    return (s, '') if s is not None else (None, 'カットがない')


def ac_rows(exp, mods, device, prov):
    """(行, {(AC#, 時刻): AcScore}, 飛ばした理由)。"""
    rows, scores, skipped = [], {}, []
    for result in exp.results('AC'):
        label = f'AC#{result.no} {result.when}'
        log = exp.pm_log('AC', result.no, result.when)
        if log is None:
            skipped.append(f'{label}: 50 ms ログ（PM_AC）がない')
            continue
        s, reason = ac_score_of(mods, result, log)
        if s is None:
            skipped.append(f'{label}: {reason}')
            continue
        row = dict(mods.ac.score_row(exp.name, device, result.fields, s))
        row.update(prov)
        rows.append(row)
        scores[(result.no, result.when)] = s
    return rows, scores, skipped


# --- EXSV ---
def exsv_rows(exp, mods, device, prov):
    rows, skipped = [], []
    for result in exp.results('EXSV'):
        label = f'EXSV#{result.no} {result.when}'
        pml, pm = exp.exsv_logs(result.no, result.when)
        if pml is None or pm is None:
            skipped.append(f'{label}: PML / PM ログがない')
            continue
        try:
            s = mods.exsv_score.score_of_fields(result.fields, _column(pml, 5), _column(pm, 5))
        except (KeyError, ValueError) as exc:
            skipped.append(f'{label}: 結果の値が読めない ({exc})')
            continue
        if s is None:
            skipped.append(f'{label}: ログが短すぎる')
            continue
        row = dict(mods.exsv_score.history_row(exp.name, device, result.fields, s))
        row.update(prov)
        rows.append(row)
    return rows, skipped


# --- Gap Generation（Auto GG の 1 回分） ---
def stage_status(stage, fields):
    """結果ファイルから見た工程の終わり方。FC は FB success?、AC は QC。ほかは結果があれば completed。"""
    if stage == 'FC':
        success = next((v for k, v in fields.items() if k.strip() == 'FB success?'), '')
        return 'completed' if success.strip().lower() in ('yes', '') else 'failed'
    if stage == 'AC':
        return 'completed' if fields.get('AC QC', 'Good') in ('Good', '') else 'failed'
    return 'completed'


def gg_rounds(exp, mods, ac_scores, device, prov):
    """EXSV の時刻で区切った区間ごとに、各工程の最後の試行を 1 回分として採点した行。"""
    results = {stage: exp.results(stage) for stage in STAGES}
    if not any(results.values()):
        return [], ['Gap Generation（FC〜CAL）の結果がない']
    exsv_times = sorted({r.time for r in exp.results('EXSV') if r.time})
    bounds = [None] + exsv_times + [None]
    rows = []
    for lo, hi in zip(bounds, bounds[1:]):
        picked = {}
        for stage, trials in results.items():
            inside = [r for r in trials if r.time and (lo is None or r.time > lo) and (hi is None or r.time <= hi)]
            if inside:
                picked[stage] = inside[-1]
        if not picked:
            continue
        stages = []
        for stage in STAGES:
            if stage not in picked:
                continue
            result = picked[stage]
            fields = OrderedDict(result.fields)
            if stage == 'AC':
                s = ac_scores.get((result.no, result.when))
                if s is not None:
                    fields.update(mods.ac.score_fields(s))
            stages.append(mods.gg_review.evaluate(stage, fields, stage_status(stage, fields)))
        overall = mods.gg_review.aggregate(stages)
        start = min(r.time for r in picked.values())
        row = {'Experimental ID': exp.name, 'Device Code': device, 'Round': str(len(rows) + 1),
               'Round start': start.strftime(TIME_FORMAT), 'Round end': hi.strftime(TIME_FORMAT) if hi else '',
               'AutoGG Score': _number(overall['score']),
               'Stages scored': f"{overall['scored_stages']} / {overall['selected_stages']}",
               'Stages used': ';'.join(s['stage'] for s in stages),
               'Stages missing': ';'.join(stage for stage in STAGES if stage not in picked), 'Decision': ''}
        row.update({f"{s['stage']} score": _number(s['score']) for s in stages})
        row.update(prov)
        rows.append(row)
    return rows, []


# --- 時代 ---
def era_of_experiment(exp, machine, table):
    """Python 版 AXN の痕跡（AutoGG フォルダ、R_AC の AC Score）があれば Python、なければ era.json の日付で。"""
    if exp.has_folder('AutoGG') or any('AC Score' in r.fields for r in exp.results('AC')):
        return score_store.ERA_PYTHON
    return score_store.era_of(machine, machine_data.experiment_time(exp.name), table)


# --- 1 実験 ---
def rescore_experiment(name, machine, machines_table, mods, era_table):
    outcome = Outcome(name, machine)
    info = machines_table.get(machine)
    if info is None:
        outcome.skipped.append(f'装置 {machine} が Machine_N.csv にない')
        return outcome
    exp = experiment_files.locate(name, info, machine)
    if exp is None:
        outcome.skipped.append('PC / Tmp_backup / SQ_backup のどこにも見当たらない')
        return outcome
    with exp:
        outcome.location = f'{exp.location.place}: {exp.location.text}'
        outcome.era = era_of_experiment(exp, machine, era_table)
        device = exp.device_code()
        prov = score_store.provenance(machine, RECONSTRUCTED, outcome.era, mods.criteria, exp.location.text)
        outcome.rows['AC'], ac_scores, skipped = ac_rows(exp, mods, device, prov)
        outcome.skipped += skipped
        outcome.rows['EXSV'], skipped = exsv_rows(exp, mods, device, prov)
        outcome.skipped += skipped
        outcome.rows['AutoGG'], skipped = gg_rounds(exp, mods, ac_scores, device, prov)
        outcome.skipped += skipped
    return outcome


def save(outcomes, folder=None):
    """再構成の行を score_store に足す。{kind: (足した数, 置き換えた数)}。"""
    result = {}
    for kind in ('AC', 'EXSV', 'AutoGG'):
        rows = [row for outcome in outcomes for row in outcome.rows[kind]]
        result[kind] = score_store.upsert(kind, rows, folder) if rows else (0, 0)
    return result


def rescore_many(names, machine, folder=None, progress=None, mods=None, machines_table=None, era_table=None):
    """実験を順に再構成して保存する。progress(i, n, message) を呼ぶ。戻り値は (Outcome の一覧, 保存の数)。"""
    mods = axn_bridge.load() if mods is None else mods
    machines_table = machine_data.machines() if machines_table is None else machines_table
    era_table = score_store.era_table(folder) if era_table is None else era_table
    outcomes = []
    for i, name in enumerate(names):
        if progress:
            progress(i, len(names), name)
        outcomes.append(rescore_experiment(name, machine, machines_table, mods, era_table))
    saved = save(outcomes, folder)
    if progress:
        progress(len(names), len(names), '保存しました')
    return outcomes, saved


def reconstructed_names(folder=None):
    """すでに再構成の行がある Experimental ID（どの種類でも）。"""
    names = set()
    for kind in ('AC', 'EXSV', 'AutoGG'):
        names.update(row['Experimental ID'] for row in score_store.read(kind, folder)
                     if row.get('Source') == RECONSTRUCTED)
    return names


# --- 計測時の Score.csv の取り込み ---
def _measured_gg(raw):
    scores = {stage: raw.get(f'{stage} score', '') for stage in STAGES}
    used = [stage for stage in STAGES if scores[stage] != '']
    row = {'Experimental ID': raw.get('Experimental ID', ''), 'Device Code': raw.get('Device Code', ''), 'Round': '',
           'Round start': raw.get('AutoGG Started', ''), 'Round end': raw.get('AutoGG Finished', ''),
           'AutoGG Score': raw.get('AutoGG Score', ''), 'Stages scored': raw.get('Stages scored', ''),
           'Stages used': ';'.join(used), 'Stages missing': ';'.join(s for s in STAGES if s not in used),
           'Decision': raw.get('Decision', '')}
    row.update({f'{stage} score': scores[stage] for stage in STAGES})
    return row


def measured_rows(machine, kind, era_table, root=None):
    """計測時の Score.csv（machinedb）の行を score_store の列にして返す（Source = 計測時）。"""
    path = machine_data.score_history_path(machine, kind, root)
    out = []
    for raw in machine_data.score_history(machine, kind, root):
        name = raw.get('Experimental ID', '')
        if not name:
            continue
        era = score_store.era_of(machine, machine_data.experiment_time(name), era_table)
        row = _measured_gg(raw) if kind == 'AutoGG' else dict(raw)
        row.update(score_store.provenance(machine, MEASURED, era, MEASURED_CRITERIA, str(path)))
        out.append(row)
    return out


def import_measured(machine, folder=None, root=None, era_table=None):
    """計測時の Score.csv を 3 種類とも取り込む。{kind: (足した数, 置き換えた数)}。"""
    era_table = score_store.era_table(folder) if era_table is None else era_table
    result = {}
    for kind in ('AC', 'EXSV', 'AutoGG'):
        rows = measured_rows(machine, kind, era_table, root)
        result[kind] = score_store.upsert(kind, rows, folder) if rows else (0, 0)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='EXSV / AC / Gap Generation スコアの再構成と取り込み')
    parser.add_argument('--machine', required=True, help='装置コード（AXN2 など）')
    parser.add_argument('--experiment', action='append', default=[], help='Experimental ID（複数可）')
    parser.add_argument('--all', action='store_true', help='SQserver の一覧にある実験のうち、まだ再構成していないもの全部')
    parser.add_argument('--measured', action='store_true', help='計測時の Score.csv を取り込む')
    parser.add_argument('--folder', default=None, help='保存先（既定 D:\\Qstatus\\database\\Scores）')
    parser.add_argument('--dry-run', action='store_true', help='計算だけして保存しない')
    args = parser.parse_args(argv)
    folder = args.folder
    if args.measured:
        for kind, (added, replaced) in import_measured(args.machine, folder).items():
            print(f'{kind}: 計測時の行 {added} 件を足し、{replaced} 件を置き換えました')
    names = list(args.experiment)
    if args.all:
        done = reconstructed_names(folder)
        names += sorted(n for n in machine_data.inventory(args.machine) if n not in done and n not in names)
    if not names:
        if args.measured and not args.dry_run:
            print(f'台帳: {write_ledger(folder)}')
        return 0
    mods = axn_bridge.load()
    print(f'AXN: {mods.root} ({mods.criteria})')
    table, era = machine_data.machines(), score_store.era_table(folder)
    outcomes = []
    for i, name in enumerate(names, 1):
        outcome = rescore_experiment(name, args.machine, table, mods, era)
        outcomes.append(outcome)
        print(f'[{i}/{len(names)}] {outcome.summary()}')
        for line in outcome.skipped:
            print(f'    - {line}')
    if not args.dry_run:
        for kind, (added, replaced) in save(outcomes, folder).items():
            print(f'{kind}: {added} 件を足し、{replaced} 件を置き換えました → {score_store.path(kind, folder)}')
        print(f'台帳: {write_ledger(folder)}')
    return 0


def write_ledger(folder=None):
    """保存済みのスコアから台帳 experiment_scores.csv を書き直す。戻り値はそのパス。"""
    from . import experiment_ledger
    return experiment_ledger.write(experiment_ledger.build(experiment_ledger.load_scores(folder)), folder)


if __name__ == '__main__':
    sys.exit(main())
