#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
zip_stocked.py
 
TOP_Dataserver_Zip7.py を書き換えた統合スクリプト。
対象フォルダ直下の各サブフォルダをZIP圧縮し、元フォルダを削除する。
 
差分（対象サーバー・対象exフォルダ等）は、
下の if __name__ == '__main__': ブロック内の変数を書き換えて指定する。
 
使用例（下の __main__ ブロックに書く内容の例）
------
server = 'SQserver'
keyfolder = 'data_stocked'
ex_list = ['AN5']
"""
 
import os
import sys
 
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_transfer_common import server_path, list_subfolder_names, zip_and_delete_folder
 
 
def zip_stocked(server, keyfolder, ex_list):
    """
    対象フォルダ直下の各サブフォルダをZIP圧縮し、元フォルダを削除する。
 
    server    : サーバー名 例: 'SQserver'
    keyfolder : 共有フォルダ名 例: 'data_stocked'
    ex_list   : 対象exフォルダ名のリスト
    """
    for ex in ex_list:
        base = server_path(server, keyfolder, ex)
        names = list_subfolder_names(base)
        for name in names:
            item_path = base + "/" + name
            print(item_path)
            zip_and_delete_folder(item_path, item_path)
        print("end")
 
 
if __name__ == '__main__':
    #ターゲットサーバー名
    server = 'SQserver'
 
    #ターゲットサーバー内の共有フォルダ名
    keyfolder = 'data_stocked'
 
    #対象exフォルダ名のリスト（複数指定可）
    ex_list = ['AN5']
 
    zip_stocked(server, keyfolder, ex_list)
