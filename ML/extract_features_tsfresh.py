# -*- coding: utf-8 -*-
"""
extract_features_tsfresh.py

【このファイルについて】
TOP_Feex_rmc_tsfresh_20260803.py のリファクタリング版。
TDMS読込・イベント抽出のコア処理は common/tdms_io.py に共通化した。
このファイルには「data/features/rmc フォルダへの保存 + tsfresh用CSV/meta CSVの出力」
というrmc_tsfresh固有の挙動だけを残している。

【追記保存対応（今回の変更点）】
以前は同じサンプル名で本スクリプトを再実行すると、既存の .npy / meta.csv /
tsfresh_input.csv を丸ごと上書きしていた。今回から common/incremental_save.py の
merge_new_events() を使い、「既存データ + 新しく見つかったイベントだけを追記」する
方式に変更した。
  - .npy / meta.csv : 既存データ＋新規イベントをまとめたもので毎回作り直す
                       （merge_new_events() が既に重複除去・event_id振り直し
                        済みの完全なマージ結果を返すため、これで問題ない）
  - tsfresh_input.csv: 新規追加分の long format 行だけを merge_new_events() から
                        受け取り、既存CSVを読み込んで concat する
                        （こちらは全件書き直すとファイルサイズが大きく非効率なため、
                         新規分のみ追記する形にしている）

※ サンプルが大きい場合、ALL_LONG_ROWSを全てメモリに溜め込む方式のため
   メモリ不足になりやすい。その場合は extract_features_chronos.py
   （逐次CSV追記方式）をベースにする方が安全。

【出力先について】
common/paths.py に統一（data/features/rmc/ 以下に保存される）。
この配下は train_lightgbm_tsfresh.py / train_xgboost_tsfresh.py / predict_mix_xgboost_tsfresh.py が
読み込む先と一致させているため、フォルダ名を個別に変更しないこと。
"""

import os

import pandas as pd
import numpy as np

import common.paths as paths
from common.tdms_io import exfoler_check, collect_events_for_sample, META_COLUMNS
from common.incremental_save import load_existing_npy, merge_new_events

# この抽出手法の系統名（common/paths.py 側のフォルダ名と揃える）
FEATURE_SET = 'rmc'


if __name__ == '__main__':
    server = 'Rackstation'
    keyfolder = 'analysis'
    ex = 'Suzuki_Lys'

    ExPath = '//' + server + '/' + keyfolder + '/' + ex + '/'

    samples = exfoler_check(server, keyfolder, ex)
    # samples = ['L','Adenine','OxoG','OMeG']  # テスト時に限定する場合
    #samples = ['GO6MeG1-1']

    # 出力先フォルダ（data/features/rmc/ 以下。無ければ自動作成される）
    OUTPUT_DIR = paths.feature_dir(FEATURE_SET)

    for sample in samples:
        CX, ALL_LONG_ROWS = collect_events_for_sample(server, keyfolder, ex, sample)

        SamplePath = sample + '_10k_Sample'
        TargetPath = 'ANAL'

        npy_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_' + 'rmc.npy')
        tsfresh_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_tsfresh_input.csv')
        meta_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_meta.csv')

        # --- 既存データとマージ（新しいイベントだけを追記） ---
        existing_array = load_existing_npy(npy_path)
        merged_array, merged_long_rows, n_added, n_skipped = merge_new_events(
            existing_array, CX, new_long_rows=ALL_LONG_ROWS
        )

        # --- 特徴量（メタ情報 + 12点波形特徴量）を保存 ---
        np.save(npy_path, merged_array)
        print(
            f"[{sample}] 新規追加: {n_added}件 / 重複スキップ: {n_skipped}件 "
            f"/ 合計: {len(merged_array)}件 -> {npy_path}"
        )

        # --- tsfresh用 long format データを保存（新規追加分のみ既存CSVに追記） ---
        if merged_long_rows:
            new_long_df = pd.DataFrame(merged_long_rows)
            if tsfresh_path.exists():
                existing_long_df = pd.read_csv(tsfresh_path)
                # 念のための二重チェック（通常はmerge_new_events()の時点で
                # 重複除去済みなので、ここで実際に弾かれることはないはず）
                new_long_df = new_long_df[~new_long_df['id'].isin(existing_long_df['id'])]
                combined_long_df = pd.concat(
                    [existing_long_df, new_long_df], axis=0, ignore_index=True
                )
            else:
                combined_long_df = new_long_df
            combined_long_df.to_csv(tsfresh_path, index=False)
            print(
                f"tsfresh入力データを保存: {tsfresh_path} "
                f"(events={combined_long_df['id'].nunique()}, rows={len(combined_long_df)})"
            )
        else:
            print("新規追加分のtsfreshデータが無いため、tsfresh_input.csvは変更していません。")

        # --- メタ情報（event_id付き）も別途CSVで保存（マージ済み全件で作り直す） ---
        if len(merged_array) > 0:
            n_wave_features = len(merged_array[0]) - len(META_COLUMNS)
            wave_columns = [f'wave_{i}' for i in range(n_wave_features)]
            meta_df = pd.DataFrame(merged_array, columns=META_COLUMNS + wave_columns)
            meta_df.to_csv(meta_path, index=False)
            print(f"メタ情報+波形特徴量を保存: {meta_path}")

    print('end')
