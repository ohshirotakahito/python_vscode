# -*- coding: utf-8 -*-
"""
common/tdms_io.py

【このモジュールについて】
以下の4ファイルに重複していたTDMS読込・イベント抽出処理を統合したもの。
  - TOP_Feex_rmb_20260803.py
  - TOP_Feex_rmc_tsfresh_20260803.py
  - TOP_Feex_rmc_tsfresh_chronos_ad_data.py
  - TOP_Feex_rmd_tsfresh_chronos_20260803.py

各ファイルの apick/clip_segment/fp/build_tsfresh_rows/exfoler_check/
tdms_checker/Analtfoler/tdmslist_files はコメント・docstringの差異を除き
完全に同一のロジックだったため、最も整理されたバージョン（docstring付き・
Analtfolerがglobで再帰探索するバージョン）をこのモジュールに一本化した。

【rmb.py からの変更点（統合にあたっての唯一の実質変更）】
rmb.py だけは古い apick（event_id を持たず AX のみを返す・tsfresh用の
long_rows を作らない・Analtfolerがos.listdirの非再帰探索）を使っていた。
統合にあたり rmb.py 側もこのモジュールの新しい apick/Analtfoler に統一した。
影響:
  - rmb.py の出力データに event_id 列が追加される（後方互換: 既存の列は
    そのまま残るので、この列を使わない既存コードは影響を受けない）
  - Analtfoler がサブフォルダをより深い階層まで再帰的に探索するようになる
    （'ANAL' を含むフォルダの検出漏れが減る想定）
  - tsfresh用 long_rows も生成されるようになる（rmb.py 側で使わなければ
    単に破棄すればよい）
"""

import os
import glob
import math

import nptdms
import numpy as np
from tqdm import tqdm


def clip_segment(Y_data, st, ss, se):
    """イベント周辺の波形切り出し（fp / tsfresh 双方から共有）。

    Y_data: 1D ndarray（pA単位想定）
    st: シグナル長（既に*10済みの整数）
    ss, se: シグナルの開始・終了サンプルインデックス（*10000済み）

    戻り値: (start, stop, y0_data)
      start, stop: Y_data上の切り出し範囲（クリップ済み）
      y0_data: 切り出された生の波形（1D配列）。範囲が無効/短すぎる場合は空配列。
    """
    # 半窓
    mr = int(st / 2)

    # 切り出し範囲をクリップ（0〜len(Y_data)）
    start = max(0, ss - 1 - mr)
    stop = min(len(Y_data), se + 1 + mr)

    seg_len = stop - start
    if seg_len <= 1:
        return start, stop, np.array([])

    y0_data = Y_data[start:stop]
    return start, stop, y0_data


def fp(Y_data, st, sb, ss, se, si, ss_n):
    """特徴量作成関数（堅牢化）。

    Y_data: 1D ndarray（pA単位想定）
    st: シグナル長（ms→ここでは既に*10済みで整数）
    ss, se: サンプルインデックス（*10000 済み）
    sb: baseline (pA)
    si: scale (pA)  ※ゼロ割り保護あり
    ss_n: 分割数（13 推奨） → 出力は ss_n-1 個
    """
    _, _, y0_data = clip_segment(Y_data, st, ss, se)

    if len(y0_data) <= 1:
        return [0.0] * (ss_n - 1)

    sn = ss_n
    sru = len(y0_data) / sn  # 区間長の実数

    PX = []
    # u = 0..sn-2 で ss_n-1 点の特徴量
    for u in range(sn - 1):
        x = sru * u  # float
        mf = int(math.floor(x))
        mc = int(math.ceil(x))

        # 境界処理
        if mf < 0:
            mf = 0
        if mc < 0:
            mc = 0
        if mf >= len(y0_data):
            mf = len(y0_data) - 1
        if mc >= len(y0_data):
            mc = len(y0_data) - 1

        if mc == mf:
            # 同一点 → 補間不要
            y_val = float(y0_data[mf])
        else:
            y1 = float(y0_data[mf])
            y2 = float(y0_data[mc])
            denom = (mc - mf)
            if denom == 0:
                y_val = y1
            else:
                y_val = y1 + (y2 - y1) * ((x - mf) / denom)

        # ベースライン補正・正規化（pA → mA換算の *1000 は元コード踏襲）
        denom_si = si if si != 0 else 1e-12  # ゼロ割り回避
        y_scaled = round((y_val * 1000 - sb) / denom_si, 5)
        PX.append(y_scaled)

    return PX


