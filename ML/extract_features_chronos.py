# -*- coding: utf-8 -*-
"""
extract_features_chronos.py

【このファイルについて】
TOP_Feex_rmd_tsfresh_chronos_20260803.py のリファクタリング版。
TDMS読込・イベント抽出のコア処理は common/tdms_io.py に共通化した。
このファイルは、大規模サンプルでもメモリ不足にならないよう
tdmsファイル1つ処理するたびにtsfresh_input.csv / chronos_emb.csvへ
逐次追記していく方式（rmc版からのメモリ対策）を保持している。

【中断・再開対応】(このバージョンで追加)
実行に時間がかかるため、以下の方式で「途中で止めても再実行すれば
続きから再開できる」ようにしている。

  ・サンプルごとに <SamplePath>_<TargetPath>_checkpoint.json を書き出し、
    「処理済みtdmsファイルのパス一覧」「next_event_id」「各CSVの
    ヘッダ書き込み済みフラグ」を保持する。
  ・tdmsファイルを1つ処理し終えるたびにチェックポイントを更新する
    （tempfile + os.replace によるatomic writeなので、書き込み中に
    プロセスが落ちてもチェックポイント自体は壊れない）。
  ・波形特徴量(CX)もメモリに溜めず meta.csv へ逐次追記するように変更。
    rmd.npy はサンプル完了時に meta.csv から作り直す。
  ・サンプル処理が最後まで終わったら <SamplePath>_<TargetPath>.done を
    作成し、チェックポイントJSONは削除する。次回実行時、.done がある
    サンプルは丸ごとスキップする。
  ・起動時にチェックポイントJSONがあれば「途中から再開」、なければ
    「新規実行」として関連CSV/meta.csvを削除してから開始する
    （中途半端に混ざったデータが残らないようにするため）。

現時点でこのシリーズ（rmb→rmc→rmd）の中では最も改良されたバージョン。
チームで新規にTDMS抽出スクリプトを書く場合は、このファイルをベースにするのが良い。

事前準備:
    pip install chronos-forecasting torch

【出力先について】
common/paths.py に統一（data/features/rmd/ 以下に保存される）。
"""

import os
import json
import tempfile

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
        print(f"チェックポイントが読み込めないため、新規実行として扱います: {checkpoint_path}")
        return None


