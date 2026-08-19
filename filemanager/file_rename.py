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
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_transfer_common import (
    server_path,
    list_files_in_folder,
    now_str,
)

# 元コードの正規表現をそのまま踏襲
# 例: "AB@1234_5678_9012_ID01xxx.tdms" -> "AB@1234_5678_9012_ID01.tdms"
DEFAULT_PATTERN = r"([A-Z@0-9]+)(\d{4}_\d{4}_\d{4})_([A-Za-z0-9#]+).*"


def rename_files(file_paths, pattern=DEFAULT_PATTERN, ext=".tdms"):
    """
    file_paths内の各ファイルを、正規表現patternでパースして
    "{group1}{group2}_{group3}{ext}" 形式にリネームする。
    元コードの rename_files と同一ロジック（拡張子を引数化）。
    """
    renamed = []
    for file_path in file_paths:
        file_name = os.path.basename(file_path)
        match = re.match(pattern, file_name)

        if match:
            base_name = match.group(1)
            date = match.group(2)
            identifier = match.group(3)

            new_file_name = f"{base_name}{date}_{identifier}{ext}"
            new_file_path = os.path.join(os.path.dirname(file_path), new_file_name)

            os.rename(file_path, new_file_path)
            renamed.append(new_file_path)
            print(f"Renamed: {file_path} -> {new_file_path}", now_str())
        else:
            print(f"Skipping: {file_path} (No match)")
    return renamed


def rename_in_sample(server, keyfolder, ex, sample, sample_suffix='_10k_Sample',
                      target_subpath='T', pattern=DEFAULT_PATTERN, ext='.tdms'):
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
    """
    sample_folder_name = sample + sample_suffix
    target_folder = server_path(server, keyfolder, ex, sample, sample_folder_name)
    if target_subpath:
        target_folder = target_folder + "/" + target_subpath

    file_paths = list_files_in_folder(target_folder)
    print(sample, ":", target_folder, len(file_paths), "files found")

    return rename_files(file_paths, pattern=pattern, ext=ext)


def rename(server, keyfolder, ex, samples, sample_suffix='_10k_Sample',
           target_subpath='T', pattern=DEFAULT_PATTERN, ext='.tdms'):
    """
    複数サンプルに対してリネームを実行する。

    samples : サンプル名のリスト
    """
    for sample in samples:
        rename_in_sample(server, keyfolder, ex, sample,
                          sample_suffix=sample_suffix,
                          target_subpath=target_subpath,
                          pattern=pattern, ext=ext)
    print("end")


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

    if samples is None:
        from data_transfer_common import list_subfolder_names
        samples = list_subfolder_names(server_path(server, keyfolder, ex))

    rename(server, keyfolder, ex, samples,
           sample_suffix=sample_suffix, target_subpath=target_subpath)
