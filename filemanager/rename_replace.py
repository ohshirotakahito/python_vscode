#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
rename_replace.py

file_rename.py（パターンを解析して決まった書式に組み立て直す用途）とは別に、
「特定の文字列（例: 綴りを間違えた単語）が名前に含まれるファイル・フォルダを、
指定したフォルダ配下すべてから漏れなく探し出し、正しい文字列に置き換える」
ための汎用リネームスクリプト。

【想定用途】
例えば最初に付けた名前 'vassopressin'（誤字）を、正しい綴りや別の名前に
一括で直したい場合。ファイル名だけでなく、フォルダ名にも同じ誤字が
含まれているケースがあるため、両方をまとめて対象にする。

【対象範囲について】
root_path 配下を再帰的に（サブフォルダの中のそのまた中まで）探索し、
old_string を含むファイル名・フォルダ名をすべて new_string に置換する。
「一部だけ直し忘れる」事故を防ぐため、範囲を絞らない限り全階層が対象になる。

【処理順序について（重要）】
フォルダ名を変更すると、その配下にあるファイル・フォルダのパスも
連動して変わる。このスクリプトは「浅い階層から順に」処理することで、
処理中にパスを見失わないようにしている（os.walkのtopdown探索を利用し、
フォルダをリネームした直後にos.walkの探索リストを新しい名前に
差し替えることで、深い階層への探索が正しい新パスで続行される）。

【安全機能について（file_rename.pyと同じ考え方）】
リネームは元に戻せない操作であり、かつこのスクリプトは影響範囲が
広い（配下全体が対象）ため、特に慎重な確認が必要。

  1. dry_run（既定で有効）
     実際にはリネームせず、「何を何に変える予定か」を一覧表示するだけ。

  2. リネーム履歴のログ保存（本実行時のみ）
     本実行のたびに、変更内容の対応表を
     filemanager/rename_logs/ 以下にCSVとして保存する
     （例: replace_log_20260819_144501.csv）。
     列: old_path, new_path, item_type(file/folder), renamed_at

  3. undo_from_log() による復元
     2で保存したログファイルを渡すと元に戻す
     （ログの記録順と逆順に処理することで、フォルダ・ファイル入り混じった
     変更でも正しい順序で復元される）。

使用例（下の __main__ ブロックに書く内容の例）
------
root_path = r'//Rackstation/analysis/Kumamoto_N2'
old_string = 'vassopressin'
new_string = 'vasopressin'
include_folders = True   # フォルダ名も対象にするか

【動作確認の手順（推奨・特に範囲が広いスクリプトなので必ず踏むこと）】
  1. dry_run=True（既定）のまま一度実行し、表示される
     "変更予定" の一覧を最初から最後まで目視で確認する。
     件数が多い場合は、想定より多すぎないか・関係ない箇所まで
     引っかかっていないかを特に注意して見る。
  2. 問題なければ、max_items で件数を絞った上で dry_run=False にして
     本実行し、結果を確認する。
  3. 想定と違っていたら、直後に表示されるログファイルパスを
     undo_from_log() に渡してすぐ元に戻す。
  4. 問題なければ、件数の制限を外して本実行する。
"""

import csv
import os
from datetime import datetime

try:
    from .data_transfer_common import list_subfolder_names, server_path
except ImportError:
    # rename_replace.py を直接実行する場合
    from data_transfer_common import list_subfolder_names, server_path


def exfoler_check(server, keyfolder, ex):
    """実験フォルダ直下のサブフォルダ名（サンプル名）を取得する。"""
    return list_subfolder_names(server_path(server, keyfolder, ex))


def now_str():
    """現在時刻を "YYYY-MM-DD HH:MM:SS" 形式で返す"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# リネーム履歴ログの保存先（このスクリプトと同じ場所の rename_logs/ 配下）
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rename_logs')


def _new_log_path():
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return os.path.join(LOG_DIR, f'replace_log_{timestamp}.csv')