def save_checkpoint(checkpoint_path, state):
    """チェックポイントをatomicに保存する。
    一時ファイルに書いてからos.replaceで置き換えることで、
    書き込み中にプロセスが落ちてもチェックポイントファイル自体は
    「更新前」か「更新後」のどちらかの完全な状態を保つ。
    """
    dir_ = os.path.dirname(checkpoint_path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix='.ckpt_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp_path, checkpoint_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# =====================================================================
# メイン処理
# =====================================================================

if __name__ == '__main__':
    server = 'QTserver'
    keyfolder = 'analysis'
    ex = 'Chirality_N2'

    ExPath = '//' + server + '/' + keyfolder + '/' + ex + '/'

    samples = exfoler_check(server, keyfolder, ex)
    # samples = ['L','Adenine','OxoG','OMeG']  # テスト時に限定する場合

    # 出力先フォルダ（data/features/rmd/ 以下。無ければ自動作成される）
    OUTPUT_DIR = paths.feature_dir(FEATURE_SET)

    CHRONOS_MODEL = "amazon/chronos-t5-small"
    CHRONOS_DEVICE = "cpu"  # GPUがあれば "cuda"

    for sample in samples:
        SamplePath = sample + '_10k_Sample'
        TargetPath = 'ANAL'

        tsfresh_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '_tsfresh_input.csv')
        emb_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '_chronos_emb.csv')
        meta_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '_meta.csv')
        checkpoint_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '_checkpoint.json')
        done_marker_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '.done')

        # このサンプルは既に完了済み（.doneがある）ならまるごとスキップ
        if os.path.exists(done_marker_path):
            print(f"[{sample}] 完了済みのためスキップします。")
            continue

        folderlist = Analtfoler(server, keyfolder, ex, sample)

        checkpoint = load_checkpoint(checkpoint_path)

        if checkpoint is None:
            # 新規実行: 古い出力が中途半端に残っていると混ざるので削除してから開始
            for p in (tsfresh_path, emb_path, meta_path):
                if os.path.exists(p):
                    os.remove(p)
            processed_files = set()
            next_event_id = 0
            tsfresh_header_written = False
            emb_header_written = False
            meta_header_written = False
            wave_columns = None
            total_long_rows = 0
            total_events_with_emb = 0
            total_meta_rows = 0
            print(f"[{sample}] 新規実行として開始します。")
        else:
            processed_files = set(checkpoint['processed_files'])
            next_event_id = checkpoint['next_event_id']
            tsfresh_header_written = checkpoint['tsfresh_header_written']
            emb_header_written = checkpoint['emb_header_written']
            meta_header_written = checkpoint['meta_header_written']
            wave_columns = checkpoint['wave_columns']
            total_long_rows = checkpoint['total_long_rows']
            total_events_with_emb = checkpoint['total_events_with_emb']
            total_meta_rows = checkpoint['total_meta_rows']
            print(f"[{sample}] チェックポイントから再開します "
                  f"(処理済み {len(processed_files)} ファイル, next_event_id={next_event_id})")

        def write_checkpoint():
            save_checkpoint(checkpoint_path, {
                'processed_files': sorted(processed_files),
                'next_event_id': next_event_id,
                'tsfresh_header_written': tsfresh_header_written,
                'emb_header_written': emb_header_written,
                'meta_header_written': meta_header_written,
                'wave_columns': wave_columns,
                'total_long_rows': total_long_rows,
                'total_events_with_emb': total_events_with_emb,
                'total_meta_rows': total_meta_rows,
            })

        # folderlist / tdms_files はそれぞれ glob / os.listdir 由来で
        # 実行のたびに順序が変わり得る（tdms_io.py の実装依存）。
        # 済/未済は絶対パスの集合で管理しているので順序自体は正しさに
        # 影響しないが、ログを見やすく・再現しやすくするためソートしておく。
        for folder_path in sorted(folderlist):
            tdms_files = sorted(tdmslist_files(folder_path))

            for tdms_file_name in tdms_files:
                tdms_file_path = os.path.join(folder_path, tdms_file_name)

                if tdms_file_path in processed_files:
                    continue  # 処理済み（再開時はここでスキップされる）

                basename = os.path.basename(tdms_file_path)
                print(basename)

                echec = tdms_checker(tdms_file_path)

                # ---- ここでは計算のみ行い、まだCSVには一切書き込まない ----
                # (extract_chronos_embeddings は例外を握りつぶさず外へ伝播するため、
                #  推論失敗時にCSVへ中途半端に書き込み済み、という状態を避けるために
                #  「全部計算してから、まとめて書き込む」順序にしている)
                meta_chunk = None
                tsfresh_chunk = None
                emb_df = None
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
                        tsfresh_chunk = pd.DataFrame(long_rows)
                        tsfresh_chunk['id'] = tsfresh_chunk['id'].astype('int32')
                        tsfresh_chunk['time'] = tsfresh_chunk['time'].astype('int32')
                        tsfresh_chunk['value'] = tsfresh_chunk['value'].astype('float32')

                        # ここで例外が起きても、まだ何もCSVに書いていないので安全
                        emb_df = extract_chronos_embeddings(
                            long_rows,
                            model_name=CHRONOS_MODEL,
                            device=CHRONOS_DEVICE,
                        )

                # ---- ここまで来て初めて、このファイル分の結果をまとめて書き込む ----
                if meta_chunk is not None:
                    meta_header_written = append_df_to_csv(
                        meta_chunk, meta_path, meta_header_written
                    )
                    total_meta_rows += len(meta_chunk)

                if tsfresh_chunk is not None:
                    tsfresh_header_written = append_df_to_csv(
                        tsfresh_chunk, tsfresh_path, tsfresh_header_written
                    )
                    total_long_rows += len(tsfresh_chunk)

                if emb_df is not None:
                    emb_header_written = append_df_to_csv(
                        emb_df, emb_path, emb_header_written
                    )
                    total_events_with_emb += len(emb_df)

                next_event_id = candidate_next_event_id
                del meta_chunk, tsfresh_chunk, emb_df

                # このtdmsファイルの計算・書き込みが最後まで終わった時点で「処理済み」にする
                # （途中で例外が出た場合はここに到達しないので、次回再実行時に
                #   このファイルからやり直しになる＝中途半端なデータは残らない）
                processed_files.add(tdms_file_path)
                write_checkpoint()

        # ---- ここまででこのサンプルの全tdmsファイル処理が完了 ----

        if total_long_rows > 0:
            print(f"tsfresh入力データを保存: {tsfresh_path} (rows={total_long_rows})")
        else:
            print("tsfresh用データが空のため保存をスキップしました。")

        meta_df = None
        if total_meta_rows > 0:
            meta_df = pd.read_csv(meta_path)
            save_path = os.path.join(OUTPUT_DIR, SamplePath + '_' + TargetPath + '_' + 'rmd.npy')
            np.save(save_path, meta_df.to_numpy())
            print(f"メタ情報+波形特徴量を保存: {meta_path} / {save_path}")
        else:
            print("メタ情報が空のため保存をスキップしました。")

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

        # このサンプルは完全に完了したので .done マーカーを作り、
        # チェックポイントJSONは不要になるので削除する
        with open(done_marker_path, 'w', encoding='utf-8') as f:
            f.write('done')
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)

    print('end')
