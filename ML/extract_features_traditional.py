# -*- coding: utf-8 -*-
"""
extract_features_traditional.py

【このファイルについて】
TOP_Feex_rmb_20260803.py のリファクタリング版。
TDMS読込・イベント抽出のコア処理は common/tdms_io.py に共通化した。
このファイルには「data/features/rmb フォルダへの保存」というrmb固有の挙動だけを残している。

【追記保存対応（今回の変更点）】
以前は同じサンプル名（例: 'Guanine'）で本スクリプトを再実行すると、
既存の .npy を丸ごと上書きしていた。これだと、別日に追加計測したデータを
反映させたくて再実行しただけのつもりが、以前の計測データが消えてしまう
リスクがあった。
今回から common/incremental_save.py の merge_new_events() を使い、
「既存データ + 新しく見つかったイベントだけを追記」する方式に変更した。
同じtdmsファイルを何度再実行しても、(file, signal_position) の組み合わせで
重複判定されるため、二重にイベントが増えることはない。

【共通化に伴う変更点】
common/tdms_io.py の apick() は event_id とtsfresh用long_rowsも返すよう
統一されている（元のrmb.pyのapickはAXのみを返す古い版だった）。
このファイルではlong_rowsは使わないため単純に無視している。

【出力先について】
common/paths.py に統一（data/features/rmb/ 以下に保存される）。
"""

import numpy as np

import common.paths as paths
from common.tdms_io import exfoler_check, collect_events_for_sample
from common.incremental_save import load_existing_npy, merge_new_events

# この抽出手法の系統名（common/paths.py 側のフォルダ名と揃える）
FEATURE_SET = 'rmb'


if __name__ == '__main__':
    server = 'Rackstation'
    keyfolder = 'analysis'
    ex = 'Sakano_01'

    ExPath = '//' + server + '/' + keyfolder + '/' + ex + '/'

    # sampleリスト非限定（テスト時に使用）
    samples = exfoler_check(server, keyfolder, ex)

    # sampleリスト限定（特定フォルダごと作成）
    samples = ["Guanine",'OMeG','OxoG']
    # samples = ['Guanine']

    # 出力先フォルダ（data/features/rmb/ 以下。無ければ自動作成される）
    OUTPUT_DIR = paths.feature_dir(FEATURE_SET)

    for sample in samples:
        CX, _ALL_LONG_ROWS = collect_events_for_sample(server, keyfolder, ex, sample)

        # 保存先パス
        SamplePath = sample + '_10k_Sample'
        TargetPath = 'ANAL'
        save_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_' + 'rmb.npy')

        # --- 既存データとマージ（新しいイベントだけを追記） ---
        existing_array = load_existing_npy(save_path)
        merged_array, _merged_long_rows, n_added, n_skipped = merge_new_events(
            existing_array, CX, new_long_rows=None
        )

        np.save(save_path, merged_array)
        print(
            f"[{sample}] 新規追加: {n_added}件 / 重複スキップ: {n_skipped}件 "
            f"/ 合計: {len(merged_array)}件 -> {save_path}"
        )

    print('end')
