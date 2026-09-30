# -*- coding: utf-8 -*-
"""ML/app/pages/2_統計ヒストグラム.py

学習を行わず、RMC特徴量のヒストグラムと統計量を確認するページ。
stat_features_rmc.create_histograms() をそのまま呼び出す（変更不要な既存関数）。
"""

import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
_ML_DIR = _APP_DIR.parent
for _p in (_ML_DIR, _APP_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd
import streamlit as st

import common.paths as paths
from lib.catalog import list_available_samples
from lib.runs import make_zip_bytes

st.set_page_config(page_title="統計ヒストグラム", page_icon="📊", layout="wide")
st.title("② 統計ヒストグラム（学習なし）")
st.caption("識別モデルを学習せず、特徴量ごとの分布（ヒストグラム）と統計量を確認します。")

available_samples = list_available_samples("rmc")
if not available_samples:
    st.warning(
        "data/features/rmc/ に抽出済みのサンプルが見つかりません。"
        "先に「① 特徴量抽出」で tsfresh 手法での抽出を実行してください。"
    )

samples = st.multiselect(
    "サンプル（複数選択可）", options=available_samples,
    default=available_samples[:2] if available_samples else [],
)

with st.expander("詳細設定（任意）"):
    bins_text = st.text_input("bins（整数、または auto）", value="auto")
    use_distance_filter = st.checkbox("distance範囲で絞り込む", value=False)
    dcol1, dcol2 = st.columns(2)
    distance_min = dcol1.number_input("distance 下限", value=0.0, step=0.01, format="%.3f",
                                       disabled=not use_distance_filter)
    distance_max = dcol2.number_input("distance 上限", value=1.0, step=0.01, format="%.3f",
                                       disabled=not use_distance_filter)

run = st.button("ヒストグラムを作成", type="primary", disabled=not samples)

if run:
    from stat_features_rmc import (
        create_histograms, DEFAULT_UPPER_LIMITS, DEFAULT_LOWER_LIMITS, DEFAULT_OUTPUT_ROOT,
    )

    bins = int(bins_text) if bins_text.strip().isdigit() else "auto"

    with st.spinner("集計中..."):
        try:
            output_dir = create_histograms(
                samples=samples,
                data_root=paths.feature_dir("rmc"),
                output_root=DEFAULT_OUTPUT_ROOT,
                bins=bins,
                upper_limits=DEFAULT_UPPER_LIMITS,
                lower_limits=DEFAULT_LOWER_LIMITS,
                distance_min=distance_min if use_distance_filter else None,
                distance_max=distance_max if use_distance_filter else None,
            )
        except Exception as e:  # noqa: BLE001
            st.exception(e)
            output_dir = None

    if output_dir is not None:
        st.success(f"完了しました: {output_dir}")

        stats_path = output_dir / "summary_stats.csv"
        if stats_path.exists():
            st.dataframe(pd.read_csv(stats_path))

        image_paths = sorted(output_dir.glob("*.png"))
        cols = st.columns(2)
        for i, img_path in enumerate(image_paths):
            cols[i % 2].image(str(img_path), caption=img_path.name, use_column_width=True)

        zip_bytes = make_zip_bytes(output_dir)
        st.download_button(
            "結果フォルダをZIPダウンロード", data=zip_bytes,
            file_name=f"{output_dir.name}.zip", mime="application/zip",
        )
