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


def fo_xx(server, keyfolder, ex, sample):
    #対象となるフォルダを指定する．
    ServerPath = '//' + server +'/' + keyfolder +'/'
    DataPath = ex +'/'+sample
    SamplePath = sample+'_10k_Sample'
    BlankPath = sample+'_10k_Blank'
    SamplePath1 = sample+'_100k_Sample'
    BlankPath2 = sample+'_100k_Blank'
    RawPath = 'raw data'
    
    #対象となるフォルダパスを作成
    #Folderpath0 = ServerPath + ex
    Folderpath1 = ServerPath + DataPath
    Folderpath2 = ServerPath + DataPath +'/'+ SamplePath
    Folderpath3 = ServerPath + DataPath +'/'+ SamplePath1
    Folderpath4 = ServerPath + DataPath +'/'+ BlankPath
    Folderpath5 = ServerPath + DataPath +'/'+ BlankPath2
    Folderpath6 = ServerPath + DataPath +'/'+ RawPath
    Folderpath7 = ServerPath + DataPath +'/'+ SamplePath+'/T'
    Folderpath8 = ServerPath + DataPath +'/'+ BlankPath+'/T'
    Folderpath9 = ServerPath + DataPath +'/'+ SamplePath+'/stocked'
    Folderpath10 = ServerPath + DataPath +'/'+ BlankPath+'/stocked'
    Folderpath11 = ServerPath + DataPath +'/'+ SamplePath+'/T/ANAL'
    Folderpath12 = ServerPath + DataPath +'/'+ SamplePath+'/T/stocked'
    Folderpath13 = ServerPath + DataPath +'/'+ BlankPath+'/T/ANAL'
    Folderpath14 = ServerPath + DataPath +'/'+ BlankPath+'/T/stocked'
    
    #対象となるフォルダパスをリスト作成
    Folders = [Folderpath1,Folderpath2,Folderpath3,Folderpath4,Folderpath5,
               Folderpath6,Folderpath7,Folderpath8,Folderpath9,Folderpath10,
               Folderpath11,Folderpath12,Folderpath13,Folderpath14]
    
    #対象となるフォルダの作成
    for folder in Folders:
        os.makedirs(folder, exist_ok=True)
        print(folder)   
            
if __name__ =='__main__':
    #ターゲットサーバー名
    server = 'Rackstation'
    
    #ターゲットサーバー内の元フォルダの場所
    keyfolder = 'analysis'
    
    #元フォルダ内の対象フォルダの場所
    ex = 'Suzuki_I'
    
    #サンプルリスト
    samples =[ 'P','2I']

    
    for sample in samples:
        XX=fo_xx(server, keyfolder, ex, sample)
    print('end')