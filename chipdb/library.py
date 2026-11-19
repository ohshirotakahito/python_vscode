"""チップ履歴CSVの索引: 集計（summary）と使用日（search）を、変わったファイルだけ読み直して持つ。

1 チップ = 1 CSV（ChipDB/DB）。更新のたびに全ファイルの更新日時・サイズだけを調べ（3501 件で約 0.06 秒）、
前回と同じファイルは控えの結果を使い、変わったファイル・新しいファイルだけを読む。消えたファイルは外す。
1 回の読み込みで、全チップ比較の集計と使用日検索の日付の両方を作る。

控えは PC ごと（%LOCALAPPDATA%/QuantumWorkbench/chip_atlas_cache/）。共有フォルダには書かない（同期の競合を避ける）。
集計の決まり（summary.py・search.py）が変わると控えの版が変わり、全件を読み直す。
同期の仕組みによっては中身が変わっても日時とサイズが同じことがあり得るので、全件読み直し（force）も用意する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
import os
import threading
from pathlib import Path
from uuid import uuid4

from chipdb.search import usage_dates
from chipdb.summary import summarize

FORMAT = 2            # 2: 操作ごとの回数（kinds）を追加（2026-11-18）


def rules_version():
    """集計・使用日の決まりの版: summary.py と search.py の中身の指紋。変われば控えを使わない。"""
    digest = hashlib.sha1(str(FORMAT).encode())
    for name in ('summary.py', 'search.py'):
        digest.update((Path(__file__).with_name(name)).read_bytes())
    return digest.hexdigest()[:12]


def cache_path(db):
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.config')) / 'QuantumWorkbench' / 'chip_atlas_cache'
    key = hashlib.sha1(os.path.normcase(str(Path(db).resolve())).encode('utf-8')).hexdigest()[:16]
    return base / f'{key}.json'


def kind_counts(rows):
    """{操作: 回数}（FC・MA・TA・AC・CAL・GAP・EXSV など、Experiment 行の操作名の # より前）。"""
    counts = {}
    for row in rows:
        if len(row) > 6 and row[0] == 'Experiment':
            kind = row[5].split('#')[0]
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def read_chip(path):
    """1 つのCSVを読み、(集計の行, 使用日の一覧, エラー, 操作ごとの回数) を返す。集計できないときは record が None。"""
    rows = [line.split(',') for line in Path(path).read_bytes().decode('cp932').splitlines()]
    dates = sorted(day.isoformat() for day in usage_dates(rows))
    kinds = kind_counts(rows)
    try:
        record = summarize(rows)
    except ValueError as exc:
        return None, dates, str(exc), kinds
    return record, dates, '', kinds


@dataclass
class Entry:
    size: int
    mtime_ns: int
    record: dict | None
    dates: list
    error: str = ''
    kinds: dict = field(default_factory=dict)    # {操作: 回数}

    @property
    def usage_dates(self):
        return {date.fromisoformat(d) for d in self.dates}


@dataclass
class UpdateResult:
    reused: int = 0
    read: int = 0
    removed: int = 0
    errors: list = field(default_factory=list)       # [(path, reason)]

    @property
    def text(self):
        return f'読み直し {self.read} · 変更なし {self.reused} · 削除 {self.removed} · エラー {len(self.errors)}'


class Index:
    """{CSVのパス: Entry}。update() で差分だけ読み直す。"""

    def __init__(self, db):
        self.db = Path(db)
        self.entries = {}
        self.version = rules_version()
        self._lock = threading.Lock()      # 更新を 1 つずつ（前の更新が止まるのを待つ）
        self._load()

    def _load(self):
        try:
            data = json.loads(cache_path(self.db).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if data.get('version') != self.version:
            return
        for name, item in data.get('entries', {}).items():
            try:
                self.entries[self.db / name] = Entry(int(item['size']), int(item['mtime_ns']), item.get('record'),
                                                     list(item.get('dates', [])), item.get('error', ''),
                                                     dict(item.get('kinds', {})))
            except (KeyError, TypeError, ValueError):
                continue

    def save(self):
        target = cache_path(self.db)
        data = dict(version=self.version, db=str(self.db), entries={
            path.name: dict(size=e.size, mtime_ns=e.mtime_ns, record=e.record, dates=e.dates, error=e.error, kinds=e.kinds)
            for path, e in self.entries.items()})
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f'.{target.stem}_{uuid4().hex}.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, target)
        except OSError:
            pass                       # 控えが書けなくても、次回全件を読むだけ

    def refresh(self, path):
        """1 ファイルを読み直す（アプリ内で編集・保存した直後など）。読めなければ索引から外してエラーを返す。"""
        path = Path(path)
        try:
            stat = path.stat()
            record, dates, error, kinds = read_chip(path)
        except (OSError, UnicodeError) as exc:
            self.entries.pop(path, None)
            return str(exc)
        self.entries[path] = Entry(stat.st_size, stat.st_mtime_ns, record, dates, error, kinds)
        return error

    def update(self, paths, force=False, progress=None, cancelled=None):
        """paths（今あるCSV）に合わせて索引を更新する。progress(i, n) を変わったファイルを読むたびに呼ぶ。
        cancelled() が真になると途中でやめて None を返す（読み終えた分は残り、次の更新で使われる）。"""
        with self._lock:
            return self._update(paths, force, progress, cancelled)

    def _update(self, paths, force, progress, cancelled):
        result = UpdateResult()
        paths = [Path(p) for p in paths]
        wanted = set(paths)
        for path in [p for p in self.entries if p not in wanted]:
            del self.entries[path]
            result.removed += 1
        stale = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError as exc:
                self.entries.pop(path, None)
                result.errors.append((str(path), str(exc)))
                continue
            entry = self.entries.get(path)
            if not force and entry is not None and entry.size == stat.st_size and entry.mtime_ns == stat.st_mtime_ns:
                result.reused += 1
            else:
                stale.append(path)
        for i, path in enumerate(stale):
            if cancelled and cancelled():
                self.save()
                return None
            error = self.refresh(path)
            result.read += 1
            if path not in self.entries:
                result.errors.append((str(path), error))
            if progress and (i % 50 == 0 or i == len(stale) - 1):
                progress(i + 1, len(stale))
        result.errors += [(str(p), e.error) for p, e in self.entries.items() if e.error]
        self.save()
        return result
