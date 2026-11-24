"""後からのスコア計算（再構成）と、計測時の Score.csv の取り込み。

- AC: 結果 R_AC（G0 current・nG0 → 系抵抗）と 50 ms ログ PM_AC から `axn.ac.score`
- EXSV: 結果 R_EXSV と 0.5 s ログ PML（ブロック RMS）・50 ms ログ PM（ピエゾ）から `axn.exsv_score.score`
- Gap Generation（Auto GG の 1 回分 = FC・MA・TA・AC・CAL）: 手動の GG には「1 回分」の区切りが無いので、
  その実験の EXSV の時刻で区切り、区間ごとに各工程の最後の試行を 1 回分とする（最初の区間が「最初の EXSV より前の、
  各工程の最後の試行」）。工程点は `axn.gg_review.evaluate`、総合点は `axn.gg_review.aggregate`
  （= ある工程の平均。欠けている工程は 0 点にしないで `Stages missing` に必ず出す）。AC の工程点は上の AC スコア。

時代（LabVIEW / Python）は 1 回ごとにファイルの目印で決める（run_era）。同じ装置でも実験ごと、1 実験の中でも
入れ替わるので、日付の境目は使えない（2026-11-18、AXN2・AXN3 の実データで確認）:
- 結果ファイルに `AC Score` / `EXSV Score` の行がある → Python（Python 版だけが書く）
- FC〜CAL の 50 ms ログの時刻が 0.05 秒の刻みどおり → LabVIEW、刻みから外れる行が 1 割以上 → Python
  （LabVIEW は回数 × 0.05 を書き、Python は実時刻を書く。調べた全回で例外なし）
- EXSV は刻みで区別できないので、同じ実験で直前の Gap Generation の回の時代を引き継ぐ（LabVIEW と Python は
  同時に装置へつなげない）
- 2026-10-29（Python 版の開発開始）より前は必ず LabVIEW

計算は AXN の関数（axn_bridge）。SQserver 上のファイルは読むだけ。結果は score_store（D:\\Qstatus\\database\\Scores）へ。

    python -m scoring.rescore --machine AXN2 --experiment "20240220_0833 ANAN#2Pex1n1_LE"
    python -m scoring.rescore --machine AXN2 --all            # 一覧（Upload）にある実験で、まだ再構成していないもの
    python -m scoring.rescore --machine all --all              # 全装置
    python -m scoring.rescore --machine AXN2 --measured       # 計測時の Score.csv を取り込む
"""
import argparse
from collections import OrderedDict
from dataclasses import dataclass, field
import sys
import time

from . import axn_bridge, experiment_files, machine_data, score_store
from .machine_data import STAGES
from .score_store import ERA_LABVIEW, ERA_PYTHON, ERA_UNKNOWN, MEASURED, RECONSTRUCTED
from shared import chip_aliases, machinedb

_ALIAS_TABLE = None


def current_device(code):
    """チップID（Device Code）を、チップIDの対応表（shared.chip_aliases）で今の名前に。実験フォルダの結果ファイルや
    計測時の Score.csv には直す前の名前が残っているため（サーバーの実験データは書き換えない。docs/chip_id_cleanup.md）。"""
    global _ALIAS_TABLE
    if _ALIAS_TABLE is None:
        try:
            _ALIAS_TABLE = chip_aliases.load(chip_aliases.table_path(machinedb.ROOT.parent))
        except (OSError, ValueError):
            _ALIAS_TABLE = chip_aliases.empty()
    return chip_aliases.new_name(code, _ALIAS_TABLE) if code else code

TIME_FORMAT = '%Y%m%d_%H:%M'        # AXN の「<工程> Time」と同じ書き方
MEASURED_CRITERIA = 'AXN Score.csv'
GRID_S = 0.05                       # 50 ms ログの刻み
GRID_MIN_ROWS = 20                  # これより短いログでは判定しない
PYTHON_OFF_GRID = 0.1               # 刻みから外れる行がこの割合以上なら Python


