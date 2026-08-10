# -*- coding: utf-8 -*-
"""
check_available_tdms_data.py

【このスクリプトについて】
extract_features_*.py を実行する前に、
  server / keyfolder / ex / sample
の組み合わせについて、以下の2つのフォルダそれぞれの .tdms ファイル数を
確認するためのツール。

  - <sample>_10k_Sample\\T\\*.tdms          : 生データ側のイベント数(n_t)
  - <sample>_10k_Sample\\T\\ANAL\\*.tdms    : 解析済みイベント数(n_anal)

sample一覧の取得には、本番の抽出スクリプトと同じ経路(common/tdms_io.py の
exfoler_check())を使う。T/ANALフォルダのtdms数は、Analtfoler()が返す
raw data配下のEXSV/EXZSV等も含めた全フォルダの合算ではなく、上記2フォルダ
だけをピンポイントで直接数える（合算だと「解析済みかどうか」が
n_anal=0でも他のフォルダのカウントに埋もれて見えなくなるため）。

n_anal == 0 の場合は「そのサンプルはまだ解析(ANAL変換)が行われていない」
ことを意味するため、status='ANAL_NOT_DONE' として明示的に区別する。

【出力先について(今回の変更点)】
以前はリポジトリ直下に available_tdms_data_<timestamp>.csv として
保存していたが、実行のたびにファイルが増えてルートフォルダが散らかる上、
Git管理からも外れにくい場所だった。
今回から common/paths.py の data_check_dir(server) を使い、
  ML/data_checks/<server>/<timestamp>_available_tdms_data.csv  … 実行履歴として蓄積
  ML/data_checks/<server>/latest.csv                            … 常に最新版を上書き保存
の2つに分けて保存するようにした（サーバーが複数指定された場合は、
結果をサーバーごとにグループ分けしてから、それぞれのフォルダに保存する）。
このフォルダは .gitignore で除外される想定（他のdata/models/resultsと同様）。

【使い方】
1. 下の CONFIG セクションを書き換える。
   - SERVERS / KEYFOLDERS は絞りたい場合はリストで指定（例: ['Rackstation']）。
   - EX_LIST は必ず指定する（exfoler_check()自体がexを引数に取るため、
     「exの一覧を自動列挙する」既存関数が無い前提。もし共通関数がある
     場合は discover_ex_names() を書き換えて差し替えてください）。
   - SAMPLE_LIST は None なら exfoler_check() の結果をそのまま全部使う。
2. 実行する:
     python check_available_tdms_data.py
3. 結果はコンソール表示 + ML/data_checks/<server>/ 以下のCSVに出力。
"""

import os
import csv
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import common.paths as paths
from common.tdms_io import exfoler_check

# ============================================================
# CONFIG（ここを必要に応じて書き換える）
# ============================================================

SERVERS = ['QDserver']      # None -> ['QTserver', 'QDserver', 'Rackstation']
KEYFOLDERS =['analysis']  # None -> ['analysis', 'archive']

# ex（実験フォルダ）は既存関数に「一覧を取得する」ものが無いため、
# 基本的にはここで直接指定する。
# 例: EX_LIST = ['Shanli_thy', 'Sakano_01', 'Sakano_02']
EX_LIST = None #['Shanli_thy']

# sampleを絞りたい場合はリストで指定。None なら exfoler_check() の結果を全部使う。
SAMPLE_LIST = None

MAX_WORKERS = 16


# ============================================================
# ex一覧の自動列挙（フォールバック。tdms_io.py に対応する専用関数が
# あるなら、そちらに差し替えることを推奨）
# ============================================================

def discover_ex_names(server, keyfolder):
    """//server/keyfolder/ 直下のフォルダ名一覧を返す（アクセス不能ならNone）。
    common/tdms_io.py に相当する専用関数が見つかった場合はそちらに置き換えること。
    """
    path = f'//{server}/{keyfolder}/'
    try:
        with os.scandir(path) as it:
            return sorted(e.name for e in it if e.is_dir())
    except (FileNotFoundError, PermissionError, NotADirectoryError, OSError):
        return None


# ============================================================
# T / ANAL フォルダのtdmsカウント
# ============================================================

def count_tdms_direct(path: str):
    """指定フォルダの直下（サブフォルダは見ない）にある.tdmsファイル数を返す。
    フォルダが存在しない/アクセスできない場合は None。
    """
    if not os.path.isdir(path):
        return None
    try:
        with os.scandir(path) as it:
            return sum(1 for e in it if e.is_file() and e.name.lower().endswith('.tdms'))
    except (PermissionError, OSError):
        return None


def unc_path(*parts) -> str:
    return '//' + '/'.join(str(p) for p in parts)


def check_one_sample(server, keyfolder, ex, sample):
    """T フォルダと T\\ANAL フォルダそれぞれの tdms 数を直接数える。"""
    sample_root = unc_path(server, keyfolder, ex, sample)
    t_dir = os.path.join(sample_root, f"{sample}_10k_Sample", "T")
    anal_dir = os.path.join(t_dir, "ANAL")

    n_t = count_tdms_direct(t_dir)
    n_anal = count_tdms_direct(anal_dir)

    if n_t is None:
        status = 'NO_T_FOLDER'          # <sample>_10k_Sample\T 自体が無い
    elif n_t == 0:
        status = 'T_EMPTY'              # Tフォルダはあるがtdmsが0件
    elif n_anal is None:
        status = 'NO_ANAL_FOLDER'       # ANALフォルダ自体が無い(=未解析)
    elif n_anal == 0:
        status = 'ANAL_NOT_DONE'        # ★ ご指摘の「解析未達」ケース
    else:
        status = 'OK'

    return {
        'server': server, 'keyfolder': keyfolder, 'ex': ex, 'sample': sample,
        'n_t': n_t if n_t is not None else 0,
        'n_anal': n_anal if n_anal is not None else 0,
        't_dir': t_dir,
        'anal_dir': anal_dir,
        'status': status,
    }


