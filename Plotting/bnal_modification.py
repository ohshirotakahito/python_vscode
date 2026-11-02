# -*- coding: utf-8 -*-
"""修飾率の解析（Tk非依存）。

BNAL@ の target は基本配列（例: hsa-miR-3613-5p）と、修飾配列（基本配列 + _M_/_N_/_F_ + 位置、
例: hsa-miR-3613-5p_M_22）に分かれていて、それぞれ別の解析で読みが割り当てられている。
修飾位置（MS_IDX = 名前の番号）では未修飾の塩基（C など）と修飾塩基（M など）の値の範囲が分かれているので、
その位置に割り当てられた行の数を数えて比べる。

  修飾率（補正なし） r = n_修飾 / (n_未修飾 + 同じ位置にある修飾配列すべての n)

0% と 100% の決め方（METHODS）:
  raw  : 補正なし。0% = 修飾配列に1つも割り当てられない、100% = すべて修飾配列に割り当てられる
  zero : 0% 対照サンプル（未修飾の合成配列など）の r0 を引く       (r - r0) / (1 - r0)
  both : 0% 対照の r0 と 100% 対照の r1 の間で線形に換算する     (r - r0) / (r1 - r0)
補正後の信頼区間は、対象サンプルの区間を同じ式で換算したもの（対照側のばらつきは含めない）。

信頼区間（CI_METHODS）:
  wilson : 行を独立とみなした二項分布の Wilson 区間
  chunk  : TDMS ファイル（#番号＝同じ測定データの区切り）単位でリサンプリングするブートストラップ。
           ファイル間のばらつきも含むので、通常は wilson より広い
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

VALUE = 'Relative Averaged Data'
POSITION = 'MS_IDX'
BASE = 'Assigned Base'
Q_VALUE = 'Q value [x]'

# 修飾配列の target 名（bnal_source.MODIFIED_SUFFIX と同じ形）。位置は MS_IDX と同じ番号
SITE = re.compile(r'^(?P<sequence>.+)_(?P<kind>[MNF])_(?P<position>\d+)$')
# 同じ測定データの区切り（target が違ってもファイル名の #番号が同じなら同じ区間）
CHUNK = re.compile(r'#(\d+)\.tdms$', re.IGNORECASE)

METHODS = {
    '補正なし（0% = 修飾の割り当てなし、100% = すべて修飾）': 'raw',
    '0% 対照で補正（対照の率を 0% とする）': 'zero',
    '0%・100% 対照で補正（2つの対照の間で換算）': 'both',
}
CI_METHODS = {
    'ファイル単位ブートストラップ（ばらつき込み）': 'chunk',
    'Wilson（行を独立とみなす）': 'wilson',
}
N_BOOT = 1000

DATASET_COLORS = ('#2f6fd0', '#e0662f', '#2a9d5c', '#b23a8f', '#8a6d1f', '#5b5b5b', '#17a2b8', '#c0392b')
UNMODIFIED_COLOR = '#7f8c8d'


@dataclass
class Dataset:
    """読み込み済みの1 target（LoadResult から必要なものだけ）"""
    experiment: str
    sample: str
    target: str
    data: pd.DataFrame
    group: str = ''

    @property
    def sample_key(self):
        return (self.experiment, self.sample)


def parse_site(target):
    """'hsa-miR-3613-5p_M_22' -> ('hsa-miR-3613-5p', 'M', 22)。修飾配列でなければ None"""
    m = SITE.match(target)
    return (m['sequence'], m['kind'], int(m['position'])) if m else None


def chunk_of(filename):
    m = CHUNK.search(str(filename))
    return f'#{int(m.group(1)):03d}' if m else str(filename)


def sample_label(key):
    return f'{key[0]} / {key[1]}'


# ============= 数える =============
def site_rows(df: pd.DataFrame, position, min_q=0.0):
    """(その位置の行, q値で絞ったか)。値の無い行は除く。

    q値の列が無い・すべて空（古いキャッシュ）のときは絞らない。
    """
    if df.empty:
        return df, False
    pos = pd.to_numeric(df[POSITION], errors='coerce')
    val = pd.to_numeric(df[VALUE], errors='coerce')
    rows = df[(pos == position) & val.notna()]
    if min_q <= 0 or Q_VALUE not in rows or rows[Q_VALUE].isna().all():
        return rows, False
    return rows[pd.to_numeric(rows[Q_VALUE], errors='coerce') >= min_q], True


def _major(series):
    s = series.dropna().astype(str).str.strip()
    s = s[s != '']
    return s.value_counts().index[0] if len(s) else ''


@dataclass
class SiteCounts:
    """1サンプルの1修飾配列について、修飾位置での行数"""
    experiment: str
    sample: str
    sequence: str
    kind: str
    position: int
    target: str
    ref_base: str = ''            # 未修飾配列でその位置に割り当てられた塩基
    mod_base: str = ''            # 修飾配列でその位置に割り当てられた塩基
    n_mod: int = 0
    n_unmod: int = 0
    n_other: int = 0              # 同じ位置の、ほかの修飾配列の行数
    chunks: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=['mod', 'total']))
    values: dict = field(default_factory=dict)    # 図用: 名前 -> その位置の値（未修飾・修飾ごと）
    has_unmod: bool = True
    q_filtered: bool = False
    notes: list = field(default_factory=list)

    @property
    def sample_key(self):
        return (self.experiment, self.sample)

    @property
    def site_key(self):
        """サンプルをまたいで同じ修飾を対応づけるキー"""
        return (self.sequence, self.kind, self.position)

    @property
    def total(self):
        return self.n_mod + self.n_unmod + self.n_other

    @property
    def site_label(self):
        return f'{self.ref_base or "?"}{self.position}→{self.mod_base or self.kind}'


def _pick(datasets):
    """同じ target が複数の共有から読まれていたら行数の多いほうを使う"""
    return max(datasets, key=lambda d: len(d.data))


def collect_sites(datasets, min_q=0.0) -> list[SiteCounts]:
    """読み込み済みデータから、サンプル × 修飾配列ごとの行数をまとめる"""
    by_target: dict[tuple, list[Dataset]] = {}
    for d in datasets:
        by_target.setdefault((d.experiment, d.sample, d.target), []).append(d)
    chosen = {key: _pick(items) for key, items in by_target.items()}

    # (実験, サンプル, 基本配列, 位置) -> その位置の修飾配列
    variants: dict[tuple, list[tuple[str, Dataset]]] = {}
    for (experiment, sample, target), d in chosen.items():
        site = parse_site(target)
        if site:
            sequence, kind, position = site
            variants.setdefault((experiment, sample, sequence, position), []).append((kind, d))

    sites = []
    for (experiment, sample, sequence, position), items in variants.items():
        unmod = chosen.get((experiment, sample, sequence))
        counted = []      # (kind, dataset, rows)
        q_filtered = False
        for kind, d in sorted(items, key=lambda kd: kd[1].target):
            rows, used = site_rows(d.data, position, min_q)
            counted.append((kind, d, rows))
            q_filtered |= used
        unmod_rows, used = site_rows(unmod.data, position, min_q) if unmod is not None else (pd.DataFrame(), False)
        q_filtered |= used

        def per_chunk(rows):
            if rows.empty or 'file' not in rows:
                return pd.Series(dtype=float)
            return rows['file'].map(chunk_of).value_counts()

        all_chunks = [per_chunk(rows) for _, _, rows in counted]
        parts = [c for c in (per_chunk(unmod_rows), *all_chunks) if len(c)]
        total_chunks = pd.concat(parts).groupby(level=0).sum() if parts else pd.Series(dtype=int)
        for i, (kind, d, rows) in enumerate(counted):
            others = sum(len(r) for j, (_, _, r) in enumerate(counted) if j != i)
            chunks = pd.DataFrame({'mod': all_chunks[i], 'total': total_chunks}).fillna(0).astype(int)
            site = SiteCounts(experiment, sample, sequence, kind, position, d.target,
                              ref_base=_major(unmod_rows[BASE]) if BASE in unmod_rows else '',
                              mod_base=_major(rows[BASE]) if BASE in rows else '',
                              n_mod=len(rows), n_unmod=len(unmod_rows), n_other=others, chunks=chunks,
                              has_unmod=unmod is not None, q_filtered=q_filtered)
            site.values = {sequence: pd.to_numeric(unmod_rows.get(VALUE, pd.Series(dtype=float))).to_numpy()}
            for _, other, r in counted:
                site.values[other.target] = pd.to_numeric(r[VALUE]).to_numpy()
            if unmod is None:
                site.notes.append('未修飾配列が未読込')
            if others:
                site.notes.append(f'同じ位置の修飾配列 {len(counted) - 1} 件を分母に含む')
            if min_q > 0 and not q_filtered:
                site.notes.append('q値なし（Q で絞っていない）')
            sites.append(site)
    return sorted(sites, key=lambda s: (s.experiment.casefold(), s.sample.casefold(), s.sequence, s.position, s.kind))


# ============= 率と区間 =============
def wilson(k, n, z=1.96):
    if n <= 0:
        return math.nan, math.nan
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(center - half, 0.0), min(center + half, 1.0)


def chunk_bootstrap(chunks: pd.DataFrame, n_boot=N_BOOT, seed=0):
    """ファイル（#番号）単位で復元抽出したときの率の 2.5% / 97.5% 点"""
    chunks = chunks[chunks['total'] > 0]
    if len(chunks) < 2:
        return math.nan, math.nan
    mod, total = chunks['mod'].to_numpy(float), chunks['total'].to_numpy(float)
    idx = np.random.default_rng(seed).integers(0, len(chunks), size=(n_boot, len(chunks)))
    rates = mod[idx].sum(axis=1) / total[idx].sum(axis=1)
    lo, hi = np.percentile(rates, [2.5, 97.5])
    return float(lo), float(hi)


