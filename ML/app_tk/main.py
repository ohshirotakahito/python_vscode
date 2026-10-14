# -*- coding: utf-8 -*-
"""ML/app_tk/main.py — MLパネル（Tkinter版）。

起動方法:
    ML/app_tk/launch_tk.bat をダブルクリック
    または: python ML/app_tk/main.py

Streamlit版（ML/app/）と同じ処理（各スクリプトの run_*() と ML/app/lib/）を
デスクトップアプリの画面から呼び出す。ブラウザ・サーバーは使わない。
"""

import multiprocessing
import os
import sys
from pathlib import Path

_TK_DIR = Path(__file__).resolve().parent
_ML_DIR = _TK_DIR.parent
# ML/app/lib（Streamlit版と共有する集計・一覧ロジック）を "lib" として import するため ML/app も通す
for _p in (_TK_DIR, _ML_DIR / "app", _ML_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# 学習スクリプトは作業スレッド内で matplotlib.pyplot を使って画像を保存する。
# GUI用バックエンドのままだと別スレッドからの描画で落ちることがあるため、
# pyplot 側は画像出力専用の Agg にしておく（画面のグラフは FigureCanvasTkAgg で別に描く）。
import matplotlib  # noqa: E402

matplotlib.use("Agg")

import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

from widgets import JP_FONT, TaskRunner  # noqa: E402


def main():
    root = tk.Tk()
    root.title("ML解析パネル")
    root.geometry("1400x880")
    root.minsize(1000, 640)
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72)
    except tk.TclError:
        pass

    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    root.option_add("*Font", JP_FONT)
    style.configure(".", font=JP_FONT)
    style.configure("Treeview", rowheight=22)
    style.configure("TNotebook.Tab", padding=(14, 4))

    runner = TaskRunner(root)
    activity = ttk.LabelFrame(root, text="実行状況", padding=6)
    activity.pack(fill="x", padx=6, pady=(6, 0))
    activity_label = ttk.Label(activity, text="待機中", font=("Meiryo UI", 11, "bold"))
    activity_label.pack(anchor="w")
    activity_bar = ttk.Progressbar(activity, mode="indeterminate")
    activity_bar.pack(fill="x", pady=(4, 0))
    runner.add_busy_listener(lambda busy: activity_bar.start(20) if busy else activity_bar.stop())

    def show_activity(busy, elapsed, stage, fraction):
        duration = f"{elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}"
        activity_label.configure(
            text=(f"● 処理実行中　経過 {duration}　工程進捗 {fraction:.0%}　{stage}" if busy
                  else f"待機中　直前の処理時間 {duration}"),
            foreground="#174ea6" if busy else "#555")
    runner.add_status_listener(show_activity)

    # 各タブの import は Tk 作成後に行う（重いライブラリの読み込み中もウィンドウを出すため）
    from tab_browser import BrowserTab
    from tab_extract import ExtractTab
    from tab_predict import PredictTab
    from tab_stat import StatTab
    from tab_review import ReviewTab
    from tab_train import TrainTab

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=6, pady=6)

    browser = BrowserTab(notebook)
    stat = StatTab(notebook, runner)
    tabs = {}

    def open_review_training(feature_set, samples, versions, rules):
        if tabs['train'].apply_review_selection(feature_set, samples, versions, rules):
            notebook.select(tabs['train'])

    review = ReviewTab(notebook, runner, on_train=open_review_training)

    def features_changed():
        # 抽出が終わったら、各タブのサンプル一覧を最新にする
        for tab in (stat, review, tabs["train"], tabs["predict"]):
            tab.refresh_samples()

    def runs_changed():
        browser.refresh()

    extract = ExtractTab(notebook, runner, on_features_changed=features_changed)
    tabs["train"] = TrainTab(notebook, runner, on_runs_changed=runs_changed)
    tabs["predict"] = PredictTab(notebook, runner, on_runs_changed=runs_changed)

    notebook.add(extract, text="① 特徴量抽出")
    notebook.add(stat, text="② 統計ヒストグラム")
    notebook.add(review, text="抽出ファイル精査")
    notebook.add(tabs["train"], text="③ 学習・評価")
    notebook.add(browser, text="④ 結果ブラウザ")
    notebook.add(tabs["predict"], text="⑤ 混合予測")

    def on_close():
        busy = runner.busy
        if busy and not messagebox.askyesno(
                "終了確認", "処理を実行中です。途中で終了しますか？\n（途中までの結果は保存されない場合があります）"):
            return
        root.destroy()
        if busy:
            # 作業スレッドやtsfreshの子プロセスが残らないよう、プロセスごと終了する
            os._exit(0)

    bottom = ttk.Frame(root, padding=(8, 2, 8, 6))
    bottom.pack(fill="x", side="bottom", before=notebook)
    status = ttk.Label(bottom, text="待機中", anchor="w")
    status.pack(side="left", fill="x", expand=True)
    ttk.Button(bottom, text="アプリを終了", command=on_close).pack(side="right")
    runner.add_busy_listener(lambda busy: status.configure(
        text="処理を実行中です…（完了まで他の実行ボタンは押せません）" if busy else "待機中"))

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    # tsfresh は内部で multiprocessing を使うため、Windows では必須
    multiprocessing.freeze_support()
    main()
