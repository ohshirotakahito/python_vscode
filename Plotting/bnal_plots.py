# -*- coding: utf-8 -*-
"""BNAL（LAP Table）データの外れ値処理・ヒストグラム・図の作成（Tk非依存）。

view_bnaltdms_plot.py の処理をGUI用に移したもの。pyplot は使わず matplotlib.figure.Figure を返すので、
画面への埋め込み（FigureCanvasTkAgg）にもPNG保存にも同じ関数を使える。
複数の読み込み済みデータを渡すと、同じ軸・同じビンで並べて比較できる。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime

import matplotlib
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

matplotlib.rcParams['font.family'] = ['Yu Gothic', 'Meiryo', 'sans-serif']

VALUE = 'Relative Averaged Data'
POSITION = 'MS_IDX'
POSITION_LABEL = 'MS_IDX（位置）'

# 外れ値判定（IQR基準、Tukeyのフェンス方式）
# 暫定対応: BNAL生成側（LabVIEW）の計算過程で、基準信号が0近傍になる区間に
# 桁違いの負の値が連続して出力される既知の問題があるため。
# k=4 は「正常なデータは一切誤って除外しない」ことを優先し、やや緩めに設定している。
# 根本原因はBNAL生成コードをPythonで書き直す際に対応予定。
OUTLIER_IQR_K = 4.0
# IQRが極端に狭い/ゼロに近いときの誤爆を防ぐためのフォールバック閾値（絶対値）
OUTLIER_ABS_THRESHOLD = 10.0
BIN_WIDTH = 0.025

# 複数データ比較時の系列色（データごと）
DATASET_COLORS = ('#2f6fd0', '#e0662f', '#2a9d5c', '#b23a8f', '#8a6d1f', '#5b5b5b', '#17a2b8', '#c0392b')


# ============= 前処理 =============
def numeric(df: pd.DataFrame) -> pd.DataFrame:
    """値と位置を数値化し、どちらかが欠けた行を除く"""
    d = df.copy()
    d[VALUE] = pd.to_numeric(d[VALUE], errors='coerce')
    d[POSITION] = pd.to_numeric(d[POSITION], errors='coerce')
    d = d.dropna(subset=[VALUE, POSITION])
    d[POSITION] = d[POSITION].astype(int)
    return d


def _fence(values: pd.Series, iqr_k, abs_threshold):
    q1, q3 = values.quantile(0.25), values.quantile(0.75)
    iqr = q3 - q1
    if iqr < 1e-6:
        return -abs_threshold, abs_threshold
    return q1 - iqr_k * iqr, q3 + iqr_k * iqr


def split_outliers(df: pd.DataFrame, iqr_k=OUTLIER_IQR_K, abs_threshold=OUTLIER_ABS_THRESHOLD, per_position=False):
    """(clean, outliers, bounds) を返す。bounds は {位置 or 'all': (下限, 上限)}。

    per_position=False: target 全体（全位置まとめて）で1つの範囲を決める（旧スクリプトと同じ）
    per_position=True : 位置（MS_IDX）ごとに範囲を決める
    """
    d = numeric(df)
    if d.empty:
        return d, d, {}
    if per_position:
        bounds = {pos: _fence(g[VALUE], iqr_k, abs_threshold) for pos, g in d.groupby(POSITION)}
        lo = d[POSITION].map(lambda p: bounds[p][0])
        hi = d[POSITION].map(lambda p: bounds[p][1])
    else:
        bounds = {'all': _fence(d[VALUE], iqr_k, abs_threshold)}
        lo, hi = bounds['all']
    mask = (d[VALUE] < lo) | (d[VALUE] > hi)
    return d[~mask], d[mask], bounds


def compute_bin_edges(values, bin_width=BIN_WIDTH, pad_bins=2) -> np.ndarray:
    """実データの min/max から bin_width の倍数に揃えたビン境界を作る（pad_bins 分の余白付き）"""
    s = pd.to_numeric(pd.Series(values), errors='coerce').dropna()
    if s.empty:
        return np.arange(-0.1, 3.4 + bin_width, bin_width)
    lo = np.floor(s.min() / bin_width) * bin_width - pad_bins * bin_width
    hi = np.ceil(s.max() / bin_width) * bin_width + pad_bins * bin_width
    return np.arange(lo, hi + bin_width / 2, bin_width)


def histogram_table(df: pd.DataFrame, bin_edges: np.ndarray) -> pd.DataFrame:
    """bin_left, bin_right, bin_center と、位置ごとの正規化ヒストグラム（MS_IDX_n 列）"""
    out = pd.DataFrame({'bin_left': bin_edges[:-1], 'bin_right': bin_edges[1:],
                        'bin_center': (bin_edges[:-1] + bin_edges[1:]) / 2})
    for pos, g in df.groupby(POSITION):
        counts, _ = np.histogram(g[VALUE].to_numpy(), bins=bin_edges)
        total = counts.sum()
        out[f'MS_IDX_{pos}'] = counts / total if total > 0 else counts
    return out


def position_columns(table: pd.DataFrame):
    return [c for c in table.columns if c.startswith('MS_IDX_')]


@dataclass
class Prepared:
    """作図用に前処理済みの1データ（1つの共有上の1 target）"""
    label: str                 # 図の凡例・タイトル用
    raw: pd.DataFrame          # 読み込んだ全行（外れ値・値なし行を含む）
    clean: pd.DataFrame        # 作図に使う行
    outliers: pd.DataFrame
    bounds: dict
    experiment: str = ''
    sample: str = ''
    target: str = ''
    group: str = ''

    @property
    def positions(self):
        return sorted(self.clean[POSITION].unique().tolist())

    def summary(self):
        n = len(numeric(self.raw))
        missing = len(self.raw) - n
        ratio = len(self.outliers) / n * 100 if n else 0.0
        text = f'{self.label}: {len(self.clean):,} 行を使用'
        if len(self.outliers):
            vals = self.outliers[VALUE]
            text += f'／外れ値 {len(self.outliers):,} 行（{ratio:.2f}%、値 {vals.min():.3g}〜{vals.max():.3g}）'
        if missing:
            text += f'／値なし {missing:,} 行'
        if 'all' in self.bounds:
            lo, hi = self.bounds['all']
            text += f'／許容範囲 [{lo:.3f}, {hi:.3f}]'
        return text


def prepare(raw: pd.DataFrame, label, exclude_outliers=True, iqr_k=OUTLIER_IQR_K, per_position=False,
            **meta) -> Prepared:
    if exclude_outliers:
        clean, outliers, bounds = split_outliers(raw, iqr_k=iqr_k, per_position=per_position)
    else:
        clean, outliers, bounds = numeric(raw), raw.iloc[0:0], {}
    return Prepared(label, raw, clean, outliers, bounds, **meta)


def shared_bin_edges(preps, bin_width=BIN_WIDTH):
    """比較するデータ全体で共通のビン境界（同じ軸で並べるため）"""
    values = pd.concat([p.clean[VALUE] for p in preps]) if preps else pd.Series(dtype=float)
    return compute_bin_edges(values, bin_width=bin_width)


# ============= 図 =============
# 位置ごとの色分け（1データ表示時の箱ひげ・バイオリン・ヒストグラム）
POSITION_COLOR_SCHEMES = {
    'グラデーション（viridis・旧スクリプトと同じ）': 'viridis',
    '値で色分け（ヒートマップと同じ YlGnBu）': 'value',
    '単色': 'single',
    'カテゴリ色（tab20）': 'tab20',
}
SINGLE_COLOR = '#5b9bd5'


def _position_colors(positions, scheme='viridis', preps=()):
    """位置 -> 色。

    viridis: 位置の順番をグラデーションで表す（旧スクリプトと同じ）
    value  : その位置の中央値で色を決める（ヒートマップと同じ配色。値の高い位置ほど濃い）
    single : すべて同じ色
    tab20  : 位置ごとに別々のカテゴリ色
    """
    n = len(positions)
    if scheme == 'single':
        return {pos: SINGLE_COLOR for pos in positions}
    if scheme == 'tab20':
        cmap = matplotlib.colormaps['tab20']
        return {pos: cmap(i % 20) for i, pos in enumerate(positions)}
    if scheme == 'value' and preps:
        medians = pd.concat([p.clean for p in preps]).groupby(POSITION)[VALUE].median()
        lo, hi = medians.min(), medians.max()
        cmap = matplotlib.colormaps['YlGnBu']
        # 両端の白に近い色・黒に近い色を避けて 0.2〜0.9 の範囲を使う
        return {pos: cmap(0.2 + 0.7 * ((medians.get(pos, lo) - lo) / (hi - lo) if hi > lo else 0.5))
                for pos in positions}
    cmap = matplotlib.colormaps['viridis']
    return {pos: cmap(i / max(n - 1, 1)) for i, pos in enumerate(positions)}


def _dataset_color(i):
    return DATASET_COLORS[i % len(DATASET_COLORS)]


def _new_figure(figure=None, size=(10, 6)):
    """constrained layout で、タイトル・凡例・カラーバーが重ならず見切れないようにする"""
    if figure is None:
        return Figure(figsize=size, facecolor='white', layout='constrained')
    figure.clear()
    figure.set_layout_engine('constrained')
    return figure


def _grouped_positions(preps):
    """全データの位置の和集合と、データごとの横ずらし量・幅"""
    positions = sorted(set().union(*(p.positions for p in preps)))
    n = len(preps)
    width = 0.8 / n
    offsets = [(i - (n - 1) / 2) * width for i in range(n)]
    return positions, offsets, width


def assigned_base_table(preps):
    """TDMSの行ごとの対応を使う。外れ値・値なし行も配列情報には使用する。"""
    frames = []
    for prep in preps:
        if 'Assigned Base' not in prep.raw:
            continue
        frame = prep.raw[[POSITION, 'Assigned Base']].copy()
        frame[POSITION] = pd.to_numeric(frame[POSITION], errors='coerce')
        frame['Assigned Base'] = frame['Assigned Base'].fillna('').astype(str).str.strip()
        frame = frame[np.isfinite(frame[POSITION]) & (frame[POSITION] % 1 == 0)
                      & ~frame['Assigned Base'].str.lower().isin(['', 'nan', 'none', '<na>'])]
        frame[POSITION] = frame[POSITION].astype(int)
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=[POSITION, 'Assigned Base'])
    return (pd.concat(frames, ignore_index=True).drop_duplicates()
            .sort_values([POSITION, 'Assigned Base']).reset_index(drop=True))


def _finish_position_axis(ax, positions, preps=()):
    table = assigned_base_table(preps)
    bases = table.groupby(POSITION)['Assigned Base'].agg('/'.join).to_dict()
    ax.set_xticks(range(len(positions)))
    ax.set_xticklabels([f'{p}\n{bases.get(p, "—")}' if bases else str(p) for p in positions])
    ax.set_xlabel(f'{POSITION_LABEL} / Assigned Base' if bases else POSITION_LABEL)
    ax.set_xlim(-0.6, len(positions) - 0.4)
    ax.grid(axis='y', alpha=0.3)


def _title(preps, kind):
    return f'{kind}: {preps[0].label}' if len(preps) == 1 else f'{kind}（{len(preps)} データ比較）'


def _dataset_legend(ax, preps):
    from matplotlib.patches import Patch
    handles = [Patch(facecolor=_dataset_color(i), alpha=0.7, label=p.label) for i, p in enumerate(preps)]
    ax.legend(handles=handles, loc='best', fontsize=8)


def plot_violin(preps, figure=None, scheme='viridis'):
    fig = _new_figure(figure)
    ax = fig.add_subplot(111)
    positions, offsets, width = _grouped_positions(preps)
    single = len(preps) == 1
    colors = _position_colors(positions, scheme, preps)
    for i, prep in enumerate(preps):
        groups = {pos: g[VALUE].to_numpy() for pos, g in prep.clean.groupby(POSITION)}
        usable = [pos for pos in positions if pos in groups and len(groups[pos]) > 1]
        if not usable:
            continue
        xs = [positions.index(pos) + offsets[i] for pos in usable]
        parts = ax.violinplot([groups[pos] for pos in usable], positions=xs, widths=width * 0.95,
                              showmedians=True, showextrema=False)
        for pos, body in zip(usable, parts['bodies']):
            body.set_facecolor(colors[pos] if single else _dataset_color(i))
            body.set_edgecolor('gray')
            body.set_alpha(0.7)
        parts['cmedians'].set_color('black')
    _finish_position_axis(ax, positions, preps)
    ax.set_ylabel(VALUE)
    ax.set_title(_title(preps, 'バイオリンプロット'))
    if not single:
        _dataset_legend(ax, preps)
    return fig


def plot_box(preps, figure=None, show_fliers=True, scheme='viridis'):
    fig = _new_figure(figure)
    ax = fig.add_subplot(111)
    positions, offsets, width = _grouped_positions(preps)
    single = len(preps) == 1
    colors = _position_colors(positions, scheme, preps)
    for i, prep in enumerate(preps):
        groups = {pos: g[VALUE].to_numpy() for pos, g in prep.clean.groupby(POSITION)}
        usable = [pos for pos in positions if pos in groups and len(groups[pos])]
        if not usable:
            continue
        xs = [positions.index(pos) + offsets[i] for pos in usable]
        bp = ax.boxplot([groups[pos] for pos in usable], positions=xs, widths=width * 0.8, patch_artist=True,
                        showfliers=show_fliers, medianprops={'color': 'black'},
                        flierprops={'markersize': 3, 'alpha': 0.4})
        for pos, box in zip(usable, bp['boxes']):
            box.set_facecolor(colors[pos] if single else _dataset_color(i))
            box.set_alpha(0.75)
    _finish_position_axis(ax, positions, preps)
    ax.set_ylabel(VALUE)
    ax.set_title(_title(preps, '箱ひげ図'))
    if not single:
        _dataset_legend(ax, preps)
    return fig


def plot_median(preps, figure=None):
    """位置ごとの中央値と四分位範囲（データ間の比較向け）"""
    fig = _new_figure(figure)
    ax = fig.add_subplot(111)
    positions, offsets, _ = _grouped_positions(preps)
    for i, prep in enumerate(preps):
        stats = prep.clean.groupby(POSITION)[VALUE].quantile([0.25, 0.5, 0.75]).unstack()
        stats = stats.reindex(positions)
        xs = np.arange(len(positions)) + offsets[i] * 0.5
        median = stats[0.5].to_numpy()
        err = np.vstack([median - stats[0.25].to_numpy(), stats[0.75].to_numpy() - median])
        ax.errorbar(xs, median, yerr=err, fmt='o-', color=_dataset_color(i), capsize=3, markersize=5,
                    linewidth=1.2, label=prep.label)
    _finish_position_axis(ax, positions, preps)
    ax.set_ylabel(f'{VALUE}（中央値、エラーバー: 四分位範囲）')
    ax.set_title(_title(preps, '位置ごとの中央値'))
    ax.legend(loc='best', fontsize=8)
    return fig


def plot_histogram(preps, bin_edges, figure=None, scheme='viridis'):
    """データごとに1段。各段で位置ごとの正規化ヒストグラムを重ね描きする"""
    fig = _new_figure(figure)
    n = len(preps)
    axes = fig.subplots(n, 1, sharex=True, squeeze=False)[:, 0]
    positions = sorted(set().union(*(p.positions for p in preps)))
    colors = _position_colors(positions, scheme, preps)
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    for ax, prep in zip(axes, preps):
        table = histogram_table(prep.clean, bin_edges)
        for col in position_columns(table):
            pos = int(col.rsplit('_', 1)[1])
            ax.plot(centers, table[col], color=colors[pos], linewidth=1.1, alpha=0.9, label=str(pos))
        ax.set_ylabel('正規化頻度')
        ax.set_title(prep.label, fontsize=10)
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel(f'{VALUE}（ビン中心）')
    axes[0].legend(title=POSITION_LABEL, fontsize=7, title_fontsize=8, ncol=2,
                   loc='upper left', bbox_to_anchor=(1.01, 1.0))
    fig.suptitle(_title(preps, 'ヒストグラム重ね描き') if n > 1 else 'ヒストグラム重ね描き')
    return fig


def heatmap_matrix(prep: Prepared, bin_edges, positions):
    """行=ビン、列=位置 の正規化ヒストグラム行列（その位置にデータが無ければ NaN）"""
    table = histogram_table(prep.clean, bin_edges)
    return np.column_stack([table[f'MS_IDX_{p}'].to_numpy() if f'MS_IDX_{p}' in table
                            else np.full(len(table), np.nan) for p in positions])


def plot_heatmap(preps, bin_edges, figure=None):
    """データごとに1列。位置の間に隙間を空けて、位置ごとの分布を縦の帯で表示する"""
    fig = _new_figure(figure)
    n = len(preps)
    axes = fig.subplots(1, n, sharey=True, squeeze=False)[0]
    positions = sorted(set().union(*(p.positions for p in preps)))
    mats = [heatmap_matrix(p, bin_edges, positions) for p in preps]
    vmax = max((np.nanmax(m) for m in mats if np.isfinite(m).any()), default=1.0)
    # 各位置を幅0.8の帯にし、間の0.2は NaN（白）にする
    x_edges = np.array([x for i in range(len(positions)) for x in (i - 0.4, i + 0.4)])
    cols = [c for i in range(len(positions)) for c in (i, None)][:-1]
    cmap = matplotlib.colormaps['YlGnBu'].copy()
    cmap.set_bad('white')
    mesh = None
    for ax, prep, mat in zip(axes, preps, mats):
        rows = len(bin_edges) - 1
        data = np.column_stack([mat[:, c] if c is not None else np.full(rows, np.nan) for c in cols])
        mesh = ax.pcolormesh(x_edges, bin_edges, np.ma.masked_invalid(data),
                             cmap=cmap, vmin=0, vmax=vmax, shading='flat')
        ax.set_facecolor('white')
        _finish_position_axis(ax, positions, [prep])
        ax.grid(False)
        ax.set_title(prep.label, fontsize=10)
    axes[0].set_ylabel(VALUE)
    fig.colorbar(mesh, ax=list(axes), label='正規化頻度', fraction=0.04 if n > 1 else 0.05)
    fig.suptitle(_title(preps, 'ヒートマップ') if n > 1 else 'ヒートマップ')
    return fig


# 島状の密度図の配色（外側の薄い水色 → 青緑 → 緑 → 黄色の芯）
DENSITY_CMAP = matplotlib.colors.LinearSegmentedColormap.from_list(
    'bnal_density', ['#e3f4f8', '#a6dcea', '#5cc2d6', '#3fae9e', '#7cc95a', '#cfe04a', '#f6d23c'])


def _kde_bandwidth(values):
    """KDE本体と描画範囲で共通の平滑化幅。"""
    values = values[np.isfinite(values)]
    if values.size < 2:
        return 0.01
    std = values.std()
    iqr = np.subtract(*np.percentile(values, [75, 25]))
    spread = min(std, iqr / 1.34) if iqr > 0 else std
    return max(0.9 * spread * values.size ** -0.2, 0.01)   # Silverman の目安（下限あり）


def _kde_1d(values, grid):
    """1次元のガウスKDE（細かいヒストグラムをガウス核で平滑化。大量データでも速い）"""
    values = values[np.isfinite(values)]
    if values.size < 2:
        return np.zeros_like(grid)
    bw = _kde_bandwidth(values)
    step = grid[1] - grid[0]
    edges = np.append(grid - step / 2, grid[-1] + step / 2)
    counts, _ = np.histogram(values, bins=edges)
    half = int(np.ceil(4 * bw / step))
    kernel = np.exp(-0.5 * (np.arange(-half, half + 1) * step / bw) ** 2)
    return np.convolve(counts, kernel / kernel.sum(), mode='same')


def plot_density(preps, figure=None, threshold=0.04, x_sigma=0.18, show_mode=True):
    """位置ごとの値の分布を、横方向にもぼかした「島」として描く（論文で使われる伝導度の分布図に近い形）。

    縦方向: その位置の値の KDE（多峰性もそのまま残る）
    横方向: 位置を中心とするガウス形（幅は見た目のためのもので、データ由来ではない）
    濃淡は位置ごとに最大値=1で正規化する（データ数の違う位置どうしでも形を比べやすくするため）。
    threshold 未満は描かない（背景を白に保つ）。show_mode=True で各位置の最頻値に点を打つ。
    """
    fig = _new_figure(figure)
    n = len(preps)
    axes = fig.subplots(1, n, sharey=True, squeeze=False)[0]
    positions = sorted(set().union(*(p.positions for p in preps)))
    values = pd.concat([p.clean[VALUE] for p in preps])
    lo, hi = values.min(), values.max()
    # 少数の位置の島も省かず、ガウス核の裾（±4帯域幅）まで範囲に含める。
    bandwidth = max((_kde_bandwidth(g[VALUE].to_numpy())
                     for p in preps for _, g in p.clean.groupby(POSITION)), default=0.01)
    pad = max((hi - lo) * 0.08, 4 * bandwidth)
    y = np.linspace(lo - pad, hi + pad, 500)
    dx = np.linspace(-0.45, 0.45, 61)
    x_profile = np.exp(-0.5 * (dx / x_sigma) ** 2)
    levels = np.linspace(threshold, 1.0, 12)
    contour = None
    for ax, prep in zip(axes, preps):
        groups = {pos: g[VALUE].to_numpy() for pos, g in prep.clean.groupby(POSITION)}
        for i, pos in enumerate(positions):
            if pos not in groups or len(groups[pos]) < 2:
                continue
            dens = _kde_1d(groups[pos], y)
            if dens.max() <= 0:
                continue
            dens = dens / dens.max()
            z = np.outer(dens, x_profile)
            contour = ax.contourf(i + dx, y, z, levels=levels, cmap=DENSITY_CMAP, vmin=0, vmax=1, extend='neither')
            if show_mode:
                ax.plot(i, y[np.argmax(dens)], 'o', markersize=3.5, color='#2f8f3a',
                        markeredgecolor='#1d5c25', markeredgewidth=0.6, zorder=3)
        _finish_position_axis(ax, positions, [prep])
        ax.grid(False)
        ax.set_ylim(y[0], y[-1])
        ax.set_title(prep.label, fontsize=10)
    axes[0].set_ylabel(VALUE)
    if contour is not None:
        fig.colorbar(contour, ax=list(axes), label='相対密度（位置ごとに最大=1）', fraction=0.04 if n > 1 else 0.05,
                     ticks=[0.25, 0.5, 0.75, 1.0])
    fig.suptitle(_title(preps, '分布の島図') if n > 1 else '分布の島図（位置ごとのKDE）')
    return fig


def _split_cmap(color):
    """データ色から、薄い色 → データ色 → 濃い色 の島用カラーマップを作る（一覧の選択色と同じ系統）"""
    rgb = np.array(matplotlib.colors.to_rgb(color))
    white = np.ones(3)
    return matplotlib.colors.LinearSegmentedColormap.from_list(
        f'bnal_split_{color}', [rgb * 0.13 + white * 0.87, rgb * 0.5 + white * 0.5, rgb, rgb * 0.6])


def plot_density_split(preps, figure=None, threshold=0.04, x_sigma=0.18, show_mode=True):
    """複数データの島図を1つの軸に並べる。各位置の島を縦に n 等分し、左から選択順にデータを割り当てる。

    島全体の横幅・ガウス形は plot_density と同じで、短冊ごとにそのデータの KDE で塗る。
    縦方向の KDE・正規化（位置ごと・データごとに最大=1）も同じ。y 軸が共通なので、
    同じ位置での分布の高さ・広がり・多峰性の違いを横並びで直接比べられる。
    短冊の色はデータ色（_dataset_color、画面の一覧の選択色と同じ）。
    """
    if len(preps) < 2:
        raise ValueError('並べて比較する島図は、データを2つ以上選んでください（Ctrl+クリック）')
    fig = _new_figure(figure)
    ax = fig.add_subplot(111)
    n = len(preps)
    positions = sorted(set().union(*(p.positions for p in preps)))
    values = pd.concat([p.clean[VALUE] for p in preps])
    lo, hi = values.min(), values.max()
    bandwidth = max((_kde_bandwidth(g[VALUE].to_numpy())
                     for p in preps for _, g in p.clean.groupby(POSITION)), default=0.01)
    pad = max((hi - lo) * 0.08, 4 * bandwidth)
    y = np.linspace(lo - pad, hi + pad, 500)
    bounds = np.linspace(-0.45, 0.45, n + 1)   # 短冊の境界（左から1つ目のデータ）
    levels = np.linspace(threshold, 1.0, 12)
    # 3つ以上では端の短冊が薄くなりすぎないよう、島の横の広がりを大きくする（2つなら元の島図と同じ形）
    x_sigma = x_sigma * max(1.0, n / 2)
    for k, prep in enumerate(preps):
        dx = np.linspace(bounds[k], bounds[k + 1], max(61 // n, 8))
        x_profile = np.exp(-0.5 * (dx / x_sigma) ** 2)
        cmap = _split_cmap(_dataset_color(k))
        mode_color = tuple(np.array(matplotlib.colors.to_rgb(_dataset_color(k))) * 0.45)
        groups = {pos: g[VALUE].to_numpy() for pos, g in prep.clean.groupby(POSITION)}
        for i, pos in enumerate(positions):
            if pos not in groups or len(groups[pos]) < 2:
                continue
            dens = _kde_1d(groups[pos], y)
            if dens.max() <= 0:
                continue
            dens = dens / dens.max()
            ax.contourf(i + dx, y, np.outer(dens, x_profile), levels=levels, cmap=cmap,
                        vmin=0, vmax=1, extend='neither')
            if show_mode:
                ax.plot(i + (bounds[k] + bounds[k + 1]) / 2, y[np.argmax(dens)], 'o',
                        markersize=3.5 if n <= 3 else 2.5, color=mode_color,
                        markeredgecolor='white', markeredgewidth=0.5, zorder=3)
    for i in range(len(positions)):
        for b in bounds[1:-1]:
            ax.axvline(i + b, color='#888888', linewidth=0.3, alpha=0.4, zorder=0)
    _finish_position_axis(ax, positions, preps)
    ax.grid(False)
    ax.set_ylim(y[0], y[-1])
    ax.set_ylabel(VALUE)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=_dataset_color(k), label=f'{k + 1}: {p.label}')
                       for k, p in enumerate(preps)], loc='best', fontsize=8,
              title='島の左から', title_fontsize=8)
    ax.set_title('濃さ＝相対密度（位置ごと・データごとに最大=1）、点＝最頻値', fontsize=9)
    fig.suptitle(f'分布の島図（{n} データ比較）')
    return fig


def depth_table(prep: Prepared) -> pd.DataFrame:
    """位置ごとのカウント数（Depth）。1つの読み（L#）の中で同じ MS_IDX は重複しないため、
    行数＝その位置を通った読みの数になる。

    列: depth（値なしも含む全行）, used（作図に使用）, outliers（外れ値として除外）, missing（値なし）
    """
    raw_pos = pd.to_numeric(prep.raw[POSITION], errors='coerce')
    raw_val = pd.to_numeric(prep.raw[VALUE], errors='coerce')
    has_pos = raw_pos.notna()
    table = pd.DataFrame({
        'depth': raw_pos[has_pos].astype(int).value_counts(),
        'used': prep.clean[POSITION].value_counts(),
        'outliers': prep.outliers[POSITION].astype(int).value_counts() if len(prep.outliers) else pd.Series(dtype=int),
        'missing': raw_pos[has_pos & raw_val.isna()].astype(int).value_counts(),
    }).fillna(0).astype(int).sort_index()
    table.index.name = POSITION
    return table


def plot_depth(preps, figure=None):
    """位置ごとのカウント数（Depth）の棒グラフ。

    1データ: 使用 / 外れ値 / 値なし の積み上げ（棒の上に合計＝Depth）
    複数データ: データごとに並べた Depth（合計）の棒
    """
    fig = _new_figure(figure)
    ax = fig.add_subplot(111)
    tables = [depth_table(p) for p in preps]
    positions = sorted(set().union(*(t.index for t in tables)))
    xs = np.arange(len(positions))
    if len(preps) == 1:
        t = tables[0].reindex(positions, fill_value=0)
        bottom = np.zeros(len(positions))
        for col, label, color in (('used', '使用', '#4a90c2'), ('outliers', '外れ値（除外）', '#e07b39'),
                                  ('missing', '値なし', '#b0b0b0')):
            if t[col].any():
                ax.bar(xs, t[col], bottom=bottom, width=0.75, color=color, label=label)
                bottom += t[col].to_numpy()
        if len(positions) <= 40:
            for x, total in zip(xs, t['depth']):
                ax.annotate(f'{total:,}', (x, total), xytext=(0, 2), textcoords='offset points',
                            ha='center', va='bottom', fontsize=7)
    else:
        _, offsets, width = _grouped_positions(preps)
        for i, (prep, t) in enumerate(zip(preps, tables)):
            ax.bar(xs + offsets[i], t['depth'].reindex(positions, fill_value=0), width=width * 0.95,
                   color=_dataset_color(i), label=prep.label)
    _finish_position_axis(ax, positions, preps)
    ax.set_ylabel('カウント数（その位置を通った読みの数）')
    ax.set_title(_title(preps, 'Depth（位置ごとのカウント数）'))
    ax.set_ylim(0, max(t['depth'].max() for t in tables) * 1.18)  # 棒の上の数値と凡例のための余白
    ax.legend(loc='upper center', ncol=min(len(preps), 3) if len(preps) > 1 else 3, fontsize=8)
    return fig


PLOTS = {
    'ヒストグラム重ね描き': 'histogram',
    'バイオリンプロット': 'violin',
    '箱ひげ図': 'box',
    'ヒートマップ': 'heatmap',
    '分布の島図（KDE）': 'density',
    '位置ごとの中央値（比較用）': 'median',
    'Depth（位置ごとのカウント数）': 'depth',
}


def draw(kind, preps, bin_edges, figure=None, scheme='viridis'):
    preps = [p for p in preps if not p.clean.empty]
    if not preps:
        raise ValueError('描画できるデータがありません（全行が値なし・外れ値です）')
    if kind == 'histogram':
        return plot_histogram(preps, bin_edges, figure, scheme=scheme)
    if kind == 'violin':
        return plot_violin(preps, figure, scheme=scheme)
    if kind == 'box':
        return plot_box(preps, figure, scheme=scheme)
    if kind == 'heatmap':
        return plot_heatmap(preps, bin_edges, figure)
    if kind == 'density':   # 1データは島図、複数なら1つの軸で島を短冊に分けて並べる
        return plot_density(preps, figure) if len(preps) == 1 else plot_density_split(preps, figure)
    if kind == 'depth':
        return plot_depth(preps, figure)
    if kind == 'median':
        return plot_median(preps, figure)
    raise ValueError(kind)


# ============= 保存（旧スクリプトと同じ一式） =============
def _sanitize(s):
    return re.sub(r'[\\/:*?"<>|]+', '_', s.replace('@', ''))


def save_outputs(prep: Prepared, bin_edges, base_dir, timestamp=None, scheme='viridis') -> str:
    """heat_data/{timestamp}_{ex}_{sample}_{target}/ に CSV と PNG 一式を保存してフォルダを返す"""
    timestamp = timestamp or datetime.now().strftime('%Y%m%d%H%M')
    folder = os.path.join(base_dir, f'{timestamp}_{_sanitize(prep.experiment)}_{_sanitize(prep.sample)}'
                                    f'_{_sanitize(prep.target)}')
    os.makedirs(folder, exist_ok=True)
    prep.raw.to_csv(os.path.join(folder, 'raw_data.csv'), index=False)
    assigned_base_table([prep]).to_csv(os.path.join(folder, 'assigned_bases.csv'), index=False)
    if len(prep.outliers):
        prep.outliers.to_csv(os.path.join(folder, 'outliers_report.csv'), index=False)
    histogram_table(prep.clean, bin_edges).to_csv(os.path.join(folder, 'histogram_table.csv'), index=False)
    depth_table(prep).to_csv(os.path.join(folder, 'depth.csv'))
    positions = prep.positions
    pd.DataFrame(heatmap_matrix(prep, bin_edges, positions), columns=[f'MS_IDX_{p}' for p in positions]) \
        .assign(bin_center=(bin_edges[:-1] + bin_edges[1:]) / 2) \
        .to_csv(os.path.join(folder, 'heatmap_matrix.csv'), index=False)
    for kind, name in (('histogram', 'histogram.png'), ('violin', 'violin.png'), ('box', 'boxplot.png'),
                       ('heatmap', 'heatmap.png'), ('density', 'density.png'), ('median', 'median.png'), ('depth', 'depth.png')):
        draw(kind, [prep], bin_edges, scheme=scheme).savefig(os.path.join(folder, name), dpi=150)
    return folder