def build_tsfresh_rows(Y_data, event_id, st, sb, ss, se, si):
    """1イベント分の切り出し波形を、tsfresh が要求する long format
    （id, time, value の3列）に変換する。

    - fp() のようにリサンプリング（12点圧縮）は行わず、
      サンプリング解像度をそのまま保持する（tsfreshは可変長系列を扱えるため）。
    - ベースライン補正・スケール正規化は fp() と同じ式を適用し、
      イベント間でスケールを揃える（tsfreshの特徴量が絶対値スケールに
      引っ張られないようにするため）。

    戻り値: list[dict]  例: [{'id': event_id, 'time': 0, 'value': ...}, ...]
            切り出しが無効な場合は空リスト。
    """
    _, _, y0_data = clip_segment(Y_data, st, ss, se)

    if len(y0_data) <= 1:
        return []

    denom_si = si if si != 0 else 1e-12  # ゼロ割り回避（fp()と同じ処理）

    rows = []
    for t, y in enumerate(y0_data):
        y_scaled = (float(y) * 1000 - sb) / denom_si
        rows.append({'id': event_id, 'time': t, 'value': y_scaled})

    return rows


def apick(path_load, sample, start_event_id=0):
    """ANALファイル（1つのtdmsファイル）より実験情報・イベントを取得する関数。

    戻り値:
      AX          : 既存の特徴量行リスト（各行の先頭に event_id を追加）
      long_rows   : tsfresh用 long format 行のリスト（dictのlist）
      next_event_id: 次に採番すべき event_id（呼び出し側でファイルをまたいで
                     累積カウントするために返す）
    """
    AX = []
    long_rows = []
    event_id = start_event_id
    try:
        # Tdmsファイル読み込み
        tdms_file = nptdms.TdmsFile(path_load)

        # 時系列データ（raw data）
        Y_data = tdms_file['Data']['Ch1'].raw_data

        # 実験情報
        Data_name = tdms_file['AR Table']['Filename'].raw_data
        Distance = tdms_file['AR Table']['Distance (nm)'].raw_data
        Ex_ID = tdms_file['AR Table']['Ex ID'].raw_data

        target0 = ' D_'
        F_name = Data_name[0]
        idx0 = F_name.find(target0)

        Sample_name = F_name[:idx0 + len(target0) - 3]
        e0 = Sample_name
        e1 = Ex_ID[0]
        e2 = Distance[0]
        e3 = sample

        # S_Table data の再構成
        SP_data = tdms_file['S Table']['S Peak Position [s]'].raw_data
        SI_data = tdms_file['S Table']['Signal [pA]'].raw_data
        SB_data = tdms_file['S Table']['Region BL [pA]'].raw_data
        SD_data = tdms_file['S Table']['Region STD [pA]'].raw_data
        SS_data = tdms_file['S Table']['Signal S [s]'].raw_data
        SE_data = tdms_file['S Table']['Signal E (s)'].raw_data
        ST_data = tdms_file['S Table']['S TL [ms]'].raw_data
        SL_data = tdms_file['S Table']['S DL [s]'].raw_data

        # 型変換
        SP_data = [float(s) for s in SP_data]
        SI_data = [float(s) for s in SI_data]
        SB_data = [float(s) for s in SB_data]
        SD_data = [float(s) for s in SD_data]
        SS_data = [float(s) for s in SS_data]
        SE_data = [float(s) for s in SE_data]
        ST_data = [float(s) for s in ST_data]
        SL_data = [float(s) for s in SL_data]

        count = 0

        for m in tqdm(range(len(SP_data))):
            ssp = []
            sp = SP_data[m] * 10000
            ss = SS_data[m] * 10000
            se = SE_data[m] * 10000
            st = ST_data[m] * 10
            si = SI_data[m]
            sb = SB_data[m]
            sd = SD_data[m]
            sl = SL_data[m] * 10000

            sp = int(sp)
            ss = int(ss)
            se = int(se)
            st = int(st)

            sb = round(sb, 6)
            sd = round(sd, 6)
            sl = round(sl, 6)

            if st > 10:  # default = 10
                ss_n = 13  # 分割数設定
                ssp = fp(Y_data, st, sb, ss, se, si, ss_n)
                count += 1

                # このイベントの一意ID（ファイルをまたいで累積カウント）
                current_event_id = event_id
                event_id += 1

                # データ行（先頭に event_id を追加。tsfresh特徴量と結合する際のキーになる）
                XX = [current_event_id, e0, e1, e2, e3, sp, si, st, ss, se, sb] + ssp
                AX.append(XX)

                # tsfresh用 long format 行を収集
                rows = build_tsfresh_rows(Y_data, current_event_id, st, sb, ss, se, si)
                long_rows.extend(rows)

    except ValueError as e:
        print(f"ValueError in apick ({path_load}): {e}")
        AX = []
        long_rows = []

    except KeyError as e:
        # 想定外のグループ/チャンネル名不足など
        print(f"KeyError in apick ({path_load}): {e}")
        AX = []
        long_rows = []

    except (OSError, IOError) as e:
        # ファイルが開けない・存在しない等
        print(f"File error in apick ({path_load}): {e}")
        AX = []
        long_rows = []

    except Exception as e:
        # 想定外のその他エラー（ファイル破損など）を捕捉してクラッシュを防ぐ
        print(f"Unexpected error in apick ({path_load}): {e}")
        AX = []
        long_rows = []

    return AX, long_rows, event_id


