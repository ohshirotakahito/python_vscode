# -*- coding: utf-8 -*-
"""BNAL（LAP Table）のアセンブリ結果（Assigned Base の文字列）と q値を可視化する（Tk非依存）。

LAP Table の行の並び:
    MS_IDX 0 (B) → MS_IDX 末端 (B) → 1本の読みの位置が連続して並ぶ（昇順＝順方向、降順＝逆方向）
を繰り返す。読みの番号（L#）があればそれで、無ければ（古いキャッシュ）両端の B を区切りとして
読みに切り分け、各読みが参照配列のどの区間に並んだか（＝アセンブリ結果）を、塩基ごとに色分けした
文字列・パイルアップ図で表示する。

q値は LCP Table の塩基ごとの Q value / ASCII_Q（Phred+33）を bnal_source が LAP の各行に付けたもの。
"""
from __future__ import annotations

import warnings
from collections import Counter
from dataclasses import dataclass

import matplotlib
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch, Rectangle

from bnal_plots import DATASET_COLORS, POSITION, VALUE

BASE = 'Assigned Base'
Q_VALUE = 'Q value [x]'
BOUNDARY = 'B'
FORWARD, REVERSE = '→', '←'

# 塩基の色（図＝白背景用）。A/U(T)/G/C は配列ビューアで一般的な系統、修飾塩基は別系統の色にする
BASE_COLORS = {
    'A': '#2e9e44', 'U': '#d6332a', 'T': '#d6332a', 'G': '#f0a020', 'C': '#2f6fd0',
    'M': '#9b3fc4', 'N': '#e0529c', 'S': '#17a2b8', 'X': '#7a4a1e', 'Z': '#5b5bd6', 'F': '#c2185b',
}
OTHER_COLOR = '#9a9a9a'
GAP_COLOR = '#ffffff'
# 画面のテキスト表示（暗い背景）用。同じ色相で明るめにする
TEXT_COLORS = {
    'A': '#5fd27a', 'U': '#ff6b5e', 'T': '#ff6b5e', 'G': '#ffc24d', 'C': '#6fa8ff',
    'M': '#d08bff', 'N': '#ff8cc6', 'S': '#4fd6e8', 'X': '#d9a066', 'Z': '#9d9dff', 'F': '#ff6fa3',
}
STANDARD_BASES = set('AUTGC')
# 読みの向きは塩基の色と被らないよう無彩色系にする
DIRECTION_COLORS = {FORWARD: '#455a64', REVERSE: '#a7b4bd'}

# q値の表示範囲。塩基の Q は多くが 3〜13 程度（両端の B は 20〜40）なので、塩基の差が見える範囲にする
Q_FADE = (3.0, 15.0)          # 淡く表示: Q_FADE[0] 以下で最も淡く、Q_FADE[1] 以上で元の色
Q_FADE_MIN_ALPHA = 0.18
Q_CMAP = 'RdYlGn'
Q_SCALE_MAX = 20.0
Q_BANDS = (5.0, 10.0)         # 文字列表示の色分け: 低 < 5 ≦ 中 < 10 ≦ 高
Q_MAX_CHAR = 40               # ASCII_Q の上限（'I'）


def base_color(base, text=False):
    return (TEXT_COLORS if text else BASE_COLORS).get(base, '#cfcfcf' if text else OTHER_COLOR)


def q_char(q):
    """Q値 -> Phred+33 の1文字（Q なしは空白）"""
    if q is None or not np.isfinite(q):
        return ' '
    return chr(int(round(min(max(q, 0), Q_MAX_CHAR))) + 33)


def q_band(q):
    if q is None or not np.isfinite(q):
        return 'none'
    return 'low' if q < Q_BANDS[0] else 'mid' if q < Q_BANDS[1] else 'high'


def q_alpha(q):
    """Q が低いほど淡くするための不透明度（Q なしは 1）"""
    q = np.asarray(q, dtype=float)
    lo, hi = Q_FADE
    a = Q_FADE_MIN_ALPHA + (1 - Q_FADE_MIN_ALPHA) * np.clip((q - lo) / (hi - lo), 0, 1)
    return np.where(np.isfinite(q), a, 1.0)