# ============================================================
# CSV保存（サーバーごとに ML/data_checks/<server>/ 以下へ）
# ============================================================

CSV_FIELDNAMES = ['server', 'keyfolder', 'ex', 'sample', 'n_t', 'n_anal',
                   't_dir', 'anal_dir', 'status']


def save_results_by_server(results, timestamp):
    """resultsをserver列でグループ分けし、サーバーごとのフォルダに
    タイムスタンプ付きCSV(履歴用)とlatest.csv(常に最新版)を保存する。

    戻り値: {server: 保存したタイムスタンプ付きCSVのPath} の辞書
    """
    by_server = {}
    for r in results:
        by_server.setdefault(r['server'], []).append(r)

    saved_paths = {}
    for server, server_results in by_server.items():
        out_dir = paths.data_check_dir(server)

        # 履歴用（実行のたびに増える）
        dated_path = out_dir / f"{timestamp}_available_tdms_data.csv"
        with open(dated_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(server_results)

        # 最新版（常に同じファイル名に上書き）
        latest_path = out_dir / "latest.csv"
        with open(latest_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(server_results)

        saved_paths[server] = dated_path
        print(f"  [{server}] 保存: {dated_path}  (latest.csv も更新)")

    return saved_paths


# ============================================================
# メイン処理
# ============================================================

def main():
    t0 = time.time()

    # --- 1. samples一覧をまず出す（exfoler_check経由） ---
    tasks = []  # (server, keyfolder, ex, sample)
    for server in SERVERS:
        for keyfolder in KEYFOLDERS:
            ex_names = EX_LIST if EX_LIST is not None else discover_ex_names(server, keyfolder)
            if not ex_names:
                print(f"[SKIP] {server}/{keyfolder}: exが見つかりません（未指定 or アクセス不可）")
                continue

            for ex in ex_names:
                try:
                    samples = exfoler_check(server, keyfolder, ex)
                except Exception as e:
                    print(f"[ERROR] exfoler_check({server}, {keyfolder}, {ex}) 失敗: {e}")
                    continue

                if not samples:
                    print(f"[INFO] {server}/{keyfolder}/{ex}: sampleが0件でした")
                    continue

                target_samples = SAMPLE_LIST if SAMPLE_LIST is not None else samples
                print(f"[{server}/{keyfolder}/{ex}] samples ({len(samples)}件): {samples}")

                for sample in target_samples:
                    tasks.append((server, keyfolder, ex, sample))

    print(f"\n合計 {len(tasks)} 件のsampleについて、T/ANALフォルダのtdms数を確認します...")

    # --- 2. 各sampleのT/ANALフォルダのtdms数を並列カウント ---
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_one_sample, *task): task for task in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if i % 10 == 0 or i == len(futures):
                print(f"  {i}/{len(futures)} 件確認済み...", end='\r')
    print()

    order = {'OK': 0, 'ANAL_NOT_DONE': 1, 'NO_ANAL_FOLDER': 2, 'T_EMPTY': 3, 'NO_T_FOLDER': 4}
    results.sort(key=lambda r: (order.get(r['status'], 9), r['server'], r['keyfolder'],
                                 r['ex'], r['sample']))

    ok_results = [r for r in results if r['status'] == 'OK']
    anal_pending = [r for r in results if r['status'] == 'ANAL_NOT_DONE']
    other_problems = [r for r in results if r['status'] not in ('OK', 'ANAL_NOT_DONE')]

    print(f"\n=== 解析済み・処理可能: {len(ok_results)} 件 ===")
    for r in ok_results:
        print(f"  server={r['server']:<11} keyfolder={r['keyfolder']:<9} "
              f"ex={r['ex']:<15} sample={r['sample']:<20} "
              f"T={r['n_t']}件 / ANAL={r['n_anal']}件")

    if anal_pending:
        print(f"\n=== ★ 解析未達（Tにtdmsはあるが ANAL が0件）: {len(anal_pending)} 件 ===")
        for r in anal_pending:
            print(f"  server={r['server']:<11} keyfolder={r['keyfolder']:<9} "
                  f"ex={r['ex']:<15} sample={r['sample']:<20} "
                  f"T={r['n_t']}件 / ANAL=0件")

    if other_problems:
        print(f"\n=== 要確認（Tフォルダが無い/空等）: {len(other_problems)} 件 ===")
        for r in other_problems:
            print(f"  [{r['status']}] server={r['server']} keyfolder={r['keyfolder']} "
                  f"ex={r['ex']} sample={r['sample']}")

    # --- 3. CSV保存（サーバーごとに ML/data_checks/<server>/ 以下へ） ---
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    saved_paths = save_results_by_server(results, timestamp)

    print(f"\n所要時間: {time.time() - t0:.1f}秒")

    if ok_results:
        print("\n--- extract_features_*.py にコピペできる設定例（先頭5件） ---")
        for r in ok_results[:5]:
            print(f"# server='{r['server']}', keyfolder='{r['keyfolder']}', "
                  f"ex='{r['ex']}', samples=['{r['sample']}']  "
                  f"# T={r['n_t']}件 / ANAL={r['n_anal']}件")

    return results, saved_paths


if __name__ == '__main__':
    main()
