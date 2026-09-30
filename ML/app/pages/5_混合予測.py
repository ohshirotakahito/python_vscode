# -*- coding: utf-8 -*-
"""ML/app/pages/5_混合予測.py

純粋分子サンプルで学習したモデルを使い、混合サンプルの各イベントを分類して
クラスごとの割合（カウント）を表示するページ。

predict_mix_xgboost_tsfresh.run_prediction() / predict_mix_xgboost_traditional.run_prediction()
を呼び出す。予測結果はイベント単位で predict_<混合サンプル>_events.csv に保存されるため、
「全体 / ファイルごと / ファイル内N秒ごと」の切り替えは予測をやり直さずに行える。
"""

import importlib
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
_ML_DIR = _APP_DIR.parent
for _p in (_ML_DIR, _APP_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd
import plotly.express as px
import streamlit as st

from lib.catalog import list_available_samples
from lib.mix_summary import (
    UNIT_FILE, UNIT_TOTAL, UNIT_WINDOW, chart_classes, group_keys, list_mix_runs,
    load_events, summarize, to_long_for_chart,
)
from lib.progress import StreamlitProgress
from lib.runs import make_zip_bytes

st.set_page_config(page_title="混合予測", page_icon="🧮", layout="wide")
st.title("⑤ 混合予測（カウント）")
st.caption(
    "純粋分子サンプルで学習したモデルで、混合サンプルの各イベントを分類し、"
    "クラスごとの割合を表示します。"
)

PIPELINES = {
    "rmc + XGBoost（tsfresh併用）": {"feature_set": "rmc", "module": "predict_mix_xgboost_tsfresh"},
    "rmb + XGBoost（波形12点特徴量のみ）": {"feature_set": "rmb", "module": "predict_mix_xgboost_traditional"},
}

# =====================
# 1. 予測の実行
# =====================
with st.expander("新しく予測を実行する", expanded=not list_mix_runs()):
    pipeline_label = st.selectbox("パイプライン", list(PIPELINES.keys()))
    pipeline = PIPELINES[pipeline_label]

    available = list_available_samples(pipeline["feature_set"])
    if not available:
        st.warning(
            f"data/features/{pipeline['feature_set']}/ に抽出済みのサンプルがありません。"
            "先に「① 特徴量抽出」で純粋分子と混合サンプルの両方を抽出してください。"
        )

    smns = st.multiselect("学習に使う純粋分子（クラス）", options=available)
    test_smns = st.multiselect(
        "予測する混合サンプル（複数可）", options=[s for s in available if s not in smns],
    )
    col1, col2 = st.columns(2)
    threshold = col1.slider(
        "高信頼度とみなす pmax（予測確率の最大値）の閾値", 0.5, 0.99, 0.8, 0.01,
    )
    retrain = col2.checkbox(
        "モデルを強制的に再学習する",
        help="通常は、学習データが変わっていなければ保存済みモデルを再利用します。",
    )

    can_run = len(smns) >= 2 and len(test_smns) >= 1
    if not can_run:
        st.caption("純粋分子を2つ以上、混合サンプルを1つ以上選んでください。")

    if st.button("予測を実行", type="primary", disabled=not can_run):
        module = importlib.import_module(pipeline["module"])
        progress = StreamlitProgress(label="進捗")
        try:
            with st.spinner("予測中...（初回はモデル学習・特徴量計算に時間がかかります）"):
                run_dir = module.run_prediction(
                    smns, test_smns, retrain=retrain, threshold=threshold,
                    progress_callback=progress,
                )
        except Exception as e:  # noqa: BLE001 - UIにそのままエラー内容を出す
            st.exception(e)
        else:
            st.session_state.predict_run_dir = str(run_dir)
            st.success(f"完了しました。保存先: {run_dir}")

# =====================
# 2. 結果の表示
# =====================
runs = list_mix_runs()
if not runs:
    st.info("まだ予測結果がありません。上の「新しく予測を実行する」から実行してください。")
    st.stop()

labels = [
    f"{r['run_dir'].name}  |  {r['feature_set']}/{r['algorithm']}"
    + (f"  |  混合: {', '.join(r['manifest'].get('test_smns', []))}" if r["manifest"] else "")
    for r in runs
]
default_index = 0
last_run = st.session_state.get("predict_run_dir")
for i, r in enumerate(runs):
    if str(r["run_dir"]) == last_run:
        default_index = i
selected_label = st.selectbox("表示する予測結果", labels, index=default_index)
run = runs[labels.index(selected_label)]
run_dir = run["run_dir"]
manifest = run["manifest"]

st.subheader("結果")
st.caption(str(run_dir))
if manifest:
    status = {"trained": "新規学習したモデル", "reused": "保存済みモデルを再利用"}.get(
        manifest.get("model_status"), "")
    st.caption(f"クラス: {', '.join(manifest.get('class_names', []))}　／　{status}")

events = load_events(run_dir)

if events.empty:
    # イベントCSVが無い古い実行結果（ファイルごとの集計CSVのみ）
    st.info("この結果にはイベント単位のデータが無いため、ファイルごとの集計表のみ表示します。")
    for csv_path in sorted(run_dir.glob("predict_*.csv")):
        st.markdown(f"**{csv_path.name}**")
        st.dataframe(pd.read_csv(csv_path), use_container_width=True)
else:
    classes = manifest.get("class_names") or sorted(events["pred_label"].unique())

    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    unit_label = c1.radio(
        "集計単位", ["全体（混合サンプルごと）", "ファイルごと", "ファイル内の一定秒数ごと"],
        horizontal=True,
    )
    unit = {"全体（混合サンプルごと）": UNIT_TOTAL, "ファイルごと": UNIT_FILE,
            "ファイル内の一定秒数ごと": UNIT_WINDOW}[unit_label]
    window_sec = c2.number_input("区間の長さ[秒]", min_value=1.0, value=10.0, step=1.0,
                                 disabled=unit != UNIT_WINDOW)
    value_label = c3.radio("表示", ["割合(%)", "件数"], horizontal=True)
    as_percent = value_label == "割合(%)"
    high_only = c4.checkbox(
        f"高信頼度のみ（pmax ≥ {manifest.get('pmax_threshold', 0.8)}）",
        help="予測確率の最大値が閾値以上のイベントだけで集計します。",
    )
    pmax_threshold = manifest.get("pmax_threshold", 0.8) if high_only else None

    table = summarize(events, classes, unit, window_sec=window_sec,
                      pmax_threshold=pmax_threshold, as_percent=as_percent)
    keys = group_keys(unit)

    if unit == UNIT_WINDOW:
        file_options = sorted(table["file"].unique())
        chosen_file = st.selectbox("グラフに表示するファイル", file_options)
        chart_table = table[table["file"] == chosen_file]
    else:
        chart_table = table

    long_df = to_long_for_chart(chart_table, classes, keys)
    shown, colors = chart_classes(classes)
    value_title = "割合 (%)" if as_percent else "イベント数"

    if unit == UNIT_WINDOW:
        long_df["区間開始[秒]"] = long_df["window_start_s"]
        fig = px.bar(long_df, x="区間開始[秒]", y="value", color="class",
                     category_orders={"class": shown}, color_discrete_map=colors,
                     labels={"value": value_title, "class": "クラス"})
        fig.update_xaxes(dtick=window_sec if len(chart_table) <= 30 else None)
    else:
        long_df["対象"] = long_df[keys].astype(str).agg(" / ".join, axis=1)
        fig = px.bar(long_df, y="対象", x="value", color="class", orientation="h",
                     category_orders={"class": shown}, color_discrete_map=colors,
                     labels={"value": value_title, "class": "クラス", "対象": ""})
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=max(260, 32 * long_df["対象"].nunique() + 120))

    fig.update_traces(marker_line_width=1, marker_line_color="rgba(255,255,255,0.9)")
    fig.update_layout(barmode="stack", bargap=0.25, legend_title_text="クラス",
                      legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0),
                      margin=dict(l=10, r=10, t=40, b=10))
    if as_percent:
        (fig.update_xaxes if unit != UNIT_WINDOW else fig.update_yaxes)(range=[0, 100])
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(table, use_container_width=True, hide_index=True)

    suffix = {UNIT_TOTAL: "total", UNIT_FILE: "by_file",
              UNIT_WINDOW: f"by_{window_sec:g}s"}[unit]
    suffix += "_highconf" if high_only else ""
    suffix += "_pct" if as_percent else "_count"
    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "この集計表をCSVダウンロード",
        data=table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{run_dir.name}_{suffix}.csv", mime="text/csv",
    )
    dl2.download_button(
        "結果フォルダをZIPダウンロード", data=make_zip_bytes(run_dir),
        file_name=f"{run_dir.name}.zip", mime="application/zip",
    )