# ============= 読みへの切り分け =============
READ_COLUMNS = ['file', 'read', 'start', 'end', 'direction', 'length', 'sequence', 'values', 'quals', 'mean_q']


def _direction_of(label, positions):
    """LAP の Direction（5'→3' / 3'→5'）を優先し、無ければ位置の並びで決める"""
    label = str(label or '').replace('’', "'")
    if label.startswith("3'"):
        return REVERSE
    if label.startswith("5'"):
        return FORWARD
    return FORWARD if len(positions) < 2 or positions[-1] >= positions[0] else REVERSE


def extract_reads(df: pd.DataFrame) -> pd.DataFrame:
    """LAP Table を読み（1行＝1読み）に切り分ける。

    列: file, read（L#）, start, end（参照上の区間、両端含む）, direction（→ / ←）, length,
        sequence（参照の向き＝位置の昇順に並べた文字列）, values / quals（同じ順の値・Q値）, mean_q
    区切りの B（MS_IDX 0 と末端）は含めない。ファイル・L# が変わったところでも区切る。
    """
    if df.empty:
        return pd.DataFrame(columns=READ_COLUMNS)
    pos = pd.to_numeric(df[POSITION], errors='coerce').to_numpy()
    if not np.isfinite(pos).any():
        return pd.DataFrame(columns=READ_COLUMNS)
    n = len(df)
    last = int(np.nanmax(pos))
    bases = df[BASE].fillna('').astype(str).str.strip().to_numpy() if BASE in df else np.full(n, '')
    values = pd.to_numeric(df[VALUE], errors='coerce').to_numpy()
    quals = pd.to_numeric(df[Q_VALUE], errors='coerce').to_numpy() if Q_VALUE in df else np.full(n, np.nan)
    files = df['file'].to_numpy() if 'file' in df else np.full(n, '')
    reads_no = pd.to_numeric(df['L#'], errors='coerce').to_numpy() if 'L#' in df else np.full(n, np.nan)
    directions = df['Direction'].to_numpy() if 'Direction' in df else np.full(n, '')

    rows = []
    current: list[int] = []
    current_key = None

    def flush():
        if not current:
            return
        idx = np.array(current)
        p = pos[idx].astype(int)
        order = np.argsort(p, kind='stable')
        q = quals[idx][order]
        rows.append(dict(file=current_key[0], read=current_key[1], start=int(p.min()), end=int(p.max()),
                         direction=_direction_of(directions[idx[0]], p), length=len(p),
                         sequence=''.join(b or '?' for b in bases[idx][order]), values=values[idx][order],
                         quals=q, mean_q=float(np.nanmean(q)) if np.isfinite(q).any() else np.nan))

    for i in range(n):
        x = pos[i]
        if not np.isfinite(x):
            continue
        key = (files[i], reads_no[i])
        if key != current_key and not (current_key and key[0] == current_key[0]
                                       and np.isnan(key[1]) and np.isnan(current_key[1])):
            flush()
            current, current_key = [], key
        if x == 0 or x == last:
            flush()
            current = []
            continue
        current.append(i)
    flush()
    return pd.DataFrame(rows, columns=READ_COLUMNS)


def reference(df: pd.DataFrame) -> pd.Series:
    """位置 -> その位置に割り当てられた塩基（最多のもの）。区切りの B（0 と末端）は除く"""
    pos = pd.to_numeric(df[POSITION], errors='coerce')
    bases = df[BASE].fillna('').astype(str).str.strip()
    frame = pd.DataFrame({'pos': pos, 'base': bases})
    frame = frame[frame['pos'].notna() & (frame['base'] != '')]
    if frame.empty:
        return pd.Series(dtype=object)
    last = frame['pos'].max()
    frame = frame[(frame['pos'] != 0) & (frame['pos'] != last)]
    frame['pos'] = frame['pos'].astype(int)
    return frame.groupby('pos')['base'].agg(lambda s: s.value_counts().index[0]).sort_index()


