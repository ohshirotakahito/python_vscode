# -*- coding: utf-8 -*-
"""
extract_features_tsfresh.py

【このファイルについて】
TOP_Feex_rmc_tsfresh_20260803.py のリファクタリング版。
TDMS読込・イベント抽出のコア処理は common/tdms_io.py に共通化した。
このファイルには「data/features/rmc フォルダへの保存 + tsfresh用CSV/meta CSVの出力」
というrmc_tsfresh固有の挙動だけを残している。

【追記保存対応】
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

【中断・再開対応】(このバージョンで追加)
このスクリプトは「新しいtdmsファイルが増えるたびに定期的に再実行する」運用を
前提にしており、merge_new_events() が既存データとの重複を弾いてくれるため
何度再実行しても安全な設計になっている。そのため中断・再開についても
「サンプルを二度と処理しない」というマーカーではなく、以下の考え方にした:

  ・collect_events_for_sample() 相当の「全tdmsファイルを読んでCX/ALL_LONG_ROWS
    をメモリに溜め込む」処理をこのファイル内に展開し、tdmsファイル1つ処理する
    たびに、その結果を
        <sample>_collect_staging_meta.csv   (将来のCX相当)
        <sample>_collect_staging_long.csv   (将来のALL_LONG_ROWS相当)
        <sample>_collect_checkpoint.json    (処理済みファイル一覧・next_event_id)
    へ逐次追記・更新する。
  ・全tdmsファイルの収集が完了したら、上記2つのステージングCSVを読み戻して
    CX / ALL_LONG_ROWS を再構成し、そこから先（merge_new_events()での
    マージ・npy保存・tsfresh追記・meta.csv保存）は元のロジックと完全に同じ。
  ・収集が正常に完了したらステージングCSV・チェックポイントは削除する
    （次回実行時はまた新規に全tdmsファイルを収集し直す＝元の挙動のまま）。
  ・途中で中断された場合は、チェックポイントに記録された「処理済みtdmsファイル」
    をスキップして、収集フェーズの続きから再開する。

※ サンプルが大きい場合、ALL_LONG_ROWSを全てメモリに溜め込む方式のため
   メモリ不足になりやすい。その場合は extract_features_chronos.py
   （逐次CSV追記方式）をベースにする方が安全。

【出力先について】
common/paths.py に統一（data/features/rmc/ 以下に保存される）。
この配下は train_lightgbm_tsfresh.py / train_xgboost_tsfresh.py / predict_mix_xgboost_tsfresh.py が
読み込む先と一致させているため、フォルダ名を個別に変更しないこと。
"""

import os
import json
import tempfile

import pandas as pd
import numpy as np

import common.paths as paths
from common.tdms_io import (
    exfoler_check, Analtfoler, tdmslist_files, tdms_checker, apick,
    append_df_to_csv, META_COLUMNS,
)
from common.incremental_save import load_existing_npy, merge_new_events

# この抽出手法の系統名（common/paths.py 側のフォルダ名と揃える）
FEATURE_SET = 'rmc'


# =====================================================================
# チェックポイント関連（中断・再開のためのユーティリティ）
# =====================================================================

def load_checkpoint(checkpoint_path):
    """チェックポイントを読み込む。存在しない/壊れている場合はNoneを返す。"""
    if not os.path.exists(checkpoint_path):
        return None
    try:
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"チェックポイントが読み込めないため、収集を最初からやり直します: {checkpoint_path}")
        return None