@dataclass
class Outcome:
    """1 実験の再構成の結果。rows は score_store の列で、kind ごと。"""
    name: str
    machine: str
    location: str = ''
    era: str = ''
    rows: dict = field(default_factory=lambda: {'AC': [], 'EXSV': [], 'AutoGG': []})
    skipped: list = field(default_factory=list)
    seconds: float = 0.

    @property
    def found(self):
        return bool(self.location)

    def summary(self):
        counts = ', '.join(f'{kind} {len(rows)}' for kind, rows in self.rows.items())
        where = self.location or '見当たらない'
        return (f'{self.name}: {where} [{self.era or "-"}] {counts} ({self.seconds:.1f} s)'
                + (f'; 飛ばした {len(self.skipped)}' if self.skipped else ''))


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


# --- 時代 ---
def grid_era(rows):
    """50 ms ログの時刻の刻みから時代: すべて 0.05 秒の倍数なら LabVIEW、外れる行が 1 割以上なら Python。"""
    times = _column(rows or [], 0)
    if len(times) < GRID_MIN_ROWS:
        return ERA_UNKNOWN
    off = sum(1 for t in times if abs(t / GRID_S - round(t / GRID_S)) > 0.02)
    if off == 0:
        return ERA_LABVIEW
    return ERA_PYTHON if off / len(times) >= PYTHON_OFF_GRID else ERA_UNKNOWN


def run_era(exp, result):
    """1 回（R_<工程> 1 つ）の時代。"""
    when = result.time or machine_data.experiment_time(exp.name)
    if score_store.era_by_date(when) == ERA_LABVIEW:
        return ERA_LABVIEW
    if any(name.startswith(f'{result.stage} Score') for name in result.fields):
        return ERA_PYTHON
    if result.stage in STAGES:
        return grid_era(exp.pm_log(result.stage, result.no, result.when))
    return ERA_UNKNOWN


def stage_eras(exp):
    """{(工程, 番号, 時刻): 時代} と、Gap Generation の回の (時刻, 時代) の時刻順の一覧（EXSV が引き継ぐため）。"""
    eras, timeline = {}, []
    for stage in STAGES:
        for result in exp.results(stage):
            era = run_era(exp, result)
            eras[(stage, result.no, result.when)] = era
            if era != ERA_UNKNOWN and result.time:
                timeline.append((result.time, era))
    return eras, sorted(timeline)


def exsv_era(exp, result, timeline):
    era = run_era(exp, result)
    if era != ERA_UNKNOWN or result.time is None:
        return era
    before = [e for t, e in timeline if t <= result.time]
    return before[-1] if before else ERA_UNKNOWN


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


def ac_rows(exp, mods, device, prov, eras):
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
        row.update(prov, Era=eras.get(('AC', result.no, result.when), ERA_UNKNOWN))
        rows.append(row)
        scores[(result.no, result.when)] = s
    return rows, scores, skipped


# --- EXSV ---
def exsv_rows(exp, mods, device, prov, timeline):
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
        row.update(prov, Era=exsv_era(exp, result, timeline))
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


def gg_rounds(exp, mods, ac_scores, device, prov, eras):
    """EXSV の時刻で区切った区間ごとに、各工程の最後の試行を 1 回分として採点した行。
    1 回分の時代は工程の時代をまとめたもの（LabVIEW と Python が混ざれば 混在）。"""
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
        row.update(prov, Era=score_store.combine_eras(
            eras.get((stage, r.no, r.when), ERA_UNKNOWN) for stage, r in picked.items()))
        rows.append(row)
    return rows, []


