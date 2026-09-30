# -*- coding: utf-8 -*-
"""ML/app/pages/4_結果ブラウザ.py

results/ 配下の過去の実行結果（UI経由・CLI経由どちらも）を一覧し、
画像・表を再表示してZIPダウンロードできるページ。
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

from common.eval_viz import redraw_confusion_matrices
from lib.runs import list_runs, make_zip_bytes, run_zip_path

st.set_page_config(page_title="結果ブラウザ", page_icon="🗂️", layout="wide")
st.title("④ 結果ブラウザ")
st.caption("過去に実行した学習・評価結果を一覧し、再表示・ダウンロードします。")

runs = list_runs()
if not runs:
    st.info("results/ 配下に実行結果がまだありません。先に「③ 学習・評価」を実行してください。")
    st.stop()

options = {
    f"{r['manifest'].get('run_timestamp', '?')}  |  {r['feature_set']}/{r['algorithm']}  |  "
    f"{'-'.join(r['manifest'].get('smns', []))}": r
    for r in runs
}
selected_label = st.selectbox("実行結果を選択", list(options.keys()))
selected = options[selected_label]
run_dir = selected["run_dir"]
manifest = selected["manifest"]

st.markdown(f"### {selected_label}")
st.caption(str(run_dir))

with st.expander("実行条件（manifest）"):
    st.json(manifest)

report_path = run_dir / "classification_report.txt"
if report_path.exists():
    st.text(report_path.read_text(encoding="utf-8"))

comparison_path = run_dir / "step1_feature_set_comparison.csv"
if comparison_path.exists():
    st.dataframe(pd.read_csv(comparison_path, index_col=0))

cm_path = run_dir / "confusion_matrix.png"
cm_norm_path = run_dir / "confusion_matrix_normalized.png"
if not cm_path.exists() and (run_dir / "confusion_matrix.csv").exists():
    if st.button("混同行列画像を再生成する（CSVから）"):
        redraw_confusion_matrices(run_dir)
        st.rerun()

cm_paths = [p for p in (cm_path, cm_norm_path) if p.exists()]
if cm_paths:
    cols = st.columns(len(cm_paths))
    for col, img_path in zip(cols, cm_paths):
        col.image(str(img_path), caption=img_path.name, use_column_width=True)

other_images = sorted(p for p in run_dir.glob("*.png") if p not in cm_paths)
if other_images:
    with st.expander(f"その他の解析画像（{len(other_images)}件）"):
        gallery_cols = st.columns(3)
        for i, img_path in enumerate(other_images):
            gallery_cols[i % 3].image(str(img_path), caption=img_path.name, use_column_width=True)

zip_path = run_zip_path(run_dir)
data = zip_path.read_bytes() if zip_path is not None else make_zip_bytes(run_dir)
st.download_button(
    "この結果フォルダをZIPダウンロード", data=data,
    file_name=f"{run_dir.name}.zip", mime="application/zip",
)
