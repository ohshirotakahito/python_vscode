#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cleanup_folders.py
 
TOP_Dataserver_Transfer4.py を書き換えた統合スクリプト。
サンプルフォルダ配下の不要ファイル（解析済みANALフォルダの中身、
sampleフォルダ直下のテキストファイル、raw dataフォルダの中身）を削除する。
 
差分（対象サーバー・対象exフォルダ・対象サンプル等）は、
下の if __name__ == '__main__': ブロック内の変数を書き換えて指定する。
 
使用例（下の __main__ ブロックに書く内容の例）
------
server = 'Rackstation'
keyfolder = 'archives'
ex_list = ['Pan_Cancer',]
samples = None              # Noneなら各ex直下の全フォルダが対象
anal_keyword = 'A@'         # ANALフォルダ内で中身を削除する対象キーワード
"""
 
import os
import sys
 
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_transfer_common import (
    server_path,
    list_subfolder_names,
    remove_files_in_folder,
    clear_folder_contents,
    remove_matching_subfolder_contents,
)
 
 
def cleanup_sample(server, keyfolder, ex, sample, anal_keyword):
    root = server_path(server, keyfolder, ex, sample)
    sample_10k = sample + "_10k_Sample"
    blank_10k = sample + "_10k_Blank"
 
    # ANAL中のテキストファイル削除 (dpsbsfiles相当)
    anal_folders = [root + "/" + sample_10k + "/T/ANAL", root + "/" + blank_10k + "/T/ANAL"]
    for folder in anal_folders:
        print(folder)
        remove_matching_subfolder_contents(folder, keyword=anal_keyword)
 
    # sampleデータのテキストファイル削除 (dfiles相当)
    text_folders = [root + "/" + sample_10k, root + "/" + blank_10k]
    for folder in text_folders:
        print(folder)
        remove_files_in_folder(folder)
 
    # raw data中のファイル削除 (clearfolder相当)
    raw_folder = root + "/raw data"
    print(raw_folder)
    clear_folder_contents(raw_folder)
 
 
def cleanup(server, keyfolder, ex_list, samples=None, anal_keyword='A@'):
    """
    解析済みフォルダのクリーンアップを実行する。
 
    server        : サーバー名 例: 'Rackstation'
    keyfolder     : 共有フォルダ名 例: 'archives'
    ex_list       : 対象exフォルダ名のリスト
    samples       : サンプル名のリスト。Noneなら各ex直下の全フォルダを対象にする
    anal_keyword  : ANALフォルダ内で中身を削除する対象キーワード
    """
    for ex in ex_list:
        if samples is None:
            target_samples = list_subfolder_names(server_path(server, keyfolder, ex))
        else:
            target_samples = samples
 
        for sample in target_samples:
            print(sample)
            cleanup_sample(server, keyfolder, ex, sample, anal_keyword)
        print("end")
 
 
if __name__ == '__main__':
    #ターゲットサーバー名
    server = 'Rackstation'
 
    #ターゲットサーバー内の共有フォルダ名
    keyfolder = 'archives'
 
    #対象exフォルダ名のリスト
    ex_list = ['Pan_Cancer'] #ここをかきかえる
 
    #サンプルリスト（Noneにすると各ex直下の全フォルダが対象になる）
    samples = None
 
    #ANALフォルダ内で中身を削除する対象キーワード
    anal_keyword = 'A@'
 
    cleanup(server, keyfolder, ex_list, samples=samples, anal_keyword=anal_keyword)