def raw_rate(site: SiteCounts, ci='chunk', n_boot=N_BOOT):
    """(率, 下限, 上限)。未修飾配列が無い・行が無いときは NaN"""
    if not site.has_unmod or site.total == 0:
        return math.nan, math.nan, math.nan
    rate = site.n_mod / site.total
    lo, hi = chunk_bootstrap(site.chunks, n_boot) if ci == 'chunk' else wilson(site.n_mod, site.total)
    return rate, lo, hi


def rescale(x, r0=0.0, r1=1.0):
    """0% = r0、100% = r1 として x を換算（r1 <= r0 なら NaN）"""
    span = r1 - r0
    return (x - r0) / span if span > 0 and not math.isnan(x) else math.nan


TABLE_COLUMNS = ['experiment', 'sample', 'sequence', 'site', 'position', 'kind', 'target',
                 'n_mod', 'n_unmod', 'n_other', 'total', 'chunks',
                 'raw_rate', 'raw_low', 'raw_high', 'r0', 'r1', 'rate', 'low', 'high', 'note']


def rate_table(sites: list[SiteCounts], method='raw', zero=None, full=None, ci='chunk',
               n_boot=N_BOOT) -> pd.DataFrame:
    """サンプル × 修飾ごとの率の表（率は 0〜1。補正後は範囲外もそのまま残す）

    zero / full: 0% / 100% 対照のサンプル (実験, サンプル)。method に応じて使う
    """
    raws = {id(s): raw_rate(s, ci, n_boot) for s in sites}
    controls = {}
    for role, key in (('zero', zero if method in ('zero', 'both') else None),
                      ('full', full if method == 'both' else None)):
        controls[role] = {s.site_key: raws[id(s)][0] for s in sites if key is not None and s.sample_key == key}

    rows = []
    for s in sites:
        rate, lo, hi = raws[id(s)]
        notes = list(s.notes)
        r0, r1 = 0.0, 1.0
        if method in ('zero', 'both'):
            r0 = controls['zero'].get(s.site_key, math.nan)
            if math.isnan(r0):
                notes.append('0%対照にこの修飾なし')
        if method == 'both':
            r1 = controls['full'].get(s.site_key, math.nan)
            if math.isnan(r1):
                notes.append('100%対照にこの修飾なし')
            elif not math.isnan(r0) and r1 <= r0:
                notes.append('100%対照の率が0%対照以下')
        if math.isnan(r0) or math.isnan(r1):
            out = (math.nan, math.nan, math.nan)
        else:
            out = tuple(rescale(x, r0, r1) for x in (rate, lo, hi))
        rows.append(dict(experiment=s.experiment, sample=s.sample, sequence=s.sequence, site=s.site_label,
                         position=s.position, kind=s.kind, target=s.target, n_mod=s.n_mod, n_unmod=s.n_unmod,
                         n_other=s.n_other, total=s.total, chunks=int((s.chunks['total'] > 0).sum()),
                         raw_rate=rate, raw_low=lo, raw_high=hi, r0=r0, r1=r1,
                         rate=out[0], low=out[1], high=out[2], note='、'.join(notes)))
    return pd.DataFrame(rows, columns=TABLE_COLUMNS)


