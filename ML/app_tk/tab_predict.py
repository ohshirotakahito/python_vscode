# -*- coding: utf-8 -*-
"""⑤ 混合予測（カウント）タブ。

純粋分子サンプルで学習したモデル（predict_mix_xgboost_*.run_prediction）で混合サンプルの
各イベントを分類し、クラスごとの割合を「全体 / ファイルごと / ファイル内N秒ごと」に表示する。
集計の切り替えは保存済みの predict_*_events.csv から行うため、予測をやり直さない。
"""

import importlib
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from lib.catalog import list_available_samples
from lib.mix_summary import (
    UNIT_FILE, UNIT_TOTAL, UNIT_WINDOW, chart_classes, group_keys, list_mix_runs,
    load_events, summarize, to_long_for_chart,
)
from widgets import CheckList, DataTable, ProgressPanel, ScrollableFrame, open_path, save_csv_dialog, save_zip_dialog

PIPELINES = {
    "rmc + XGBoost（tsfresh併用）": {"feature_set": "rmc", "module": "predict_mix_xgboost_tsfresh"},
    "rmb + XGBoost（波形12点特徴量のみ）": {"feature_set": "rmb", "module": "predict_mix_xgboost_traditional"},
}
UNITS = {"全体（混合サンプルごと）": UNIT_TOTAL, "ファイルごと": UNIT_FILE,
         "ファイル内の一定秒数ごと": UNIT_WINDOW}
ALL_FILES = "（全ファイル合算）"

# 学習スクリプト側が plt.rcParams を書き換えることがあるため、描画時だけ明示的に指定する
CHART_RC = {
    "font.family": ["Meiryo", "Yu Gothic", "MS Gothic", "sans-serif"],
    "font.size": 9,
    "axes.edgecolor": "#bbbbbb",
    "axes.labelcolor": "#333333",
    "xtick.color": "#555555",
    "ytick.color": "#555555",
}


