# -*- coding: utf-8 -*-
"""
Created on Thu May 27 14:12:12 2021

@author: ohshi
"""
import nptdms
from nptdms import TdmsFile
from nptdms import tdms
import os
import re
import pandas as pd
import datetime

def exfoler_check(server, keyfolder, ex):
    exFolderpath = '//' + server + '/' + keyfolder + '/' + ex
    
    ex_subfolders = []
    for item in os.listdir(exFolderpath):
        item_path = os.path.join(exFolderpath, item)
        if os.path.isdir(item_path):
            last_part = os.path.basename(item_path)
            ex_subfolders.append(last_part)
            #print(last_part)
    
    return ex_subfolders

def Analtfoler(server, keyfolder, ex, sample):#フォルダ中のファイルリスト作成
    ##対象となるフォルダを指定する．
    ServerPath = '//' + server + '/' + keyfolder + '/'
    DataPath = ex +'/'+sample+'/'
    SamplePath = sample+'_10k_Sample'
    #TargetPath = 'BNAL@'+target
    #FileForm ='/*.tdms'
    
    #特定フォルダ名の指定
    target_keyword = 'ANAL'
    
    #対象となるフォルダパスを作成
    Folderpath = ServerPath + DataPath + SamplePath +'/T'
    
    #対象となるフォルダパスを作成
    Folderpath_t = ServerPath + DataPath + SamplePath +'/T/' +target_keyword
    
    subfolders = []
    for item in os.listdir(Folderpath):
        item_path = os.path.join(Folderpath, item)
        if os.path.isdir(item_path):
            #last_part = os.path.basename(item_path)
            subfolders.append(item_path)
            #print(item_path)
        
        f_subfolders = [s for s in subfolders if re.search(target_keyword, s)]
    
    return f_subfolders, Folderpath_t

def tdmslist_files(folder_path):#フォルダ中のtdmsファイルのみを抽出する
    file_list = os.listdir(folder_path)
    
    # .tdms拡張子のファイルのみを抽出します
    tdms_files = [filename for filename in file_list if filename.endswith('.tdms')]
    
    return tdms_files

def tdms_checker(path_load):#tdmsファイル中のグループの存在判定を，数字で返す
    #tdmsファイル中に存在すべきグループ名を指定
    target_group_names = ["AR Table", "R Table"] 
    
    try:
        with nptdms.TdmsFile.read(path_load) as tdms_file:
            found_groups = []
            
            # 対象のグループ名を検索し、存在する場合 'Yes' を表示
            for target_group_name in target_group_names:
                if target_group_name in tdms_file:
                    found_groups.append(target_group_name)
                    
            if len(found_groups) == 2:
                echec = 0
                #print('Yes')
            else:
                echec = 1
                #print('スキップします')

    except ValueError as e:
        echec = 1
        
    return(echec)


def a_countpick(path_load):
        ##Tdmsファイル読み込み
        tdms_file = nptdms.TdmsFile(path_load)
        
        ##TdmsファイルのAR　Table中の情報を抽出
        #全ファイル名の抽出
        n = tdms_file['AR Table']['Filename'].raw_data
        #バースト回数の抽出
        #B_count = tdms_file['AR Table']['Burst#Sum'].raw_data
        #シグナル回数の抽出
        S_count = tdms_file['AR Table']['Signal#Sum'].raw_data
        #シグナル領域数の抽出（計測秒数の抽出）
        T_count = tdms_file['R Table']['R # [n]'].raw_data
        
        #ファイル名の抽出（全ファイル名から余分な部分削除する）
        n = n[0][:n[0].find(' D_')]
        #b=int(B_count[0])
        
        #シグナル数の抽出
        s=int(S_count[0])
        
        #シグナル数の抽出
        t = len(T_count)
        #シグナル頻度の計算
        f = s/t*1000
        f0 = format(f,".2f")
        
        #返し変数の初期化
        AZ=[]
        #返し変数の格納
        AZ=[n,t,s,f0]
        
        return(AZ)
            
def run_count_for_sample(server, keyfolder, ex, sample, script_dir=None, folder_limit=1):
    """
    指定したサンプルについて、ANALフォルダ内のtdmsファイルを解析し、
    シグナル数・頻度を集計してcount_data/{sample}_sc.csvに保存する。

    view_analtdms_count.pyを直接実行した場合と同じ集計処理を、
    他スクリプト（view_tdms_signal.pyなど）からも呼び出せるようにした関数。

    戻り値: 保存したCSVファイルのパス
    """
    if script_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))

    #フォルダ中のファイルリスト作成
    folderlist, Folderpath_t = Analtfoler(server, keyfolder, ex, sample)

    #folderlist限定（元コードの仕様を踏襲。Noneを渡せば全フォルダ対象）
    if folder_limit is not None:
        folderlist = folderlist[:folder_limit]

    conc_anal = []
    for folder_path in folderlist:
        tdms_files = tdmslist_files(folder_path)

        for tdms_file in tdms_files:
            tdms_file_path = os.path.join(Folderpath_t, tdms_file)

            echec = tdms_checker(tdms_file_path)

            if echec == 0:
                C_result = a_countpick(tdms_file_path)
                print(C_result)
                conc_anal.append(C_result)

    #取得データのDataframe化
    columns = ['name', 'time', 'count', 'freq']
    df = pd.DataFrame(data=conc_anal, columns=columns)

    #合計値の計算（対象ファイルが1つも無い場合は合計行を追加しない）
    if len(df) > 0:
        total_time = df['time'].sum()
        total_count = df['count'].sum()
        total_freq = total_count / total_time * 1000
        total_freq = format(total_freq, ".2f")

        #合計行の作成
        total_row = pd.DataFrame([['Total', total_time, total_count, total_freq]], columns=columns)

        #元のDataframeに合計行を追加
        df = pd.concat([df, total_row], ignore_index=True)

    #保存先フォルダの指定（スクリプトと同じ場所のcount_dataフォルダ）
    save_dir = os.path.join(script_dir, 'count_data')
    os.makedirs(save_dir, exist_ok=True)  # フォルダが無ければ作成、あれば何もしない

    #取得Dataframeデータの保存
    path = os.path.join(save_dir, sample + '_sc.csv')

    # データフレームをCSVファイルとして保存
    df.to_csv(path, index=False)

    print(f"[SAVED] {path}")

    return path


if __name__ =='__main__':
    #このスクリプト自身が置かれているフォルダのパスを取得
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    #データ元のターゲットサーバー名
    server = 'QTserver'
    #データ元のターゲットサーバー内の元フォルダの場所
    keyfolder = 'analysis'
    
    #データ元のターゲットサーバー内の元フォルダ内の対象フォルダの場所
    ex = 'Chirality_N2'
    
    #sampleリスト（特定フォルダごと作成）
    samples = exfoler_check(server, keyfolder, ex)
    #sampleリスト限定（テスト時に使用）
    #samples = [samples[0]]
    samples =['LTrp','DTrp']
    #ssamples =['Dtrp']
    #ssamples =['N01SN5457','N02SN2461','N03SN9857','N04SN9283']
    
    for sample in samples:
        run_count_for_sample(server, keyfolder, ex, sample, script_dir=script_dir, folder_limit=1)