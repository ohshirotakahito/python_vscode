"""台帳を共有フォルダ（D:\\Qstatus\\database）に書き出し、計測PCから実験の進捗を見られるようにする。

手で実行する（設定の有効・無効に関係なく動く）:
    python database/publish.py              # 走査してから全台帳を書き出す
    python database/publish.py --no-scan    # 走査せず、保存済みDBから書き出すだけ
    python database/publish.py --full       # 夜間と同じ走査（Archived/Stocked も含める）
タスク スケジューラから1時間ごとに実行する:
    python database/publish.py --scheduled  # 自動実行が無効なら何もしない
制御:
    python database/publish.py --enable | --disable | --status
    python database/publish.py --install-task | --remove-task

書き出す台帳（UTF-8 BOM付きCSV。書き終えてから置き換えるので、途中の中身は見えない）:
    sq_experiments.csv       SQserver 実験一覧（装置・元の名前・解析着手・TDMS数）
    analysis_server_all.csv  解析サーバーのサンプルごとのANAL進捗（Analysis_serverタブと同じ）
    bnal_progress.csv        サンプル台帳で BNAL 対象のサンプルの target ごとのBNAL進捗
    sq_transfers.csv         SQserver Recent_Data の実験ごとの解析サーバーへの転送と ANAL進捗（transfer_ledger.py）
    ledger_status.csv        上の4つをいつ・どのPCが書いたか、走査の結果
"""
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
import argparse
import csv
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # リポジトリ直下（shared/ を読むため）
from shared import catalog, rack_store, sample_plan, seq_records  # noqa: E402

try:
    from . import bnal_progress, rackstation_view, sq_workflow, transfer_ledger
    from .scan_lock import ScanLock
except ImportError:
    import bnal_progress
    import rackstation_view
    import sq_workflow
    import transfer_ledger
    from scan_lock import ScanLock

BASE = Path(__file__).resolve().parent
PUBLISH_DIR = Path(r'D:\Qstatus\database')
SETTINGS_PATH = BASE / 'publish.json'
STATUS_PATH = BASE / 'logs' / 'publish_status.json'
LOG_PATH = BASE / 'logs' / 'publish.log'
TASK_NAME = 'Qstatus Ledger Publish'
FILES = dict(sq='sq_experiments.csv', analysis=rackstation_view.CSV_PATH.name, bnal='bnal_progress.csv',
             transfer='sq_transfers.csv', status='ledger_status.csv')
LEDGER_TITLES = dict(sq='SQserver 実験一覧', analysis='解析サーバー ANAL進捗', bnal='BNAL進捗',
                     transfer='SQserver→解析サーバー 転送・ANAL')
DEFAULT_SETTINGS = dict(
    enabled=False,           # タスク スケジューラからの実行（--scheduled）をするか
    scan_sq=True,            # 毎回 SQserver の Recent_Data を走査する
    scan_analysis=True,      # 毎回 解析サーバー（Analysis_server の全グループ）を走査する
    nightly_hour=3,          # この時（0〜23）の実行では Archived/Stocked と BNAL TDMS 数も確認する。null で行わない
)
EXPORTED_AT = 'CSV出力日時 UTC'
SQ_STATE_TEXT = dict(sq_needs_action='未着手・TDMSあり（優先）', sq_no_tdms='未着手・TDMS未作成',
                     sq_started='着手済み', sq_unknown='確認不可・対象外')

log = logging.getLogger('publish')


# ---------- 設定・状態 ----------

def load_settings(path=SETTINGS_PATH):
    settings = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return settings
    except (OSError, ValueError) as exc:
        log.warning('設定を読めないため既定値で動きます: %s', exc)
        return settings
    settings.update({key: value for key, value in data.items() if key in DEFAULT_SETTINGS})
    return settings


