# -*- coding: utf-8 -*-
"""
common/filters.py

【このモジュールについて】
data_pipeline.load_meta_data() が読み込む meta_df に対し、
「特定のTDMSファイル番号だけを使う」「特定の計測日だけを使う」
「特定の装置だけを使う」といった条件付きのデータ選択を行うための
共通ユーティリティ。

【前提：再抽出は不要】
file_number / machine_no / measured_at の3列は、tsfresh特徴量抽出時の
生データには直接は含まれていないが、apick()（common/tdms_io.py）が
既に保存している 'sample_name' 列・'ex_id' 列の文字列パターンから、
読み込み時にその場で導出できる（derive_filter_columns()）。
そのため、この3列でデータ選択したいがために特徴量を再抽出する必要はない。

  - file_number : sample_name 末尾の '#001' 等の連番
                  例: 'P_10k_Sample#001' -> 1
  - machine_no  : ex_id 中の 'AN#3' 等の装置番号
                  例: '20260501_1444 AN#3Pex1n1_Ph-Shanli' -> 3
  - measured_at : ex_id 先頭の '20260501_1444' (YYYYMMDD_HHMM)
                  例: '20260501_1444 AN#3...' -> Timestamp('2026-05-01 14:44')

パターンに一致しない行は該当列が NaN になる（クラッシュはしない）。
NaNの行はFILTERSでその列を条件に使った場合、自動的に除外される
（「条件に合うかどうか判定できない行」として扱う）。

【FILTERSの書式】
data_pipeline.FILTERS （または各スクリプト側の FILTERS 変数）に
以下の形式の辞書を指定する。

    FILTERS = {
        'file_number': {'min': 1, 'max': 5, 'exclude': [7]},  # 001〜005を使う、007は除外
        'machine_no':  {'include': [3]},                       # AN#3のみ使う
        'measured_at': {'min': '2026-07-01', 'max': '2026-07-31'},  # 計測日時の範囲
    }

各列について、以下のキーを指定できる（すべて省略可・自由に組み合わせ可）:
    'min'     : この値以上
    'max'     : この値以下
    'include' : このリストに含まれる値のみ
    'exclude' : このリストに含まれる値を除外

何も絞り込みたくない場合は FILTERS = None（またはFILTERS = {}）のままでよい。
"""

import re

import pandas as pd

# 'P_10k_Sample#001' -> '001'
_FILE_NUMBER_RE = re.compile(r'#(?P<file_number>\d+)\s*$')

# '20260501_1444 AN#3Pex1n1_Ph-Shanli' -> measured_at='20260501_1444', machine_no='3'
_EX_ID_RE = re.compile(
    r'^(?P<measured_at>\d{8}_\d{4})\s+AN#(?P<machine_no>\d+)'
)


def derive_filter_columns(meta_df):
    """meta_df に file_number / machine_no / measured_at 列を追加して返す
    (既存の sample_name / ex_id 列から正規表現で導出。再抽出不要)。

    既にこれらの列が存在する場合は上書きし直す。
    パターンに一致しない行は該当列が NaN になる。
    """
    meta_df = meta_df.copy()

    file_number = meta_df['sample_name'].astype(str).str.extract(_FILE_NUMBER_RE)['file_number']
    meta_df['file_number'] = pd.to_numeric(file_number, errors='coerce').astype('Int64')

    ex_id_extracted = meta_df['ex_id'].astype(str).str.extract(_EX_ID_RE)
    meta_df['machine_no'] = pd.to_numeric(ex_id_extracted['machine_no'], errors='coerce').astype('Int64')
    meta_df['measured_at'] = pd.to_datetime(
        ex_id_extracted['measured_at'], format='%Y%m%d_%H%M', errors='coerce'
    )

    n_unmatched_file = int(meta_df['file_number'].isna().sum())
    n_unmatched_ex = int(meta_df['machine_no'].isna().sum())
    if n_unmatched_file > 0:
        print(f"⚠ file_number を抽出できない行が {n_unmatched_file} 件あります（sample_nameのパターン不一致）")
    if n_unmatched_ex > 0:
        print(f"⚠ machine_no/measured_at を抽出できない行が {n_unmatched_ex} 件あります（ex_idのパターン不一致）")

    return meta_df


def apply_filters(meta_df, filters):
    """FILTERS辞書に従って meta_df を絞り込む。

    対象列（file_number/machine_no/measured_at）がまだ無い場合は
    derive_filter_columns() を自動で呼んで補う。

    filters が None または空辞書の場合は、meta_df をそのまま返す
    （何もしない）。

    戻り値: 絞り込み後の meta_df（新しいDataFrame）
    """
    if not filters:
        return meta_df

    derivable_cols = {'file_number', 'machine_no', 'measured_at'}
    needed_cols = derivable_cols & set(filters.keys())
    if needed_cols - set(meta_df.columns):
        meta_df = derive_filter_columns(meta_df)

    mask = pd.Series(True, index=meta_df.index)
    n_before = len(meta_df)

    for col, cond in filters.items():
        if col not in meta_df.columns:
            print(f"⚠ FILTERS: 列 '{col}' が meta_df に存在しないためこの条件はスキップします")
            continue

        series = meta_df[col]
        col_mask = pd.Series(True, index=meta_df.index)

        if 'min' in cond:
            val = pd.to_datetime(cond['min']) if col == 'measured_at' else cond['min']
            col_mask &= (series >= val)
        if 'max' in cond:
            val = pd.to_datetime(cond['max']) if col == 'measured_at' else cond['max']
            col_mask &= (series <= val)
        if 'include' in cond:
            col_mask &= series.isin(cond['include'])
        if 'exclude' in cond:
            col_mask &= ~series.isin(cond['exclude'])

        # パターン不一致でNaNになった行は、条件に合致するか判定不能なので除外する
        col_mask &= series.notna()

        mask &= col_mask

    filtered = meta_df[mask].copy()
    n_after = len(filtered)
    print(f"FILTERS適用: {n_before}件 -> {n_after}件 ({n_before - n_after}件除外)")

    return filtered
