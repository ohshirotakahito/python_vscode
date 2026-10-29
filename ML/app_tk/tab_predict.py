# -*- coding: utf-8 -*-
"""⑤ 混合予測（カウント）タブ。

③ 学習・評価タブで作った学習器（純粋分子の組み合わせごと）を一覧から選び、混合サンプルの
各イベントを分類して、クラスごとの割合を「全体 / ファイルごと / ファイル内N秒ごと」に表示する。
このタブでは学習しない。学習器が無い組み合わせは ③ 学習・評価タブで作る。
集計の切り替えは保存済みの predict_*_events.csv から行うため、予測をやり直さない。
"""

import json
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from lib.catalog import list_available_samples
from common import paths, trained_models
from common.feature_store import inspection_inputs, list_versions, resolve_version
from lib.mix_summary import (
    UNIT_FILE, UNIT_TOTAL, UNIT_WINDOW, chart_classes, group_keys, list_mix_runs,
    load_events, summarize, to_long_for_chart,
)
from theme import MUTED, WARNING
from widgets import CheckList, DataTable, ProgressPanel, ScrollableFrame, open_path, save_csv_dialog, save_zip_dialog

FEATURE_SETS = {"rmc（tsfresh特徴量）": "rmc", "rmb（波形12点特徴量）": "rmb"}
MODEL_COLUMNS = (("classes", "クラス（組み合わせ）", 220), ("algorithm", "アルゴリズム", 80),
                 ("created", "作成日時", 130), ("test_f1", "テストF1", 70),
                 ("tsfresh", "tsfresh", 70), ("balance", "偏りへの対処", 90))