def coverage(reads: pd.DataFrame, positions) -> pd.DataFrame:
    """位置ごとの読みの数（順方向・逆方向）"""
    out = pd.DataFrame(0, index=list(positions), columns=[FORWARD, REVERSE])
    for row in reads.itertuples():
        out.loc[row.start:row.end, row.direction] += 1
    return out


def position_quality(reads: pd.DataFrame, positions) -> pd.DataFrame:
    """位置ごとの Q 値の平均・中央値・四分位（全読みの塩基から）"""
    pos_list, q_list = [], []
    for row in reads.itertuples():
        pos_list.extend(range(row.start, row.end + 1))
        q_list.extend(row.quals)
    frame = pd.DataFrame({'pos': pos_list, 'q': q_list}).dropna()
    stats = frame.groupby('pos')['q'].describe()[['mean', '25%', '50%', '75%']] if len(frame) else None
    return (stats if stats is not None else pd.DataFrame(columns=['mean', '25%', '50%', '75%'])).reindex(positions)


def collapse(reads: pd.DataFrame) -> pd.DataFrame:
    """同じ区間・同じ向き・同じ文字列の読みを1行にまとめ、件数（count）と値・Q の平均を付ける"""
    if reads.empty:
        return reads.assign(count=pd.Series(dtype=int))
    groups = reads.groupby(['start', 'end', 'direction', 'sequence'], sort=False)
    out = groups.size().rename('count').reset_index()
    out['length'] = out['end'] - out['start'] + 1
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)   # Q がすべて欠けた位置の nanmean
        out['values'] = [np.nanmean(np.vstack(g['values'].to_list()), axis=0) for _, g in groups]
        out['quals'] = [np.nanmean(np.vstack(g['quals'].to_list()), axis=0) for _, g in groups]
    out['mean_q'] = [float(np.nanmean(q)) if np.isfinite(q).any() else np.nan for q in out['quals']]
    return out


SORTS = {
    '開始位置 → 長さ': 'start',
    '件数の多い順': 'count',
    '長さの長い順': 'length',
    '平均Qの高い順': 'quality',
    '向き → 開始位置': 'direction',
    '読み込み順（まとめない時）': 'original',
}


def sort_reads(reads: pd.DataFrame, how='start') -> pd.DataFrame:
    if reads.empty or how == 'original':
        return reads.reset_index(drop=True)
    if how == 'count' and 'count' in reads:
        keys, asc = ['count', 'start', 'length'], [False, True, False]
    elif how == 'length':
        keys, asc = ['length', 'start'], [False, True]
    elif how == 'quality':
        keys, asc = ['mean_q', 'start'], [False, True]
    elif how == 'direction':
        keys, asc = ['direction', 'start', 'length'], [True, True, False]
    else:
        keys, asc = ['start', 'length'], [True, False]
    return reads.sort_values(keys, ascending=asc, kind='stable', na_position='last').reset_index(drop=True)


def display_rows(asm: Assembly, sort='start', merge=True) -> pd.DataFrame:
    """図・文字列表示に使う行（まとめる／まとめない、並べ替え済み）"""
    reads = collapse(asm.reads) if merge else asm.reads.assign(count=1)
    if not merge and sort == 'count':
        sort = 'start'
    return sort_reads(reads, sort)