# --- 1 実験 ---
def rescore_experiment(name, machine, machines_table, mods):
    started = time.monotonic()
    outcome = Outcome(name, machine)
    info = machines_table.get(machine)
    if info is None:
        outcome.skipped.append(f'装置 {machine} が Machine_N.csv にない')
        return outcome
    try:
        exp = experiment_files.locate(name, info, machine)
    except OSError as exc:
        outcome.skipped.append(f'置き場所を調べられない ({exc})')
        return outcome
    if exp is None:
        outcome.skipped.append('PC / Tmp_backup / SQ_backup のどこにも見当たらない')
        return outcome
    try:
        with exp:
            outcome.location = f'{exp.location.place}: {exp.location.text}'
            device = current_device(exp.device_code())
            prov = score_store.provenance(machine, RECONSTRUCTED, ERA_UNKNOWN, mods.criteria, exp.location.text)
            eras, timeline = stage_eras(exp)
            outcome.rows['AC'], ac_scores, skipped = ac_rows(exp, mods, device, prov, eras)
            outcome.skipped += skipped
            outcome.rows['EXSV'], skipped = exsv_rows(exp, mods, device, prov, timeline)
            outcome.skipped += skipped
            outcome.rows['AutoGG'], skipped = gg_rounds(exp, mods, ac_scores, device, prov, eras)
            outcome.skipped += skipped
    except (OSError, ValueError, KeyError, RuntimeError) as exc:     # 壊れた zip・読めないファイル: この実験だけ飛ばす
        outcome.skipped.append(f'読めない ({type(exc).__name__}: {exc})')
    outcome.era = score_store.combine_eras(r.get('Era', '') for rows in outcome.rows.values() for r in rows)
    outcome.seconds = time.monotonic() - started
    return outcome


def save(outcomes, folder=None):
    """再構成の行を score_store に足し、計測時の行の時代を合わせる。{kind: (足した数, 置き換えた数)}。"""
    result = {}
    for kind in ('AC', 'EXSV', 'AutoGG'):
        rows = [row for outcome in outcomes for row in outcome.rows[kind]]
        result[kind] = score_store.upsert(kind, rows, folder) if rows else (0, 0)
    score_store.sync_measured_eras(folder)
    return result


def rescore_many(names, machine, folder=None, progress=None, mods=None, machines_table=None):
    """実験を順に再構成して保存する。progress(i, n, message) を呼ぶ。戻り値は (Outcome の一覧, 保存の数)。"""
    mods = axn_bridge.load() if mods is None else mods
    machines_table = machine_data.machines() if machines_table is None else machines_table
    outcomes = []
    for i, name in enumerate(names):
        if progress:
            progress(i, len(names), name)
        outcomes.append(rescore_experiment(name, machine, machines_table, mods))
    saved = save(outcomes, folder)
    if progress:
        progress(len(names), len(names), '保存しました')
    return outcomes, saved


def reconstructed_names(folder=None, machine=None):
    """すでに再構成の行がある Experimental ID（どの種類でも）。machine を渡すとその装置だけ。"""
    names = set()
    for kind in ('AC', 'EXSV', 'AutoGG'):
        names.update(row['Experimental ID'] for row in score_store.read(kind, folder)
                     if row.get('Source') == RECONSTRUCTED and (machine is None or row.get('Machine') == machine))
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


def measured_rows(machine, kind, root=None):
    """計測時の Score.csv（machinedb）の行を score_store の列にして返す（Source = 計測時）。

    時代: Auto GG は Python 版だけが書くので Python。AC・EXSV は Python 版の計測時の行と、LabVIEW の計測を
    後から採点した行が混ざるので、日付で決まらなければ 不明（再構成すると sync_measured_eras で合わせる）。"""
    path = machine_data.score_history_path(machine, kind, root)
    out = []
    for raw in machine_data.score_history(machine, kind, root):
        name = raw.get('Experimental ID', '')
        if not name:
            continue
        era = ERA_PYTHON if kind == 'AutoGG' else score_store.era_by_date(machine_data.experiment_time(name))
        row = _measured_gg(raw) if kind == 'AutoGG' else dict(raw)
        row['Device Code'] = current_device(row.get('Device Code', ''))
        row.update(score_store.provenance(machine, MEASURED, era, MEASURED_CRITERIA, str(path)))
        out.append(row)
    return out


