# -*- coding: utf-8 -*-
"""③ 学習・評価タブ。

クラスの組み合わせを指定して train_xgboost_tsfresh / train_lightgbm_tsfresh /
train_xgboost_traditional の run_analysis() を実行し、レポート・混同行列・重要度を表示する。
"""

import ast
import importlib
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from lib.catalog import list_available_samples
from tab_browser import RunResultView
from widgets import CheckList, ProgressPanel

PIPELINES = {
    "rmc + XGBoost（tsfresh併用）": {
        "feature_set": "rmc", "module": "train_xgboost_tsfresh", "tsfresh": True,
        "distance": True,
    },
    "rmc + LightGBM（tsfresh併用）": {
        "feature_set": "rmc", "module": "train_lightgbm_tsfresh", "tsfresh": True,
    },
    "rmb + XGBoost（波形12点特徴量のみ）": {
        "feature_set": "rmb", "module": "train_xgboost_traditional", "tsfresh": False,
    },
}


DISTANCE_NAMES = ("DISTANCE_VALUES", "DISTANCE_MIN", "DISTANCE_MAX")


def read_script_constants(module_name, names):
    """スクリプトを import せずに、ファイル先頭の定数（NAME = 値）の現在値を読む。

    重いライブラリを読み込まずに、画面の初期値をスクリプトの設定と揃えるため。
    """
    path = Path(__file__).resolve().parent.parent / f"{module_name}.py"
    values = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return values
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return values


def _fmt_number(value):
    return "" if value is None else f"{value:g}"