NO_MODEL_HINT = ("この特徴量の学習器がありません。③ 学習・評価タブで純粋分子の組み合わせを学習してください"
                 "（学習すると、ここに自動で並びます）。")
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
    def __init__(self, master, runner, on_runs_changed=None, on_open_train=None):
        super().__init__(master, padding=10)
        self.runner = runner
        self.on_runs_changed = on_runs_changed
        self.on_open_train = on_open_train
        self.models = []
        self.runs = []
        self.run = None
        self.events = pd.DataFrame()
        self.classes = []
        self.table_df = pd.DataFrame()
        self.mix_file_rules = []

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        self._build_run_panel(paned)
        self._build_result_panel(paned)

        runner.add_busy_listener(lambda busy: self.run_button.configure(
            state="disabled" if busy else "normal"))
        runner.add_busy_listener(lambda busy: self.filter_button.configure(
            state="disabled" if busy else "normal"))
        self._pipeline_changed()
        self.refresh_runs()

    # =====================
    # 左: 予測の実行
    # =====================
    def _build_run_panel(self, paned):
        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        paned.add(left, weight=1)

        ttk.Label(left, text="特徴量の種類").pack(anchor="w")
        self.pipeline = tk.StringVar(value=list(FEATURE_SETS)[0])
        combo = ttk.Combobox(left, textvariable=self.pipeline, values=list(FEATURE_SETS),
                             state="readonly")
        combo.pack(fill="x", pady=(0, 6))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._pipeline_changed())

        ttk.Label(left, text="学習器（③ 学習・評価タブで作成した純粋分子の組み合わせ）").pack(anchor="w")
        model_frame = ttk.Frame(left)
        model_frame.pack(fill="both", expand=True, pady=2)
        # 二段表示: 学習器が1つの組み合わせはその行で選べる。2つ以上ある組み合わせは
        # 親行（件数表示）の下に学習器が並び、「学習器を比較して選ぶ」で比べて選ぶ。
        self.model_tree = ttk.Treeview(model_frame, columns=[c for c, _t, _w in MODEL_COLUMNS],
                                       show="tree headings", height=7, selectmode="browse")
        self.model_tree.column("#0", width=28, stretch=False)
        self.model_tree.tag_configure("group", foreground=WARNING)
        for column, title, width in MODEL_COLUMNS:
            self.model_tree.heading(column, text=title)
            self.model_tree.column(column, width=width, stretch=column == "classes")
        model_scroll = ttk.Scrollbar(model_frame, orient="vertical", command=self.model_tree.yview)
        self.model_tree.configure(yscrollcommand=model_scroll.set)
        self.model_tree.pack(side="left", fill="both", expand=True)
        model_scroll.pack(side="right", fill="y")
        self.model_tree.bind("<<TreeviewSelect>>", lambda _e: self._model_selection_changed())
        model_buttons = ttk.Frame(left)
        model_buttons.pack(fill="x")
        ttk.Button(model_buttons, text="一覧を更新", command=self.refresh_models).pack(side="left")
        self.compare_button = ttk.Button(model_buttons, text="学習器を比較して選ぶ",
                                         command=self._compare_models, state="disabled")
        self.compare_button.pack(side="left", padx=4)
        ttk.Button(model_buttons, text="③ 学習・評価タブで学習器を作る",
                   command=self._open_train).pack(side="left", padx=4)
        self.model_hint = ttk.Label(left, wraplength=400, foreground=MUTED)
        self.model_hint.pack(fill="x", pady=(2, 4))

        ttk.Label(left, text="実験フォルダ（混合サンプルの出どころ）").pack(anchor="w")
        self.experiment = tk.StringVar()
        self.experiment_combo = ttk.Combobox(left, state="readonly")
        self.experiment_combo.pack(fill="x")
        self.experiment_combo.bind("<<ComboboxSelected>>", self._filter_samples)
        self.source_hint = ttk.Label(left, wraplength=400)
        self.source_hint.pack(fill="x", pady=(2, 4))

        ttk.Label(left, text="予測する混合サンプル（複数可）").pack(anchor="w", pady=(6, 0))
        self.mix = CheckList(left, height=5)
        self.mix.pack(fill="both", expand=True, pady=2)
        self.filter_button = ttk.Button(left, text="混合サンプルのファイル絞り込みを設定（任意）",
                                        command=self._choose_mix_filters)
        self.filter_button.pack(fill="x", pady=2)
        self.filter_summary = ttk.Label(left, text="混合ファイル絞り込み：なし", wraplength=400)
        self.filter_summary.pack(fill="x")

        opts = ttk.Frame(left)
        opts.pack(fill="x", pady=4)
        ttk.Label(opts, text="高信頼度とみなす pmax の閾値").grid(row=0, column=0, sticky="w")
        self.threshold = tk.DoubleVar(value=0.8)
        ttk.Spinbox(opts, from_=0.5, to=0.99, increment=0.01, textvariable=self.threshold,
                    width=6, format="%.2f").grid(row=0, column=1, padx=4)

        self.run_button = ttk.Button(left, text="予測を実行", command=self._run)
        self.run_button.pack(fill="x", pady=4)
        self.progress = ProgressPanel(left, log_height=6)
        self.progress.pack(fill="both", expand=True)

    def _feature_set(self):
        return FEATURE_SETS[self.pipeline.get()]

    def refresh_models(self):
        """保存済みの学習器を組み合わせごとに並べる（学習・評価タブで学習が終わるたびにも呼ばれる）。"""
        previous = self._selected_model()
        self.models = trained_models.list_models(self._feature_set())
        self.model_tree.delete(*self.model_tree.get_children())
        self._tree_models, self._tree_groups = {}, {}

        def values(model):
            return (", ".join(model["class_names"]), model["algorithm"], model.get("created_at", ""),
                    f"{model['test_macro_f1']:.3f}", model.get("fc_parameters_mode") or "—",
                    model.get("balance") or "")

        for g, items in enumerate(trained_models.group_models(self.models).values()):
            if len(items) == 1:
                self._tree_models[f"m{g}_0"] = items[0]
                self.model_tree.insert("", "end", iid=f"m{g}_0", values=values(items[0]))
                continue
            best = max(items, key=lambda m: m["test_macro_f1"])
            self._tree_groups[f"g{g}"] = items
            self.model_tree.insert("", "end", iid=f"g{g}", open=False, tags=("group",), values=(
                ", ".join(items[0]["class_names"]), f"{len(items)}件", f"最新 {items[0].get('created_at', '')}",
                f"最高 {best['test_macro_f1']:.3f}", "比較して選択", ""))
            for i, model in enumerate(items):
                self._tree_models[f"m{g}_{i}"] = model
                self.model_tree.insert(f"g{g}", "end", iid=f"m{g}_{i}", values=values(model))
        keep = next((iid for iid, m in self._tree_models.items()
                     if previous and m["run_dir"] == previous["run_dir"]), None)
        if keep is None and self.model_tree.get_children():
            keep = self.model_tree.get_children()[0]
        if keep is not None:
            self.model_tree.see(keep)
            self.model_tree.selection_set(keep)
        self._model_selection_changed()

    def _selected_iid(self):
        selection = self.model_tree.selection() if hasattr(self, "model_tree") else ()
        return selection[0] if selection else None

    def _selected_model(self):
        return getattr(self, "_tree_models", {}).get(self._selected_iid())

    def _selected_group(self):
        """選択行が属する組み合わせの学習器（2つ以上ある場合のみ）。"""
        iid = self._selected_iid()
        if iid is None:
            return []
        parent = self.model_tree.parent(iid) or iid
        return self._tree_groups.get(parent, [])

    def _model_selection_changed(self):
        model, group = self._selected_model(), self._selected_group()
        self.compare_button.configure(state="normal" if group else "disabled")
        if not self.models:
            hint = NO_MODEL_HINT
        elif group and model is None:
            hint = (f"この組み合わせには学習器が {len(group)} 件あります。"
                    "「学習器を比較して選ぶ」で構成を比べるか、行を開いて1つ選んでください。")
        elif group:
            hint = (f"同じ組み合わせの学習器が {len(group)} 件あります（{model.get('created_at')} を選択中）。"
                    "「学習器を比較して選ぶ」で違いを確認できます。")
        elif model:
            hint = f"選択中：{model.get('created_at')} に作成した学習器（この組み合わせでは1件のみ）。"
        else:
            hint = "使う学習器を選んでください。"
        self.model_hint.configure(text=hint)
        self._update_mix_options()

    def _compare_models(self):
        group = self._selected_group()
        if not group:
            return
        from model_compare import ModelCompareDialog
        ModelCompareDialog(self, group, self._use_model, current=self._selected_model())

    def _use_model(self, model):
        iid = next(i for i, m in self._tree_models.items() if m["run_dir"] == model["run_dir"])
        parent = self.model_tree.parent(iid)
        if parent:
            self.model_tree.item(parent, open=True)
        self.model_tree.see(iid)
        self.model_tree.selection_set(iid)

    def _open_train(self):
        if self.on_open_train:
            self.on_open_train()

    def refresh_samples(self):
        feature_set = self._feature_set()
        self._sample_names = list_available_samples(feature_set)
        self._source_versions = {name: list_versions(feature_set, name) for name in self._sample_names}
        self._experiments = sorted({v['ex'] for versions in self._source_versions.values()
                                    for v in versions if v.get('ex')})
        self.experiment_combo.configure(values=['すべて（旧形式を含む）', *self._experiments])
        experiment = self.experiment.get()
        self.experiment_combo.current(self._experiments.index(experiment) + 1
                                      if experiment in self._experiments else 0)
        self._filter_samples()

    def _filter_samples(self, _event=None):
        index = self.experiment_combo.current()
        experiment = self._experiments[index - 1] if index > 0 else ''
        self.experiment.set(experiment)
        self._available = [name for name in self._sample_names if not experiment or any(
            v.get('ex') == experiment for v in self._source_versions[name])]
        self._update_mix_options()
        self.source_hint.configure(text=(
            f'{len(self._available)} サンプル：混合サンプルは選択した実験の最新完了版を使用'
            if experiment else 'すべての出どころを表示。旧形式は出どころ不明です。'))

    def _pipeline_changed(self):
        self._set_mix_filters([])
        self.refresh_samples()
        self.refresh_models()

    def _set_mix_filters(self, rules):
        self.mix_file_rules = list(rules)
        description = ' AND '.join(f'{c} {op} {v}' for c, op, v in rules) or 'なし'
        self.filter_summary.configure(text=f'混合ファイル絞り込み：{description}')

    def _choose_mix_filters(self):
        from common.file_review import load_events, file_statistics
        from training_filters import FileFilterDialog
        if self.runner.busy:
            return
        names = self.mix.get_selected()
        if not names:
            messagebox.showinfo('混合サンプルを選択', '予測する混合サンプルを1つ以上選んでください。')
            return
        feature_set = self._feature_set()
        experiment = self.experiment.get()
        def task(progress):
            versions = {s: resolve_version(feature_set, s, experiment=experiment or None) for s in names}
            return file_statistics(load_events(feature_set, names, progress, versions))
        def done(files):
            if (feature_set != self._feature_set() or
                    experiment != self.experiment.get() or names != self.mix.get_selected()):
                messagebox.showinfo('再読み込みが必要です', '対象が変更されました。絞り込み設定を開き直してください。')
                return
            if files.empty:
                messagebox.showerror('入力エラー', '絞り込み対象のファイルがありません。')
                return
            FileFilterDialog(self, files, self.mix_file_rules, self._set_mix_filters, prediction=True)
        self.runner.run(task, self.progress, on_done=done)

    def _update_mix_options(self):
        model = self._selected_model()
        group = self._selected_group() if hasattr(self, "model_tree") else []
        chosen = set((model or (group[0] if group else None) or {}).get("class_names", []))
        self.mix.set_items([s for s in getattr(self, "_available", []) if s not in chosen])

    def _run(self):
        model, test_smns = self._selected_model(), self.mix.get_selected()
        if model is None and self._selected_group():
            messagebox.showinfo("学習器を選んでください",
                                "この組み合わせには学習器が複数あります。\n"
                                "「学習器を比較して選ぶ」で比べるか、行を開いて使う学習器を1つ選んでください。")
            return
        if model is None:
            if messagebox.askyesno("学習器がありません",
                                   "使う学習器が選ばれていません（またはこの特徴量の学習器がありません）。\n"
                                   "③ 学習・評価タブを開いて、純粋分子の組み合わせを学習しますか？"):
                self._open_train()
            return
        if not test_smns:
            messagebox.showerror("入力エラー", "予測する混合サンプルを1つ以上選んでください。")
            return
        feature_set = model['feature_set']
        experiment = self.experiment.get()
        threshold = float(self.threshold.get())
        mix_file_rules = list(getattr(self, 'mix_file_rules', []))

        def task(progress):
            names = list(dict.fromkeys(test_smns))
            versions = {name: resolve_version(feature_set, name, experiment=experiment or None)['run_id']
                        for name in names}
            with inspection_inputs(feature_set, names, paths.feature_dir(feature_set), versions) as selected:
                run_dir = trained_models.predict_mix(model['run_dir'], test_smns, threshold=threshold,
                                                     progress_callback=progress,
                                                     mix_file_rules=mix_file_rules or None)
                (run_dir / 'feature_inputs.json').write_text(
                    json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf-8')
                return run_dir

        def done(run_dir):
            self.unit.set(next(label for label, value in UNITS.items() if value == UNIT_TOTAL))
            self.refresh_runs(select=run_dir)
            self._save_view(notify=False)
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
        self.info = ttk.Label(right, text="", foreground=MUTED)
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
        self.previous_file = ttk.Button(controls2, text="◁", width=3, state="disabled",
                                        command=lambda: self._step_chart_file(-1))
        self.previous_file.pack(side="left")
        self.next_file = ttk.Button(controls2, text="▷", width=3, state="disabled",
                                    command=lambda: self._step_chart_file(1))
        self.next_file.pack(side="left")

        buttons = ttk.Frame(controls2)
        buttons.pack(side="right")
        ttk.Button(buttons, text="グラフ・集計を保存", command=self._save_view).pack(side="left", padx=4)
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
            self.chart_file.configure(values=[])
            self.chart_file.set('')
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
        can_step = is_window and not self.events.empty and len(self.chart_file['values']) > 1
        for button in (self.previous_file, self.next_file):
            button.configure(state="normal" if can_step else "disabled")
        self.update_view()

    def _step_chart_file(self, direction):
        values = self.chart_file['values']
        if self.chart_file.instate(['disabled']) or len(values) < 2:
            return
        self.chart_file.current((self.chart_file.current() + direction) % len(values))
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
        self._set_figure_height(2)
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
                self._set_figure_height(3.6)
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
                ax.set_xlim(positions[0], positions[-1] + window_sec)
                ax.xaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=2))
                ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:g}秒"))
                ax.set_xlabel(f"ファイル内の経過時間（棒1本 = {window_sec:g}秒間の集計）")
                ax.set_ylabel(value_label)
                if as_percent:
                    ax.set_ylim(0, 100)
                ax.yaxis.grid(True, color="#e6e6e6")
                title = self.chart_file.get()
            else:
                labels = chart_table[keys].astype(str).agg(" / ".join, axis=1).tolist()
                labels = [self._short_label(label) for label in labels]
                n = len(labels)
                self._set_figure_height(max(2.2, 0.32 * n + 1.3))
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

    def _set_figure_height(self, height_inches):
        """高さだけ変える。幅は表示欄の実際の幅に合わせる（幅を固定すると、図より右側に
        前回のグラフが消えずに残ってしまうため）。"""
        widget = self.canvas.get_tk_widget()
        width_px = widget.winfo_width()
        width = width_px / self.figure.dpi if width_px > 1 else self.figure.get_size_inches()[0]
        self.figure.set_size_inches(width, height_inches)

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

    def _save_view(self, notify=True):
        """Keep the chart, its table and display settings together in this prediction run."""
        if self.run is None or self.table_df.empty:
            if notify:
                messagebox.showinfo('保存', '保存する予測結果がありません。')
            return
        output = self.run['run_dir'] / 'views' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        try:
            output.mkdir(parents=True, exist_ok=False)
            if self._tooltip is not None:
                visible = self._tooltip.get_visible()
                self._tooltip.set_visible(False)
            else:
                visible = False
            try:
                self.figure.savefig(output / 'prediction_chart.png', dpi=180, bbox_inches='tight')
            finally:
                if self._tooltip is not None:
                    self._tooltip.set_visible(visible)
            self.table_df.to_csv(output / 'prediction_summary.csv', index=False, encoding='utf-8-sig')
            settings = dict(unit=UNITS[self.unit.get()], window_sec=self._window_sec(),
                            chart_file=self.chart_file.get(), as_percent=self.as_percent.get(),
                            high_confidence_only=self.high_only.get(), pmax_threshold=self.threshold_used,
                            class_names=self.classes, source_run=str(self.run['run_dir']))
            (output / 'display_settings.json').write_text(
                json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        except (OSError, ValueError) as exc:
            messagebox.showerror('予測表示を保存できません', str(exc))
            return
        if notify:
            messagebox.showinfo('予測表示を保存しました', str(output))
