#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
transfer_copy.py

TOP_Data_transfer_for_ANAL.py / for_BNAL.py / for_file.py / for_Text.py
を統合した汎用コピースクリプト。
差分（拡張子・検索キーワード・作成するフォルダツリー等）はすべて
コマンドライン引数で指定する。

使用例
------
# ANAL用 (.tdms, sample/T 配下の "ANAL" を含むフォルダを検索してコピー)
#   ※ 元 TOP_Data_transfer_for_ANAL.py / for_file.py 相当
python transfer_copy.py --server Rackstation --source-keyfolder analysis \
    --dest-keyfolder RT_server --ex Kumamoto_N2 --ext .tdms \
    --search-subpath T --keyword ANAL \
    --extra-tree "T,stocked,T/ANAL,T/stocked"

# BNAL用 (.tdms, sample/T 配下の "BNAL@" を含むフォルダを検索してコピー)
#   ※ 元 TOP_Data_transfer_for_BNAL.py 相当
python transfer_copy.py --server Rackstation --source-keyfolder analysis \
    --dest-keyfolder RT_server --ex Kiyotani --samples 0272,0380 --ext .tdms \
    --search-subpath T --keyword "BNAL@" --extra-tree "T"

# Text用 (.txt, sampleフォルダ直下から直接コピー、キーワード検索なし)
#   ※ 元 TOP_Data_transfer_for_Text.py 相当
python transfer_copy.py --server Rackstation --source-keyfolder archives \
    --dest-keyfolder QT_server --ex Chirality_N2 --samples LTyr,LVal --ext .txt
"""

import argparse
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


def parse_args():
    p = argparse.ArgumentParser(description="サーバー間ファイル転送(コピー)統合スクリプト")
    p.add_argument("--server", required=True, help="サーバー名 例: Rackstation")
    p.add_argument("--source-keyfolder", required=True, help="コピー元の共有フォルダ名 例: analysis")
    p.add_argument("--dest-keyfolder", required=True, help="コピー先の共有フォルダ名 例: RT_server")
    p.add_argument("--ex", required=True, help="対象exフォルダ名")
    p.add_argument("--samples", default=None,
                    help="カンマ区切りのサンプル名。省略時はex直下の全フォルダを対象にする")
    p.add_argument("--ext", required=True, help="コピー対象の拡張子 例: .tdms .txt")
    p.add_argument("--sample-suffix", default="_10k_Sample",
                    help="サンプルフォルダ名の接尾辞 (デフォルト: _10k_Sample)")
    p.add_argument("--search-subpath", default="",
                    help="サンプルフォルダ配下の検索対象サブパス 例: T (省略時はサンプルフォルダ直下)")
    p.add_argument("--keyword", default=None,
                    help="検索対象サブパス内でこの文字列を含むフォルダのみコピー元にする。"
                         "省略時は検索対象サブパス自体から直接コピーする")
    p.add_argument("--extra-tree", default="",
                    help="転送先に事前作成するサブフォルダ(カンマ区切り, サンプルフォルダからの相対パス) "
                         "例: T,stocked,T/ANAL,T/stocked")
    return p.parse_args()


def main():
    args = parse_args()

    if args.samples:
        samples = [s.strip() for s in args.samples.split(",") if s.strip()]
    else:
        samples = list_subfolder_names(server_path(args.server, args.source_keyfolder, args.ex))

    extra_tree = [s.strip().replace("\\", "/") for s in args.extra_tree.split(",") if s.strip()]

    for sample in samples:
        sample_folder_name = sample + args.sample_suffix

        # --- 転送先フォルダツリーの作成 ---
        dest_ex = server_path(args.server, args.dest_keyfolder, args.ex)
        dest_data = server_path(args.server, args.dest_keyfolder, args.ex, sample)
        dest_sample = server_path(args.server, args.dest_keyfolder, args.ex, sample, sample_folder_name)
        tree = [dest_ex, dest_data, dest_sample]
        for rel in extra_tree:
            tree.append(dest_sample + "/" + rel)
        ensure_folders(tree)

        # --- コピー元の検索 ---
        src_sample = server_path(args.server, args.source_keyfolder, args.ex, sample, sample_folder_name)
        search_dir = src_sample + ("/" + args.search_subpath if args.search_subpath else "")

        if args.keyword:
            source_folders = list_subfolder_paths(search_dir, keyword=args.keyword)
            print(source_folders)
            # 一致したフォルダに対応する転送先も作成しておく
            dest_folders = [s.replace(args.source_keyfolder, args.dest_keyfolder) for s in source_folders]
            ensure_folders(dest_folders)
        else:
            source_folders = [search_dir]

        # --- コピー実行 ---
        for n, folder_path in enumerate(source_folders, start=1):
            copy_files_by_ext(folder_path, args.source_keyfolder, args.dest_keyfolder, args.ext)
            progress = (n / len(source_folders) * 100) if source_folders else 100.0
            print(sample, ":", os.path.basename(folder_path), format(progress, ".2f") + " %", now_str())

    print("end")


if __name__ == "__main__":
    main()
