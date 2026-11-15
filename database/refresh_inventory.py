"""Database-owned inventory discovery, callable by other applications as a process."""
import argparse
import json
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import catalog, rack_store
from database.scan_lock import ScanLock


def targets(path, group=None):
    sources = {catalog.root_identity(f'//{server}/{share}'): dict(
        server=server, root=f'//{server}/{share}', kind='analysis')
        for server, share in rack_store.ANALYSIS_GROUPS}
    if Path(path).exists():
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='rack_roots'").fetchone():
                for root, server in db.execute('SELECT root, server FROM rack_roots'):
                    sources[catalog.root_identity(root)] = dict(root=root, server=server, kind='analysis')
    result = list(sources.values())
    if group:
        def label(source):
            share = source['root'].replace('\\', '/').rstrip('/').split('/')[-1]
            return rack_store.group_label(source['server'], share)
        result = [source for source in result if label(source) == group]
    if not result:
        raise ValueError('更新対象のサーバー共有が見つかりません。')
    return result


def refresh(path, sources, cancel, emit):
    lock = ScanLock('シグナル解析からのサーバー一覧更新')
    if not lock.acquire():
        raise RuntimeError(f'ほかのサーバー調査が実行中です。終了後に再実行してください。\n{lock.holder()}')
    results = []
    try:
        for source in sources:
            if cancel.is_set():
                break
            emit('progress', f"{source['server']} {source['root']} を調査中")
            try:
                count = rack_store.scan_group(source, path, cancel,
                    lambda event, value: emit(event, value) if event == 'progress' else None)
                results.append(f"{source['root']}: {count}サンプル")
            except catalog.ScanCancelled:
                break
            except Exception as exc:
                results.append(f"{source['root']}: 取得失敗（前回記録を保持）: {exc}")
        emit('done', ('中止しました。取得済みのサンプルは保存しました。\n' if cancel.is_set() else 'サーバー調査が終了しました。\n') + '\n'.join(results))
    finally:
        lock.release()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--group')
    args = parser.parse_args()
    cancel = threading.Event()

    def read_cancel():
        for line in sys.stdin:
            if line.strip() == 'cancel':
                cancel.set()
    threading.Thread(target=read_cancel, daemon=True).start()

    def emit(event, message):
        print(json.dumps(dict(event=event, message=message), ensure_ascii=False), flush=True)
    try:
        refresh(args.db, targets(args.db, args.group), cancel, emit)
    except Exception as exc:
        emit('error', str(exc))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