class TrainTab(ttk.Frame):
    def __init__(self, master, runner, on_runs_changed=None):
        super().__init__(master, padding=10)
        self.runner = runner
        self.on_runs_changed = on_runs_changed
        self.results = []  # [(smns, run_dir)]

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # --- 左: 設定 ---
        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        paned.add(left, weight=1)

        ttk.Label(left, text="パイプライン").pack(anchor="w")
        self.pipeline = tk.StringVar(value=list(PIPELINES)[0])
        combo = ttk.Combobox(left, textvariable=self.pipeline, values=list(PIPELINES),
                             state="readonly")
        combo.pack(fill="x", pady=(0, 6))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._pipeline_changed())

        ttk.Label(left, text="クラス（サンプル）を選んで「組み合わせに追加」").pack(anchor="w")
        self.samples = CheckList(left, height=10)
        self.samples.pack(fill="both", expand=True, pady=2)
        ttk.Button(left, text="↓ 選択中のサンプルを組み合わせに追加",
                   command=self._add_combo).pack(fill="x", pady=2)

        ttk.Label(left, text="実行する組み合わせ（1行 = 1回の学習）").pack(anchor="w", pady=(6, 0))
        self.combos = tk.Listbox(left, height=5, exportselection=False)
        self.combos.pack(fill="x", pady=2)
        ttk.Button(left, text="選択した組み合わせを削除", command=self._remove_combo).pack(fill="x")

        self.tsfresh_frame = ttk.LabelFrame(left, text="tsfresh設定", padding=6)
        self.tsfresh_frame.pack(fill="x", pady=6)
        self.fc_mode = tk.StringVar(value="minimal")
        ttk.Radiobutton(self.tsfresh_frame, text="Minimal（高速・推奨）", value="minimal",
                        variable=self.fc_mode).pack(anchor="w")
        ttk.Radiobutton(self.tsfresh_frame, text="Efficient（低速・特徴量多数）", value="efficient",
                        variable=self.fc_mode).pack(anchor="w")
        self.use_selection = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.tsfresh_frame, text="tsfresh特徴量選択を使う",
                        variable=self.use_selection).pack(anchor="w")

        self.distance_frame = ttk.LabelFrame(left, text="distance絞り込み（空欄なら全distance）",
                                             padding=6)
        self.distance_values = tk.StringVar()
        self.distance_min = tk.StringVar()
        self.distance_max = tk.StringVar()
        ttk.Label(self.distance_frame, text="指定値（カンマ区切り）").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.distance_frame, textvariable=self.distance_values, width=16).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=4)
        ttk.Label(self.distance_frame, text="範囲").grid(row=1, column=0, sticky="w", pady=(4, 0))
        range_box = ttk.Frame(self.distance_frame)
        range_box.grid(row=1, column=1, columnspan=3, sticky="w", padx=4, pady=(4, 0))
        ttk.Entry(range_box, textvariable=self.distance_min, width=8).pack(side="left")
        ttk.Label(range_box, text=" 〜 ").pack(side="left")
        ttk.Entry(range_box, textvariable=self.distance_max, width=8).pack(side="left")
        self.distance_frame.columnconfigure(1, weight=1)

        self.run_button = ttk.Button(left, text="学習を実行", command=self._run)
        self.run_button.pack(fill="x", pady=4)
        self.progress = ProgressPanel(left, log_height=6)
        self.progress.pack(fill="both", expand=True)

        # --- 右: 結果 ---
        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="今回の結果").pack(side="left")
        self.result_choice = tk.StringVar()
        self.result_combo = ttk.Combobox(top, textvariable=self.result_choice, state="readonly")
        self.result_combo.pack(side="left", fill="x", expand=True, padx=6)
        self.result_combo.bind("<<ComboboxSelected>>", lambda _e: self._show_selected())
        self.view = RunResultView(right)
        self.view.pack(fill="both", expand=True, pady=4)

        runner.add_busy_listener(lambda busy: self.run_button.configure(
            state="disabled" if busy else "normal"))
        self._pipeline_changed()

    # --- 入力 ---
    def refresh_samples(self):
        feature_set = PIPELINES[self.pipeline.get()]["feature_set"]
        self.samples.set_items(list_available_samples(feature_set))

    def _pipeline_changed(self):
        self.refresh_samples()
        pipeline = PIPELINES[self.pipeline.get()]
        if pipeline["tsfresh"]:
            self.tsfresh_frame.pack(fill="x", pady=6, before=self.run_button)
        else:
            self.tsfresh_frame.pack_forget()
        if pipeline.get("distance"):
            current = read_script_constants(pipeline["module"], DISTANCE_NAMES)
            self.distance_values.set(", ".join(_fmt_number(v) for v in current.get("DISTANCE_VALUES") or []))
            self.distance_min.set(_fmt_number(current.get("DISTANCE_MIN")))
            self.distance_max.set(_fmt_number(current.get("DISTANCE_MAX")))
            self.distance_frame.pack(fill="x", pady=(0, 6), before=self.run_button)
        else:
            self.distance_frame.pack_forget()

    def _distance_settings(self):
        """distance欄の入力を (values, min, max) に変換する。不正な値なら ValueError。"""
        def number(text, label):
            text = text.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                raise ValueError(f"distance の{label}が数値ではありません: {text}") from None

        values = [number(t, "指定値") for t in self.distance_values.get().split(",") if t.strip()]
        return values, number(self.distance_min.get(), "下限"), number(self.distance_max.get(), "上限")

    def _add_combo(self):
        selected = self.samples.get_selected()
        if len(selected) < 2:
            messagebox.showerror("入力エラー", "クラスを2つ以上選んでください。")
            return
        self.combos.insert("end", ", ".join(selected))
        self.samples.clear_selection()

    def _remove_combo(self):
        for i in reversed(self.combos.curselection()):
            self.combos.delete(i)

    # --- 実行 ---
    def _run(self):
        combinations = [[s.strip() for s in line.split(",")] for line in self.combos.get(0, "end")]
        if not combinations:
            # 組み合わせ未登録なら、今選んでいるサンプルを1件として実行する
            selected = self.samples.get_selected()
            if len(selected) < 2:
                messagebox.showerror("入力エラー",
                                     "クラスを2つ以上選ぶか、組み合わせを追加してください。")
                return
            combinations = [selected]

        pipeline = PIPELINES[self.pipeline.get()]
        distance = None
        if pipeline.get("distance"):
            try:
                distance = self._distance_settings()
            except ValueError as e:
                messagebox.showerror("入力エラー", str(e))
                return
        fc_mode = self.fc_mode.get()
        use_selection = self.use_selection.get()

        def task(progress):
            module = importlib.import_module(pipeline["module"])
            if pipeline["tsfresh"]:
                from tsfresh.feature_extraction import EfficientFCParameters, MinimalFCParameters

                fc_parameters = EfficientFCParameters() if fc_mode == "efficient" else MinimalFCParameters()
                # build_combined_dataset() などは common/data_pipeline.py 側のモジュール変数
                # (dp.FC_PARAMETERS) を参照するため、そちらも合わせて上書きする必要がある。
                module.FC_PARAMETERS = fc_parameters
                module.dp.FC_PARAMETERS = fc_parameters
                if hasattr(module, "FC_PARAMETERS_MODE"):
                    module.FC_PARAMETERS_MODE = fc_mode
                module.USE_TSFRESH_FEATURE_SELECTION = use_selection
            if distance is not None:
                module.DISTANCE_VALUES, module.DISTANCE_MIN, module.DISTANCE_MAX = distance

            results, errors = [], []
            n = len(combinations)
            for idx, smns in enumerate(combinations):
                def inner(current, total, message="", idx=idx, smns=smns):
                    # 組み合わせ全体の中での進み具合に換算して表示する
                    frac = (idx + min(current, total) / max(total, 1)) / n
                    progress(int(frac * 1000), 1000,
                             f"[{idx + 1}/{n}] {'-'.join(smns)}: {message}")

                inner(0, 1, "開始")
                try:
                    results.append((smns, module.run_analysis(smns, progress_callback=inner)))
                except Exception as e:  # noqa: BLE001 - 1件失敗しても残りは続ける
                    print(f"\n[ERROR] {smns}: {type(e).__name__}: {e}")
                    errors.append((smns, e))
            progress(1000, 1000, f"{len(results)}/{n} 件完了")
            return results, errors

        self.runner.run(task, self.progress, on_done=self._done)

    def _done(self, payload):
        results, errors = payload
        self.results = results
        labels = [f"{i + 1}. {'-'.join(smns)}" for i, (smns, _d) in enumerate(results)]
        self.result_combo.configure(values=labels)
        if labels:
            self.result_combo.current(0)
            self._show_selected()
        if errors:
            messagebox.showwarning(
                "一部失敗", "\n".join(f"{'-'.join(s)}: {e}" for s, e in errors))
        if self.on_runs_changed:
            self.on_runs_changed()

    def _show_selected(self):
        idx = self.result_combo.current()
        if 0 <= idx < len(self.results):
            self.view.show(self.results[idx][1])
