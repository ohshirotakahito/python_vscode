#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
file_rename.py

TOP_file_rename_for_ANAL_20250206.py を data_transfer_common.py ベースに
書き換えた統合スクリプト。

① create_folders → ② transfer_copy → (解析作業) → ③ cleanup_folders → ④ zip_stocked
の一連の流れとは別に、解析前後にファイル名を整形したい場合に使う。

対象フォルダ(サンプルフォルダ配下の T フォルダ、または T/ANAL フォルダ)
直下のファイルを走査し、旧命名のファイル名を
"{接頭辞}{日付}_{識別子}.tdms" 形式に統一してリネームする。

差分（対象サーバー・対象exフォルダ・対象サンプル・対象サブパス等）は、
下の if __name__ == '__main__': ブロック内の変数を書き換えて指定する。

【安全機能について】
リネームは元に戻せない操作なので、事故を防ぐために以下を用意している。

  1. dry_run（既定で有効）
     実際にはリネームせず、「何を何に変える予定か」を表示するだけ。
     まずこれで対象ファイルが正しいか確認してから、dry_run=False にして
     本実行すること。

  2. リネーム履歴のログ保存（本実行時のみ）
     本実行(dry_run=False)のたびに、実行した変更の対応表を
     filemanager/rename_logs/ 以下にCSVとして保存する
     （例: rename_log_20260819_144501.csv）。
     列: old_path, new_path, renamed_at

  3. undo_from_log() による復元
     2で保存したログファイルを渡すと、new_path -> old_path の向きに
     戻す（ログに記録された順序と逆順に実行する）。
     元ファイルが既に無い/上書き先が既に存在する等の場合は、
     その1件だけスキップしてエラー内容を表示する（他の行の処理は続行）。

使用例（下の __main__ ブロックに書く内容の例）
------
# Tフォルダ直下のリネーム（元 TOP_file_rename_for_ANAL_20250206.py 相当）
server = 'Rackstation'
keyfolder = 'analysis'
ex = 'Seeds_Kaneko'
samples = ['let7aW22']
sample_suffix = '_10k_Sample'
target_subpath = 'T'

# ANALフォルダ配下のリネームをしたい場合
target_subpath = 'T/ANAL'

# 少数のファイルだけで試す場合（安全確認用）
# → samples を1件だけにする、または max_files で件数を絞る

【動作確認の手順（推奨）】
  1. dry_run=True（既定）のまま一度実行し、表示される
     "変更予定" の一覧を目視で確認する。
  2. 問題なければ、対象を1〜2件（samples を絞る、または
     max_files=2 を指定）にした上で dry_run=False にして本実行する。
  3. 結果を確認する。想定と違っていたら、直後に表示される
     ログファイルパスを undo_from_log() に渡してすぐ元に戻す。
  4. 問題なければ、件数の制限を外して本実行する。
