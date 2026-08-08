# -*- coding: utf-8 -*-
"""
extract_features_chronos.py

【このファイルについて】
TOP_Feex_rmd_tsfresh_chronos_20260803.py のリファクタリング版。
TDMS読込・イベント抽出のコア処理は common/tdms_io.py に共通化した。
このファイルは、大規模サンプルでもメモリ不足にならないよう
tdmsファイル1つ処理するたびにtsfresh_input.csv / chronos_emb.csvへ
逐次追記していく方式（rmc版からのメモリ対策）を保持している。

現時点でこのシリーズ（rmb→rmc→rmd）の中では最も改良されたバージョン。
チームで新規にTDMS抽出スクリプトを書く場合は、このファイルをベースにするのが良い。

事前準備:
    pip install chronos-forecasting torch

【出力先について】
common/paths.py に統一（data/features/rmd/ 以下に保存される）。
"""

import os

import numpy as np
import pandas as pd
import torch
from chronos import ChronosPipeline

import common.paths as paths
from common.tdms_io import (
    exfoler_check, Analtfoler, tdmslist_files, tdms_checker, apick,
    append_df_to_csv, META_COLUMNS,
)

# この抽出手法の系統名（common/paths.py 側のフォルダ名と揃える）
FEATURE_SET = 'rmd'


# =====================================================================
# Chronos埋め込み抽出（このファイル固有）
# =====================================================================

_CHRONOS_PIPELINE = None


def get_chronos_pipeline(model_name="amazon/chronos-t5-small", device="cpu"):
    global _CHRONOS_PIPELINE
    if _CHRONOS_PIPELINE is None:
        _CHRONOS_PIPELINE = ChronosPipeline.from_pretrained(
            model_name,
            device_map=device,
            torch_dtype=torch.float32,
        )
    return _CHRONOS_PIPELINE


def long_rows_to_series_list(long_rows):
    df = pd.DataFrame(long_rows)
    if df.empty:
        return [], []

    event_ids = []
    series_list = []
    for event_id, g in df.sort_values(["id", "time"]).groupby("id"):
        values = g["value"].to_numpy(dtype=np.float32)
        if len(values) < 2:
            continue
        event_ids.append(event_id)
        series_list.append(torch.tensor(values, dtype=torch.float32))
    return event_ids, series_list


def extract_chronos_embeddings(
    long_rows,
    model_name="amazon/chronos-t5-small",
    device="cpu",
    batch_size=32,
):
    event_ids, series_list = long_rows_to_series_list(long_rows)
    if not series_list:
        return pd.DataFrame()

    pipeline = get_chronos_pipeline(model_name, device)

    all_embeddings = []
    for i in range(0, len(series_list), batch_size):
        batch = series_list[i: i + batch_size]
        embedding, _ = pipeline.embed(batch)
        pooled = embedding.mean(dim=1)
        all_embeddings.append(pooled.to(torch.float32).cpu().numpy())

    emb_matrix = np.concatenate(all_embeddings, axis=0)
    emb_cols = [f"chronos_emb_{i}" for i in range(emb_matrix.shape[1])]
    emb_df = pd.DataFrame(emb_matrix, columns=emb_cols)
    emb_df.insert(0, "event_id", event_ids)
    return emb_df


# =====================================================================
# メイン処理
#
# 変更点（メモリ対策）:
#   ALL_LONG_ROWSをリストに溜め込まず、tdmsファイルを1つ処理するたびに
#     ・tsfresh_input.csv への追記
#     ・Chronos埋め込みの計算とchronos_emb.csvへの追記
#   をその場で行う（CXは12点波形特徴量+メタ情報のみで軽量なため保持）。
# =====================================================================

