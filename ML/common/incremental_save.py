# -*- coding: utf-8 -*-
"""
common/incremental_save.py

【このモジュールについて】
extract_features_traditional.py / extract_features_tsfresh.py で、同じサンプル名（例: 'Guanine'）を
複数回（別日に追加計測したデータを含めて）実行しても、既存の特徴量ファイルを
上書きせず、新しく見つかったイベントだけを追記できるようにするための
共通ユーティリティ。

【重複判定について】
apick()（common/tdms_io.py）が返す各イベント行は
    [event_id, file(sample_name), Ex_ID, distance, sample, signal_position, ...]
という並びになっている。このうち (file, signal_position) の組み合わせを
イベントの一意キーとして扱う。
  - file            : tdmsファイル由来の識別子（例 'GO6MeG1-1_10k_Sample#001'）
  - signal_position : イベントのピーク位置（tdms内の絶対時間位置、*10000した整数）
同じtdmsファイルを再度読み込んでapick()し直しても、同じイベントは同じ
(file, signal_position) になるはずなので、これを使って
「既に保存済みのイベントかどうか」を判定する。

【event_idについて】
apick()/collect_events_for_sample() は毎回 event_id を 0 から採番し直す。
そのままでは既存データの event_id と衝突するため、新規追加分の event_id は
「既存データの最大 event_id + 1」からオフセットして振り直す。
tsfresh用 long format 行（rmc版で使用）の 'id' 列も同じオフセットでずらし、
meta側の event_id と整合させる。

【保存されている.npyの型について】
apick() の出力は event_id(int)・文字列・float が混在したリストのリストであり、
np.save()時にnumpyが自動的に共通の文字列dtype（例: '<U38'）へキャストする。
このモジュールもその挙動を踏襲し、明示的なdtype指定はせず、
np.array(merged_rows) が自動キャストするのに任せている
（呼び出し側の既存コードが文字列前提でastype(float)等を行っているため、
 挙動を変えないようにするため）。
"""

from typing import List, Optional, Tuple

import numpy as np

# apick() が返す行の中での列インデックス（common/tdms_io.py の apick()/
# META_COLUMNS の並びと一致させること）
EVENT_ID_COL_IDX = 0
FILE_COL_IDX = 1
SIGNAL_POSITION_COL_IDX = 5


def load_existing_npy(path):
    """既存の.npyがあれば読み込み、無ければNoneを返す。

    path: pathlib.Path を想定（os.path.exists / .exists() どちらでも動くよう
          呼び出し側で Path化しておくこと）
    """
    if path is None:
        return None
    if not path.exists():
        return None
    return np.load(path, allow_pickle=True)


def merge_new_events(
    existing_array: Optional[np.ndarray],
    new_cx: List[list],
    new_long_rows: Optional[List[dict]] = None,
) -> Tuple[np.ndarray, Optional[List[dict]], int, int]:
    """既存データ(existing_array)と新規抽出データ(new_cx)をマージする。

    - 既存データに無い (file, signal_position) の組み合わせのイベントだけを
      「新規」とみなして追加する（重複していたイベントはスキップする）。
    - 新規追加分の event_id は、既存データの最大 event_id + 1 から振り直す。
    - new_long_rows（tsfresh用 long format 行）が渡された場合、
      重複としてスキップされたイベントに属する行は除外し、
      新規に追加されたイベントの行だけを event_id のオフセット後の値に
      付け替えて返す（呼び出し側で既存の tsfresh_input.csv と結合すること）。

    戻り値: (merged_array, merged_long_rows, n_added, n_skipped_duplicate)
      merged_array      : 既存 + 新規追加分をまとめた配列（そのまま np.save 可能）
      merged_long_rows  : 新規追加分のみの long format 行のリスト（未指定ならNone）
      n_added           : 実際に追加された新規イベント数
      n_skipped_duplicate: 重複と判定されてスキップされたイベント数
    """
    if existing_array is None or len(existing_array) == 0:
        existing_rows: List[list] = []
        existing_keys = set()
        max_event_id = -1
    else:
        existing_rows = [list(row) for row in existing_array]
        existing_keys = {
            (str(row[FILE_COL_IDX]), str(row[SIGNAL_POSITION_COL_IDX]))
            for row in existing_rows
        }
        max_event_id = max(
            int(float(row[EVENT_ID_COL_IDX])) for row in existing_rows
        )

    id_offset = max_event_id + 1

    new_rows_to_add: List[list] = []
    id_remap = {}  # 今回の抽出時点のevent_id -> オフセット後のevent_id
    n_skipped = 0

    for row in new_cx:
        row = list(row)
        key = (str(row[FILE_COL_IDX]), str(row[SIGNAL_POSITION_COL_IDX]))

        if key in existing_keys:
            n_skipped += 1
            continue

        old_event_id = row[EVENT_ID_COL_IDX]
        new_event_id = int(old_event_id) + id_offset
        id_remap[old_event_id] = new_event_id
        row[EVENT_ID_COL_IDX] = new_event_id

        new_rows_to_add.append(row)
        existing_keys.add(key)  # 同一バッチ内での重複追加も防止

    merged_rows = existing_rows + new_rows_to_add
    if merged_rows:
        merged_array = np.array(merged_rows)
    else:
        merged_array = np.empty((0,), dtype=object)

    merged_long_rows: Optional[List[dict]] = None
    if new_long_rows is not None:
        merged_long_rows = []
        for r in new_long_rows:
            old_id = r.get("id")
            if old_id not in id_remap:
                # 重複としてスキップされたイベントに属する行は除外
                continue
            r2 = dict(r)
            r2["id"] = id_remap[old_id]
            merged_long_rows.append(r2)

    return merged_array, merged_long_rows, len(new_rows_to_add), n_skipped