# ============= 図 =============
GROUP_METHODS = {'サンプル修飾率の平均（各サンプルを同じ重み）': 'mean',
                 '行数を合算（修飾行の合計 / 全行の合計）': 'pooled'}


def validate_groups(groups):
    """Membership uses (experiment, sample), so identical sample names stay distinct."""
    result, seen = {}, set()
    for i, (name, members) in enumerate(groups):
        name = str(name).strip() or f'Group {chr(65 + i) if i < 26 else i + 1}'
        if not isinstance(members, (list, tuple)) or any(
                not isinstance(key, (list, tuple)) or len(key) != 2 or
                any(not isinstance(value, str) for value in key) for key in members):
            raise ValueError('所属には実験名・サンプル名の組を指定してください。')
        members = [tuple(key) for key in members]
        if name in result or not members or any(len(key) != 2 for key in members):
            raise ValueError('グループ名の重複・空の所属・不正なサンプル指定を確認してください。')
        if len(set(members)) != len(members) or seen.intersection(members):
            raise ValueError('同じサンプルを複数のグループに登録できません。')
        result[name] = members
        seen.update(members)
    if not result:
        raise ValueError('グループを1つ以上登録してください。')
    return result


def group_rate_table(sites, groups, method='raw', zero=None, full=None, aggregation='mean',
                     n_boot=N_BOOT, seed=0):
    """Group estimates with source-sample bootstrap; fixed controls, incomplete pairs excluded.

    Returns (table, pooled SiteCounts for the value-distribution detail).
    """
    groups = validate_groups(groups.items())
    if aggregation not in ('mean', 'pooled'):
        raise ValueError('不明なグループ集計方法です。')
    if n_boot < 1:
        raise ValueError('ブートストラップの回数は1以上にしてください。')
    individual = rate_table(sites, method, zero=zero, full=full, ci='wilson')
    index = {(s.sample_key, s.site_key): s for s in sites}
    rows, details = [], []
    for name, members in groups.items():
        keys = sorted({s.site_key for s in sites})
        for key in keys:
            found = [index[(member, key)] for member in members if (member, key) in index]
            valid = [s for s in found if s.has_unmod and s.total > 0]
            template = next(s for s in sites if s.site_key == key)
            selected = individual[(individual.sequence == key[0]) & (individual.kind == key[1]) &
                                  (individual.position == key[2])]
            valid_keys = {v.sample_key for v in valid}
            selected = selected[[ (e, s) in valid_keys
                                 for e, s in zip(selected.experiment, selected['sample']) ]]
            notes = list(dict.fromkeys(note for s in valid for note in s.notes))
            if len(valid) != len(members):
                notes.append(f'有効サンプル {len(valid)}/{len(members)}（未読込・未修飾なし・行なしを除外）')
            if len(valid) < 2:
                notes.append('有効サンプル2未満：サンプル単位の95%区間を算出不可')
            r0 = float(selected.r0.iloc[0]) if len(selected) else math.nan
            r1 = float(selected.r1.iloc[0]) if len(selected) else math.nan
            mod_counts = np.array([s.n_mod for s in valid], dtype=float)
            totals = np.array([s.total for s in valid], dtype=float)
            raw = (float(np.mean(mod_counts / totals)) if aggregation == 'mean' else
                   float(mod_counts.sum() / totals.sum())) if valid else math.nan
            lo = hi = math.nan
            if len(valid) >= 2:
                # Batch resampling bounds memory even for large groups.
                rng = np.random.default_rng(seed)
                estimates = []
                for start in range(0, n_boot, 100):
                    draw = rng.integers(len(valid), size=(min(100, n_boot-start), len(valid)))
                    estimates.extend((mod_counts[draw] / totals[draw]).mean(axis=1) if aggregation == 'mean'
                                     else mod_counts[draw].sum(axis=1) / totals[draw].sum(axis=1))
                lo, hi = map(float, np.percentile(estimates, [2.5, 97.5]))
            if not np.isfinite(r0) or not np.isfinite(r1) or r1 <= r0:
                notes.append('対照なし・対照の範囲不正：補正率を算出不可')
            pooled = SiteCounts('グループ', name, *key, template.target,
                                ref_base=template.ref_base, mod_base=template.mod_base,
                                n_mod=sum(s.n_mod for s in valid), n_unmod=sum(s.n_unmod for s in valid),
                                n_other=sum(s.n_other for s in valid), has_unmod=bool(valid))
            parts = [s.chunks.assign(source=sample_label(s.sample_key)).reset_index() for s in valid]
            pooled.chunks = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=['mod', 'total'])
            for target in {target for s in valid for target in s.values}:
                pooled.values[target] = np.concatenate([s.values[target] for s in valid if target in s.values])
            details.append(pooled)
            row = dict(experiment='グループ', sample=name, sequence=key[0], kind=key[1], position=key[2],
                       target=template.target, site=template.site_label, n_mod=pooled.n_mod,
                       n_unmod=pooled.n_unmod, n_other=pooled.n_other, total=pooled.total,
                       chunks=sum(int((s.chunks.total > 0).sum()) for s in valid),
                       raw_rate=raw, raw_low=lo, raw_high=hi, r0=r0, r1=r1,
                       rate=rescale(raw, r0, r1), low=rescale(lo, r0, r1), high=rescale(hi, r0, r1),
                       note='、'.join(notes), group_name=name,
                       members='; '.join(sample_label(member) for member in members),
                       valid_members='; '.join(sample_label(s.sample_key) for s in valid),
                       n_samples=len(valid), n_members=len(members), aggregation=aggregation,
                       interval_method='sample_bootstrap', n_boot=n_boot,
                       control_uncertainty='not_included')
            rows.append(row)
    extra = ['group_name', 'members', 'valid_members', 'n_samples', 'n_members', 'aggregation',
             'interval_method', 'n_boot', 'control_uncertainty']
    return pd.DataFrame(rows, columns=TABLE_COLUMNS + extra), details


