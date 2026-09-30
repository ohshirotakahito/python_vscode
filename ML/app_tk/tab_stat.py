# -*- coding: utf-8 -*-
"""② 統計ヒストグラムタブ。

stat_features_rmc.create_histograms() で、学習せずに特徴量ごとの分布と統計量を確認する。
"""

import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd

import common.paths as paths
from lib.catalog import list_available_samples
from widgets import CheckList, DataTable, ImageGallery, ProgressPanel, open_path, save_zip_dialog


class StatTab(ttk.Frame):
    def __init__(self, master, runner):
        super().__init__(master, padding=10)
        self.runner = runner
        self.output_dir = None

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # --- 左: 設定 ---
        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        paned.add(left, weight=1)
        ttk.Label(left, text="サンプル（rmc、複数選択可）").pack(anchor="w")
        self.samples = CheckList(left, height=12)
        self.samples.pack(fill="both", expand=True, pady=4)

        opts = ttk.LabelFrame(left, text="詳細設定（任意）", padding=6)
        opts.pack(fill="x", pady=4)
        self.bins = tk.StringVar(value="auto")
        ttk.Label(opts, text="bins（整数 または auto）").grid(row=0, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.bins, width=10).grid(row=0, column=1, sticky="w")
        self.use_distance = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="distance範囲で絞り込む", variable=self.use_distance,
                        command=self._toggle_distance).grid(row=1, column=0, columnspan=2, sticky="w")
        self.dmin = tk.DoubleVar(value=0.0)
        self.dmax = tk.DoubleVar(value=1.0)
        ttk.Label(opts, text="下限").grid(row=2, column=0, sticky="w")
        self.dmin_spin = ttk.Spinbox(opts, from_=0, to=10, increment=0.01, textvariable=self.dmin,
                                     width=10, state="disabled")
        self.dmin_spin.grid(row=2, column=1, sticky="w")
        ttk.Label(opts, text="上限").grid(row=3, column=0, sticky="w")
        self.dmax_spin = ttk.Spinbox(opts, from_=0, to=10, increment=0.01, textvariable=self.dmax,
                                     width=10, state="disabled")
        self.dmax_spin.grid(row=3, column=1, sticky="w")

        self.run_button = ttk.Button(left, text="ヒストグラムを作成", command=self._run)
        self.run_button.pack(fill="x", pady=4)
        self.progress = ProgressPanel(left, log_height=6)
        self.progress.pack(fill="both", expand=True)

        # --- 右: 結果 ---
        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        bar = ttk.Frame(right)
        bar.pack(fill="x")
        self.result_label = ttk.Label(bar, text="結果はまだありません", foreground="#666")
        self.zip_button = ttk.Button(bar, text="ZIPで保存", state="disabled",
                                     command=lambda: save_zip_dialog(self.output_dir))
        self.zip_button.pack(side="right")
        self.open_button = ttk.Button(bar, text="フォルダを開く", state="disabled",
                                      command=lambda: open_path(self.output_dir))
        self.open_button.pack(side="right", padx=4)
        self.result_label.pack(side="left", fill="x", expand=True)

        self.table = DataTable(right, height=6)
        self.table.pack(fill="x", pady=4)
        self.gallery = ImageGallery(right, columns=2)
        self.gallery.pack(fill="both", expand=True)

        runner.add_busy_listener(lambda busy: self.run_button.configure(
            state="disabled" if busy else "normal"))
        self.refresh_samples()

    def refresh_samples(self):
        self.samples.set_items(list_available_samples("rmc"))

    def _toggle_distance(self):
        state = "normal" if self.use_distance.get() else "disabled"
        self.dmin_spin.configure(state=state)
        self.dmax_spin.configure(state=state)

    def _run(self):
        samples = self.samples.get_selected()
        if not samples:
            messagebox.showerror("入力エラー", "サンプルを1つ以上選んでください。")
            return
        bins_text = self.bins.get().strip()
        bins = int(bins_text) if bins_text.isdigit() else "auto"
        use_distance = self.use_distance.get()
        dmin, dmax = self.dmin.get(), self.dmax.get()

        def task(progress):
            from stat_features_rmc import (
                DEFAULT_LOWER_LIMITS, DEFAULT_OUTPUT_ROOT, DEFAULT_UPPER_LIMITS, create_histograms,
            )
            progress(0, 1, "集計中")
            return create_histograms(
                samples=samples, data_root=paths.feature_dir("rmc"),
                output_root=DEFAULT_OUTPUT_ROOT, bins=bins,
                upper_limits=DEFAULT_UPPER_LIMITS, lower_limits=DEFAULT_LOWER_LIMITS,
                distance_min=dmin if use_distance else None,
                distance_max=dmax if use_distance else None,
            )

        self.runner.run(task, self.progress, on_done=self._show)

    def _show(self, output_dir):
        self.output_dir = output_dir
        self.result_label.configure(text=str(output_dir), foreground="")
        self.zip_button.configure(state="normal")
        self.open_button.configure(state="normal")
        stats_path = output_dir / "summary_stats.csv"
        self.table.show(pd.read_csv(stats_path) if stats_path.exists() else None)
        self.gallery.show(sorted(output_dir.glob("*.png")))