def save_settings(settings, path=SETTINGS_PATH):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({key: settings[key] for key in DEFAULT_SETTINGS}, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    temporary.replace(path)


def load_status(path=STATUS_PATH):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def load_configured(path=None):
    """Same sources.json as the database app (its defaults when missing or broken)."""
    try:
        from . import app
    except ImportError:
        import app
    try:
        data = json.loads(Path(path or app.CONFIG).read_text(encoding='utf-8'))
        if isinstance(data, list) and all(isinstance(s, dict) and s.get('kind') in catalog.SOURCE_KINDS
                                          and all(isinstance(s.get(k), str) for k in ('server', 'root'))
                                          for s in data):
            return data
        log.warning('保存先設定の形式が不正なため既定値を使います')
    except (OSError, ValueError) as exc:
        log.warning('保存先設定を読めないため既定値を使います: %s', exc)
    return [dict(s) for s in app.DEFAULTS]


# ---------- 書き出し ----------

def write_csv(destination, header, rows):
    """Write a complete UTF-8 (BOM) CSV next to the destination, then swap it in."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='', dir=destination.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sq_rows(entries, cell, exported_at):
    from_app = _app()
    rows = []
    for entry in entries:
        values = [cell(entry, key) for key in from_app.SQ_ENTRY_COLUMNS]
        rows.append(values + [SQ_STATE_TEXT[from_app.sq_entry_tag(entry)], exported_at])
    return rows


def export_sq(db, configured, destination, cell):
    from_app = _app()
    entries = from_app.App.read_inventory(db, '', configured)[4]
    exported_at = now_utc()
    write_csv(destination, from_app.SQ_ENTRY_TITLES + ('区分', EXPORTED_AT), sq_rows(entries, cell, exported_at))
    return len(entries)


def analysis_rows_by_group(db, configured):
    return {rack_store.group_label(server, share):
            rackstation_view.inventory(db, rackstation_view.default_root(configured, server, share))
            for server, share in rack_store.ANALYSIS_GROUPS}


def export_analysis(db, configured, destination, cell):
    rows = analysis_rows_by_group(db, configured)
    rackstation_view.export_all_csv(rows, cell, destination)
    return sum(len(group) for group in rows.values())


NO_SEQLIST_STATE = 'SEQlist未設定（実在するBNAL@のみ）'


def bnal_targets(samples, plan, bnals=()):
    """(experiment, sample) routed to BNAL in the sample ledger, or holding saved BNAL@ folders,
    among the saved samples; samples marked excluded in the ledger are left out."""
    found = {(b['experiment'], b['sample']) for b in bnals}
    chosen = []
    for key in bnal_progress.sample_choices(samples):
        state = plan.get(sample_plan.key(*key), {}).get('bnal')
        if state == 'target' or (key in found and state != 'excluded'):
            chosen.append(key)
    return chosen


def bnal_progress_rows(samples, bnals, plan, labels):
    """Targets from each sample's SEQlists; samples without SEQlists list only the BNAL@ folders that exist."""
    selected = bnal_targets(samples, plan, bnals)
    planned = [key for key in selected if plan.get(sample_plan.key(*key), {}).get('seqlists')]
    rows, _, _, warnings, _ = bnal_progress.build_progress_by_plan(planned, plan, samples, bnals, labels)
    unplanned = [key for key in selected if key not in set(planned)]
    extra, _, _, _ = bnal_progress.build_progress(unplanned, [], samples, bnals, labels, show_extra=True)
    for row in extra:
        row['state'] = NO_SEQLIST_STATE
    return rows + extra, warnings


def bnal_rows(rows, cell, exported_at):
    out = []
    for row in rows:
        values = dict(row)
        for key in ('anal_tdms', 'bnal_tdms', 'base_tdms'):
            values[key] = '—' if row.get(key) is None else row[key]
        values['rerun'] = row.get('rerun') or ''
        values['tdms_checked'] = row.get('tdms_checked') or '—'
        out.append([cell(values, key) for key in bnal_progress.DETAIL_COLUMNS] + [exported_at])
    return out


def export_bnal(db, configured, destination, cell, plan=None):
    samples, bnals = bnal_progress.load_saved(db)
    plan = sample_plan.load() if plan is None else plan
    rows, warnings = bnal_progress_rows(samples, bnals, plan, bnal_progress.group_labels(configured))
    for warning in warnings:
        if '台帳に SEQlist が未設定' not in warning:  # those samples are exported with NO_SEQLIST_STATE
            log.info('BNAL: %s', warning)
    write_csv(destination, bnal_progress.DETAIL_TITLES + (EXPORTED_AT,), bnal_rows(rows, cell, now_utc()))
    return len(rows)


def export_transfers(db, configured, destination, cell):
    from_app = _app()
    entries = from_app.App.read_inventory(db, '', configured)[4]
    machines = seq_records.machines(seq_records.MACHINE_CSV)
    history = seq_records.history(seq_records.LEGACY_HISTORY, seq_records.LOCAL_HISTORY)
    rows = transfer_ledger.build_rows(entries, machines, history, analysis_rows_by_group(db, configured))
    exported_at = now_utc()
    write_csv(destination, transfer_ledger.TITLES + (EXPORTED_AT,),
              [[row[key] for key in transfer_ledger.COLUMNS] + [exported_at] for row in rows])
    return len(rows)


EXPORTERS = dict(sq=export_sq, analysis=export_analysis, bnal=export_bnal, transfer=export_transfers)


def export_all(db, configured, out_dir, scans):
    """Write every ledger; one failure (e.g. open in Excel) does not stop the others."""
    cell = _app().App.cell
    results = {}
    for name, exporter in EXPORTERS.items():
        started = time.monotonic()
        try:
            count = exporter(db, configured, Path(out_dir) / FILES[name], cell)
            results[name] = dict(ok=True, rows=count, at=now_utc())
            log.info('書き出し %s: %d行 (%.0f 秒)', FILES[name], count, time.monotonic() - started)
        except Exception as exc:
            results[name] = dict(ok=False, error=str(exc), at=now_utc())
            log.error('書き出し失敗 %s: %s', FILES[name], exc)
    host = socket.gethostname()
    status_rows = [[LEDGER_TITLES[name], FILES[name], r.get('rows', ''), r['at'], host,
                    '' if r['ok'] else f"失敗（前回のファイルのまま）: {r['error']}"] for name, r in results.items()]
    status_rows += [['走査', name, '', r['at'], host, r['result']] for name, r in scans.items()]
    try:
        write_csv(Path(out_dir) / FILES['status'], ('台帳', 'ファイル', '行数', '出力・走査日時 UTC', 'PC', '備考'),
                  status_rows)
    except Exception as exc:
        log.error('書き出し失敗 %s: %s', FILES['status'], exc)
    return results


# ---------- 走査 ----------

def scan_sq(configured, db, stages):
    results = {}
    for stage in stages:
        for source, scope in sq_workflow.jobs_for_stage(configured, stage):
            started = time.monotonic()
            try:
                count = catalog.scan_source(source, db, scope=scope)
                result = f'完了 {count:,}ファイル'
            except Exception as exc:  # one unreachable share must not stop the others
                result = f'失敗: {exc}'
            results[source['root']] = dict(result=f'{result} ({time.monotonic() - started:.0f} 秒)', at=now_utc())
            log.info('走査 %s: %s', source['root'], results[source['root']]['result'])
    return results


def scan_analysis(configured, db, count_bnal=None):
    results = {}
    for server, share in rack_store.ANALYSIS_GROUPS:
        label = rack_store.group_label(server, share)
        source = dict(server=server, root=rackstation_view.default_root(configured, server, share), kind='analysis')
        started = time.monotonic()
        try:
            count = rack_store.scan_group(source, db, threading.Event(), lambda *args: None, count_bnal)
            result = f'完了 {count}サンプル'
        except Exception as exc:
            result = f'失敗: {exc}'
        results[label] = dict(result=f'{result} ({time.monotonic() - started:.0f} 秒)', at=now_utc())
        log.info('走査 %s: %s', label, results[label]['result'])
    return results


def run(settings, scan=True, full=False, db=catalog.DEFAULT_DB, out_dir=PUBLISH_DIR, configured=None,
        lock=None, status_path=STATUS_PATH):
    """Scan (when asked and no other scan is running), then write every ledger. Returns the status dict."""
    started = now_utc()
    configured = load_configured() if configured is None else configured
    if not Path(db).exists():
        catalog.initialize(db)
    scans = {}
    if scan and (settings['scan_sq'] or settings['scan_analysis']):
        lock = lock or ScanLock('台帳の書き出し（publish.py）')
        if lock.acquire():
            try:
                # Writing the schema needs the DB write lock: only while this run owns the scan.
                catalog.initialize(db)
                if settings['scan_sq']:
                    stages = ('Recent_Data', 'Archived_Data', 'Stocked_Data') if full else ('Recent_Data',)
                    scans.update(scan_sq(configured, db, stages))
                if settings['scan_analysis']:
                    scans.update(scan_analysis(configured, db, (lambda experiment: True) if full else None))
            finally:
                lock.release()
        else:
            holder = lock.holder()
            scans['走査'] = dict(result=f'ほかの走査が実行中のため省略（{holder}）', at=now_utc())
            log.info('ほかの走査が実行中のため走査を省略し、保存済みDBから書き出します: %s', holder)
    exports = export_all(db, configured, out_dir, scans)
    status = dict(started=started, finished=now_utc(), full=full, scanned=bool(scan), scans=scans,
                  exports=exports, ok=all(r['ok'] for r in exports.values())
                  and not any(r['result'].startswith('失敗') for r in scans.values()))
    try:
        Path(status_path).parent.mkdir(parents=True, exist_ok=True)
        Path(status_path).write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError as exc:
        log.warning('実行結果を保存できません: %s', exc)
    return status


# ---------- タスク スケジューラ ----------

def windowless_python():
    exe = Path(sys.executable)
    windowed = exe.with_name('pythonw.exe')
    return windowed if windowed.exists() else exe


def task_command():
    return f'"{windowless_python()}" "{Path(__file__).resolve()}" --scheduled'


def install_task():
    return subprocess.run(['schtasks', '/Create', '/F', '/TN', TASK_NAME, '/SC', 'HOURLY', '/ST', '00:05',
                           '/TR', task_command()], capture_output=True, text=True)


def remove_task():
    return subprocess.run(['schtasks', '/Delete', '/F', '/TN', TASK_NAME], capture_output=True, text=True)


def task_registered():
    try:
        return subprocess.run(['schtasks', '/Query', '/TN', TASK_NAME], capture_output=True, text=True,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode == 0
    except OSError:
        return False


def start_background(*args):
    """Start this script without a console (used by the database app's buttons)."""
    return subprocess.Popen([str(windowless_python()), str(Path(__file__).resolve()), *args],
                            cwd=str(BASE.parent), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


# ---------- 実行 ----------

def _app():
    try:
        from . import app
    except ImportError:
        import app
    return app


def setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S'))
    log.addHandler(handler)
    if sys.stderr is not None:  # pythonw has no console
        log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)


def main(argv=None):
    parser = argparse.ArgumentParser(description='台帳を共有フォルダに書き出す')
    parser.add_argument('--scheduled', action='store_true', help='タスク スケジューラ用（無効なら何もしない）')
    parser.add_argument('--no-scan', action='store_true', help='走査せず保存済みDBから書き出すだけ')
    parser.add_argument('--full', action='store_true', help='Archived/Stocked と BNAL TDMS 数も確認する')
    parser.add_argument('--out', default=str(PUBLISH_DIR), help='書き出し先フォルダ')
    for flag in ('enable', 'disable', 'status', 'install-task', 'remove-task'):
        parser.add_argument('--' + flag, action='store_true')
    args = parser.parse_args(argv)
    setup_logging()
    settings = load_settings()
    if args.enable or args.disable:
        settings['enabled'] = bool(args.enable)
        save_settings(settings)
        log.info('自動実行を%sにしました', '有効' if args.enable else '無効')
        return 0
    if args.install_task or args.remove_task:
        result = install_task() if args.install_task else remove_task()
        log.info('%s %s', result.stdout.strip(), result.stderr.strip())
        return 0 if result.returncode == 0 else 1
    if args.status:
        print(json.dumps(dict(settings=settings, task_registered=task_registered(), last_run=load_status()),
                         ensure_ascii=False, indent=2))
        return 0
    if args.scheduled and not settings['enabled']:
        return 0
    full = args.full or (args.scheduled and settings['nightly_hour'] is not None
                         and datetime.now().hour == settings['nightly_hour'])
    log.info('開始（%s%s%s）→ %s', '自動' if args.scheduled else '手動', '・走査なし' if args.no_scan else '',
             '・夜間の全走査' if full and not args.no_scan else '', args.out)
    status = run(settings, scan=not args.no_scan, full=full, out_dir=Path(args.out))
    log.info('終了: %s', '正常' if status['ok'] else '一部失敗（ログを確認）')
    return 0 if status['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