class PredictTab(ttk.Frame):
    def __init__(self, master, runner, on_runs_changed=None):
        super().__init__(master, padding=10)
        self.runner = runner
        self.on_runs_changed = on_runs_changed
        self.runs = []
        self.run = None
        self.events = pd.DataFrame()
        self.classes = []
        self.table_df = pd.DataFrame()

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        self._build_run_panel(paned)
        self._build_result_panel(paned)

        runner.add_busy_listener(lambda busy: self.run_button.configure(
            state="disabled" if busy else "normal"))
        self._pipeline_changed()
        self.refresh_runs()

    # =====================
    # 左: 予測の実行
    # =====================
    def _build_run_panel(self, paned):
        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        paned.add(left, weight=1)

        ttk.Label(left, text="パイプライン").pack(anchor="w")
        self.pipeline = tk.StringVar(value=list(PIPELINES)[0])
        combo = ttk.Combobox(left, textvariable=self.pipeline, values=list(PIPELINES),
                             state="readonly")
        combo.pack(fill="x", pady=(0, 6))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._pipeline_changed())

        ttk.Label(left, text="学習に使う純粋分子（2つ以上）").pack(anchor="w")
        self.pure = CheckList(left, height=7, on_change=lambda _s: self._update_mix_options())
        self.pure.pack(fill="both", expand=True, pady=2)
        ttk.Label(left, text="予測する混合サンプル（複数可）").pack(anchor="w", pady=(6, 0))
        self.mix = CheckList(left, height=5)
        self.mix.pack(fill="both", expand=True, pady=2)

        opts = ttk.Frame(left)
        opts.pack(fill="x", pady=4)
        ttk.Label(opts, text="高信頼度とみなす pmax の閾値").grid(row=0, column=0, sticky="w")
        self.threshold = tk.DoubleVar(value=0.8)
        ttk.Spinbox(opts, from_=0.5, to=0.99, increment=0.01, textvariable=self.threshold,
                    width=6, format="%.2f").grid(row=0, column=1, padx=4)
        self.retrain = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="モデルを強制的に再学習する", variable=self.retrain).grid(
            row=1, column=0, columnspan=2, sticky="w")

        self.run_button = ttk.Button(left, text="予測を実行", command=self._run)
        self.run_button.pack(fill="x", pady=4)
        self.progress = ProgressPanel(left, log_height=6)
        self.progress.pack(fill="both", expand=True)

    def refresh_samples(self):
        feature_set = PIPELINES[self.pipeline.get()]["feature_set"]
        self._available = list_available_samples(feature_set)
        self.pure.set_items(self._available)
        self._update_mix_options()

    def _pipeline_changed(self):
        self.refresh_samples()

    def _update_mix_options(self):
        chosen = set(self.pure.get_selected())
        self.mix.set_items([s for s in getattr(self, "_available", []) if s not in chosen])

    def _run(self):
        smns, test_smns = self.pure.get_selected(), self.mix.get_selected()
        if len(smns) < 2 or not test_smns:
            messagebox.showerror("入力エラー", "純粋分子を2つ以上、混合サンプルを1つ以上選んでください。")
            return
        module_name = PIPELINES[self.pipeline.get()]["module"]
        threshold, retrain = float(self.threshold.get()), self.retrain.get()

        def task(progress):
            module = importlib.import_module(module_name)
            return module.run_prediction(smns, test_smns, retrain=retrain, threshold=threshold,
                                         progress_callback=progress)

        def done(run_dir):
            self.refresh_runs(select=run_dir)
            if self.on_runs_changed:
                self.on_runs_changed()

        self.runner.run(task, self.progress, on_done=done)

    # =====================
    # 右: 結果の表示
    # =====================
    def _build_result_panel(self, paned):
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="表示する予測結果").pack(side="left")
        self.run_choice = ttk.Combobox(top, state="readonly")
        self.run_choice.pack(side="left", fill="x", expand=True, padx=6)
        self.run_choice.bind("<<ComboboxSelected>>", lambda _e: self._load_selected_run())
        ttk.Button(top, text="再読み込み", command=self.refresh_runs).pack(side="left")
        self.info = ttk.Label(right, text="", foreground="#555")
        self.info.pack(anchor="w", pady=(2, 4))

        controls = ttk.Frame(right)
        controls.pack(fill="x")
        self.unit = tk.StringVar(value=list(UNITS)[0])
        for label in UNITS:
            ttk.Radiobutton(controls, text=label, value=label, variable=self.unit,
                            command=self._on_unit_changed).pack(side="left", padx=(0, 6))
        ttk.Label(controls, text="区間[秒]").pack(side="left", padx=(8, 2))
        self.window_sec = tk.DoubleVar(value=10.0)
        self.window_spin = ttk.Spinbox(controls, from_=1, to=600, increment=1, width=5,
                                       textvariable=self.window_sec, command=self.update_view)
        self.window_spin.pack(side="left")
        self.window_spin.bind("<Return>", lambda _e: self.update_view())
        self.window_spin.bind("<FocusOut>", lambda _e: self.update_view())

        controls2 = ttk.Frame(right)
        controls2.pack(fill="x", pady=4)
        self.as_percent = tk.BooleanVar(value=True)
        ttk.Radiobutton(controls2, text="割合(%)", value=True, variable=self.as_percent,
                        command=self.update_view).pack(side="left")
        ttk.Radiobutton(controls2, text="件数", value=False, variable=self.as_percent,
                        command=self.update_view).pack(side="left", padx=(0, 12))
        self.high_only = tk.BooleanVar(value=False)
        self.high_check = ttk.Checkbutton(controls2, text="高信頼度のみ", variable=self.high_only,
                                          command=self.update_view)
        self.high_check.pack(side="left", padx=(0, 12))
        ttk.Label(controls2, text="グラフのファイル").pack(side="left")
        self.chart_file = ttk.Combobox(controls2, state="disabled", width=36)
        self.chart_file.pack(side="left", padx=4)
        self.chart_file.bind("<<ComboboxSelected>>", lambda _e: self.update_view())

        buttons = ttk.Frame(controls2)
        buttons.pack(side="right")
        ttk.Button(buttons, text="集計表をCSV保存", command=self._save_csv).pack(side="left")
        ttk.Button(buttons, text="ZIPで保存", command=lambda: self.run and save_zip_dialog(
            self.run["run_dir"])).pack(side="left", padx=4)
        ttk.Button(buttons, text="フォルダを開く", command=lambda: self.run and open_path(
            self.run["run_dir"])).pack(side="left")

        body = ttk.PanedWindow(right, orient="vertical")
        body.pack(fill="both", expand=True)
        self.chart_area = ScrollableFrame(body)
        body.add(self.chart_area, weight=3)
        self.figure = Figure(figsize=(8, 4), dpi=100, layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.chart_area.inner)
        self.canvas.get_tk_widget().pack(fill="x", expand=True)
        self.canvas.mpl_connect("motion_notify_event", self._on_hover)
        self._bar_info = {}
        self._tooltip = None

        self.table = DataTable(body, height=8)
        body.add(self.table, weight=2)

    def refresh_runs(self, select=None):
        self.runs = list_mix_runs()
        labels = []
        for r in self.runs:
            label = f"{r['run_dir'].name}  |  {r['feature_set']}/{r['algorithm']}"
            if r["manifest"].get("test_smns"):
                label += f"  |  混合: {', '.join(r['manifest']['test_smns'])}"
            labels.append(label)
        self.run_choice.configure(values=labels)
        if not self.runs:
            self.run_choice.set("")
            self.info.configure(text="まだ予測結果がありません。左で条件を選んで「予測を実行」してください。")
            return
        index = 0
        if select is not None:
            for i, r in enumerate(self.runs):
                if str(r["run_dir"]) == str(select):
                    index = i
        self.run_choice.current(index)
        self._load_selected_run()

    def _load_selected_run(self):
        index = self.run_choice.current()
        if index < 0:
            return
        self.run = self.runs[index]
        manifest = self.run["manifest"]
        self.events = load_events(self.run["run_dir"])
        self.threshold_used = manifest.get("pmax_threshold", 0.8)
        self.high_check.configure(text=f"高信頼度のみ（pmax ≥ {self.threshold_used}）")

        if self.events.empty:
            self.classes = []
        else:
            self.classes = manifest.get("class_names") or sorted(self.events["pred_label"].unique())
            mixes = sorted(self.events["mix_sample"].unique())
            files = sorted(self.events["file"].unique())
            self.chart_file.configure(values=[f"{ALL_FILES} {m}" for m in mixes] + files)
            self.chart_file.current(0)

        status = {"trained": "新規学習したモデル", "reused": "保存済みモデルを再利用"}.get(
            manifest.get("model_status"), "")
        info = f"クラス: {', '.join(self.classes)}" if self.classes else ""
        if status:
            info += f"　／　{status}"
        self.info.configure(text=info)
        self._on_unit_changed()

    def _on_unit_changed(self):
        is_window = UNITS[self.unit.get()] == UNIT_WINDOW
        self.window_spin.configure(state="normal" if is_window else "disabled")
        self.chart_file.configure(state="readonly" if is_window and not self.events.empty else "disabled")
        self.update_view()

    def _window_sec(self):
        try:
            value = float(self.window_sec.get())
        except (tk.TclError, ValueError):
            value = 10.0
        return max(value, 0.1)

    def update_view(self):
        if self.run is None:
            return
        if self.events.empty:
            # イベントCSVが無い古い実行結果: ファイルごとの集計CSV(all)をそのまま表示
            csvs = sorted(self.run["run_dir"].glob("predict_*_all.csv"))
            df = pd.concat([pd.read_csv(p) for p in csvs], ignore_index=True) if csvs else None
            self.table_df = df if df is not None else pd.DataFrame()
            self.table.show(df)
            self._draw_message("この結果にはイベント単位のデータが無いため、\n"
                               "ファイルごとの集計表（下）のみ表示します。")
            return

        unit = UNITS[self.unit.get()]
        window_sec = self._window_sec()
        pmax = self.threshold_used if self.high_only.get() else None
        events = self.events
        chart_file = self.chart_file.get()
        if unit == UNIT_WINDOW and chart_file.startswith(ALL_FILES):
            # 全ファイル合算: 選んだ混合サンプルの全ファイルを「ファイル内の経過時間」だけで集計する
            mix = chart_file[len(ALL_FILES):].strip()
            events = events[events["mix_sample"] == mix].assign(file=chart_file)
        table = summarize(events, self.classes, unit, window_sec=window_sec,
                          pmax_threshold=pmax, as_percent=self.as_percent.get())
        self.table_df = table
        self.table.show(table)

        keys = group_keys(unit)
        chart_table = table
        if unit == UNIT_WINDOW:
            chart_table = table[table["file"] == chart_file]
        self._draw_chart(chart_table, keys, unit, window_sec)

    # =====================
    # グラフ
    # =====================
    def _draw_message(self, text):
        self.figure.clear()
        self.figure.set_size_inches(8, 2)
        with matplotlib.rc_context(CHART_RC):
            self.figure.text(0.5, 0.5, text, ha="center", va="center", color="#666")
        self._bar_info = {}
        self._resize_canvas()

    def _draw_chart(self, chart_table, keys, unit, window_sec):
        self.figure.clear()
        self._bar_info = {}
        if chart_table.empty:
            self._draw_message("表示するデータがありません")
            return

        long_df = to_long_for_chart(chart_table, self.classes, keys)
        shown, colors = chart_classes(self.classes)
        as_percent = self.as_percent.get()
        value_label = "割合 (%)" if as_percent else "イベント数"

        with matplotlib.rc_context(CHART_RC):
            if unit == UNIT_WINDOW:
                self.figure.set_size_inches(8, 3.6)
                ax = self.figure.add_subplot(111)
                positions = sorted(chart_table["window_start_s"].unique())
                bottom = pd.Series(0.0, index=positions)
                for cls in shown:
                    values = (long_df[long_df["class"] == cls]
                              .set_index("window_start_s")["value"].reindex(positions).fillna(0))
                    bars = ax.bar(positions, values.values, width=window_sec * 0.85, align="edge",
                                  bottom=bottom.values, color=colors[cls], label=cls,
                                  edgecolor="white", linewidth=1)
                    for bar, pos, value in zip(bars, positions, values.values):
                        self._bar_info[bar] = f"{pos:g}〜{pos + window_sec:g} 秒\n{cls}: {self._fmt(value)}"
                    bottom += values
                ax.set_xlabel("ファイル内の経過時間 [秒]")
                ax.set_ylabel(value_label)
                if as_percent:
                    ax.set_ylim(0, 100)
                ax.yaxis.grid(True, color="#e6e6e6")
                title = self.chart_file.get()
            else:
                labels = chart_table[keys].astype(str).agg(" / ".join, axis=1).tolist()
                labels = [self._short_label(label) for label in labels]
                n = len(labels)
                self.figure.set_size_inches(8, max(2.2, 0.32 * n + 1.3))
                ax = self.figure.add_subplot(111)
                y = list(range(n))
                left = [0.0] * n
                for cls in shown:
                    values = long_df[long_df["class"] == cls]["value"].fillna(0).tolist()
                    bars = ax.barh(y, values, left=left, height=0.7, color=colors[cls], label=cls,
                                   edgecolor="white", linewidth=1)
                    for bar, label, value in zip(bars, labels, values):
                        self._bar_info[bar] = f"{label}\n{cls}: {self._fmt(value)}"
                    left = [a + b for a, b in zip(left, values)]
                ax.set_yticks(y)
                ax.set_yticklabels(labels)
                ax.set_ylim(n - 0.5, -0.5)  # 上から順に並べ、既定の上下余白は付けない
                ax.set_xlabel(value_label)
                if as_percent:
                    ax.set_xlim(0, 100)
                ax.xaxis.grid(True, color="#e6e6e6")
                title = ""

            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncol=min(len(shown), 8),
                      frameon=False, title=title or None, alignment="left")
            self._tooltip = ax.annotate(
                "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
                bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#999999"),
                fontsize=9, visible=False, zorder=10)
        self._resize_canvas()

    def _fmt(self, value):
        return f"{value:.1f} %" if self.as_percent.get() else f"{int(value)} 件"

    def _short_label(self, label):
        # "GO6MeG1-1 / GO6MeG1-1_10k_Sample#001" → "GO6MeG1-1 / #001"
        parts = label.split(" / ")
        if len(parts) == 2 and parts[1].startswith(parts[0]):
            rest = parts[1][len(parts[0]):].replace("_10k_Sample", "")
            return f"{parts[0]} / {rest or parts[1]}"
        return label

    def _resize_canvas(self):
        width, height = self.figure.get_size_inches() * self.figure.dpi
        self.canvas.get_tk_widget().configure(height=int(height))
        self.canvas.draw_idle()

    def _on_hover(self, event):
        if self._tooltip is None or not self._bar_info:
            return
        if event.inaxes is not None:
            for bar, text in self._bar_info.items():
                if bar.contains(event)[0]:
                    self._tooltip.xy = (event.xdata, event.ydata)
                    self._tooltip.set_text(text)
                    self._tooltip.set_visible(True)
                    self.canvas.draw_idle()
                    return
        if self._tooltip.get_visible():
            self._tooltip.set_visible(False)
            self.canvas.draw_idle()

    def _save_csv(self):
        if self.run is None or self.table_df.empty:
            messagebox.showinfo("保存", "保存する集計表がありません。")
            return
        unit = UNITS[self.unit.get()]
        suffix = {UNIT_TOTAL: "total", UNIT_FILE: "by_file",
                  UNIT_WINDOW: f"by_{self._window_sec():g}s"}[unit]
        suffix += "_highconf" if self.high_only.get() else ""
        suffix += "_pct" if self.as_percent.get() else "_count"
        save_csv_dialog(self.table_df, f"{self.run['run_dir'].name}_{suffix}.csv")