def import_measured(machine, folder=None, root=None):
    """計測時の Score.csv を 3 種類とも取り込む。{kind: (足した数, 置き換えた数)}。"""
    result = {}
    for kind in ('AC', 'EXSV', 'AutoGG'):
        rows = measured_rows(machine, kind, root)
        result[kind] = score_store.upsert(kind, rows, folder) if rows else (0, 0)
    score_store.sync_measured_eras(folder)
    return result


def write_ledger(folder=None):
    """保存済みのスコアから台帳 experiment_scores.csv を書き直す。戻り値はそのパス。"""
    from . import experiment_ledger
    return experiment_ledger.write(experiment_ledger.build(experiment_ledger.load_scores(folder)), folder)


def main(argv=None):
    parser = argparse.ArgumentParser(description='EXSV / AC / Gap Generation スコアの再構成と取り込み')
    parser.add_argument('--machine', required=True, help='装置コード（AXN2 など）。all で全装置')
    parser.add_argument('--experiment', action='append', default=[], help='Experimental ID（複数可）')
    parser.add_argument('--all', action='store_true', help='SQserver の一覧にある実験IDのうち、まだ再構成していないもの全部')
    parser.add_argument('--redo', action='store_true', help='--all で、再構成済みの実験IDも計算し直す')
    parser.add_argument('--measured', action='store_true', help='計測時の Score.csv を取り込む')
    parser.add_argument('--folder', default=None, help='保存先（既定 D:\\Qstatus\\database\\Scores）')
    parser.add_argument('--save-every', type=int, default=250,
                        help='この件数ごとに保存する（途中で止まっても残る）。共有フォルダの大きなファイルを何度も書き直すと'
                             '同期の競合コピーができるので、間隔は広めに（2026-11-18）')
    parser.add_argument('--dry-run', action='store_true', help='計算だけして保存しない')
    args = parser.parse_args(argv)
    folder = args.folder
    machines = machine_data.measurement_machines() if args.machine == 'all' else [args.machine]
    if args.experiment and len(machines) != 1:
        parser.error('--experiment は装置を 1 台指定してください')
    if args.measured:
        for machine in machines:
            for kind, (added, replaced) in import_measured(machine, folder).items():
                print(f'{machine} {kind}: 計測時の行 {added} 件を足し、{replaced} 件を置き換えました')
    jobs = [(machines[0], name) for name in args.experiment]
    if args.all:
        for machine in machines:
            done = set() if args.redo else reconstructed_names(folder, machine)
            jobs += [(machine, n) for n in sorted(machine_data.inventory(machine), reverse=True)
                     if n not in done and (machine, n) not in jobs]
    if not jobs:
        if args.measured and not args.dry_run:
            print(f'台帳: {write_ledger(folder)}')
        return 0
    mods = axn_bridge.load()
    table = machine_data.machines()
    print(f'AXN: {mods.root} ({mods.criteria}); {len(jobs)} 件の実験ID', flush=True)
    pending, totals = [], {'AC': 0, 'EXSV': 0, 'AutoGG': 0}
    started = time.monotonic()

    def flush():
        if pending and not args.dry_run:
            for kind, (added, replaced) in save(pending, folder).items():
                totals[kind] += added + replaced
        pending.clear()

    for i, (machine, name) in enumerate(jobs, 1):
        outcome = rescore_experiment(name, machine, table, mods)
        pending.append(outcome)
        elapsed = time.monotonic() - started
        print(f'[{i}/{len(jobs)} {elapsed / 60:.0f} min] {machine} {outcome.summary()}', flush=True)
        for line in outcome.skipped[:5]:
            print(f'    - {line}', flush=True)
        if len(outcome.skipped) > 5:
            print(f'    - ほか {len(outcome.skipped) - 5} 件', flush=True)
        if len(pending) >= args.save_every:
            flush()
    flush()
    if not args.dry_run:
        print('保存した行: ' + ', '.join(f'{k} {v}' for k, v in totals.items()) + f' → {score_store.default_dir() if folder is None else folder}')
        print(f'台帳: {write_ledger(folder)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
