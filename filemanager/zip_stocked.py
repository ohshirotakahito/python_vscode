#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
zip_stocked.py

TOP_Dataserver_Zip7.py を引数化した統合スクリプト。
対象フォルダ直下の各サブフォルダをZIP圧縮し、元フォルダを削除する。

使用例
------
python zip_stocked.py --server SQserver --keyfolder data_stocked --ex AN5
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_transfer_common import server_path, list_subfolder_names, zip_and_delete_folder


def parse_args():
    p = argparse.ArgumentParser(description="フォルダをZIP圧縮して削除する")
    p.add_argument("--server", required=True)
    p.add_argument("--keyfolder", required=True)
    p.add_argument("--ex", nargs="+", required=True, help="対象exフォルダ名（複数指定可）")
    return p.parse_args()


def main():
    args = parse_args()
    for ex in args.ex:
        base = server_path(args.server, args.keyfolder, ex)
        names = list_subfolder_names(base)
        for name in names:
            item_path = base + "/" + name
            print(item_path)
            zip_and_delete_folder(item_path, item_path)
        print("end")


if __name__ == "__main__":
    main()
