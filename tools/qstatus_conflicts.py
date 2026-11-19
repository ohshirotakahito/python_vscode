"""共有フォルダ（D:\\Qstatus）の同期の競合コピーを確かめ、安全なものを本体に統合する（shared.sync_conflicts）。

    python tools/qstatus_conflicts.py                 # 確かめるだけ（何も変えない）
    python tools/qstatus_conflicts.py --apply         # 片付け・統合を行う
    python tools/qstatus_conflicts.py --register      # 毎晩 2:20 に --apply を動かすタスクを登録（PC が止まっていたら起動後すぐ）
    python tools/qstatus_conflicts.py --unregister

結果は D:\\Qstatus\\database\\ConflictBackups\\latest.json（自動で触らなかったものの理由も）と、
このリポジトリの database/logs/conflicts.log に残す。
"""
import argparse
from datetime import datetime
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from shared import chip_aliases, machinedb, sync_conflicts  # noqa: E402

ROOT = machinedb.ROOT.parent                     # D:\Qstatus
TASK_NAME = 'Qstatus Conflict Merge'
TASK_TIME = '02:20'                              # 毎時 :05（台帳）・:35（AXN 一覧）、3 時台（夜間の台帳）を避ける
LOG = REPO / 'database' / 'logs' / 'conflicts.log'


def log(text):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as stream:
        stream.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n')
    if sys.stdout is not None:
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode('ascii', 'replace').decode())


def register():
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(__file__).resolve()
    # schtasks では「時刻を逃したら起動後すぐ」（StartWhenAvailable）を付けられないので PowerShell で登録する
    command = (
        f"$a = New-ScheduledTaskAction -Execute '{exe}' -Argument '\"{script}\" --apply --scheduled' -WorkingDirectory '{REPO}'; "
        f"$t = New-ScheduledTaskTrigger -Daily -At {TASK_TIME}; "
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s "
        "-Description '共有フォルダ D:\\Qstatus の同期の競合コピーを安全なものだけ本体に統合（Python_VScode tools/qstatus_conflicts.py）' -Force | Out-Null; "
        f"(Get-ScheduledTask -TaskName '{TASK_NAME}').Settings.StartWhenAvailable")
    return subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True, text=True)


def unregister():
    return subprocess.run(['schtasks', '/Delete', '/F', '/TN', TASK_NAME], capture_output=True, text=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--scheduled', action='store_true', help='タスクから動いたときの印（記録に残すだけ）')
    parser.add_argument('--min-age-minutes', type=float, default=sync_conflicts.MIN_AGE_S / 60,
                        help='本体がこの分数以内に書き込まれていたら触らない（既定 30。計測中かもしれないため）')
    parser.add_argument('--register', action='store_true')
    parser.add_argument('--unregister', action='store_true')
    args = parser.parse_args(argv)
    if args.register:
        result = register()
        print(result.stdout.strip() or result.stderr.strip())
        return result.returncode
    if args.unregister:
        result = unregister()
        print(result.stdout.strip() or result.stderr.strip())
        return result.returncode
    try:
        table = chip_aliases.load(chip_aliases.table_path(ROOT))
    except (OSError, ValueError):
        table = None
    items = sync_conflicts.plan(ROOT, table, min_age_s=args.min_age_minutes * 60)
    counts = {action: sum(i.action == action for i in items) for action in ('archive', 'merge', 'review')}
    log(f"{'scheduled ' if args.scheduled else ''}{'apply' if args.apply else 'check'}: "
        f"{len(items)} 個（片付け {counts['archive']}・統合 {counts['merge']}・要確認 {counts['review']}）")
    results, session = [], None
    if args.apply and (counts['archive'] or counts['merge']):
        session, results = sync_conflicts.apply(items, ROOT, alias_table=table)
        done = sum(r['status'] == 'done' for r in results)
        log(f'  済 {done}・止めた {len(results) - done}（保管場所 {session}）')
        for r in results:
            if r['status'] != 'done':
                log(f"  止めた: {r['copy']}: {r['status']}")
    for item in items:
        if item.action == 'review':
            log(f'  要確認: {item.copy}: {item.reason}')
        elif not args.apply:
            log(f'  {item.action}: {item.copy}: {item.reason}')
    status = dict(checked_at=datetime.now().isoformat(timespec='seconds'), applied=args.apply,
                  backup=str(session) if session else '', counts=counts,
                  items=[dict(i.summary(), result=next((r['status'] for r in results if r['copy'] == i.copy), ''))
                         for i in items])
    target = ROOT / 'database' / 'ConflictBackups' / 'latest.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