@dataclass
class Assembly:
    """1データ（1つの共有上の1 target）のアセンブリ結果"""
    label: str
    target: str
    reference: pd.Series      # 位置 -> 塩基
    reads: pd.DataFrame       # extract_reads の結果（フィルタ後）
    total_reads: int          # フィルタ前の読みの数

    @property
    def positions(self):
        return list(self.reference.index)

    @property
    def sequence(self):
        return ''.join(self.reference.tolist())

    @property
    def has_quality(self):
        return bool(len(self.reads)) and self.reads['mean_q'].notna().any()

    def summary(self):
        r = self.reads
        if r.empty:
            return f'{self.label}: 読み 0 本（全 {self.total_reads:,} 本）'
        fwd = int((r['direction'] == FORWARD).sum())
        lengths = Counter(r['length'])
        top = '、'.join(f'{n}塩基 {c:,}' for n, c in lengths.most_common(3))
        shown = f'{len(r):,}' + (f' / 全 {self.total_reads:,}' if len(r) != self.total_reads else '')
        text = (f'{self.label}: 参照 {self.sequence}（{len(self.reference)} 塩基）／読み {shown} 本'
                f'（→ {fwd:,} / ← {len(r) - fwd:,}）／長さ 平均 {r["length"].mean():.1f}（{top}）')
        if self.has_quality:
            q = np.concatenate([x for x in r['quals']])
            text += f'／塩基の Q 中央値 {np.nanmedian(q):.1f}（読みの平均Q 中央値 {r["mean_q"].median():.1f}）'
        else:
            text += '／q値なし（古いキャッシュ。サーバーから読み直すと付きます）'
        return text


def build(raw: pd.DataFrame, label, target='', direction='both', min_length=1, min_read_q=0.0) -> Assembly:
    """min_read_q: 読みの平均Q がこれ未満の読みを除く（q値の無い読みは残す）"""
    reads = extract_reads(raw)
    total = len(reads)
    if direction in (FORWARD, REVERSE):
        reads = reads[reads['direction'] == direction]
    reads = reads[reads['length'] >= min_length]
    if min_read_q > 0:
        reads = reads[~(reads['mean_q'] < min_read_q)]
    return Assembly(label, target, reference(raw), reads.reset_index(drop=True), total)


# ============= 図 =============
def _letter_color(base):
    """色付きのマスに載せる文字色（明るい色の上は黒、それ以外は白）"""
    r, g, b = matplotlib.colors.to_rgb(base_color(base))
    return '#202020' if 0.299 * r + 0.587 * g + 0.114 * b > 0.6 else 'white'


def _draw_sequence_row(ax, positions, bases, y, height=0.9, fontsize=11, highlight=()):
    for i, (pos, base) in enumerate(zip(positions, bases)):
        if not base:
            continue
        ax.add_patch(Rectangle((i - 0.45, y - height / 2), 0.9, height, facecolor=base_color(base),
                               edgecolor='black' if pos in highlight else 'none',
                               linewidth=2.0 if pos in highlight else 0))
        ax.text(i, y, base, ha='center', va='center', fontsize=fontsize, fontweight='bold',
                color=_letter_color(base), family='Consolas')


def _base_legend(fig, letters, directions=True, fade=False):
    handles = [Patch(facecolor=base_color(b), label=b + ('（修飾）' if b not in STANDARD_BASES else ''))
               for b in letters]
    if directions:
        handles += [Patch(facecolor=DIRECTION_COLORS[FORWARD], label='順方向 →'),
                    Patch(facecolor=DIRECTION_COLORS[REVERSE], label='逆方向 ←')]
    if fade:
        handles += [Patch(facecolor=matplotlib.colors.to_rgba(BASE_COLORS['A'], 1.0), label=f'Q≥{Q_FADE[1]:.0f}'),
                    Patch(facecolor=matplotlib.colors.to_rgba(BASE_COLORS['A'], 0.45), label='Q 中'),
                    Patch(facecolor=matplotlib.colors.to_rgba(BASE_COLORS['A'], Q_FADE_MIN_ALPHA),
                          label=f'Q≤{Q_FADE[0]:.0f}（淡いほど低品質）')]
    fig.legend(handles=handles, loc='lower center', ncol=len(handles), fontsize=8, frameon=False,
               bbox_to_anchor=(0.5, 0.0))


def _sorted_letters(bases):
    return sorted(set(bases) - {''}, key=lambda b: (b not in STANDARD_BASES, b))


