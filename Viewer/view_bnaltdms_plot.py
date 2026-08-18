# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Created on ...
"""

import os
import re
import glob
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import seaborn as sns

from tqdm import tqdm
from nptdms import TdmsFile

# ============= 設定 =============
VERBOSE = True  # 進捗ログを詳しく出すなら True

# 外れ値判定の設定（IQR基準、Tukeyのフェンス方式）
# 暫定対応: BNAL生成側（LabVIEW）の計算過程で、基準信号が0近傍になる区間に
# 桁違いの負の値が連続して出力される既知の問題があるため。
# 固定の絶対値閾値だと「異常の少ないサンプル」と「異常の多いサンプル」で
# 過不足が出るため、サンプルごとの分布（四分位範囲）から自動で基準を決める。
# k=4 は「正常なサンプルは一切誤って除外しない」ことを優先し、やや緩めに設定している。
# 根本原因はBNAL生成コードをPythonで書き直す際に対応予定。それまでの間、
# 可視化からは除外しつつ、除外内容は outliers_report.csv に記録する。
OUTLIER_IQR_K = 4.0
# IQRが極端に狭い/ゼロに近いサンプルでの誤爆を防ぐためのフォールバック閾値
OUTLIER_ABS_THRESHOLD = 10.0

# スクリプト自身の場所を基準にする（どこから実行しても heat_data の位置がぶれないように）
SCRIPT_DIR = Path(__file__).resolve().parent

# ============= ユーティリティ =============
def _fmt_sec(sec: float) -> str:
    if sec < 1:
        return f"{sec*1000:.0f} ms"
    if sec < 60:
        return f"{sec:.1f} s"
    m, s = divmod(sec, 60)
    return f"{int(m)}m{s:.0f}s"

def _log(msg: str, level: str = "INFO"):
    # 進捗ログの共通フォーマット
    if level == "DEBUG" and not VERBOSE:
        return
    print(f"[{level}] {msg}")

def _sanitize_for_dirname(s: str) -> str:
    # フォルダ名に使えない文字（\ / : * ? " < > | や @ など）を除去・置換
    s = s.replace("@", "")
    return re.sub(r'[\\/:*?"<>|]+', "_", s)

def make_result_dir(base_dir: str, run_timestamp: str, ex: str, sample: str, target: str) -> str:
    """
    heat_data/{timestamp}_{ex}_{sample}_{target}/ を作成してパスを返す
    """
    folder_name = f"{run_timestamp}_{_sanitize_for_dirname(ex)}_{_sanitize_for_dirname(sample)}_{_sanitize_for_dirname(target)}"
    result_dir = os.path.join(base_dir, folder_name)
    os.makedirs(result_dir, exist_ok=True)
    return result_dir

# ============= TDMSユーティリティ =============
def tdms_data_checker_multi_channel(tdms_file_path: str, target_group: str, target_channels: List[str]) -> Dict[str, np.ndarray | None]:
    """
    TDMSファイルから指定グループ・複数チャンネルのデータを取得。
    返り値: {channel_name: np.ndarray or None}
    """
    data_dict: Dict[str, np.ndarray | None] = {}
    file_name = os.path.basename(tdms_file_path)

    try:
        tdms_file = TdmsFile.read(tdms_file_path)
    except FileNotFoundError:
        _log(f"TDMS file not found: {file_name}", "ERROR")
        return {}
    except Exception as e:
        _log(f"TDMS open failed ({file_name}): {e}", "ERROR")
        return {}

    # グループ取得
    group = next((grp for grp in tdms_file.groups() if grp.name == target_group), None)
    if group is None:
        _log(f"Group '{target_group}' not found: {file_name}", "WARN")
        return {}

    # チャンネル取得
    for ch_name in target_channels:
        ch = next((ch for ch in group.channels() if ch.name == ch_name), None)
        if ch is None:
            data_dict[ch_name] = None
            _log(f"Channel missing '{ch_name}' in '{target_group}' -> {file_name}", "DEBUG")
        else:
            try:
                data_dict[ch_name] = ch.data
            except Exception as e:
                data_dict[ch_name] = None
                _log(f"Channel read error '{ch_name}' -> {file_name}: {e}", "ERROR")

    return data_dict

def _nice_tick_positions_and_labels(y_values: np.ndarray, target_num_ticks: int = 10):
    """
    y_values（各行に対応する実数値、昇順）に対して、
    0.0, 0.2, 0.4 のような「キリのいい」候補値を選び、
    それぞれに最も近い y_values のインデックスを目盛り位置として返す。
    戻り値: (tick_indices, tick_labels)
    """
    y_min, y_max = float(y_values.min()), float(y_values.max())
    if y_min == y_max:
        return [0], [round(y_min, 3)]

    locator = MaxNLocator(nbins=target_num_ticks, steps=[1, 2, 2.5, 5, 10])
    nice_vals = [v for v in locator.tick_values(y_min, y_max) if y_min <= v <= y_max]
    if not nice_vals:
        nice_vals = [y_min, y_max]

    tick_indices = []
    tick_labels = []
    seen = set()
    for v in nice_vals:
        idx = int(np.argmin(np.abs(y_values - v)))
        if idx in seen:
            continue
        seen.add(idx)
        tick_indices.append(idx)
        tick_labels.append(round(v, 3))

    return tick_indices, tick_labels


def plotting_heatmap_with_zero_gaps(
    data_2d: np.ndarray,
    sample: str,
    target: str,
    bin_edges: np.ndarray | None = None,
    ms_idx_labels: list | None = None,
    save_path: str | None = None,
) -> np.ndarray:
    """
    2D配列（行=系列, 列=ビン）に 0 行を1行おきに挿入してヒートマップ描画。
    bin_edges: ヒストグラムの実際のビン境界（省略時は行数から等間隔の連番になる）。
               これを渡すことで、Y軸目盛りが実データの範囲・値と一致する。
    ms_idx_labels: 各行に対応する実際の MS_IDX 値のリスト（省略時は連番 0,1,2,...）。
                   X軸をboxplot/violinと同じ実際の MS_IDX 表記に揃えられる。
    save_path が指定されていれば png として保存する（バッチ実行を止めないよう show() はしない）。
    """
    if data_2d is None or data_2d.size == 0:
        _log(f"No data to plot for {sample}_{target}. Skipped heatmap.", "INFO")
        return np.empty((0, 0))

    if data_2d.ndim != 2:
        raise ValueError("plotting_heatmap_with_zero_gaps: input must be 2D array.")

    n_rows, n_cols = data_2d.shape
    zero_row = np.zeros((1, n_cols))

    new_rows = [zero_row]
    for i in range(n_rows):
        new_rows.append(data_2d[i:i+1, :])
        if i != n_rows - 1:
            new_rows.append(zero_row)
    new_rows.append(zero_row)

    new_data = np.vstack(new_rows)  # 形状: (挿入後の行数, n_cols)
    plot_data = new_data.T  # 行=ビン(=元のn_cols), 列=系列(=挿入後の行数)

    # ヒートマップ
    plt.figure(figsize=(8, 6))
    sns.heatmap(plot_data, annot=False, cmap="YlGnBu")
    plt.title(f"Heatmap for Sample: {sample}, Target: {target}", fontsize=14)

    # y軸目盛り: 実際のビン中心値を使う（bin_edges が渡された場合）。
    # データ範囲外に無駄な余白を作らないよう、常に実データ由来の値に揃える。
    n_bins = plot_data.shape[0]
    if bin_edges is not None and len(bin_edges) - 1 == n_bins:
        y_values = (bin_edges[:-1] + bin_edges[1:]) / 2
    else:
        y_values = np.arange(n_bins)

    tick_indices, tick_labels = _nice_tick_positions_and_labels(y_values, target_num_ticks=10)
    if tick_indices:
        plt.yticks(ticks=tick_indices, labels=tick_labels, rotation=0)
    plt.ylabel("Relative Averaged Data")

    # x軸目盛り: 実際の MS_IDX 値を使う（0行挿入により2つ飛ばしの位置に本データがある）
    if ms_idx_labels is not None:
        n_series = len(ms_idx_labels)
        data_positions = [1 + 2 * i for i in range(n_series)]  # 先頭ゼロ行の次から2つ飛ばし
        step_x = max(1, n_series // 15)
        idxs_to_show = list(range(0, n_series, step_x))
        plt.xticks(
            ticks=[data_positions[i] for i in idxs_to_show],
            labels=[ms_idx_labels[i] for i in idxs_to_show],
            rotation=90,
        )
        plt.xlabel("MS_IDX")

    plt.gca().invert_yaxis()
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150)
        _log(f"Saved heatmap image: {save_path}", "INFO")

    plt.close()

    return plot_data  # 列=系列、行=ビン（元コード互換）

def _grouped_raw_series_by_msidx(df: pd.DataFrame, sample: str, target: str, plot_kind: str):
    """
    boxplot/violinの共通前処理: raw_data を MS_IDX でグルーピングし、
    (ms_idx_sorted, data_by_idx) を返す。データ不足時は (None, None)。
    """
    if df is None or df.empty:
        _log(f"No data to plot for {sample}_{target}. Skipped {plot_kind}.", "INFO")
        return None, None

    required_cols = {"Relative Averaged Data", "MS_IDX"}
    if not required_cols.issubset(df.columns):
        _log(f"Required columns missing for {plot_kind}. Skipped.", "WARN")
        return None, None

    d = df.copy()
    d["Relative Averaged Data"] = pd.to_numeric(d["Relative Averaged Data"], errors="coerce")
    d["MS_IDX"] = pd.to_numeric(d["MS_IDX"], errors="coerce")
    d = d.dropna(subset=["Relative Averaged Data", "MS_IDX"])
    d["MS_IDX"] = d["MS_IDX"].astype(int)

    if d.empty:
        _log(f"No valid rows for {plot_kind} ({sample}_{target}). Skipped.", "INFO")
        return None, None

    ms_idx_sorted = sorted(d["MS_IDX"].unique())
    data_by_idx = [d.loc[d["MS_IDX"] == idx, "Relative Averaged Data"].to_numpy() for idx in ms_idx_sorted]
    valid = [(idx, arr) for idx, arr in zip(ms_idx_sorted, data_by_idx) if arr.size > 0]
    if not valid:
        _log(f"Not enough data points per MS_IDX for {plot_kind} ({sample}_{target}). Skipped.", "INFO")
        return None, None

    ms_idx_sorted, data_by_idx = zip(*valid)
    return list(ms_idx_sorted), list(data_by_idx)

def _apply_ms_idx_xticks(ms_idx_sorted: list) -> None:
    n = len(ms_idx_sorted)
    step = max(1, n // 15)
    positions = list(range(n))
    tick_positions = positions[::step]
    tick_labels = [ms_idx_sorted[i] for i in range(0, n, step)]
    plt.xticks(tick_positions, tick_labels, rotation=90)

def plotting_boxplot_by_msidx(df: pd.DataFrame, sample: str, target: str, save_path: str | None = None) -> None:
    """
    raw_data (final_df) の 'Relative Averaged Data' を 'MS_IDX' ごとにグルーピングし、
    箱ひげ図（boxplot）として描画・保存する（生値ベース）。
    MS_IDX の順番をカラーグラデーションで表現する。
    """
    ms_idx_sorted, data_by_idx = _grouped_raw_series_by_msidx(df, sample, target, "boxplot")
    if ms_idx_sorted is None:
        return

    n = len(ms_idx_sorted)
    positions = list(range(n))

    plt.figure(figsize=(max(8, n * 0.3), 6))
    bp = plt.boxplot(
        data_by_idx,
        positions=positions,
        widths=0.6,
        patch_artist=True,
        showfliers=True,
        medianprops={"color": "black"},
    )

    # MS_IDX の順番をカラーグラデーションで表現
    cmap = plt.cm.viridis
    for i, box in enumerate(bp["boxes"]):
        box.set_facecolor(cmap(i / max(n - 1, 1)))
        box.set_alpha(0.8)

    _apply_ms_idx_xticks(ms_idx_sorted)

    plt.xlabel("MS_IDX")
    plt.ylabel("Relative Averaged Data")
    plt.title(f"Boxplot for Sample: {sample}, Target: {target}", fontsize=14)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150)
        _log(f"Saved boxplot image: {save_path}", "INFO")

    plt.close()

def plotting_violin_by_msidx(df: pd.DataFrame, sample: str, target: str, save_path: str | None = None) -> None:
    """
    raw_data (final_df) の 'Relative Averaged Data' を 'MS_IDX' ごとにグルーピングし、
    バイオリンプロットとして描画・保存する（ヒストグラムのビン化を経由しない生値ベース）。
    MS_IDX の順番をカラーグラデーションで表現する。
    save_path が指定されていれば png として保存する（バッチ実行を止めないよう show() はしない）。
    """
    ms_idx_sorted, data_by_idx = _grouped_raw_series_by_msidx(df, sample, target, "violin plot")
    if ms_idx_sorted is None:
        return

    # violinplot は要素数1以下の系列でエラーになるため除外
    filtered = [(idx, arr) for idx, arr in zip(ms_idx_sorted, data_by_idx) if arr.size > 1]
    if not filtered:
        _log(f"Not enough data points per MS_IDX for violin plot ({sample}_{target}). Skipped.", "INFO")
        return
    ms_idx_sorted, data_by_idx = zip(*filtered)
    ms_idx_sorted, data_by_idx = list(ms_idx_sorted), list(data_by_idx)
    n = len(ms_idx_sorted)
    positions = list(range(n))

    plt.figure(figsize=(max(8, n * 0.3), 6))
    parts = plt.violinplot(data_by_idx, positions=positions, showmedians=True, widths=0.8)

    # MS_IDX の順番をカラーグラデーションで表現
    cmap = plt.cm.viridis
    for i, body in enumerate(parts["bodies"]):
        body.set_facecolor(cmap(i / max(n - 1, 1)))
        body.set_edgecolor("gray")
        body.set_alpha(0.7)

    _apply_ms_idx_xticks(ms_idx_sorted)

    plt.xlabel("MS_IDX")
    plt.ylabel("Relative Averaged Data")
    plt.title(f"Violin plot for Sample: {sample}, Target: {target}", fontsize=14)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150)
        _log(f"Saved violin plot image: {save_path}", "INFO")

    plt.close()

def plotting_histogram_overlay(hist_df: pd.DataFrame, sample: str, target: str, save_path: str | None = None) -> None:
    """
    histogram_table() の出力 (bin_center + MS_IDX_* 列) を使い、
    各 MS_IDX の分布を重ね描きする折れ線グラフを描画・保存する。
    MS_IDX の順番をカラーグラデーションで表現する。
    """
    if hist_df is None or hist_df.empty:
        _log(f"No histogram data to plot for {sample}_{target}. Skipped.", "INFO")
        return

    ms_cols = [c for c in hist_df.columns if c.startswith("MS_IDX_")]
    if not ms_cols:
        _log(f"No MS_IDX columns found for {sample}_{target}. Skipped histogram plot.", "INFO")
        return

    n_series = len(ms_cols)
    cmap = plt.cm.viridis

    plt.figure(figsize=(8, 6))
    for i, col in enumerate(ms_cols):
        color = cmap(i / max(n_series - 1, 1))
        plt.plot(hist_df["bin_center"], hist_df[col], color=color, alpha=0.8, linewidth=1)

    plt.xlabel("Relative Averaged Data (bin center)")
    plt.ylabel("Normalized Frequency")
    plt.title(f"Histogram overlay for Sample: {sample}, Target: {target}")

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(vmin=0, vmax=max(n_series - 1, 1)))
    sm.set_array([])
    plt.colorbar(sm, ax=plt.gca(), label="MS_IDX order (early → late)")

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150)
        _log(f"Saved histogram image: {save_path}", "INFO")

    plt.close()

def split_outliers(
    df: pd.DataFrame,
    column: str = "Relative Averaged Data",
    iqr_k: float = OUTLIER_IQR_K,
    abs_threshold: float = OUTLIER_ABS_THRESHOLD,
):
    """
    df を「正常値のみ」と「外れ値」に分割する。
    判定はサンプルごとの四分位範囲（IQR）から動的に計算するTukeyのフェンス方式
    （下限 = Q1 - iqr_k*IQR, 上限 = Q3 + iqr_k*IQR）を基本とする。
    ただしIQRが極端に狭い/ゼロに近いサンプルでは閾値が過敏になりすぎるため、
    その場合は abs_threshold（絶対値基準）にフォールバックする。
    可視化は正常値側のみを使う想定。
    戻り値: (clean_df, outlier_df, (lower_bound, upper_bound))
    """
    if df.empty or column not in df.columns:
        return df, df.iloc[0:0], (None, None)

    vals = pd.to_numeric(df[column], errors="coerce")
    valid_vals = vals.dropna()

    if valid_vals.empty:
        return df, df.iloc[0:0], (None, None)

    q1, q3 = valid_vals.quantile(0.25), valid_vals.quantile(0.75)
    iqr = q3 - q1

    # IQRが小さすぎる（ほぼ一定値のデータ）場合はフォールバックの絶対値基準を使う
    min_reasonable_iqr = 1e-6
    if iqr < min_reasonable_iqr:
        lower_bound, upper_bound = -abs_threshold, abs_threshold
    else:
        lower_bound = q1 - iqr_k * iqr
        upper_bound = q3 + iqr_k * iqr

    outlier_mask = (vals < lower_bound) | (vals > upper_bound)
    clean_df = df[~outlier_mask].copy()
    outlier_df = df[outlier_mask].copy()
    return clean_df, outlier_df, (lower_bound, upper_bound)

def save_outlier_report(
    outlier_df: pd.DataFrame,
    result_dir: str,
    sample: str,
    target: str,
    total_rows: int,
    bounds: tuple = (None, None),
    column: str = "Relative Averaged Data",
) -> str | None:
    """
    除外した外れ値の詳細行をCSVとして保存し、簡単なサマリーをログに出す。
    保存先: {result_dir}/outliers_report.csv
    """
    if outlier_df is None or outlier_df.empty:
        _log(f"No outliers detected for {sample}_{target}.", "INFO")
        return None

    report_path = os.path.join(result_dir, "outliers_report.csv")
    outlier_df.to_csv(report_path, index=False)

    n_outliers = len(outlier_df)
    ratio = (n_outliers / total_rows * 100) if total_rows > 0 else 0.0

    # 環境によって列がobject dtype（文字列混在）のまま残ることがあるため、
    # ログ表示用に明示的に数値へ変換してからmin/maxを取る（フォーマットエラー防止）
    numeric_vals = pd.to_numeric(outlier_df[column], errors="coerce").dropna()
    if not numeric_vals.empty:
        val_min = float(numeric_vals.min())
        val_max = float(numeric_vals.max())
        val_range_str = f"[{val_min:.3f}, {val_max:.3f}]"
    else:
        val_range_str = "N/A"

    lower_bound, upper_bound = bounds
    bound_str = f"[{lower_bound:.4f}, {upper_bound:.4f}]" if lower_bound is not None else "N/A"
    _log(
        f"Outliers excluded [{sample}_{target}]: {n_outliers}/{total_rows} rows "
        f"({ratio:.2f}%), value range {val_range_str}, "
        f"acceptable range={bound_str} (IQR k={OUTLIER_IQR_K})",
        "WARN",
    )
    _log(f"Saved outlier report: {report_path}", "INFO")
    return report_path

def compute_bin_edges(series: pd.Series, bin_width: float = 0.025, pad_bins: int = 2) -> np.ndarray:
    """
    実データの min/max から動的にビン境界を計算する。
    bin_width の倍数に切り捨て/切り上げし、pad_bins 分だけ余白（見切れ防止）を足す。
    データが空の場合は従来の固定範囲 (-0.1〜3.4) にフォールバックする。
    """
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.arange(-0.1, 3.4 + bin_width, bin_width)

    data_min = float(s.min())
    data_max = float(s.max())
    lo = np.floor(data_min / bin_width) * bin_width - pad_bins * bin_width
    hi = np.ceil(data_max / bin_width) * bin_width + pad_bins * bin_width
    return np.arange(lo, hi + bin_width, bin_width)

def histogram_matrix(df: pd.DataFrame, bin_edges: np.ndarray | None = None) -> np.ndarray:
    """
    df から 'Relative Averaged Data' と 'MS_IDX' を使い、
    各 MS_IDX ごとの正規化ヒストグラム行列（行=MS_IDX, 列=ビン）を返す。
    bin_edges を省略した場合は実データ範囲から動的に計算する（compute_bin_edges）。
    ヒートマップ描画（plotting_heatmap_with_zero_gaps）の入力として使用する。
    """
    if df.empty:
        return np.empty((0, 0))

    required_cols = {"Relative Averaged Data", "MS_IDX"}
    if not required_cols.issubset(df.columns):
        _log("Required columns missing for histogram. Skipped.", "WARN")
        return np.empty((0, 0))

    df = df.copy()
    df["Relative Averaged Data"] = pd.to_numeric(df["Relative Averaged Data"], errors="coerce")
    df["MS_IDX"] = pd.to_numeric(df["MS_IDX"], errors="coerce")
    df = df.dropna(subset=["Relative Averaged Data", "MS_IDX"])
    df["MS_IDX"] = df["MS_IDX"].astype(int)

    if df.empty:
        return np.empty((0, 0))

    grouped = df.groupby("MS_IDX")["Relative Averaged Data"].apply(lambda x: x.dropna().to_numpy())
    if grouped.empty:
        return np.empty((0, 0))

    bin_width = 0.025
    if bin_edges is None:
        bin_edges = compute_bin_edges(df["Relative Averaged Data"], bin_width=bin_width)

    rows = []
    for idx in grouped.sort_index().index:
        arr = grouped.loc[idx]
        if arr.size == 0:
            rows.append(np.zeros(len(bin_edges) - 1))
            continue
        counts, _ = np.histogram(arr, bins=bin_edges)
        total = counts.sum()
        rows.append(counts / total if total > 0 else counts)

    if len(rows) == 0:
        return np.empty((0, 0))

    return np.vstack(rows)

def histogram_table(df: pd.DataFrame, bin_edges: np.ndarray | None = None) -> pd.DataFrame:
    """
    各 MS_IDX ごとの正規化ヒストグラムを、
    bin_left, bin_right, bin_center を含む DataFrame として返す。
    bin_edges を省略した場合は実データ範囲から動的に計算する（compute_bin_edges）。
    """
    if df.empty:
        return pd.DataFrame()

    required_cols = {"Relative Averaged Data", "MS_IDX"}
    if not required_cols.issubset(df.columns):
        _log("Required columns missing for histogram table. Skipped.", "WARN")
        return pd.DataFrame()

    df = df.copy()
    df["Relative Averaged Data"] = pd.to_numeric(df["Relative Averaged Data"], errors="coerce")
    df["MS_IDX"] = pd.to_numeric(df["MS_IDX"], errors="coerce")
    df = df.dropna(subset=["Relative Averaged Data", "MS_IDX"])
    df["MS_IDX"] = df["MS_IDX"].astype(int)

    if df.empty:
        return pd.DataFrame()

    grouped = df.groupby("MS_IDX")["Relative Averaged Data"].apply(lambda x: x.dropna().to_numpy())
    if grouped.empty:
        return pd.DataFrame()

    bin_width = 0.025
    if bin_edges is None:
        bin_edges = compute_bin_edges(df["Relative Averaged Data"], bin_width=bin_width)

    out = pd.DataFrame({
        "bin_left": bin_edges[:-1],
        "bin_right": bin_edges[1:],
        "bin_center": (bin_edges[:-1] + bin_edges[1:]) / 2
    })

    for idx in grouped.sort_index().index:
        arr = grouped.loc[idx]
        counts, _ = np.histogram(arr, bins=bin_edges)
        total = counts.sum()
        hist = counts / total if total > 0 else counts
        out[f"MS_IDX_{idx}"] = hist

    return out

# ============= パス検索 =============
def bnal_folder(server: str, keyfolder: str, ex: str, sample: str, target: str) -> List[str]:
    """
    BNAL@<target> 配下の .tdms ファイル一覧を返す
    UNC例: //Rackstation/analysis/Kiyotani/0237/0237_10k_Sample/T/BNAL@<target>/*.tdms
    """
    server_path = f"//{server}/{keyfolder}/"
    data_path = f"{ex}/{sample}/"
    sample_path = f"{sample}_10k_Sample"
    target_path = f"BNAL@{target}"
    pattern = os.path.join(server_path, data_path, sample_path, "T", target_path, "*.tdms")
    return glob.glob(pattern)

def list_targets(server: str, keyfolder: str, ex: str, sample: str) -> List[str]:
    """
    //server/keyfolder/ex/sample/sample_10k_Sample/T/ 下の BNAL@* フォルダから target 名一覧を返す
    """
    server_path = f"//{server}/{keyfolder}/"
    data_path = f"{ex}/{sample}/"
    sample_path = f"{sample}_10k_Sample"
    base_dir = Path(server_path) / data_path / sample_path / "T"
    if not base_dir.exists():
        _log(f"T directory not found: {base_dir}", "ERROR")
        return []
    targets = [p.name.replace("BNAL@", "") for p in base_dir.iterdir() if p.is_dir() and "BNAL@" in p.name]
    return sorted(targets)

# ============= メイン =============
if __name__ == "__main__":
    start_all = time.time()

    # 設定
    server = "Rackstation"
    keyfolder = "analysis"
    ex = "Takahagi_Zenoamino"
    sample = "KF4"

    # 出力フォルダ（heat_data 直下に「タイムスタンプ_ex_sample_target」フォルダを作成する）
    # ここを変えれば保存先を変更可能（例: Path(r"D:\Python_VScode\Viewer\heat_data")）
    OUTPUT_BASE_DIR = str(SCRIPT_DIR / "heat_data")
    os.makedirs(OUTPUT_BASE_DIR, exist_ok=True)
    run_timestamp = datetime.now().strftime("%Y%m%d%H%M")  # 実行開始時に1回だけ取得（全target共通）

    # 対象ターゲット一覧
    targets = list_targets(server, keyfolder, ex, sample)
    if not targets:
        _log("No targets found. Abort.", "ERROR")
        raise SystemExit(1)

    _log(f"Found targets ({len(targets)}): {', '.join(targets)}")

    # チャンネル
    required_channels = ["Relative Averaged Data", "MS_IDX"]
    optional_channels = ["Assigned Base", "Base Time length [ms]"]
    target_group = "LAP Table"
    channel_list = required_channels + optional_channels

    # メトリクス集計
    grand_total_files = 0
    grand_success_files = 0
    grand_skipped_required_missing = 0
    grand_group_missing = 0
    grand_errors = 0
    generated_csvs: List[str] = []
    generated_heat_csvs: List[str] = []
    generated_heat_png_files: List[str] = []
    generated_boxplot_png_files: List[str] = []
    generated_violin_png_files: List[str] = []
    generated_hist_csvs: List[str] = []
    generated_hist_png_files: List[str] = []
    generated_outlier_reports: List[str] = []

    # ターゲットごと処理
    for target in targets:
        _log(f"=== Target: {target} ===")

        filelist = bnal_folder(server, keyfolder, ex, sample, target)
        n_files = len(filelist)
        if n_files == 0:
            _log(f"No TDMS files for target '{target}'. Skipped.", "WARN")
            continue

        _log(f"TDMS files: {n_files}", "INFO")

        dfs = []
        t0_target = time.time()

        success_files = 0
        skipped_required_missing = 0
        group_missing = 0
        errors = 0

        for tdms_path in tqdm(filelist, desc=f"[{target}] reading", unit="file"):
            fname = os.path.basename(tdms_path)
            t0 = time.time()
            df = None

            try:
                data = tdms_data_checker_multi_channel(tdms_path, target_group, channel_list)
                if not data:
                    # グループ欠如 or 読み込み失敗時に data == {}
                    group_missing += 1
                    _log(f"Skipped (group missing or open error) -> {fname}", "DEBUG")
                    continue

                # 必須列が揃っているか（Noneが含まれていないか）
                if any(data.get(col) is None for col in required_channels):
                    skipped_required_missing += 1
                    miss_cols = [c for c in required_channels if data.get(c) is None]
                    _log(f"Skipped (required missing: {miss_cols}) -> {fname}", "DEBUG")
                    continue

                # DataFrame化（欠損列は空配列で埋める）
                row_dict = {col: (data[col] if data.get(col) is not None else []) for col in channel_list}
                df = pd.DataFrame(row_dict)
                if df.empty:
                    _log(f"Empty DataFrame -> {fname}", "DEBUG")
                else:
                    dfs.append(df)
                    success_files += 1

            except Exception as e:
                errors += 1
                _log(f"Exception during read -> {fname}: {e}", "ERROR")

            finally:
                elapsed = time.time() - t0
                n_rows = 0 if df is None else len(df)
                _log(f"Read {fname} in {_fmt_sec(elapsed)} (rows: {n_rows})", "DEBUG")

        # ターゲット集計
        elapsed_target = time.time() - t0_target
        grand_total_files += n_files
        grand_success_files += success_files
        grand_skipped_required_missing += skipped_required_missing
        grand_group_missing += group_missing
        grand_errors += errors

        _log(
            f"Target summary [{target}] "
            f"files={n_files}, ok={success_files}, "
            f"req-miss={skipped_required_missing}, grp-miss={group_missing}, err={errors}, "
            f"time={_fmt_sec(elapsed_target)}",
            "INFO",
        )

        if not dfs:
            _log(f"No valid data for target '{target}'. Skipped aggregation.", "INFO")
            continue

        final_df = pd.concat(dfs, ignore_index=True)

        # 複数TDMSファイルを結合する過程で列の dtype が object（文字列混在）になることが
        # あるため、以降の計算・フォーマットでエラーにならないよう明示的に数値へ揃える。
        # （TDMS側の型が微妙に食い違う場合の防御策。値そのものは変更しない）
        final_df["Relative Averaged Data"] = pd.to_numeric(final_df["Relative Averaged Data"], errors="coerce")
        if "MS_IDX" in final_df.columns:
            final_df["MS_IDX"] = pd.to_numeric(final_df["MS_IDX"], errors="coerce")

        # このtarget用の結果フォルダ: heat_data/{timestamp}_{ex}_{sample}_{target}/
        result_dir = make_result_dir(OUTPUT_BASE_DIR, run_timestamp, ex, sample, target)

        # CSV保存（生データ結合、外れ値を含む全件をそのまま保存）
        out_csv = os.path.join(result_dir, "raw_data.csv")
        final_df.to_csv(out_csv, index=False)
        generated_csvs.append(out_csv)
        _log(f"Saved: {out_csv}", "INFO")

        # 外れ値を分離（可視化からは除外し、内容は outliers_report.csv に記録）
        # 暫定対応: 根本原因はBNAL生成コード側の書き直しで対応予定（OUTLIER_IQR_K 参照）
        clean_df, outlier_df, outlier_bounds = split_outliers(final_df)
        outlier_report_path = save_outlier_report(outlier_df, result_dir, sample, target, total_rows=len(final_df), bounds=outlier_bounds)
        if outlier_report_path:
            generated_outlier_reports.append(outlier_report_path)

        if clean_df.empty:
            _log(f"All rows were outliers for {sample}_{target}. Visualization skipped.", "WARN")
            continue

        # ビン境界を実データ範囲（外れ値除外後）から動的に計算し、ヒストグラム表・ヒートマップで共有する
        # （固定範囲だと実データより広すぎる/狭すぎる軸になり、boxplot/violinと見た目が揃わないため）
        shared_bin_edges = compute_bin_edges(clean_df["Relative Averaged Data"])

        # ヒストグラム表を保存（各x軸ビンごとの値、外れ値除外後のデータで計算）
        hist_df = histogram_table(clean_df, bin_edges=shared_bin_edges)
        if not hist_df.empty:
            hist_path = os.path.join(result_dir, "histogram_table.csv")
            hist_df.to_csv(hist_path, index=False)
            generated_hist_csvs.append(hist_path)
            _log(f"Saved histogram table: {hist_path}", "INFO")

            # ヒストグラムの可視化（重ね描き折れ線グラフ）を画像として保存
            hist_png_path = os.path.join(result_dir, "histogram.png")
            plotting_histogram_overlay(hist_df, sample, target, save_path=hist_png_path)
            if os.path.exists(hist_png_path):
                generated_hist_png_files.append(hist_png_path)
        else:
            _log(f"Histogram table empty for {target}.", "INFO")

        # バイオリンプロットの描画（外れ値除外後の生値ベース、png画像として result_dir に保存）
        violin_path = os.path.join(result_dir, "violin.png")
        plotting_violin_by_msidx(clean_df, sample, target, save_path=violin_path)
        if os.path.exists(violin_path):
            generated_violin_png_files.append(violin_path)

        # boxplotの描画（外れ値除外後の生値ベース、png画像として result_dir に保存）
        box_path = os.path.join(result_dir, "boxplot.png")
        plotting_boxplot_by_msidx(clean_df, sample, target, save_path=box_path)
        if os.path.exists(box_path):
            generated_boxplot_png_files.append(box_path)

        # ヒスト行列生成（ヒートマップ描画の入力。ヒストグラム表と同じビン境界を使う）
        n_data = histogram_matrix(clean_df, bin_edges=shared_bin_edges)
        if n_data.size == 0:
            _log(f"Histogram matrix empty for {target}. Heatmap skipped.", "INFO")
            continue

        # ヒートマップ＋0行挿入の描画（png画像として result_dir に保存）
        # bin_edges / ms_idx_labels を渡すことで、boxplot・violinと同じ実データの軸に揃える
        ms_idx_labels = sorted(pd.to_numeric(clean_df["MS_IDX"], errors="coerce").dropna().astype(int).unique().tolist())
        heat_png_path = os.path.join(result_dir, "heatmap.png")
        plotted = plotting_heatmap_with_zero_gaps(
            n_data, sample, target,
            bin_edges=shared_bin_edges,
            ms_idx_labels=ms_idx_labels,
            save_path=heat_png_path,
        )
        if os.path.exists(heat_png_path):
            generated_heat_png_files.append(heat_png_path)

        # ヒートマップ元データ保存
        if plotted.size > 0:
            p_df = pd.DataFrame(plotted)
            p_path = os.path.join(result_dir, "heatmap_matrix.csv")
            p_df.to_csv(p_path, index=False)
            generated_heat_csvs.append(p_path)
            _log(f"Saved heatmap matrix: {p_path}", "INFO")

    # ===== 全体サマリー =====
    total_elapsed = time.time() - start_all
    _log("=== Run summary ===")
    _log(f"Targets processed : {len(targets)}")
    _log(f"Files total      : {grand_total_files}")
    _log(f"  OK             : {grand_success_files}")
    _log(f"  Req-missing    : {grand_skipped_required_missing}")
    _log(f"  Group-missing  : {grand_group_missing}")
    _log(f"  Errors         : {grand_errors}")
    _log(f"CSV generated    : {len(generated_csvs)}")
    _log(f"Hist CSVs        : {len(generated_hist_csvs)}")
    _log(f"Hist PNGs        : {len(generated_hist_png_files)}")
    _log(f"Violin PNGs      : {len(generated_violin_png_files)}")
    _log(f"Boxplot PNGs     : {len(generated_boxplot_png_files)}")
    _log(f"Heat CSVs        : {len(generated_heat_csvs)}")
    _log(f"Heat PNGs        : {len(generated_heat_png_files)}")
    _log(f"Outlier reports  : {len(generated_outlier_reports)}")
    _log(f"Total time       : {_fmt_sec(total_elapsed)}")
    _log("end", "INFO")