def exfoler_check(server, keyfolder, ex):
    """実験フォルダ配下のサブフォルダ（=サンプル名）一覧を取得する。"""
    exFolderpath = '//' + server + '/' + keyfolder + '/' + ex
    ex_subfolders = []
    for item in os.listdir(exFolderpath):
        item_path = os.path.join(exFolderpath, item)
        if os.path.isdir(item_path):
            last_part = os.path.basename(item_path)
            ex_subfolders.append(last_part)
    return ex_subfolders


def tdms_checker(path_load):
    """tdmsファイル中のグループ/チャンネルの存在判定を、数字で返す。

    1: 正常（'S Table'/'Signal [pA]' が存在しデータ点数3以上）
    2: データ点数が不足
    3: 'Signal [pA]' チャンネルがない
    4: 'S Table' グループがない
    0: ファイル読み込み自体に失敗
    """
    target_group_name = 'S Table'
    target_channel_name = 'Signal [pA]'

    try:
        with nptdms.TdmsFile.read(path_load) as tdms_file:
            if target_group_name in tdms_file:
                group = tdms_file[target_group_name]
                if target_channel_name in group:
                    channel = group[target_channel_name]
                    data_point_count = len(channel)
                    if data_point_count >= 3:
                        echec = 1
                    else:
                        echec = 2
                else:
                    echec = 3
            else:
                echec = 4
    except Exception:
        echec = 0

    return echec


def Analtfoler(server, keyfolder, ex, sample):
    """指定サンプル配下から 'ANAL' を含むフォルダを再帰的に探索して一覧を返す。"""
    ServerPath = '//' + server + '/' + keyfolder + '/'
    DataPath = ex + '/' + sample + '/'
    SamplePath = sample + '_10k_Sample'

    # 探索の起点となるパス
    Folderpath = ServerPath + DataPath + SamplePath + '/T'

    if not os.path.isdir(Folderpath):
        return []

    # globで、何階層下にあっても 'ANAL' を含むフォルダをすべて列挙する
    search_pattern = os.path.join(Folderpath, '**', '*ANAL*')
    all_matched_paths = glob.glob(search_pattern, recursive=True)

    # ディレクトリ（フォルダ）であるものだけを抽出して返す
    f_subfolders = [p for p in all_matched_paths if os.path.isdir(p)]

    print(f"[{sample}] 発見した ANAL フォルダ数: {len(f_subfolders)}")
    return f_subfolders


def tdmslist_files(folder_path):
    """フォルダ中の .tdms ファイル名のみを抽出する。"""
    if not os.path.isdir(folder_path):
        return []
    file_list = os.listdir(folder_path)
    tdms_files = [filename for filename in file_list if filename.endswith('.tdms')]
    return tdms_files


def collect_events_for_sample(server, keyfolder, ex, sample):
    """1サンプル分の 'ANAL' フォルダを全て探索し、イベント特徴量(CX)と
    tsfresh用long format行(ALL_LONG_ROWS)をまとめて返す便利関数。

    各 TOP_Feex_*.py の main 部分で共通していた
    「folderlist取得 → tdmsファイル一覧取得 → tdms_checkerでフィルタ → apick」
    のループをそのまま関数化したもの。
    """
    folderlist = Analtfoler(server, keyfolder, ex, sample)

    CX = []
    ALL_LONG_ROWS = []
    next_event_id = 0

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
                    ALL_LONG_ROWS.extend(long_rows)

    return CX, ALL_LONG_ROWS


# 特徴量行(CX)の列名（先頭に event_id。ssp（12点波形特徴量）は後ろに可変長で続く）
META_COLUMNS = ['event_id', 'sample_name', 'ex_id', 'distance', 'sample',
                'peak_pos', 'signal', 'duration', 'start', 'end', 'baseline']


def append_df_to_csv(df, path, wrote_header_flag):
    """dfをCSVに追記する。ファイル未作成（wrote_header_flag=False）なら
    ヘッダー付きで新規作成し、以降はヘッダーなしで追記する。

    大量イベントをメモリに溜め込まず、tdmsファイル1つ処理するたびに
    その場でCSVへ書き出していくための補助関数
    （TOP_Feex_rmd_tsfresh_chronos.py のようなメモリ対策版で使用）。

    戻り値: 更新後の wrote_header_flag
    """
    if df is None or df.empty:
        return wrote_header_flag

    if not wrote_header_flag:
        df.to_csv(path, index=False, mode='w')
    else:
        df.to_csv(path, index=False, mode='a', header=False)
    return True