"""

import csv
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_transfer_common import (
    server_path,
    list_files_in_folder,
    now_str,
)

# 元コードの正規表現をそのまま踏襲
# 例: "AB@1234_5678_9012_ID01xxx.tdms" -> "AB@1234_5678_9012_ID01.tdms"
DEFAULT_PATTERN = r"([A-Z@0-9]+)(\d{4}_\d{4}_\d{4})_([A-Za-z0-9#]+).*"

# リネーム履歴ログの保存先（このスクリプトと同じ場所の rename_logs/ 配下）
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rename_logs')


def _new_log_path():
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return os.path.join(LOG_DIR, f'rename_log_{timestamp}.csv')


def rename_files(file_paths, pattern=DEFAULT_PATTERN, ext=".tdms",
                  dry_run=True, max_files=None, log_path=None):
    """
    file_paths内の各ファイルを、正規表現patternでパースして
    "{group1}{group2}_{group3}{ext}" 形式にリネームする。

    dry_run   : True（既定）なら実際にはリネームせず、予定だけを表示する。
                False にすると実際にリネームし、対応表をCSVログに保存する。
    max_files : 指定すると、対象ファイルの先頭からこの件数だけを処理する
                （少数のファイルだけで試したいときに使う）。
    log_path  : 本実行時にログを書き込むCSVパス。省略時は自動生成される
                （filemanager/rename_logs/rename_log_<timestamp>.csv）。

    戻り値: (renamed, skipped, log_path)
      renamed  : 実際に（またはdry_runなら予定として）リネームされる
                 (旧パス, 新パス) のタプルのリスト
      skipped  : パターン不一致でスキップされたファイルパスのリスト
      log_path : 本実行時は書き込んだログのパス。dry_run時はNone。
    """
    if max_files is not None:
        file_paths = file_paths[:max_files]

    renamed = []
    skipped = []

    for file_path in file_paths:
        file_name = os.path.basename(file_path)
        match = re.match(pattern, file_name)

        if not match:
            print(f"Skipping: {file_path} (No match)")
            skipped.append(file_path)
            continue

        base_name = match.group(1)
        date = match.group(2)
        identifier = match.group(3)

        new_file_name = f"{base_name}{date}_{identifier}{ext}"
        new_file_path = os.path.join(os.path.dirname(file_path), new_file_name)

        if dry_run:
            print(f"[DRY RUN] {file_path} -> {new_file_path}")
            renamed.append((file_path, new_file_path))
            continue

        if os.path.exists(new_file_path):
            print(f"⚠ スキップ（変更先が既に存在します）: {new_file_path}")
            skipped.append(file_path)
            continue

        os.rename(file_path, new_file_path)
        renamed.append((file_path, new_file_path))
        print(f"Renamed: {file_path} -> {new_file_path}", now_str())

    written_log_path = None
    if not dry_run and renamed:
        written_log_path = log_path or _new_log_path()
        write_header = not os.path.exists(written_log_path)
        with open(written_log_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(['old_path', 'new_path', 'renamed_at'])
            ts = now_str()
            for old_path, new_path in renamed:
                writer.writerow([old_path, new_path, ts])
        print(f"\nリネーム履歴を保存しました: {written_log_path}")
        print("元に戻したい場合は undo_from_log() にこのパスを渡してください。")

    if dry_run:
        print(f"\n[DRY RUN] {len(renamed)}件が変更対象、{len(skipped)}件がスキップ対象です。")
        print("問題なければ dry_run=False にして本実行してください。")

    return renamed, skipped, written_log_path


def undo_from_log(log_path):
    """
    rename_files() が dry_run=False 時に保存したCSVログを読み込み、
    new_path -> old_path の向きにリネームして元に戻す
    （ログの記録順と逆順に処理する）。

    - 既に new_path が存在しない（別の作業で消えた/移動した等）場合や、
      old_path が既に存在する（衝突する）場合は、その1件だけをスキップし、
      理由を表示して処理を続行する。
    - 戻り値: (reverted, skipped) それぞれ (old_path, new_path) のリスト
    """
    if not os.path.exists(log_path):
        print(f"ログファイルが見つかりません: {log_path}")
        return [], []

    with open(log_path, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    reverted = []
    skipped = []

    # 記録順と逆順に戻す（後から行われた変更から順に取り消す）
    for row in reversed(rows):
        old_path = row['old_path']
        new_path = row['new_path']

        if not os.path.exists(new_path):
            print(f"⚠ スキップ（現在のファイルが見つかりません）: {new_path}")
            skipped.append((old_path, new_path))
            continue

        if os.path.exists(old_path):
            print(f"⚠ スキップ（戻し先に既に別のファイルがあります）: {old_path}")
            skipped.append((old_path, new_path))
            continue

        os.rename(new_path, old_path)
        reverted.append((old_path, new_path))
        print(f"Reverted: {new_path} -> {old_path}")

    print(f"\n{len(reverted)}件を元に戻しました。{len(skipped)}件はスキップされました。")
    return reverted, skipped


def rename_in_sample(server, keyfolder, ex, sample, sample_suffix='_10k_Sample',
                      target_subpath='T', pattern=DEFAULT_PATTERN, ext='.tdms',
                      dry_run=True, max_files=None, log_path=None):
    """
    1サンプル分の対象フォルダのファイルをリネームする。

    server         : サーバー名 例: 'Rackstation'
    keyfolder      : 共有フォルダ名 例: 'analysis'
    ex             : 対象exフォルダ名
    sample         : 対象サンプル名
    sample_suffix  : サンプルフォルダ名の接尾辞 (デフォルト: '_10k_Sample')
    target_subpath : サンプルフォルダ配下、対象ファイルがあるサブパス
                     例: 'T' または 'T/ANAL'
    pattern        : ファイル名パース用の正規表現
    ext            : リネーム後の拡張子
    dry_run        : True（既定）なら予定表示のみ。False で本実行。
    max_files      : 指定件数だけ処理（安全確認用）
    log_path       : 本実行ログの書き込み先（rename()から複数サンプル分を
                     1つのログにまとめる際に使用）
    """
    sample_folder_name = sample + sample_suffix
    target_folder = server_path(server, keyfolder, ex, sample, sample_folder_name)
    if target_subpath:
        target_folder = target_folder + "/" + target_subpath

    file_paths = list_files_in_folder(target_folder)
    print(sample, ":", target_folder, len(file_paths), "files found")

    return rename_files(file_paths, pattern=pattern, ext=ext,
                         dry_run=dry_run, max_files=max_files, log_path=log_path)


def rename(server, keyfolder, ex, samples, sample_suffix='_10k_Sample',
           target_subpath='T', pattern=DEFAULT_PATTERN, ext='.tdms',
           dry_run=True, max_files=None):
    """
    複数サンプルに対してリネームを実行する。

    samples   : サンプル名のリスト
    dry_run   : True（既定）なら予定表示のみ。False で本実行。
    max_files : サンプルごとに、この件数だけ処理（安全確認用）

    戻り値: 本実行(dry_run=False)時、複数サンプル分をまとめた
            1つのログファイルのパス（何も変更が無かった場合はNone）。
            dry_run時は常にNone。
    """
    log_path = _new_log_path() if not dry_run else None

    for sample in samples:
        rename_in_sample(server, keyfolder, ex, sample,
                          sample_suffix=sample_suffix,
                          target_subpath=target_subpath,
                          pattern=pattern, ext=ext,
                          dry_run=dry_run, max_files=max_files,
                          log_path=log_path)
    print("end")
    return log_path


if __name__ == '__main__':
    #データ元のターゲットサーバー名
    server = 'Rackstation'

    #データ元のターゲットサーバー内の元フォルダの場所
    keyfolder = 'analysis'

    #データ元のターゲットサーバー内の元フォルダ内の対象フォルダの場所
    ex = 'Seeds_Kaneko'

    #サンプルリスト（Noneにすると list_subfolder_names で ex 直下の全フォルダが対象になる）
    samples = ['let7aW22']

    #サンプルフォルダ名の接尾辞
    sample_suffix = '_10k_Sample'

    #対象ファイルがあるサブパス（Tフォルダ直下なら 'T'、ANALフォルダ配下なら 'T/ANAL'）
    target_subpath = 'T'

    # ============================================================
    # 安全確認用の設定（初めて実行する時・新しいexフォルダで試す時は
    # 必ずこの状態のまま一度実行し、表示内容を確認すること）
    # ============================================================
    # True: 実際にはリネームしない（予定を表示するだけ）。まずこれで確認する。
    # False: 実際にリネームする。dry_runで確認が済んでから切り替えること。
    DRY_RUN = True

    # 最初の本実行では 1〜2 件程度に絞ってテストすることを推奨。
    # 件数制限をしない場合は None にする。
    MAX_FILES = 2
    # ============================================================

    if samples is None:
        from data_transfer_common import list_subfolder_names
        samples = list_subfolder_names(server_path(server, keyfolder, ex))

    rename(server, keyfolder, ex, samples,
           sample_suffix=sample_suffix, target_subpath=target_subpath,
           dry_run=DRY_RUN, max_files=MAX_FILES)

    # ============================================================
    # 元に戻したくなった場合は、上の本実行で表示されたログファイルパスを
    # 使って以下のように実行する（別途、対話環境やコメントアウトを外して使用）:
    #
    # from file_rename import undo_from_log
    # undo_from_log(r"D:\Python_VScode\filemanager\rename_logs\rename_log_20260819_144501.csv")
    # ============================================================