def _style_top_axis(ax, positions):
    ax.tick_params(axis='x', top=True, labeltop=True, bottom=False, labelbottom=False)
    ax.set_xticks(range(len(positions)))
    ax.set_xticklabels([str(p) for p in positions], fontsize=8)
    for spine in ax.spines.values():
        spine.set_visible(False)


COLOR_MODES = {
    '塩基の色': 'base',
    '塩基の色＋Q で濃淡（淡い＝低品質）': 'base_q',
    'Q 値で塗る': 'q',
    '値（Relative Averaged Data）で塗る': 'value',
}


def plot_pileup(asm: Assembly, figure=None, sort='start', merge=True, max_rows=300, color='base_q'):
    """1データのアセンブリ: 参照配列（色付き文字）・カバレッジ・位置ごとのQ・読みのパイルアップ

    color: 'base'（塩基の色） / 'base_q'（塩基の色、Q が低いほど淡く） / 'q'（Q値の色） / 'value'（値の色）
    """
    fig = figure or Figure(figsize=(10, 7), facecolor='white')
    fig.clear()
    fig.set_layout_engine('constrained')
    positions = asm.positions
    if not positions:
        raise ValueError('Assigned Base が無いため、アセンブリを表示できません')
    has_q = asm.has_quality
    if color in ('q', 'base_q') and not has_q:
        color = 'base'
    col = {p: i for i, p in enumerate(positions)}
    reads = display_rows(asm, sort, merge)
    hidden = max(len(reads) - max_rows, 0)
    shown = reads.head(max_rows)

    heights = [0.9, 1.3] + ([1.1] if has_q else []) + [6]
    grid = fig.add_gridspec(len(heights), 2, height_ratios=heights, width_ratios=[14, 1.6 if merge else 0.6],
                            hspace=0.02, wspace=0.02)
    ax_ref = fig.add_subplot(grid[0, 0])
    ax_cov = fig.add_subplot(grid[1, 0], sharex=ax_ref)
    ax_q = fig.add_subplot(grid[2, 0], sharex=ax_ref) if has_q else None
    ax_pile = fig.add_subplot(grid[-1, 0], sharex=ax_ref)
    ax_side = fig.add_subplot(grid[-1, 1], sharey=ax_pile)

    # 参照配列
    _draw_sequence_row(ax_ref, positions, asm.reference.tolist(), 0, fontsize=12 if len(positions) <= 30 else 8)
    ax_ref.set_ylim(-0.6, 0.6)
    ax_ref.set_yticks([0])
    ax_ref.set_yticklabels(['参照'])
    _style_top_axis(ax_ref, positions)

    # カバレッジ（順方向・逆方向の積み上げ）
    cov = coverage(asm.reads, positions)
    xs = np.arange(len(positions))
    ax_cov.bar(xs, cov[FORWARD], width=0.8, color=DIRECTION_COLORS[FORWARD], alpha=0.85)
    ax_cov.bar(xs, cov[REVERSE], width=0.8, bottom=cov[FORWARD], color=DIRECTION_COLORS[REVERSE], alpha=0.85)
    ax_cov.set_ylabel('読み数', fontsize=8)
    ax_cov.tick_params(labelbottom=False, labelsize=7)
    ax_cov.grid(axis='y', alpha=0.3)
    ax_cov.set_ylim(0, max(cov.sum(axis=1).max(), 1) * 1.1)

    # 位置ごとの Q（中央値の棒を Q の色で塗り、四分位をひげで）
    if ax_q is not None:
        stats = position_quality(asm.reads, positions)
        cmap = matplotlib.colormaps[Q_CMAP]
        med = stats['50%'].to_numpy(dtype=float)
        ax_q.bar(xs, np.nan_to_num(med), width=0.8,
                 # cmap に整数を渡すと色表の番号として扱われるので、必ず 0〜1 の実数にする
                 color=[cmap(float(min(m / Q_SCALE_MAX, 1.0))) if np.isfinite(m) else '#dddddd' for m in med])
        ax_q.errorbar(xs, med, yerr=np.vstack([med - stats['25%'].to_numpy(dtype=float),
                                               stats['75%'].to_numpy(dtype=float) - med]),
                      fmt='none', ecolor='#444444', elinewidth=0.8, capsize=2)
        ax_q.set_ylabel('Q\n(中央値)', fontsize=8)
        ax_q.tick_params(labelbottom=False, labelsize=7)
        ax_q.grid(axis='y', alpha=0.3)
        upper = stats['75%'].to_numpy(dtype=float)
        top = np.nanmax(upper) * 1.15 if np.isfinite(upper).any() else 5
        ax_q.set_ylim(0, min(max(top, 5), Q_SCALE_MAX * 1.5))   # 読みの少ない位置の大きなひげで潰れないように

    # パイルアップ（RGB 画像として作る）
    n = len(shown)
    rgb = np.ones((max(n, 1), len(positions), 3))
    scalar = np.full((max(n, 1), len(positions)), np.nan)
    letters = _sorted_letters(asm.reference.tolist() + [b for s in shown['sequence'] for b in s])
    for r, row in enumerate(shown.itertuples()):
        for k, base in enumerate(row.sequence):
            c = col.get(row.start + k)
            if c is None:
                continue
            q = row.quals[k] if k < len(row.quals) else np.nan
            if color == 'value':
                scalar[r, c] = row.values[k] if k < len(row.values) else np.nan
            elif color == 'q':
                scalar[r, c] = q
            else:
                base_rgb = np.array(matplotlib.colors.to_rgb(base_color(base)))
                a = float(q_alpha(q)) if color == 'base_q' else 1.0
                rgb[r, c] = base_rgb * a + (1 - a)
    extent = (-0.5, len(positions) - 0.5, max(n, 1) - 0.5, -0.5)
    if color in ('value', 'q'):
        cmap = 'YlGnBu' if color == 'value' else Q_CMAP
        vmin, vmax = (None, None) if color == 'value' else (0, Q_SCALE_MAX)
        image = ax_pile.imshow(np.ma.masked_invalid(scalar), aspect='auto', interpolation='nearest',
                               cmap=cmap, vmin=vmin, vmax=vmax, extent=extent)
    else:
        image = ax_pile.imshow(rgb, aspect='auto', interpolation='nearest', extent=extent)
    if n <= 120:
        for r in range(1, n):
            ax_pile.axhline(r - 0.5, color='white', linewidth=0.6)
    # マスに文字を載せる（行が少ないとき）。Q で塗る時は塩基の文字を、それ以外は塩基を載せる
    if n <= 40 and color != 'value':
        size = 9 if n <= 25 else 7
        for r, row in enumerate(shown.itertuples()):
            for k, base in enumerate(row.sequence):
                c = col.get(row.start + k)
                if c is None:
                    continue
                if color == 'q':
                    text_color = '#202020'
                elif color == 'base_q' and q_alpha(row.quals[k]) < 0.55:
                    text_color = '#303030'
                else:
                    text_color = _letter_color(base)
                ax_pile.text(c, r, base, ha='center', va='center', fontsize=size, family='Consolas',
                             color=text_color, fontweight='bold')
    if n <= 150:
        for r, row in enumerate(shown.itertuples()):
            if row.direction == FORWARD:
                ax_pile.plot(col[row.end] + 0.5, r, '>', color=DIRECTION_COLORS[FORWARD], markersize=4, clip_on=False)
            else:
                ax_pile.plot(col[row.start] - 0.5, r, '<', color=DIRECTION_COLORS[REVERSE], markersize=4, clip_on=False)
    ax_pile.set_xticks(range(len(positions)))
    ax_pile.set_xticklabels([f'{p}\n{b}' for p, b in zip(positions, asm.reference)], fontsize=7)
    ax_pile.set_xlabel(f'{POSITION}（位置） / 参照の塩基')
    ax_pile.set_ylabel('まとめた読み（同じ区間・向き）' if merge else '読み')
    ax_pile.set_yticks([])

    # 右: 件数（まとめる時）・向き
    side_colors = [DIRECTION_COLORS[d] for d in shown['direction']]
    if merge:
        ax_side.barh(range(n), shown['count'], color=side_colors, height=0.8)
        ax_side.set_xlabel('件数', fontsize=8)
        ax_side.tick_params(labelleft=False, labelsize=7)
        ax_side.grid(axis='x', alpha=0.3)
        if n <= 40:
            for r, c in enumerate(shown['count']):
                ax_side.text(c, r, f' {c:,}', va='center', fontsize=7)
            ax_side.set_xlim(0, shown['count'].max() * 1.35 if n else 1)
    else:
        ax_side.barh(range(n), [1] * n, color=side_colors, height=1.0)
        ax_side.set_xticks([])
        ax_side.set_xlabel('向き', fontsize=8)
        ax_side.tick_params(labelleft=False)
    ax_side.set_ylim(max(n, 1) - 0.5, -0.5)

    note = f'（上位 {max_rows:,} 行を表示、残り {hidden:,} 行は省略）' if hidden else ''
    unit = 'まとめた行' if merge else '本'
    q_note = '' if has_q else '　※q値なし（古いキャッシュ）'
    fig.suptitle(f'アセンブリ: {asm.label}\n参照 5\'-{asm.sequence}-3\'　読み {len(asm.reads):,} 本 → '
                 f'{len(reads):,} {unit}{note}{q_note}', fontsize=10)
    if color in ('value', 'q'):
        fig.colorbar(image, ax=[ax_pile, ax_side], fraction=0.03, extend='max' if color == 'q' else 'neither',
                     label=VALUE if color == 'value' else 'Q 値（Phred）')
    else:
        _base_legend(fig, letters, fade=color == 'base_q')
        fig.get_layout_engine().set(rect=(0, 0.04, 1, 0.96))
    return fig


