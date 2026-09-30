# -*- coding: utf-8 -*-
"""ML/app/lib/mix_summary.py

混合サンプル予測（predict_mix_*.run_prediction）が保存したイベント単位の結果
predict_<混合サンプル>_events.csv を読み込み、
  - 全体（混合サンプルごとの総計）
  - tdmsファイルごと
  - ファイル内の N 秒ごと
のいずれかの単位でクラス比率を集計する。
予測自体はやり直さず、保存済みのイベントCSVから何度でも集計し直せる。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

import common.paths as paths

UNIT_TOTAL = "total"
UNIT_FILE = "file"
UNIT_WINDOW = "window"

# dataviz の検証済みカテゴリカルパレット（ライトモード、固定順で割り当てる）
CATEGORICAL_COLORS = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]
OTHER_COLOR = "#8a8a85"
OTHER_LABEL = "その他"


def list_mix_runs() -> list[dict]:
    """results/<feature_set>/<algorithm>/<timestamp>_mix_*/ を新しい順に返す。

    CLI実行で run_manifest.json が無い古い結果も、predict_*.csv があれば拾う。
    戻り値の各要素: {"run_dir", "feature_set", "algorithm", "manifest"}
    """
    runs = []
    for run_dir in paths.RESULTS_ROOT.glob("*/*/*_mix_*"):
        if not run_dir.is_dir() or not any(run_dir.glob("predict_*.csv")):
            continue
        manifest_path = run_dir / "run_manifest.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        runs.append({
            "run_dir": run_dir,
            "feature_set": run_dir.parent.parent.name,
            "algorithm": run_dir.parent.name,
            "manifest": manifest,
        })
    runs.sort(key=lambda r: r["run_dir"].name, reverse=True)
    return runs


def load_events(run_dir: Path) -> pd.DataFrame:
    """run_dir 内の predict_*_events.csv をすべて結合して返す（無ければ空）。"""
    frames = [pd.read_csv(p) for p in sorted(Path(run_dir).glob("predict_*_events.csv"))]
    if not frames:
        return pd.DataFrame()
    events = pd.concat(frames, ignore_index=True)
    events["file"] = events["file"].astype(str)
    events["pred_label"] = events["pred_label"].astype(str)
    return events


def group_keys(unit: str) -> list[str]:
    if unit == UNIT_TOTAL:
        return ["mix_sample"]
    if unit == UNIT_FILE:
        return ["mix_sample", "file"]
    return ["mix_sample", "file", "window_start_s"]


def summarize(events: pd.DataFrame, classes: list[str], unit: str,
              window_sec: float = 10.0, pmax_threshold: float | None = None,
              as_percent: bool = True) -> pd.DataFrame:
    """イベント単位の予測結果を unit ごとに集計し、クラス列を持つ横長の表を返す。

    classes        : 列に並べるクラス名（モデルのクラス順）
    pmax_threshold : 指定すると pmax >= 閾値 のイベントだけで集計する（高信頼度版）
    as_percent     : True=割合(%)、False=件数
    戻り値の列: <group_keys> + count (+ count_excluded) + classes + mean_pmax
    """
    df = events
    if unit == UNIT_WINDOW:
        df = df.assign(window_start_s=np.floor(df["time_s"] / window_sec) * window_sec)
    keys = group_keys(unit)

    total_counts = df.groupby(keys).size().rename("count_all")
    if pmax_threshold is not None:
        df = df[df["pmax"] >= pmax_threshold]

    counts = pd.crosstab([df[k] for k in keys], df["pred_label"])
    counts = counts.reindex(columns=classes, fill_value=0)
    counts = counts.reindex(total_counts.index, fill_value=0)
    n_used = counts.sum(axis=1)

    if as_percent:
        values = counts.div(n_used.replace(0, np.nan), axis=0).mul(100).round(2)
    else:
        values = counts

    table = pd.DataFrame({"count": n_used})
    if pmax_threshold is not None:
        table["count_excluded"] = total_counts - n_used
    table = table.join(values)
    table["mean_pmax"] = df.groupby(keys)["pmax"].mean().round(4)
    return table.reset_index()


def chart_classes(classes: list[str]) -> tuple[list[str], dict[str, str]]:
    """グラフ用のクラス一覧と色の対応を返す。

    パレットは8色まで。9クラス以上ある場合は先頭7クラス＋「その他」にまとめる
    （色を循環させて別クラスに同じ色を使うのを避けるため）。
    """
    if len(classes) <= len(CATEGORICAL_COLORS):
        shown = list(classes)
        colors = dict(zip(shown, CATEGORICAL_COLORS))
    else:
        shown = list(classes[:7]) + [OTHER_LABEL]
        colors = dict(zip(classes[:7], CATEGORICAL_COLORS))
        colors[OTHER_LABEL] = OTHER_COLOR
    return shown, colors


def to_long_for_chart(table: pd.DataFrame, classes: list[str], keys: list[str]) -> pd.DataFrame:
    """summarize() の表を、グラフ描画用の縦長形式（keys, class, value）に変換する。"""
    shown, _ = chart_classes(classes)
    wide = table[keys + classes].copy()
    if OTHER_LABEL in shown:
        folded = [c for c in classes if c not in shown]
        wide[OTHER_LABEL] = wide[folded].sum(axis=1)
        wide = wide.drop(columns=folded)
    return wide.melt(id_vars=keys, value_vars=shown, var_name="class", value_name="value")