if __name__ == '__main__':
    server = 'Rackstation'
    keyfolder = 'analysis'
    ex = 'Sakano_02'

    ExPath = '//' + server + '/' + keyfolder + '/' + ex + '/'

    samples = exfoler_check(server, keyfolder, ex)
    # samples = ['L','Adenine','OxoG','OMeG']  # テスト時に限定する場合

    # 出力先フォルダ（data/features/rmd/ 以下。無ければ自動作成される）
    OUTPUT_DIR = paths.feature_dir(FEATURE_SET)

    CHRONOS_MODEL = "amazon/chronos-t5-small"
    CHRONOS_DEVICE = "cpu"  # GPUがあれば "cuda"

    for sample in samples:
        folderlist = Analtfoler(server, keyfolder, ex, sample)

        SamplePath = sample + '_10k_Sample'
        TargetPath = 'ANAL'

        tsfresh_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '_tsfresh_input.csv')
        emb_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '_chronos_emb.csv')

        # 既存ファイルが残っていると追記モードで混ざってしまうため、実行開始時に削除しておく
        for p in (tsfresh_path, emb_path):
            if os.path.exists(p):
                os.remove(p)

        CX = []
        next_event_id = 0
        tsfresh_header_written = False
        emb_header_written = False
        total_long_rows = 0
        total_events_with_emb = 0

        for folder_path in folderlist:
            tdms_files = tdmslist_files(folder_path)

            for tdms_file_name in tdms_files:
                tdms_file_path = os.path.join(folder_path, tdms_file_name)
                basename = os.path.basename(tdms_file_path)
                print(basename)

                echec = tdms_checker(tdms_file_path)

                if echec == 1:
                    AX, long_rows, next_event_id = apick(
                        tdms_file_path, sample, start_event_id=next_event_id
                    )
                    if AX:
                        CX.extend(AX)

                    if long_rows:
                        file_df = pd.DataFrame(long_rows)
                        file_df['id'] = file_df['id'].astype('int32')
                        file_df['time'] = file_df['time'].astype('int32')
                        file_df['value'] = file_df['value'].astype('float32')

                        tsfresh_header_written = append_df_to_csv(
                            file_df, tsfresh_path, tsfresh_header_written
                        )
                        total_long_rows += len(file_df)

                        emb_df = extract_chronos_embeddings(
                            long_rows,
                            model_name=CHRONOS_MODEL,
                            device=CHRONOS_DEVICE,
                        )
                        emb_header_written = append_df_to_csv(
                            emb_df, emb_path, emb_header_written
                        )
                        total_events_with_emb += len(emb_df)

                        del file_df, long_rows, emb_df

        save_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '_' + 'rmd.npy')
        np.save(save_path, CX)

        if total_long_rows > 0:
            print(f"tsfresh入力データを保存: {tsfresh_path} (rows={total_long_rows})")
        else:
            print("tsfresh用データが空のため保存をスキップしました。")

        meta_df = None
        if CX:
            n_wave_features = len(CX[0]) - len(META_COLUMNS)
            wave_columns = [f'wave_{i}' for i in range(n_wave_features)]
            meta_df = pd.DataFrame(CX, columns=META_COLUMNS + wave_columns)
            meta_path = os.path.join(
                OUTPUT_DIR, SamplePath + '_' + TargetPath + '_meta.csv'
            )
            meta_df.to_csv(meta_path, index=False)
            print(f"メタ情報+波形特徴量を保存: {meta_path}")

        if total_events_with_emb > 0 and meta_df is not None:
            emb_df_full = pd.read_csv(emb_path)
            print(f"Chronos埋め込みを保存済み: {emb_path} "
                  f"(events={emb_df_full.shape[0]}, dim={emb_df_full.shape[1] - 1})")

            combined = meta_df.merge(emb_df_full, on="event_id", how="inner")
            combined_path = os.path.join(
                OUTPUT_DIR, SamplePath + '_' + TargetPath + '_meta_plus_chronos.csv'
            )
            combined.to_csv(combined_path, index=False)
            print(f"meta + Chronos埋め込み結合を保存: {combined_path}")
            del emb_df_full, combined
        else:
            print("Chronos埋め込みが空のため結合をスキップしました。")

    print('end')
