#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cleanup_folders.py

TOP_Dataserver_Transfer4.py を引数化した統合スクリプト。
サンプルフォルダ配下の不要ファイル（解析済みANALフォルダの中身、
sampleフォルダ直下のテキストファイル、raw dataフォルダの中身）を削除する。

使用例
------
python cleanup_folders.py --server Rackstation --keyfolder archives \
    --ex Pan_Cancer Pan_Cancer_N Numata Chirality_MX Chirality_N2 CR_SQ
"""

import argparse
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


def parse_args():
    p = argparse.ArgumentParser(description="解析済みフォルダのクリーンアップ")
    p.add_argument("--server", required=True)
    p.add_argument("--keyfolder", required=True)
    p.add_argument("--ex", nargs="+", required=True, help="対象exフォルダ名（複数指定可）")
    p.add_argument("--samples", default=None,
                    help="カンマ区切りのサンプル名。省略時は各ex直下の全フォルダを対象にする")
    p.add_argument("--anal-keyword", default="A@", help="ANALフォルダ内で中身を削除する対象キーワード")
    return p.parse_args()


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


def main():
    args = parse_args()
    for ex in args.ex:
        if args.samples:
            samples = [s.strip() for s in args.samples.split(",") if s.strip()]
        else:
            samples = list_subfolder_names(server_path(args.server, args.keyfolder, ex))

        for sample in samples:
            print(sample)
            cleanup_sample(args.server, args.keyfolder, ex, sample, args.anal_keyword)
        print("end")


if __name__ == "__main__":
    main()
