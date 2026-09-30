# -*- coding: utf-8 -*-
"""ML/app/lib/progress.py

パイプライン側の progress_callback(current, total, message) 呼び出しを
Streamlitの進捗バー（st.progress）に橋渡しするアダプタ。

使い方:
    progress = StreamlitProgress(label="ファイル")
    extract_features_traditional.run_extraction(samples, progress_callback=progress)
"""

import streamlit as st


class StreamlitProgress:
    """progress_callback(current, total, message) として呼べるcallable。

    呼ばれるたびに st.progress のバーとキャプションを更新する。
    Streamlitは同期実行中でもウィジェット更新をその都度描画するため、
    別スレッド化しなくてもリアルタイムに進捗バーが動く。
    """

    def __init__(self, container=None, label: str = ""):
        container = container or st
        self._label = label
        self._bar = container.progress(0)
        self._text = container.empty()

    def __call__(self, current: int, total: int, message: str = "") -> None:
        total = max(int(total), 1)
        current = min(max(int(current), 0), total)
        ratio = current / total
        self._bar.progress(ratio)
        prefix = f"{self._label}: " if self._label else ""
        self._text.caption(f"{prefix}{current}/{total} {message}")
