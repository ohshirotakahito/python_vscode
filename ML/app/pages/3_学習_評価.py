# -*- coding: utf-8 -*-
"""ML/app/pages/3_学習_評価.py

クラスの組み合わせを指定して識別モデルを学習し、精度・混同行列・重要度などの
指標を確認、結果をダウンロードできるページ。

train_xgboost_tsfresh.run_analysis() / train_lightgbm_tsfresh.run_analysis() /
train_xgboost_traditional.run_analysis() を呼び出す
（いずれも progress_callback 引数で進捗を通知できるようリファクタ済み）。
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
import streamlit as st

from lib.catalog import list_available_samples
from lib.progress import StreamlitProgress
from lib.runs import make_zip_bytes, run_zip_path

st.set_page_config(page_title="学習・評価", page_icon="🧠", layout="wide")
st.title("③ 学習・評価")
st.caption("クラスの組み合わせを指定して識別モデルを学習し、精度や混同行列を確認します。")

PIPELINES = {
    "rmc + XGBoost（tsfresh併用）": {
        "feature_set": "rmc", "module": "train_xgboost_tsfresh", "tsfresh": True,
    },
    "rmc + LightGBM（tsfresh併用）": {
        "feature_set": "rmc", "module": "train_lightgbm_tsfresh", "tsfresh": True,
    },
    "rmb + XGBoost（波形12点特徴量のみ）": {
        "feature_set": "rmb", "module": "train_xgboost_traditional", "tsfresh": False,
    },
}

pipeline_label = st.selectbox("パイプライン", list(PIPELINES.keys()))
pipeline = PIPELINES[pipeline_label]

available_samples = list_available_samples(pipeline["feature_set"])
if not available_samples:
    st.warning(
        f"data/features/{pipeline['feature_set']}/ に抽出済みのサンプルが見つかりません。"
        "先に「① 特徴量抽出」を実行してください。"
    )

if "combinations" not in st.session_state:
    st.session_state.combinations = [[]]

st.subheader("クラスの組み合わせ")
st.caption("1行が1回の学習・評価に対応します。「組み合わせを追加」で比較したい条件を増やせます。")

for i in range(len(st.session_state.combinations)):
    cols = st.columns([8, 1])
    st.session_state.combinations[i] = cols[0].multiselect(
        f"組み合わせ {i + 1}", options=available_samples,
        default=st.session_state.combinations[i], key=f"combo_{i}",
    )
    if cols[1].button("削除", key=f"remove_{i}") and len(st.session_state.combinations) > 1:
        st.session_state.combinations.pop(i)
        st.rerun()

if st.button("＋ 組み合わせを追加"):
    st.session_state.combinations.append([])
    st.rerun()

fc_mode_label = None
use_tsfresh_selection = True
if pipeline["tsfresh"]:
    with st.expander("tsfresh設定（任意）"):
        fc_mode_label = st.radio(
            "FCパラメータ（特徴量の種類・計算量）",
            ["Minimal（高速・推奨）", "Efficient（低速・特徴量多数）"],
            horizontal=True,
        )
        use_tsfresh_selection = st.checkbox("tsfresh特徴量選択を使う", value=True)

combinations = [combo for combo in st.session_state.combinations if combo]
run = st.button("学習を実行", type="primary", disabled=not combinations)

if run:
    module = importlib.import_module(pipeline["module"])

    if pipeline["tsfresh"]:
        from tsfresh.feature_extraction import EfficientFCParameters, MinimalFCParameters

        if fc_mode_label is not None and fc_mode_label.startswith("Efficient"):
            fc_parameters, fc_mode = EfficientFCParameters(), "efficient"
        else:
            fc_parameters, fc_mode = MinimalFCParameters(), "minimal"

        # build_combined_dataset() などは common/data_pipeline.py 側のモジュール変数
        # (dp.FC_PARAMETERS) を参照するため、そちらも合わせて上書きする必要がある。
        module.FC_PARAMETERS = fc_parameters
        module.dp.FC_PARAMETERS = fc_parameters
        if hasattr(module, "FC_PARAMETERS_MODE"):
            module.FC_PARAMETERS_MODE = fc_mode
        module.USE_TSFRESH_FEATURE_SELECTION = use_tsfresh_selection

    outer_progress = StreamlitProgress(label="組み合わせ全体")
    results = []
    for idx, smns in enumerate(combinations):
        outer_progress(idx, len(combinations), f"{', '.join(smns)} を実行中")
        st.markdown(f"#### 組み合わせ {idx + 1}/{len(combinations)}: {', '.join(smns)}")
        inner_progress = StreamlitProgress(label="この組み合わせの進捗")
        try:
            with st.spinner("学習・評価中..."):
                run_dir = module.run_analysis(smns, progress_callback=inner_progress)
        except Exception as e:  # noqa: BLE001
            st.exception(e)
            continue
        results.append((smns, run_dir))
    outer_progress(len(combinations), len(combinations), "完了")
    st.success(f"{len(results)}/{len(combinations)} 件の学習が完了しました。")

    for smns, run_dir in results:
        st.markdown(f"### 結果: {', '.join(smns)}")
        st.caption(str(run_dir))

        report_path = run_dir / "classification_report.txt"
        if report_path.exists():
            st.text(report_path.read_text(encoding="utf-8"))

        comparison_path = run_dir / "step1_feature_set_comparison.csv"
        if comparison_path.exists():
            st.dataframe(pd.read_csv(comparison_path, index_col=0))

        cm_paths = [run_dir / "confusion_matrix.png", run_dir / "confusion_matrix_normalized.png"]
        cm_paths = [p for p in cm_paths if p.exists()]
        if cm_paths:
            cm_cols = st.columns(len(cm_paths))
            for col, img_path in zip(cm_cols, cm_paths):
                col.image(str(img_path), caption=img_path.name, use_column_width=True)

        other_images = sorted(
            p for p in run_dir.glob("*.png") if p not in cm_paths
        )
        if other_images:
            with st.expander(f"その他の解析画像（{len(other_images)}件）"):
                gallery_cols = st.columns(3)
                for i, img_path in enumerate(other_images):
                    gallery_cols[i % 3].image(str(img_path), caption=img_path.name, use_column_width=True)

        zip_path = run_zip_path(run_dir)
        if zip_path is not None:
            data = zip_path.read_bytes()
        else:
            data = make_zip_bytes(run_dir)
        st.download_button(
            "結果フォルダをZIPダウンロード", data=data,
            file_name=f"{run_dir.name}.zip", mime="application/zip",
            key=f"dl_{run_dir.name}",
        )