def replace_in_tree(root_path, old_string, new_string, include_folders=True,
                     dry_run=True, max_items=None, log_path=None):
    """
    root_path配下を再帰的に探索し、ファイル名・フォルダ名に含まれる
    old_string を new_string に置換する（単純な文字列置換。大文字小文字は区別する）。

    root_path       : 探索を開始するフォルダのパス
    old_string      : 置き換えたい文字列（例: 'vassopressin'）
    new_string      : 置き換え後の文字列（例: 'vasopressin'）
    include_folders : True（既定）ならフォルダ名も対象にする。
                      False ならファイル名のみを対象にする。
    dry_run         : True（既定）なら実際には変更せず、予定だけを表示する。
                      False にすると実際にリネームし、対応表をCSVログに保存する。
    max_items       : 指定すると、変更対象の先頭からこの件数だけを処理する
                      （少数の項目だけで試したいときに使う）。
    log_path        : 本実行時にログを書き込むCSVパス。省略時は自動生成される。

    戻り値: (renamed, log_path)
      renamed  : (旧パス, 新パス, 種別'file'/'folder') のタプルのリスト
                 （dry_run時は「変更予定」のリスト、本実行時は「実際に変更した」リスト）
      log_path : 本実行時は書き込んだログのパス。dry_run時、または
                 変更が0件だった場合はNone。
    """
    if not os.path.isdir(root_path):
        print(f"指定されたフォルダが存在しません: {root_path}")
        return [], None

    renamed = []
    stopped = False

    for dirpath, dirnames, filenames in os.walk(root_path, topdown=True):
        if stopped:
            break

        # --- このディレクトリ直下のファイルを処理 ---
        for filename in filenames:
            if max_items is not None and len(renamed) >= max_items:
                stopped = True
                break
            if old_string not in filename:
                continue

            new_filename = filename.replace(old_string, new_string)
            old_path = os.path.join(dirpath, filename)
            new_path = os.path.join(dirpath, new_filename)
            _do_rename(old_path, new_path, 'file', renamed, dry_run)

        if stopped:
            break

        # --- このディレクトリ直下のサブフォルダを処理 ---
        # dirnamesを書き換えた新しい名前で更新することで、os.walkが
        # 次にこの中へ潜っていくときに正しい（変更後の）パスを使う。
        if include_folders:
            for i, dirname in enumerate(list(dirnames)):
                if max_items is not None and len(renamed) >= max_items:
                    stopped = True
                    break
                if old_string not in dirname:
                    continue

                new_dirname = dirname.replace(old_string, new_string)
                old_path = os.path.join(dirpath, dirname)
                new_path = os.path.join(dirpath, new_dirname)

                if _do_rename(old_path, new_path, 'folder', renamed, dry_run):
                    if not dry_run:
                        # 実際にリネームした場合のみ、os.walkの探索リストも
                        # 新しい名前に更新する（ディスク上に実在するのは新しい
                        # 名前の方だけなので、そちらを辿らせる必要がある）。
                        # dry_run時は何もリネームしていないため、実ディスク上に
                        # 存在する元の名前のまま辿らせないと、その配下の
                        # ファイル・フォルダを見失ってしまう。
                        dirnames[i] = new_dirname

    written_log_path = None
    if not dry_run and renamed:
        written_log_path = log_path or _new_log_path()
        write_header = not os.path.exists(written_log_path)
        with open(written_log_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(['old_path', 'new_path', 'item_type', 'renamed_at'])
            ts = now_str()
            for old_path, new_path, item_type in renamed:
                writer.writerow([old_path, new_path, item_type, ts])
        print(f"\nリネーム履歴を保存しました: {written_log_path}")
        print("元に戻したい場合は undo_from_log() にこのパスを渡してください。")

    if dry_run:
        n_files = sum(1 for _, _, t in renamed if t == 'file')
        n_folders = sum(1 for _, _, t in renamed if t == 'folder')
        print(f"\n[DRY RUN] 変更対象: ファイル{n_files}件 / フォルダ{n_folders}件")
        print("問題なければ dry_run=False にして本実行してください。")

    return renamed, written_log_path


def _do_rename(old_path, new_path, item_type, renamed, dry_run):
    """1件分のリネームを実行（またはdry_runなら予定として記録）する内部ヘルパー。
    戻り値: 実際に（またはdry_runで予定として）名前が変わったかどうか(bool)"""
    if dry_run:
        print(f"[DRY RUN] ({item_type}) {old_path} -> {new_path}")
        renamed.append((old_path, new_path, item_type))
        return True

    if os.path.exists(new_path):
        print(f"⚠ スキップ（変更先が既に存在します）: {new_path}")
        return False

    os.rename(old_path, new_path)
    renamed.append((old_path, new_path, item_type))
    print(f"Renamed ({item_type}): {old_path} -> {new_path}  {now_str()}")
    return True


def undo_from_log(log_path):
    """
    replace_in_tree() が dry_run=False 時に保存したCSVログを読み込み、
    new_path -> old_path の向きにリネームして元に戻す
    （ログの記録順と逆順に処理する。フォルダ・ファイルが混在していても、
    記録順の逆順に処理することで正しい順序で復元される）。

    - 既に new_path が存在しない、または old_path が既に存在する場合は、
      その1件だけをスキップし、理由を表示して処理を続行する。
    - 戻り値: (reverted, skipped) それぞれ (old_path, new_path, item_type) のリスト
    """
    if not os.path.exists(log_path):
        print(f"ログファイルが見つかりません: {log_path}")
        return [], []

    with open(log_path, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    reverted = []
    skipped = []

    for row in reversed(rows):
        old_path = row['old_path']
        new_path = row['new_path']
        item_type = row.get('item_type', 'file')

        if not os.path.exists(new_path):
            print(f"⚠ スキップ（現在のファイル/フォルダが見つかりません）: {new_path}")
            skipped.append((old_path, new_path, item_type))
            continue

        if os.path.exists(old_path):
            print(f"⚠ スキップ（戻し先に既に別のファイル/フォルダがあります）: {old_path}")
            skipped.append((old_path, new_path, item_type))
            continue

        os.rename(new_path, old_path)
        reverted.append((old_path, new_path, item_type))
        print(f"Reverted ({item_type}): {new_path} -> {old_path}")

    print(f"\n{len(reverted)}件を元に戻しました。{len(skipped)}件はスキップされました。")
    return reverted, skipped


if False and __name__ == '__main__':
    # ============================================================
    # 対象の指定
    # ============================================================
    # 探索を開始するフォルダ（この配下すべてが再帰的に対象になる）
    root_path = r'//Rackstation/analysis/Kumamoto_N2'

    # 置き換えたい文字列 -> 置き換え後の文字列
    old_string = 'vassopressin'
    new_string = 'vasopressin'

    # フォルダ名も対象にするか（Falseにするとファイル名のみ）
    include_folders = True

    # ============================================================
    # 安全確認用の設定（初めて実行する時は必ずこの状態のまま一度実行し、
    # 表示内容を確認すること。特にこのスクリプトは配下全体が対象になり
    # 影響範囲が広いため、件数や対象が想定通りか入念に確認すること）
    # ============================================================
    # True: 実際には変更しない（予定を表示するだけ）。まずこれで確認する。
    # False: 実際にリネームする。dry_runで確認が済んでから切り替えること。
    DRY_RUN = True

    # 最初の本実行では少数件に絞ってテストすることを推奨。
    # 件数制限をしない場合は None にする。
    MAX_ITEMS = 2
    # ============================================================

    replace_in_tree(root_path, old_string, new_string,
                     include_folders=include_folders,
                     dry_run=DRY_RUN, max_items=MAX_ITEMS)

    # ============================================================
    # 元に戻したくなった場合は、上の本実行で表示されたログファイルパスを
    # 使って以下のように実行する:
    #
    # from rename_replace import undo_from_log
    # undo_from_log(r"D:\Python_VScode\filemanager\rename_logs\replace_log_20260819_144501.csv")
    # ============================================================


if __name__ == '__main__':
    # ============================================================
    # 対象と置換文字列の設定
    # ============================================================
    server = 'Rackstation'
    keyfolder = 'analysis'
    ex = 'Takahagi_Zenoamino'

    # 全サンプルを対象にする場合はこちらを使用する。
    # samples = exfoler_check(server, keyfolder, ex)

    # 特定のサンプルだけを対象にする場合はこちらを使用する。
    samples = ['AA17LAsn']

    # 置き換えたい文字列 -> 置き換え後の文字列
    old_string = 'Asp'
    new_string = 'Asn'

    # Trueならファイル名に加えてサブフォルダ名も対象にする。
    include_folders = True

    # Noneなら全件を表示・処理する。試験時だけ整数を指定する。
    MAX_ITEMS = None

    # 取り消す場合だけ、本実行で作成されたCSVのフルパスを指定する。
    # 通常の置換では None のままにする。
    UNDO_LOG_PATH = None
    # UNDO_LOG_PATH = r'D:\GitHub\Python_VScode\filemanager\rename_logs\replace_log_YYYYMMDD_HHMMSS.csv'

    if UNDO_LOG_PATH is not None:
        print(f"次のログに記録された名前変更を取り消します:\n{UNDO_LOG_PATH}")
        answer = input("取り消しを実行しますか？ [y/N]: ").strip().lower()
        if answer == 'y':
            undo_from_log(UNDO_LOG_PATH)
        else:
            print("取り消しを中止しました。")
    else:
        # 最初に変更予定を表示する。この段階では名前を変更しない。
        previewed = []
        for sample in samples:
            root_path = server_path(server, keyfolder, ex, sample)
            print(f"\n=== 対象サンプル: {sample} ===")
            renamed, _ = replace_in_tree(
                root_path,
                old_string,
                new_string,
                include_folders=include_folders,
                dry_run=True,
                max_items=MAX_ITEMS,
            )
            previewed.extend(renamed)

        if not previewed:
            print("\n変更対象はありません。")
        else:
            print(f"\n変更予定は合計 {len(previewed)} 件です。")
            answer = input("上記の名前変更を実行しますか？ [y/N]: ").strip().lower()

            if answer == 'y':
                # 全サンプルの変更履歴を1つのCSVへ保存する。
                log_path = _new_log_path()
                total_renamed = 0
                for sample in samples:
                    root_path = server_path(server, keyfolder, ex, sample)
                    renamed, _ = replace_in_tree(
                        root_path,
                        old_string,
                        new_string,
                        include_folders=include_folders,
                        dry_run=False,
                        max_items=MAX_ITEMS,
                        log_path=log_path,
                    )
                    total_renamed += len(renamed)

                print(f"\n合計 {total_renamed} 件の名前を変更しました。")
                if total_renamed:
                    print(f"取り消しに使用するログ: {log_path}")
            else:
                print("名前変更を中止しました。")
