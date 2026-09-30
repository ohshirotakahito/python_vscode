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
from common.extract_catalog import record_extraction

# この抽出手法の系統名（common/paths.py 側のフォルダ名と揃える）
FEATURE_SET = 'rmc'


def run_extraction(samples, server='Rackstation', keyfolder='analysis', ex='Suzuki_Lys',
                    output_dir=None, progress_callback=None, files_by_sample=None):
    """指定サンプルのtdmsファイルからtsfresh用特徴量を抽出し、data/features/rmc/ 以下に保存する。

    progress_callback(処理済み, 総数, message) はファイルごとに呼ばれる
    （総数はサンプル数×1000に換算した値。サンプル内の進み具合も反映される）。
    files_by_sample: {サンプル名: [ANAL tdmsのパス, ...]} を渡すと、そのファイルだけを
                     読み込む（UIで選択したファイルのみ抽出する用）。None なら全ファイル。
    """
    if output_dir is None:
        output_dir = paths.feature_dir(FEATURE_SET)
    OUTPUT_DIR = output_dir

    total_samples = len(samples)
    for sample_index, sample in enumerate(samples, start=1):
        if progress_callback is not None:
            progress_callback(int((sample_index - 1) / total_samples * 1000), 1000,
                              f"[{sample}] ファイル一覧を取得中...")

        def file_progress(done, total, name, sample_index=sample_index, sample=sample):
            if progress_callback is not None:
                frac = (sample_index - 1 + done / max(total, 1)) / total_samples
                progress_callback(int(frac * 1000), 1000, f"[{sample}] {done + 1}/{total} {name}")

        tdms_paths = None if files_by_sample is None else files_by_sample.get(sample, [])
        file_log = []
        CX, ALL_LONG_ROWS = collect_events_for_sample(
            server, keyfolder, ex, sample, tdms_paths=tdms_paths,
            progress_callback=file_progress, file_log=file_log)

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

        record_extraction(FEATURE_SET, server, keyfolder, ex, sample, file_log,
                          n_added=n_added, n_skipped=n_skipped, n_total=len(merged_array),
                          output_path=npy_path)

        if progress_callback is not None:
            progress_callback(int(sample_index / total_samples * 1000), 1000, f"[{sample}] 完了")

    print('end')
    return OUTPUT_DIR


if __name__ == '__main__':
    from pathlib import Path

    # 実行設定（ここで読み込み元・対象サンプル・保存先を指定）
    server = 'Rackstation'
    keyfolder = 'analysis'
    ex = 'Takahagi_LTNAn'
    #samples = ['M2Lys']  # テスト時に限定する場合
    samples = ['TLTNA']
    
    # samples = exfoler_check(server, keyfolder, ex)  # サンプルリスト非限定
    output_dir = None  # None: ML/data/features/rmc/。任意の保存先は r'D:\output' など
    progress_callback = None  # 必要なら関数を指定: callback(処理済み数, 総数, message)

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    run_extraction(
        samples,
        server=server,
        keyfolder=keyfolder,
        ex=ex,
        output_dir=output_dir,
        progress_callback=progress_callback,
    )
