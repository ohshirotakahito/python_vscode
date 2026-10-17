# -*- coding: utf-8 -*-
"""
Created on Fri Sep 10 11:06:39 2021

@author: ohshi

全体の役割
ファイル	役割	単体で実行する?
data_transfer_common.py	他の4本が共通で使う関数集(部品箱)	❌ 直接実行しない
create_folders.py	実験用のフォルダツリーを新規作成	✅
transfer_copy.py	サーバー間でファイルをコピー	✅
cleanup_folders.py	解析後の不要ファイルを削除	✅
zip_stocked.py	溜まったフォルダをZIP圧縮して元を削除	✅

典型的な使う順番は 
① create_folders → ② transfer_copy → (解析作業) → ③ cleanup_folders → ④ zip_stocked 
という流れ
"""
import os


def create_sample_folders(root, ex, sample):
    """Create the standard sample tree under an arbitrary analysis root."""
    base = os.path.join(root, ex, sample)
    folders = [base, os.path.join(base, 'raw data')]
    for rate in ('10k', '100k'):
        for kind in ('Sample', 'Blank'):
            folder = os.path.join(base, f'{sample}_{rate}_{kind}')
            folders.append(folder)
            if rate == '10k':
                folders.extend(os.path.join(folder, *parts) for parts in
                               [('T',), ('stocked',), ('T', 'ANAL'), ('T', 'stocked')])
    for folder in folders:
        os.makedirs(folder, exist_ok=True)
    return folders


def fo_xx(server, keyfolder, ex, sample):
    #対象となるフォルダを指定する．
    try:
        from .data_transfer_common import server_path
    except ImportError:
        from data_transfer_common import server_path
    for folder in create_sample_folders(server_path(server, keyfolder), ex, sample):
        print(folder)

if __name__ =='__main__':
    #ターゲットサーバー名
    server = 'Rackstation'
    
    #ターゲットサーバー内の元フォルダの場所
    keyfolder = 'analysis'
    
    #元フォルダ内の対象フォルダの場所
    ex = 'Sakano_00'
    
    #サンプルリスト
    samples =[ 'OXT','AVP']

    
    for sample in samples:
        XX=fo_xx(server, keyfolder, ex, sample)
    print('end')
