# -*- coding: utf-8 -*-
"""
data_transfer_common.py

TOP_Data_transfer_for_ANAL.py / for_BNAL.py / for_file.py / for_Text.py /
TOP_Dataserver_Transfer4.py / TOP_Dataserver_Zip7.py
の6スクリプトに共通していた処理をまとめたユーティリティモジュール。

各スクリプトは差分（対象拡張子・検索キーワード・作成するフォルダツリー等）を
コマンドライン引数として与える形に書き換え、この共通処理を呼び出す。
"""

import os
import re
import shutil
from datetime import datetime


def now_str():
    """現在時刻を "YYYY-MM-DD HH:MM:SS" 形式で返す"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def server_path(server, keyfolder, *parts):
    """
    //server/keyfolder/part1/part2/... 形式のUNCパスを組み立てる。

    例: server_path('Rackstation', 'analysis', 'Kumamoto_N2', '92B')
        -> '//Rackstation/analysis/Kumamoto_N2/92B'
    """
    path = "//" + server + "/" + keyfolder
    for part in parts:
        path = path + "/" + str(part)
    return path


def list_subfolder_names(path):
    """path直下のフォルダ名（basenameのみ）のリストを返す。
    元コードの exfolder_check / folderlist / ex_list 相当。"""
    if not os.path.exists(path):
        print(f"指定されたフォルダが存在しません: {path}")
        return []
    names = []
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if os.path.isdir(item_path):
            names.append(item)
    return names


def list_subfolder_paths(path, keyword=None):
    """
    path直下のフォルダのフルパスリストを返す。
    keywordを指定すると re.search でパスに一致するもののみに絞り込む。
    元コードの datafoler_check / f_form / t_form 相当。
    """
    if not os.path.exists(path):
        print(f"指定されたフォルダが存在しません: {path}")
        return []
    paths = []
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if os.path.isdir(item_path):
            paths.append(item_path)
    if keyword:
        paths = [p for p in paths if re.search(keyword, p)]
    return paths


def list_files_in_folder(folder_path, keyword=None):
    """
    folder_path直下のファイル(フォルダは除く)のフルパスリストを返す。
    keywordを指定するとファイル名にkeywordを含むもののみに絞り込む。
    元コードの folderfile 相当。
    """
    if not os.path.exists(folder_path):
        print(f"指定されたフォルダが存在しません: {folder_path}")
        return []
    files = []
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isfile(item_path):
            files.append(item_path)
    if keyword:
        files = [f for f in files if keyword in os.path.basename(f)]
    return files


def ensure_folders(paths):
    """
    存在しないフォルダを順番に作成する（単純な os.mkdir なので、
    親フォルダが先に作られている順番でリストを渡すこと）。
    元コードの fo_xx / savefolder_f / savefolder_t 内のループ相当。
    """
    for folder in paths:
        if not os.path.exists(folder):
            os.mkdir(folder)
            print(f"作成: {folder}")


def copy_files_by_ext(folder_path, keyfolder, t_folder, ext, progress_scale=100):
    """
    folder_path 内の ext 拡張子ファイルを、パス中の keyfolder 文字列を
    t_folder に置換した保存先へコピーする。
    保存先が既に存在する場合はスキップする。
    元コードの copy_tdms_files / copy_text_files 相当（拡張子を引数化）。
    """
    if not os.path.exists(folder_path):
        print(f"指定されたフォルダが存在しません: {folder_path}")
        return []

    copied = []
    items = os.listdir(folder_path)
    n_count = 0
    for item in items:
        item_path = os.path.join(folder_path, item)
        if os.path.isfile(item_path) and item.lower().endswith(ext.lower()):
            n_count += 1
            destination_path = item_path.replace(keyfolder, t_folder)
            progress = (n_count / len(items) * progress_scale) if items else 0.0

            if not os.path.exists(destination_path):
                dest_dir = os.path.dirname(destination_path)
                if dest_dir and not os.path.exists(dest_dir):
                    os.makedirs(dest_dir)
                shutil.copy(item_path, destination_path)
                copied.append(destination_path)
                print(os.path.basename(destination_path), format(progress, ".2f") + " %", now_str())
    return copied


def remove_files_in_folder(folder_path):
    """folder_path直下のファイル（フォルダは除く）を全て削除する。
    元コードの dfiles 相当。"""
    if not os.path.exists(folder_path):
        print(f"指定されたフォルダが存在しません: {folder_path}")
        return
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isfile(item_path):
            print(f"削除: {item}")
            os.remove(item_path)


def clear_folder_contents(folder_path):
    """folder_path直下の中身（ファイル・フォルダとも）を全て削除する。
    元コードの clearfolder 相当。"""
    if not os.path.exists(folder_path):
        print(f"指定されたフォルダが存在しません: {folder_path}")
        return
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        elif os.path.isfile(item_path):
            os.remove(item_path)


def remove_matching_subfolder_contents(folder_path, keyword, exclude_name="stocked"):
    """
    folder_path直下でkeywordを含み、exclude_nameという名前ではないフォルダについて、
    その中のサブフォルダを全削除する。
    元コードの dpsbsfiles 相当（"A@" というキーワードを引数化）。
    """
    if not os.path.exists(folder_path):
        print(f"指定されたフォルダが存在しません: {folder_path}")
        return
    for item_name in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item_name)
        if os.path.isdir(item_path) and item_name != exclude_name and keyword in item_name:
            print(item_name)
            for f_name in os.listdir(item_path):
                f_path = os.path.join(item_path, f_name)
                if os.path.isdir(f_path):
                    shutil.rmtree(f_path)


def zip_and_delete_folder(folder_path, zip_file_path):
    """folder_pathをZIP圧縮し、元フォルダを削除する。
    元コードの zip_and_delete_folder と同一。"""
    if not os.path.isdir(folder_path):
        print(f"指定されたフォルダが存在しません: {folder_path}")
        return
    shutil.make_archive(zip_file_path, "zip", folder_path)
    shutil.rmtree(folder_path)
    print(f"{folder_path} はZIPに圧縮され、削除されました。")
