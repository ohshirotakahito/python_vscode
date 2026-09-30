# -*- coding: utf-8 -*-
"""ML/app/Home.py — MLパネルのトップページ。

起動方法:
    ML/app/launch_ui.bat をダブルクリック
    または: streamlit run ML/app/Home.py
"""

import sys
from pathlib import Path

_ML_DIR = Path(__file__).resolve().parent.parent
_APP_DIR = Path(__file__).resolve().parent
for _p in (_ML_DIR, _APP_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import streamlit as st

st.set_page_config(page_title="ML解析パネル", page_icon="🧪", layout="wide")

st.title("🧪 ML解析パネル")
st.markdown(
    """
計測データから特徴量を取り出し、統計確認・識別モデルの学習・評価、混合サンプルの割合予測までを
コードを書かずに実行できるパネルです。左のサイドバーから各ページに進んでください。

1. **特徴量抽出** — tdmsファイルから特徴量を計算し、`data/features/` に保存します
2. **統計ヒストグラム** — 学習せずに、特徴量の分布（ヒストグラム）を確認します
3. **学習・評価** — 識別モデルを学習し、精度・混同行列・重要度などを確認します
4. **結果ブラウザ** — 過去の実行結果を一覧・再表示し、ZIPでダウンロードします
5. **混合予測** — 学習したモデルで混合サンプルを分類し、クラスごとの割合を
   全体・ファイルごと・ファイル内N秒ごとに表示します

処理に時間がかかる場合は、各ページの実行ボタンを押した後に進捗（%）が表示されます。
"""
)

st.info("初回起動時は、サイドバーが表示されるまで少し待ってください。")
