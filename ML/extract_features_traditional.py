# -*- coding: utf-8 -*-
"""
extract_features_traditional.py

【このファイルについて】
TOP_Feex_rmb_20260803.py のリファクタリング版。
TDMS読込・イベント抽出のコア処理は common/tdms_io.py に共通化した。
このファイルには「data/features/rmb フォルダへの保存」というrmb固有の挙動だけを残している。

【追記保存対応】
以前は同じサンプル名（例: 'Guanine'）で本スクリプトを再実行すると、
既存の .npy を丸ごと上書きしていた。これだと、別日に追加計測したデータを
反映させたくて再実行しただけのつもりが、以前の計測データが消えてしまう
リスクがあった。
common/incremental_save.py の merge_new_events() を使い、
「既存データ + 新しく見つかったイベントだけを追記」する方式にしている。
同じtdmsファイルを何度再実行しても、(file, signal_position) の組み合わせで
重複判定されるため、二重にイベントが増えることはない。

【中断・再開対応】(このバージョンで追加)
このスクリプトも tsfresh 版と同様、「新しいtdmsファイルが増えるたびに定期的に
再実行する」運用を前提にしており、merge_new_events() が既存データとの重複を
弾いてくれるため何度再実行しても安全な設計になっている。そのため中断・再開に
ついても「サンプルを二度と処理しない」というマーカーではなく、以下の考え方にした:

  ・collect_events_for_sample() 相当の「全tdmsファイルを読んでCXをメモリに
    溜め込む」処理をこのファイル内に展開し、tdmsファイル1つ処理するたびに、
    その結果を
        <sample>_collect_staging_meta.csv   (将来のCX相当)
        <sample>_collect_checkpoint.json    (処理済みファイル一覧・next_event_id)
    へ逐次追記・更新する。
  ・全tdmsファイルの収集が完了したら、ステージングCSVを読み戻してCXを
    再構成し、そこから先（merge_new_events()でのマージ・npy保存）は
    元のロジックと完全に同じ。
  ・収集が正常に完了したらステージングCSV・チェックポイントは削除する
    （次回実行時はまた新規に全tdmsファイルを収集し直す＝元の挙動のまま）。
  ・途中で中断された場合は、チェックポイントに記録された「処理済みtdmsファイル」
    をスキップして、収集フェーズの続きから再開する。

  ※ このファイルはlong_rowsを使わないため、tsfresh版と違いステージングは
     meta（CX）用の1ファイルだけで済む。

【共通化に伴う変更点】
common/tdms_io.py の apick() は event_id とtsfresh用long_rowsも返すよう
統一されている（元のrmb.pyのapickはAXのみを返す古い版だった）。
このファイルではlong_rowsは使わないため単純に無視している。

【出力先について】
common/paths.py に統一（data/features/rmb/ 以下に保存される）。
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
from common.extract_catalog import record_extraction

# この抽出手法の系統名（common/paths.py 側のフォルダ名と揃える）
FEATURE_SET = 'rmb'


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


def run_extraction(samples, server='Rackstation', keyfolder='analysis', ex='Shanli_thy',
                    output_dir=None, progress_callback=None, files_by_sample=None):
    """指定サンプルのtdmsファイルから特徴量を抽出し、data/features/rmb/ 以下に保存する。

    progress_callback は progress_callback(processed_count, total_count, message) の形で
    ファイル1件処理するたびに呼ばれる（UIの進捗バー更新用。Noneなら呼ばれない）。
    files_by_sample: {サンプル名: [ANAL tdmsのパス, ...]} を渡すと、そのファイルだけを
                     読み込む（UIで選択したファイルのみ抽出する用）。None なら全ファイル。
    """
    if output_dir is None:
        output_dir = paths.feature_dir(FEATURE_SET)
    OUTPUT_DIR = output_dir

    # 全サンプル分のtdmsファイル一覧を先に集め、進捗の総数（全体件数）を確定させる。
    # （Analtfoler/tdmslist_filesは一覧取得のみなので、ここで1回呼んでおいて後で使い回す）
    sample_file_lists = {}
    for sample in samples:
        if files_by_sample is not None:
            sample_file_lists[sample] = [
                (os.path.dirname(path), os.path.basename(path))
                for path in sorted(files_by_sample.get(sample, []))
            ]
            continue
        folderlist = Analtfoler(server, keyfolder, ex, sample)
        files = [
            (folder_path, tdms_file_name)
            for folder_path in sorted(folderlist)
            for tdms_file_name in sorted(tdmslist_files(folder_path))
        ]
        sample_file_lists[sample] = files
    total_files = sum(len(files) for files in sample_file_lists.values())
    processed_count = 0

    for sample in samples:
        SamplePath = sample + '_10k_Sample'
        TargetPath = 'ANAL'
        save_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_' + 'rmb.npy')

        # 収集フェーズ（全tdmsファイル走査）の途中経過を退避するステージングファイル。
        # 収集が最後まで終わって merge_new_events() まで完了したら削除される。
        # 永続的な出力は save_path のみ。
        staging_meta_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_collect_staging_meta.csv')
        checkpoint_path = OUTPUT_DIR / (SamplePath + '_' + TargetPath + '_collect_checkpoint.json')

        checkpoint = load_checkpoint(checkpoint_path)

        if checkpoint is None:
            # 新規の収集パス: 中途半端なステージングが残っていたら削除してから開始
            if os.path.exists(staging_meta_path):
                os.remove(staging_meta_path)
            processed_files = set()
            next_event_id = 0
            meta_header_written = False
            wave_columns = None
            print(f"[{sample}] tdms収集を新規に開始します。")
        else:
            processed_files = set(checkpoint['processed_files'])
            next_event_id = checkpoint['next_event_id']
            meta_header_written = checkpoint['meta_header_written']
            wave_columns = checkpoint['wave_columns']
            print(f"[{sample}] 中断していた収集をチェックポイントから再開します "
                  f"(処理済み {len(processed_files)} ファイル, next_event_id={next_event_id})")

        # 抽出履歴用（このパスで実際に読んだファイルのみ。再開時にスキップした分は含まない）
        file_log = []

        def write_checkpoint():
            save_checkpoint(checkpoint_path, {
                'processed_files': sorted(processed_files),
                'next_event_id': next_event_id,
                'meta_header_written': meta_header_written,
                'wave_columns': wave_columns,
            })

        # folderlist / tdms_files の並び順はtdms_io.py側の実装依存で保証されない。
        # 済/未済は絶対パスの集合で管理しているので順序自体は正しさに影響しないが、
        # ログを見やすく・再現しやすくするためソートしておく（一覧自体は事前収集済み）。
        for folder_path, tdms_file_name in sample_file_lists[sample]:
            tdms_file_path = os.path.join(folder_path, tdms_file_name)

            if tdms_file_path in processed_files:
                processed_count += 1
                continue  # 処理済み（再開時はここでスキップされる）

            basename = os.path.basename(tdms_file_path)
            print(basename)

            echec = tdms_checker(tdms_file_path)

            # ---- ここでは計算のみ行い、まだステージングCSVには書き込まない ----
            meta_chunk = None
            candidate_next_event_id = next_event_id

            n_events = 0
            if echec == 1:
                AX, _long_rows, candidate_next_event_id = apick(
                    tdms_file_path, sample, start_event_id=next_event_id
                )
                n_events = len(AX) if AX else 0
                # このファイルではtsfresh用long_rowsは使わないため無視する
                # （元のコードと同じ挙動）

                if AX:
                    if wave_columns is None:
                        n_wave_features = len(AX[0]) - len(META_COLUMNS)
                        wave_columns = [f'wave_{i}' for i in range(n_wave_features)]
                    meta_chunk = pd.DataFrame(AX, columns=META_COLUMNS + wave_columns)

            # ---- ここまで来て初めて、このファイル分をステージングCSVへ書き込む ----
            if meta_chunk is not None:
                meta_header_written = append_df_to_csv(
                    meta_chunk, staging_meta_path, meta_header_written
                )

            next_event_id = candidate_next_event_id
            del meta_chunk

            # このtdmsファイルの計算・書き込みが最後まで終わった時点で「処理済み」にする
            processed_files.add(tdms_file_path)
            write_checkpoint()
            file_log.append((tdms_file_path, echec, n_events))

            processed_count += 1
            if progress_callback is not None:
                progress_callback(processed_count, total_files, f"[{sample}] {basename}")

        # ---- ここまでで、このサンプルの全tdmsファイルの「収集」が完了 ----
        # ステージングCSVを読み戻して、元の CX 相当を再構成する。
        if os.path.exists(staging_meta_path):
            CX = pd.read_csv(staging_meta_path).values.tolist()
        else:
            CX = []

        # ここから先は元のコードと完全に同じロジック --------------------------------

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
        record_extraction(FEATURE_SET, server, keyfolder, ex, sample, file_log,
                          n_added=n_added, n_skipped=n_skipped, n_total=len(merged_array),
                          output_path=save_path)

        # --- この収集パスは正常終了したので、ステージング/チェックポイントは削除 ---
        # 次回スクリプトを実行した際は、また全tdmsファイルを収集し直す
        # （merge_new_events()が重複を弾いてくれるので、これは元の挙動のまま）。
        for p in (staging_meta_path, checkpoint_path):
            if os.path.exists(p):
                os.remove(p)

    print('end')
    return OUTPUT_DIR


if __name__ == '__main__':
    # sampleリスト非限定（テスト時に使用）
    # samples = exfoler_check('Rackstation', 'analysis', 'Shanli_thy')

    # sampleリスト限定（特定フォルダごと作成）
    #samples = ["Guanine", 'OMeG', 'OxoG']
    samples = ['T2', 'T3', 'T4']
    #  samples = ['Guanine']

    run_extraction(samples)