def save_checkpoint(checkpoint_path, state):
    """チェックポイントをatomicに保存する（tempfile + os.replace）。"""
    dir_ = os.path.dirname(str(checkpoint_path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix='.ckpt_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp_path, checkpoint_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


if __name__ == '__main__':
    server = 'QTserver'
    keyfolder = 'analysis'
    ex = 'Chirality_N2'

    ExPath = '//' + server + '/' + keyfolder + '/' + ex + '/'

    samples = exfoler_check(server, keyfolder, ex)
    # samples = ['L','Adenine','OxoG','OMeG']  # テスト時に限定する場合
    #samples = ['GO6MeG1-1']

    # 出力先フォルダ（data/features/rmc/ 以下。無ければ自動作成される）
    OUTPUT_DIR = paths.feature_dir(FEATURE_SET)

    for sample in samples:
        SamplePath = sample + '_10k_Sample'
        TargetPath = 'ANAL'

        npy_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_' + 'rmc.npy')
        tsfresh_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_tsfresh_input.csv')
        meta_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_meta.csv')

        # 収集フェーズ（全tdmsファイル走査）の途中経過を退避するステージングファイル。
        # 収集が最後まで終わって merge_new_events() まで完了したら削除される。
        # 永続的な出力は npy_path / meta_path / tsfresh_path のみ。
        staging_meta_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_collect_staging_meta.csv')
        staging_long_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_collect_staging_long.csv')
        checkpoint_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_collect_checkpoint.json')

        checkpoint = load_checkpoint(checkpoint_path)

        if checkpoint is None:
            # 新規の収集パス: 中途半端なステージングが残っていたら削除してから開始
            for p in (staging_meta_path, staging_long_path):
                if os.path.exists(p):
                    os.remove(p)
            processed_files = set()
            next_event_id = 0
            meta_header_written = False
            long_header_written = False
            wave_columns = None
            print(f"[{sample}] tdms収集を新規に開始します。")
        else:
            processed_files = set(checkpoint['processed_files'])
            next_event_id = checkpoint['next_event_id']
            meta_header_written = checkpoint['meta_header_written']
            long_header_written = checkpoint['long_header_written']
            wave_columns = checkpoint['wave_columns']
            print(f"[{sample}] 中断していた収集をチェックポイントから再開します "
                  f"(処理済み {len(processed_files)} ファイル, next_event_id={next_event_id})")

        def write_checkpoint():
            save_checkpoint(checkpoint_path, {
                'processed_files': sorted(processed_files),
                'next_event_id': next_event_id,
                'meta_header_written': meta_header_written,
                'long_header_written': long_header_written,
                'wave_columns': wave_columns,
            })

        folderlist = Analtfoler(server, keyfolder, ex, sample)

        # folderlist / tdms_files の並び順はtdms_io.py側の実装依存で保証されない。
        # 済/未済は絶対パスの集合で管理しているので順序自体は正しさに影響しないが、
        # ログを見やすく・再現しやすくするためソートしておく。
        for folder_path in sorted(folderlist):
            tdms_files = sorted(tdmslist_files(folder_path))

            for tdms_file_name in tdms_files:
                tdms_file_path = os.path.join(folder_path, tdms_file_name)

                if tdms_file_path in processed_files:
                    continue  # 処理済み（再開時はここでスキップされる）

                basename = os.path.basename(tdms_file_path)
                print(basename)

                echec = tdms_checker(tdms_file_path)

                # ---- ここでは計算のみ行い、まだステージングCSVには書き込まない ----
                meta_chunk = None
                long_chunk = None
                candidate_next_event_id = next_event_id

                if echec == 1:
                    AX, long_rows, candidate_next_event_id = apick(
                        tdms_file_path, sample, start_event_id=next_event_id
                    )

                    if AX:
                        if wave_columns is None:
                            n_wave_features = len(AX[0]) - len(META_COLUMNS)
                            wave_columns = [f'wave_{i}' for i in range(n_wave_features)]
                        meta_chunk = pd.DataFrame(AX, columns=META_COLUMNS + wave_columns)

                    if long_rows:
                        long_chunk = pd.DataFrame(long_rows)

                # ---- ここまで来て初めて、このファイル分をステージングCSVへ書き込む ----
                if meta_chunk is not None:
                    meta_header_written = append_df_to_csv(
                        meta_chunk, staging_meta_path, meta_header_written
                    )
                if long_chunk is not None:
                    long_header_written = append_df_to_csv(
                        long_chunk, staging_long_path, long_header_written
                    )

                next_event_id = candidate_next_event_id
                del meta_chunk, long_chunk

                # このtdmsファイルの計算・書き込みが最後まで終わった時点で「処理済み」にする
                processed_files.add(tdms_file_path)
                write_checkpoint()

        # ---- ここまでで、このサンプルの全tdmsファイルの「収集」が完了 ----
        # ステージングCSVを読み戻して、元の CX / ALL_LONG_ROWS 相当を再構成する。
        if os.path.exists(staging_meta_path):
            CX = pd.read_csv(staging_meta_path).values.tolist()
        else:
            CX = []

        if os.path.exists(staging_long_path):
            ALL_LONG_ROWS = pd.read_csv(staging_long_path).to_dict('records')
        else:
            ALL_LONG_ROWS = []

        # ここから先は元のコードと完全に同じロジック --------------------------------

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

        # --- この収集パスは正常終了したので、ステージング/チェックポイントは削除 ---
        # 次回スクリプトを実行した際は、また全tdmsファイルを収集し直す
        # （merge_new_events()が重複を弾いてくれるので、これは元の挙動のまま）。
        for p in (staging_meta_path, staging_long_path, checkpoint_path):
            if os.path.exists(p):
                os.remove(p)

    print('end')