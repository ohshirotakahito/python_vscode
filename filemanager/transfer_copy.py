#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
transfer_copy.py
 
TOP_Data_transfer_for_ANAL.py / for_BNAL.py / for_file.py / for_Text.py
を統合した汎用コピースクリプト。
差分（拡張子・検索キーワード・作成するフォルダツリー等）は、
下の if __name__ == '__main__': ブロック内の変数を書き換えて指定する。
 
使用例（下の __main__ ブロックに書く内容の例）
------
# ANAL用 (.tdms, sample/T 配下の "ANAL" を含むフォルダを検索してコピー)
#   ※ 元 TOP_Data_transfer_for_ANAL.py / for_file.py 相当
server = 'Rackstation'
source_keyfolder = 'analysis'
dest_keyfolder = 'RT_server'
ex = 'Kumamoto_N2'
samples = None                 # Noneならex直下の全フォルダが対象
ext = '.tdms'
sample_suffix = '_10k_Sample'
search_subpath = 'T'
keyword = 'ANAL'
extra_tree = ['T', 'stocked', 'T/ANAL', 'T/stocked']
 
# BNAL用 (.tdms, sample/T 配下の "BNAL@" を含むフォルダを検索してコピー)
#   ※ 元 TOP_Data_transfer_for_BNAL.py 相当
server = 'Rackstation'
source_keyfolder = 'analysis'
dest_keyfolder = 'RT_server'
ex = 'Kiyotani'
samples = ['0272', '0380']
ext = '.tdms'
sample_suffix = '_10k_Sample'
search_subpath = 'T'
keyword = 'BNAL@'
extra_tree = ['T']
 
# Text用 (.txt, sampleフォルダ直下から直接コピー、キーワード検索なし)
#   ※ 元 TOP_Data_transfer_for_Text.py 相当
server = 'Rackstation'
source_keyfolder = 'archives'
dest_keyfolder = 'QT_server'
ex = 'Chirality_N2'
samples = ['LTyr', 'LVal']
ext = '.txt'
sample_suffix = '_10k_Sample'
search_subpath = ''
keyword = None
extra_tree = []
"""
 
import os
import sys
 
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_transfer_common import (
    server_path,
    list_subfolder_names,
    list_subfolder_paths,
    ensure_folders,
    copy_files_by_ext,
    now_str,
)
 
 
def transfer(server, source_keyfolder, dest_keyfolder, ex, samples, ext,
             sample_suffix='_10k_Sample', search_subpath='', keyword=None,
             extra_tree=None):
    """
    サーバー間ファイル転送(コピー)を実行する。
 
    server            : サーバー名 例: 'Rackstation'
    source_keyfolder  : コピー元の共有フォルダ名 例: 'analysis'
    dest_keyfolder    : コピー先の共有フォルダ名 例: 'RT_server'
    ex                : 対象exフォルダ名
    samples           : サンプル名のリスト。Noneならex直下の全フォルダを対象にする
    ext               : コピー対象の拡張子 例: '.tdms' '.txt'
    sample_suffix     : サンプルフォルダ名の接尾辞 (デフォルト: '_10k_Sample')
    search_subpath    : サンプルフォルダ配下の検索対象サブパス 例: 'T'
                        (省略時はサンプルフォルダ直下)
    keyword           : 検索対象サブパス内でこの文字列を含むフォルダのみコピー元にする。
                        Noneの場合は検索対象サブパス自体から直接コピーする
    extra_tree        : 転送先に事前作成するサブフォルダのリスト
                        (サンプルフォルダからの相対パス) 例: ['T','stocked','T/ANAL','T/stocked']
    """
    if extra_tree is None:
        extra_tree = []
 
    if samples is None:
        samples = list_subfolder_names(server_path(server, source_keyfolder, ex))
 
    extra_tree = [s.replace("\\", "/") for s in extra_tree]
 
    for sample in samples:
        sample_folder_name = sample + sample_suffix
 
        # --- 転送先フォルダツリーの作成 ---
        dest_ex = server_path(server, dest_keyfolder, ex)
        dest_data = server_path(server, dest_keyfolder, ex, sample)
        dest_sample = server_path(server, dest_keyfolder, ex, sample, sample_folder_name)
        tree = [dest_ex, dest_data, dest_sample]
        for rel in extra_tree:
            tree.append(dest_sample + "/" + rel)
        ensure_folders(tree)
 
        # --- コピー元の検索 ---
        src_sample = server_path(server, source_keyfolder, ex, sample, sample_folder_name)
        search_dir = src_sample + ("/" + search_subpath if search_subpath else "")
 
        if keyword:
            source_folders = list_subfolder_paths(search_dir, keyword=keyword)
            print(source_folders)
            # 一致したフォルダに対応する転送先も作成しておく
            dest_folders = [s.replace(source_keyfolder, dest_keyfolder) for s in source_folders]
            ensure_folders(dest_folders)
        else:
            source_folders = [search_dir]
 
        # --- コピー実行 ---
        for n, folder_path in enumerate(source_folders, start=1):
            copy_files_by_ext(folder_path, source_keyfolder, dest_keyfolder, ext)
            progress = (n / len(source_folders) * 100) if source_folders else 100.0
            print(sample, ":", os.path.basename(folder_path), format(progress, ".2f") + " %", now_str())
 
    print("end")
 
 
if __name__ == '__main__':
    #ターゲットサーバー名
    server = 'Rackstation'
 
    #コピー元の共有フォルダ名
    source_keyfolder = 'analysis'
 
    #コピー先の共有フォルダ名
    dest_keyfolder = 'RT_server'
 
    #対象exフォルダ名
    ex = 'Suzuki_I'
 
    #サンプルリスト（Noneにするとex直下の全フォルダが対象になる）
    samples = ['26I', '3I', '246I', '4I']
 
    #コピー対象の拡張子
    ext = '.tdms'
 
    #サンプルフォルダ名の接尾辞
    sample_suffix = '_10k_Sample'
 
    #サンプルフォルダ配下の検索対象サブパス（例: 'T'、直下なら ''）
    search_subpath = 'T'
 
    #検索対象サブパス内でこの文字列を含むフォルダのみコピー元にする（不要ならNone）
    keyword = 'ANAL'
 
    #転送先に事前作成するサブフォルダ（サンプルフォルダからの相対パス）
    extra_tree = ['T', 'stocked', 'T/ANAL', 'T/stocked']
 
    transfer(server, source_keyfolder, dest_keyfolder, ex, samples, ext,
             sample_suffix=sample_suffix, search_subpath=search_subpath,
             keyword=keyword, extra_tree=extra_tree)
 