# -*- coding: utf-8 -*-
"""ML/app/lib/catalog.py

data/features/<feature_set>/ 配下に既に抽出済みのサンプル名を検出するユーティリティ。
UIのサンプル選択欄を、手入力ではなく既存データからの選択式にするために使う。
"""

import re
from pathlib import Path

import common.paths as paths

_META_SUFFIX_RE = re.compile(r"_10k_Sample_ANAL_meta$")
_NPY_SUFFIX_RE = re.compile(r"_10k_Sample_ANAL_(rmb|rmc)$")


def list_available_samples(feature_set: str) -> list[str]:
    """指定した特徴量セットで既に抽出済みのサンプル名一覧を返す（無ければ空リスト）。

    extract_features_tsfresh.py が出力する *_meta.csv/.parquet と、
    extract_features_traditional.py が出力する *_rmb.npy の両方に対応する。
    """
    data_root: Path = paths.feature_dir(feature_set)
    names = set()

    for path in data_root.glob("*_10k_Sample_ANAL_meta.*"):
        match = _META_SUFFIX_RE.search(path.stem)
        if match:
            names.add(path.stem[: match.start()])

    for path in data_root.glob("*_10k_Sample_ANAL_*.npy"):
        match = _NPY_SUFFIX_RE.search(path.stem)
        if match:
            names.add(path.stem[: match.start()])

    return sorted(names)