def plot_rates(table: pd.DataFrame, figure, method='raw', detail: SiteCounts | None = None):
    """上: 修飾ごとに、サンプルを色分けした棒（エラーバー＝信頼区間）。
    下: detail（表で選んだ行）の修飾位置での値の分布（未修飾配列と修飾配列を重ねる）"""
    figure.clear()
    if detail is not None:
        grid = figure.add_gridspec(2, 1, height_ratios=(3, 2), hspace=0.45)
        ax, ax2 = figure.add_subplot(grid[0]), figure.add_subplot(grid[1])
    else:
        ax, ax2 = figure.add_subplot(111), None

    sites = list(dict.fromkeys(zip(table['sequence'], table['site'], table['target'])))
    samples = list(dict.fromkeys(zip(table['experiment'], table['sample'])))
    width = 0.8 / max(len(samples), 1)
    for j, key in enumerate(samples):
        part = table[(table['experiment'] == key[0]) & (table['sample'] == key[1])]
        xs, ys, err_lo, err_hi, labels = [], [], [], [], []
        for _, row in part.iterrows():
            i = sites.index((row['sequence'], row['site'], row['target']))
            y = row['rate'] * 100
            xs.append(i - 0.4 + width * (j + 0.5))
            ys.append(y)
            err_lo.append(max(y - row['low'] * 100, 0) if np.isfinite(row['low']) else 0)
            err_hi.append(max(row['high'] * 100 - y, 0) if np.isfinite(row['high']) else 0)
            labels.append(f'{row["n_mod"]}/{row["total"]}')
        color = DATASET_COLORS[j % len(DATASET_COLORS)]
        bars = ax.bar(xs, np.nan_to_num(ys), width * 0.92, color=color, label=sample_label(key),
                      yerr=[err_lo, err_hi], capsize=3, error_kw=dict(ecolor='#333333', lw=0.9))
        if len(samples) * len(sites) <= 24:
            for bar, text, y in zip(bars, labels, ys):
                ax.annotate('n/a' if not np.isfinite(y) else text, (bar.get_x() + bar.get_width() / 2, 0),
                            xytext=(0, 3), textcoords='offset points', ha='center', va='bottom',
                            fontsize=7, color='white' if np.isfinite(y) and y > 8 else '#555555', rotation=90)
    ax.set_xticks(range(len(sites)))
    ax.set_xticklabels([f'{seq}\n{site}' for seq, site, _ in sites], fontsize=8)
    ax.axhline(0, color='#555555', lw=0.8)
    if method == 'both':
        ax.axhline(100, color='#555555', lw=0.8, ls='--')
    ax.set_ylabel('修飾率 (%)')
    title = {'raw': '修飾率（補正なし）', 'zero': '修飾率（0% 対照で補正）', 'both': '修飾率（0%・100% 対照で換算）'}
    grouped = 'aggregation' in table.columns
    description = ('サンプル率の平均' if table['aggregation'].iloc[0] == 'mean' else '行数を合算') if grouped else ''
    suffix = f'グループ別・{description} / 95%区間: サンプル単位（対照の不確実性は含まず）' if grouped else ''
    ax.set_title(f'{title[method]}　棒の中: 修飾 / 全行数（合計・補正前）\n{suffix}', fontsize=10)
    if samples:
        ax.legend(fontsize=8, loc='upper left', bbox_to_anchor=(1.0, 1.0), frameon=False)
    ax.grid(axis='y', alpha=0.3)

    if ax2 is not None:
        _plot_site_values(ax2, detail)
    figure.subplots_adjust(left=0.07, right=0.82, top=0.93, bottom=0.08)
    return figure


def _plot_site_values(ax, site: SiteCounts):
    series = [(name, v) for name, v in site.values.items() if len(v)]
    if not series:
        ax.text(0.5, 0.5, 'この位置に値がありません', ha='center', va='center', transform=ax.transAxes)
        return
    allv = np.concatenate([v for _, v in series])
    edges = np.linspace(np.floor(allv.min() * 20) / 20, np.ceil(allv.max() * 20) / 20 + 0.05, 60)
    for k, (name, v) in enumerate(series):
        color = UNMODIFIED_COLOR if name == site.sequence else DATASET_COLORS[(k + 1) % len(DATASET_COLORS)]
        base = '未修飾' if name == site.sequence else '修飾'
        ax.hist(v, bins=edges, color=color, alpha=0.65, label=f'{base}: {name}（{len(v):,} 行）')
    ax.set_xlabel(f'{VALUE}（MS_IDX {site.position} の値）')
    ax.set_ylabel('行数')
    ax.set_title(f'{site.experiment} / {site.sample}　{site.sequence} {site.site_label} の値の分布', fontsize=10)
    ax.legend(fontsize=8, loc='upper center', frameon=False)
    ax.grid(alpha=0.3)