def plot_compare(assemblies, figure=None):
    """複数データの参照配列を並べて比較する。1番目と違う位置は太枠で強調し、
    下にカバレッジ（割合）と、q値があれば位置ごとの Q の中央値を重ねる"""
    fig = figure or Figure(figsize=(10, 7), facecolor='white')
    fig.clear()
    fig.set_layout_engine('constrained')
    positions = sorted(set().union(*(a.positions for a in assemblies)))
    if not positions:
        raise ValueError('Assigned Base が無いため、アセンブリを表示できません')
    n = len(assemblies)
    has_q = any(a.has_quality for a in assemblies)
    heights = [max(n, 2) * 0.7, 3] + ([2] if has_q else [])
    grid = fig.add_gridspec(len(heights), 1, height_ratios=heights, hspace=0.05)
    ax_seq = fig.add_subplot(grid[0])
    ax_cov = fig.add_subplot(grid[1], sharex=ax_seq)
    ax_q = fig.add_subplot(grid[2], sharex=ax_seq) if has_q else None

    first = assemblies[0].reference
    letters = set()
    for k, a in enumerate(assemblies):
        bases = [a.reference.get(p, '') for p in positions]
        letters.update(b for b in bases if b)
        diff = {p for p, b in zip(positions, bases) if k and b and b != first.get(p, '')}
        _draw_sequence_row(ax_seq, positions, bases, k, fontsize=11 if len(positions) <= 30 else 7, highlight=diff)
    ax_seq.set_ylim(n - 0.4, -0.6)
    ax_seq.set_yticks(range(n))
    ax_seq.set_yticklabels([f'{k + 1}: {a.label}' for k, a in enumerate(assemblies)], fontsize=8)
    for k, tick in enumerate(ax_seq.get_yticklabels()):
        tick.set_color(DATASET_COLORS[k % len(DATASET_COLORS)])
    _style_top_axis(ax_seq, positions)
    ax_seq.set_title('参照配列の比較（太枠＝1番目と違う塩基）', fontsize=9)

    xs = np.arange(len(positions))
    for k, a in enumerate(assemblies):
        color = DATASET_COLORS[k % len(DATASET_COLORS)]
        cov = coverage(a.reads, positions).sum(axis=1)
        ax_cov.plot(xs, cov.to_numpy() / max(len(a.reads), 1) * 100, 'o-', color=color,
                    markersize=4, linewidth=1.3, label=f'{k + 1}: {a.label}（{len(a.reads):,} 本）')
        if ax_q is not None and a.has_quality:
            ax_q.plot(xs, position_quality(a.reads, positions)['50%'].to_numpy(dtype=float), 's-', color=color,
                      markersize=3.5, linewidth=1.1)
    ax_cov.set_ylabel('その位置を通った読みの割合 [%]', fontsize=8)
    ax_cov.set_ylim(0, None)
    ax_cov.grid(alpha=0.3)
    ax_cov.legend(fontsize=8, loc='best')
    bottom = ax_q if ax_q is not None else ax_cov
    if ax_q is not None:
        ax_cov.tick_params(labelbottom=False)
        ax_q.set_ylabel('Q の中央値', fontsize=8)
        ax_q.set_ylim(0, None)
        ax_q.grid(alpha=0.3)
    bottom.set_xticks(xs)
    bottom.set_xticklabels([str(p) for p in positions], fontsize=8)
    bottom.set_xlabel(f'{POSITION}（位置）')
    fig.suptitle(f'アセンブリの比較（{n} データ）', fontsize=10)
    _base_legend(fig, _sorted_letters(letters), directions=False)
    fig.get_layout_engine().set(rect=(0, 0.04, 1, 0.96))
    return fig


