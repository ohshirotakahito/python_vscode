"""測定機（user 端末）の実験ファイルDB が読む閲覧用DB。開発PCの publish.py が毎時書き出す。

    inventory_view.sqlite3   保存済みDBのうち小さい表（sources・updates・rack_*）。files・folders は空の表だけ
                             （数百万行あり、同期で配るには重いため）
    inventory_view.view.json ② Source · SQserver の一覧（App.read_inventory の結果。開発PCの表示キャッシュと同じ形）

測定機は D:\\Qstatus（Synology で同期）を読むだけで、サーバーには接続せず、どちらのファイルにも書かない。
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from shared import catalog, machinedb

FILE = 'inventory_view.sqlite3'
LARGE_TABLES = ('files', 'folders')


def view_db():
    """D:\\Qstatus\\database\\inventory_view.sqlite3 (next to machinedb, as the published ledgers)."""
    return machinedb.ROOT.parent / 'database' / FILE


def cache_path(db):
    """The ② list published with the snapshot; the same name the app uses for its display cache."""
    return Path(db).with_suffix('.view.json')


def published_at(db):
    """'2026-11-23 10:05 UTC' from the published list, or '' when it cannot be read."""
    try:
        return json.loads(cache_path(db).read_text(encoding='utf-8')).get('published_at', '')
    except (OSError, ValueError, AttributeError):
        return ''


def export(db, inventory, destination):
    """Write the snapshot and the ② list beside it; each file is replaced whole, so a reader never sees half."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.tmp')
    temporary.unlink(missing_ok=True)
    rows = 0
    with catalog.connect(db, readonly=True) as source, closing(sqlite3.connect(temporary)) as target:
        tables = source.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
                                "AND name NOT LIKE 'sqlite_%'").fetchall()
        for name, sql in tables:
            target.execute(sql)
            if name in LARGE_TABLES:
                continue
            records = source.execute(f'SELECT * FROM "{name}"').fetchall()
            if records:
                marks = ','.join('?' * len(records[0].keys()))
                target.executemany(f'INSERT INTO "{name}" VALUES ({marks})', [tuple(r) for r in records])
                rows += len(records)
        for (sql,) in source.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
            target.execute(sql)
        target.execute(f'PRAGMA user_version={int(source.execute("PRAGMA user_version").fetchone()[0])}')
        target.commit()
    cache = cache_path(destination)
    cache_temporary = cache.with_name(cache.name + '.tmp')
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    cache_temporary.write_text(json.dumps(dict(format=1, search='', inventory=inventory, published_at=stamp),
                                          ensure_ascii=False), encoding='utf-8')
    temporary.replace(destination)
    cache_temporary.replace(cache)
    return rows
