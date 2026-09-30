# -*- coding: utf-8 -*-
"""ML/app/pages/1_特徴量抽出.py

tdmsファイルから特徴量を抽出し data/features/ 以下に保存するページ。
extract_features_traditional.run_extraction() / extract_features_tsfresh.run_extraction()
を呼び出す（どちらも progress_callback 引数で進捗を通知できるようリファクタ済み）。
"""

import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
_ML_DIR = _APP_DIR.parent
for _p in (_ML_DIR, _APP_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import importlib

import streamlit as st

from lib.progress import StreamlitProgress

st.set_page_config(page_title="特徴量抽出", page_icon="🧪", layout="wide")
st.title("① 特徴量抽出")
st.caption("計測サーバー上のtdmsファイルから特徴量を計算し、data/features/ 以下に保存します。")

METHODS = {
    "traditional（波形12点特徴量 / data/features/rmb）": {
        "module": "extract_features_traditional",
        "default_ex": "Shanli_thy",
    },
    "tsfresh（data/features/rmc）": {
        "module": "extract_features_tsfresh",
        "default_ex": "Suzuki_Lys",
    },
}

method_label = st.radio("抽出手法", list(METHODS.keys()))
method = METHODS[method_label]

with st.form("extract_form"):
    col1, col2, col3 = st.columns(3)
    server = col1.text_input("server（計測サーバー名）", value="Rackstation")
    keyfolder = col2.text_input("keyfolder", value="analysis")
    ex = col3.text_input("ex（実験フォルダ名）", value=method["default_ex"])
    samples_text = st.text_input(
        "サンプル名（カンマ区切りで複数指定可）",
        placeholder="例: T2, T3, T4",
    )
    submitted = st.form_submit_button("抽出を実行", type="primary")

if submitted:
    samples = [s.strip() for s in samples_text.split(",") if s.strip()]
    if not samples:
        st.error("サンプル名を1つ以上入力してください。")
    else:
        st.write(f"対象サンプル: {samples}")
        progress = StreamlitProgress(label="進捗")
        module = importlib.import_module(method["module"])
        try:
            output_dir = module.run_extraction(
                samples, server=server, keyfolder=keyfolder, ex=ex,
                progress_callback=progress,
            )
        except Exception as e:  # noqa: BLE001 - UIにそのままエラー内容を出す
            st.exception(e)
        else:
            st.success(f"完了しました。保存先: {output_dir}")