# ============= 文字列表示 =============
GAP_CHAR = '·'


@dataclass
class AlignmentLine:
    head: str                  # 行の見出し（件数・番号）
    seq: str                   # 参照の位置にそろえた文字列（読みの無い位置は GAP_CHAR）
    tail: str = ''             # 末尾の注記（向き・区間など）
    kind: str = 'read'         # 'ruler' / 'ref' / 'read' / 'qual' / 'note'
    quals: tuple = ()          # kind='qual' の時、seq の各文字の Q 値（色分け用。空白の位置は NaN）


def alignment_lines(asm: Assembly, sort='start', merge=True, max_rows=2000, show_quality=True):
    """テキスト表示用の行。読みごとに、q値があれば下に Phred+33 の q値文字列を位置をそろえて付ける。

    まとめた行の q値は、まとめた読みの Q の平均を文字にしたもの。
    """
    positions = asm.positions
    if not positions:
        return []
    col = {p: i for i, p in enumerate(positions)}
    width = len(positions)
    reads = display_rows(asm, sort, merge)
    show_quality = show_quality and asm.has_quality
    lines = [AlignmentLine('位置', ''.join(str(p % 10) for p in positions), kind='ruler'),
             AlignmentLine('参照', asm.sequence, f'{len(asm.reads):,} 本', kind='ref')]
    for row in reads.head(max_rows).itertuples():
        chars = [GAP_CHAR] * width
        qchars = [' '] * width
        qvals = [np.nan] * width
        for k, base in enumerate(row.sequence):
            c = col.get(row.start + k)
            if c is not None:
                chars[c] = base
                if k < len(row.quals):
                    qchars[c] = q_char(row.quals[k])
                    qvals[c] = row.quals[k]
        head = f'{row.count:>6,}×' if merge else f'{row.Index + 1:>6}'
        tail = f'{row.direction} {row.start}-{row.end}（{row.length}）'
        if np.isfinite(row.mean_q):
            tail += f'  平均Q {row.mean_q:.1f}'
        if not merge:
            tail += f'  {row.file}'
        lines.append(AlignmentLine(head, ''.join(chars), tail))
        if show_quality:
            lines.append(AlignmentLine('Q', ''.join(qchars), kind='qual', quals=tuple(qvals)))
    if len(reads) > max_rows:
        lines.append(AlignmentLine('', '', f'… 残り {len(reads) - max_rows:,} 行は省略', kind='note'))
    return lines
