# -*- coding: utf-8 -*-
"""BNAL@<target> の TDMS（LAP Table）を読み込むためのコア部分（Tk非依存）。

- 一覧: 実験ファイルDB（database/inventory.sqlite3 の rack_bnals）を読み取り専用で参照する。
  一覧のためにサーバーへはアクセスしない。
- 読み込み: //server/share/<ex>/<sample>/<sample>_10k_Sample/T/BNAL@<target>/*.tdms
- キャッシュ: 読んだ内容を cache/ に target 単位で保存し、次回はフォルダ一覧を1回取るだけで
  追加・更新されたファイルだけを読み直す（サーバー負荷を軽くするため）。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from nptdms import TdmsFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared import catalog, rack_store  # noqa: E402

PLOTTING_DIR = Path(__file__).resolve().parent
CACHE_DIR = PLOTTING_DIR / 'cache'
DEFAULT_DB = catalog.DEFAULT_DB

TARGET_GROUP = 'LAP Table'
REQUIRED_CHANNELS = ('Relative Averaged Data', 'MS_IDX')
OPTIONAL_CHANNELS = ('Assigned Base', 'Base Time length [ms]', 'L#', 'Direction')
# 塩基ごとの品質（q値）は LCP Table（変化点ごと）から LAP の各行に割り当てる
QUALITY_GROUP = 'LCP Table'
Q_VALUE = 'Q value [x]'
Q_ASCII = 'ASCII_Q'           # Phred+33 の1文字（LCP の 'CP ASCII_Q'）
QUALITY_COLUMNS = (Q_VALUE, Q_ASCII)
CHANNELS = REQUIRED_CHANNELS + OPTIONAL_CHANNELS + QUALITY_COLUMNS
FILE_COLUMN = 'file'
# キャッシュの形式。読む列を増やしたら上げる（古いキャッシュは次回サーバーから読み直す）
CACHE_VERSION = 2


# ============= DB（一覧） =============
def group_label(root):
    """'\\\\qtserver\\backup\\' -> 'QTserver/backup'（登録済みの表記に揃える）"""
    parts = [p for p in re.split(r'[\\/]+', str(root)) if p]
    server, share = (parts + ['', ''])[:2]
    for known_server, known_share in rack_store.ANALYSIS_GROUPS:
        if known_server.casefold() == server.casefold() and known_share.casefold() == share.casefold():
            return rack_store.group_label(known_server, known_share)
    return f'{server}/{share}'


def _group_order(label):
    labels = [rack_store.group_label(*g) for g in rack_store.ANALYSIS_GROUPS]
    return labels.index(label) if label in labels else len(labels)


@dataclass
class Occurrence:
    """1つの共有上の BNAL@<target> フォルダ（DBの1行）"""
    root: str
    group: str
    tdms: int | None
    last_seen: str | None


@dataclass
class TargetEntry:
    """実験 / サンプル / target ごとに、それが存在する共有をまとめたもの"""
    experiment: str
    sample: str
    target: str
    occurrences: list[Occurrence] = field(default_factory=list)

    @property
    def key(self):
        return (self.experiment, self.sample, self.target)

    def default_occurrence(self):
        """TDMS数の多い共有を既定にする（同数なら登録順）"""
        return max(self.occurrences, key=lambda o: (o.tdms or -1, -_group_order(o.group)))

    def occurrence(self, group):
        return next((o for o in self.occurrences if o.group == group), None)


# 修飾配列の target 名は「基本配列名 + _M_番号 / _N_番号 / _F_番号」（例: hsa-miR-3613-5p_M_22）
MODIFIED_SUFFIX = re.compile(r'_[MNF]_\d+$')


def sequence_base(target: str) -> str:
    """target 名から基本配列名を返す（基本配列そのものならそのまま）"""
    return MODIFIED_SUFFIX.sub('', target)


def target_alias(target: str) -> str:
    """サンプル間で同じ target を探すための名前（'hsa-miR-3613-5p_M_22' と 'miR3613-5p_M_22' を同一視）"""
    name = re.sub(r'^[a-z]{3}-(?=mir)', '', target.casefold())
    return re.sub(r'^mir-', 'mir', name)


def load_entries(db_path=DEFAULT_DB) -> list[TargetEntry]:
    """DBに保存された BNAL@ フォルダを (実験, サンプル, target) ごとにまとめて返す"""
    entries: dict[tuple, TargetEntry] = {}
    for row in rack_store.bnal_inventory(db_path):
        key = (row['experiment'], row['sample'], row['target'])
        entry = entries.setdefault(key, TargetEntry(*key))
        entry.occurrences.append(Occurrence(row['root'], group_label(row['root']), row['tdms'], row['last_seen']))
    for entry in entries.values():
        entry.occurrences.sort(key=lambda o: _group_order(o.group))
    return sorted(entries.values(), key=lambda e: (e.experiment.casefold(), e.sample.casefold(), e.target.casefold()))


# ============= パス =============
def bnal_dir(root, experiment, sample, target) -> Path:
    return Path(root) / experiment / sample / f'{sample}_10k_Sample' / 'T' / f'BNAL@{target}'


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', '_', name)


def cache_paths(group, experiment, sample, target, cache_dir=None):
    base = Path(cache_dir or CACHE_DIR) / _safe(group.replace('/', '_')) / _safe(experiment) / _safe(sample)
    stem = _safe(target)
    return base / f'{stem}.parquet', base / f'{stem}.json'


# ============= TDMS =============
def _channels(tdms, name):
    group = next((g for g in tdms.groups() if g.name == name), None)
    return {} if group is None else {ch.name: ch for ch in group.channels()}


def attach_quality(lap: pd.DataFrame, lcp: dict) -> pd.DataFrame:
    """LAP の各行に LCP Table の q値（Q value・ASCII_Q）を付ける。

    LCP は変化点ごとなので、同じ塩基が続く部分（LAP では位置ごとに同じ値の行が並ぶ）は
    読みの向きで先頭の位置の1行しか無い。(L#, MS_IDX) で合わなかった行は、
    同じ読み・同じ塩基・同じ値（小数4桁）の変化点から取る。
    """
    lap = lap.copy()
    lap[Q_VALUE], lap[Q_ASCII] = np.nan, ''
    need = ('L#', 'MS_IDX', 'Assinged BaseS', 'Relative Averaged Data', 'Q value [x]', 'CP ASCII_Q')
    if any(name not in lcp for name in need) or lap['L#'].isna().all():
        return lap
    q = pd.DataFrame({name: lcp[name] for name in need})
    if q.empty:
        return lap
    q['L#'] = pd.to_numeric(q['L#'], errors='coerce')
    q['MS_IDX'] = pd.to_numeric(q['MS_IDX'], errors='coerce')
    q['v'] = pd.to_numeric(q['Relative Averaged Data'], errors='coerce').round(4)
    q['base'] = q['Assinged BaseS'].astype(str).str.strip()
    q[Q_VALUE] = pd.to_numeric(q['Q value [x]'], errors='coerce')
    q[Q_ASCII] = q['CP ASCII_Q'].astype(str)
    key = pd.DataFrame({'L#': lap['L#'], 'MS_IDX': lap['MS_IDX'],
                        'base': lap['Assigned Base'].astype(str).str.strip(),
                        'v': lap['Relative Averaged Data'].round(4)})
    by_pos = key.merge(q.drop_duplicates(['L#', 'MS_IDX'])[['L#', 'MS_IDX', Q_VALUE, Q_ASCII]],
                       on=['L#', 'MS_IDX'], how='left')
    by_val = key.merge(q.drop_duplicates(['L#', 'base', 'v'])[['L#', 'base', 'v', Q_VALUE, Q_ASCII]],
                       on=['L#', 'base', 'v'], how='left')
    found = by_pos[Q_VALUE].notna()
    for name in QUALITY_COLUMNS:
        lap[name] = by_pos[name].where(found, by_val[name]).to_numpy()
    # それでも無い行は、同じ読みで隣に並ぶ同じ塩基の行（同じ変化点の続き）から取る
    for step in (1, -1):
        missing = lap[Q_VALUE].isna()
        if not missing.any():
            break
        same = (key['L#'] == key['L#'].shift(step)) & (key['base'] == key['base'].shift(step)) & (key['base'] != '')
        for name in QUALITY_COLUMNS:
            lap.loc[missing & same, name] = lap[name].shift(step)[missing & same]
    lap[Q_ASCII] = lap[Q_ASCII].fillna('').astype(str)
    return lap


def read_lap_table(path) -> pd.DataFrame | None:
    """1ファイルの LAP Table を DataFrame にする（LCP Table の q値付き）。必須チャンネルが無ければ None。

    サーバー上のファイルを直接ストリーミングで開くと細かい読み取りが大量に発生して非常に遅い
    （40MBで約10秒）ため、ファイル全体を1回で読み込んでからメモリ上で必要なチャンネルだけ取り出す。
    """
    with TdmsFile.open(io.BytesIO(Path(path).read_bytes())) as tdms:
        channels = _channels(tdms, TARGET_GROUP)
        if any(name not in channels for name in REQUIRED_CHANNELS):
            return None
        lap_channels = REQUIRED_CHANNELS + OPTIONAL_CHANNELS
        data = {name: channels[name][:] for name in lap_channels if name in channels}
        lcp = {name: ch[:] for name, ch in _channels(tdms, QUALITY_GROUP).items()}
    n = max(len(v) for v in data.values())
    columns = {}
    for name in lap_channels:
        values = data.get(name)
        if values is None:
            columns[name] = np.full(n, np.nan)
        elif len(values) < n:
            # チャンネル間で長さが違う場合は欠損で埋める
            padded = pd.Series(values, dtype=object if values.dtype.kind in 'OUS' else float)
            columns[name] = padded.reindex(range(n)).to_numpy()
        else:
            columns[name] = values
    df = pd.DataFrame(columns)
    for name in ('Relative Averaged Data', 'MS_IDX', 'Base Time length [ms]', 'L#'):
        df[name] = pd.to_numeric(df[name], errors='coerce')
    for name in ('Assigned Base', 'Direction'):
        df[name] = df[name].astype(str).replace({'nan': ''})
    return attach_quality(df, lcp)


def list_tdms(folder) -> dict[str, tuple[int, float]]:
    """フォルダ直下の .tdms を {名前: (サイズ, 更新時刻)} で返す（サーバーへの1回の一覧取得）"""
    with os.scandir(folder) as it:
        return {e.name: (e.stat().st_size, e.stat().st_mtime) for e in it
                if e.is_file() and e.name.lower().endswith('.tdms')}


# ============= 読み込み（キャッシュ付き） =============
@dataclass
class LoadResult:
    entry: TargetEntry
    occurrence: Occurrence
    data: pd.DataFrame
    files_total: int = 0
    files_read: int = 0           # 今回サーバーから読んだ数
    files_cached: int = 0         # キャッシュから使った数
    skipped: list[str] = field(default_factory=list)   # LAP Table / 必須チャンネルなし
    errors: list[str] = field(default_factory=list)
    offline: bool = False         # サーバーを見ずにキャッシュだけで読んだ
    old_cache: bool = False       # 古い形式のキャッシュ（q値なし）をそのまま使った

    @property
    def label(self):
        return f'{self.occurrence.group} / {self.entry.experiment} / {self.entry.sample} / {self.entry.target}'


def _load_cache(data_path, meta_path):
    """(data, files, 現行形式か)。古い形式もキャッシュだけで読む時のために返す（無い列は空で足す）"""
    if not (data_path.exists() and meta_path.exists()):
        return pd.DataFrame(columns=[FILE_COLUMN, *CHANNELS]), {}, True
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    data = pd.read_parquet(data_path)
    for name in CHANNELS:
        if name not in data:
            data[name] = '' if name in (Q_ASCII, 'Direction') else np.nan
    return data, meta.get('files', {}), meta.get('version', 1) == CACHE_VERSION


def _save_cache(data_path, meta_path, data, files, occurrence):
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data.to_parquet(data_path, index=False)
    meta = dict(version=CACHE_VERSION, root=occurrence.root, group=occurrence.group, files=files)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')


def load_target(entry: TargetEntry, occurrence: Occurrence, offline=False, cache_dir=None,
                progress=None, cancel=None) -> LoadResult:
    """1つの BNAL@<target> を読み込む。

    progress(done, total, name): ファイルごとの進捗通知（任意）
    cancel(): True を返したら途中で止める（それまでの読み込み分はキャッシュに残す）
    """
    data_path, meta_path = cache_paths(occurrence.group, *entry.key, cache_dir=cache_dir)
    cached, cached_files, current_format = _load_cache(data_path, meta_path)
    result = LoadResult(entry, occurrence, cached, offline=offline, old_cache=not current_format)

    if offline:
        result.files_total = result.files_cached = len(cached_files)
        return result
    if not current_format:     # 古い形式（q値なし）は全ファイルを読み直す
        cached, cached_files = cached.iloc[0:0], {}
        result.old_cache = False

    current = list_tdms(bnal_dir(occurrence.root, *entry.key))
    result.files_total = len(current)
    # 変化なし（同名・同サイズ・同更新時刻）はキャッシュを使い、それ以外を読み直す
    keep = {name for name, stat in current.items() if cached_files.get(name) == list(stat)}
    todo = sorted(set(current) - keep)
    frames = [cached[cached[FILE_COLUMN].isin(keep)]] if keep else []
    files = {name: list(current[name]) for name in keep}
    result.files_cached = len(keep)

    folder = bnal_dir(occurrence.root, *entry.key)
    for i, name in enumerate(todo):
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(i, len(todo), name)
        try:
            df = read_lap_table(folder / name)
        except Exception as exc:  # 1ファイルの失敗で target 全体を止めない
            result.errors.append(f'{name}: {exc}')
            continue
        files[name] = list(current[name])
        result.files_read += 1
        if df is None:
            result.skipped.append(name)
            continue
        df.insert(0, FILE_COLUMN, name)
        frames.append(df)
    if progress is not None:
        progress(len(todo), len(todo), '')

    frames = [f for f in frames if not f.empty]
    result.data = (pd.concat(frames, ignore_index=True) if frames
                   else pd.DataFrame(columns=[FILE_COLUMN, *CHANNELS]))
    if todo or set(cached_files) != set(files):
        _save_cache(data_path, meta_path, result.data, files, occurrence)
    return result


def cache_info(entry: TargetEntry, occurrence: Occurrence, cache_dir=None) -> tuple[int, bool] | None:
    """(キャッシュ済みファイル数, 現行形式か)。未キャッシュなら None。サーバーにはアクセスしない。"""
    _, meta_path = cache_paths(occurrence.group, *entry.key, cache_dir=cache_dir)
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return len(meta.get('files', {})), meta.get('version', 1) == CACHE_VERSION


def cache_state(entry: TargetEntry, occurrence: Occurrence, cache_dir=None) -> int | None:
    """キャッシュ済みファイル数（未キャッシュなら None）。サーバーにはアクセスしない。"""
    info = cache_info(entry, occurrence, cache_dir)
    return None if info is None else info[0]
