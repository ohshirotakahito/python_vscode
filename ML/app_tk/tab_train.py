# -*- coding: utf-8 -*-
"""③ 学習・評価タブ。

クラスの組み合わせを指定して train_xgboost_tsfresh / train_lightgbm_tsfresh /
train_xgboost_traditional の run_analysis() を実行し、レポート・混同行列・重要度を表示する。
"""

import ast
import importlib
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from lib.catalog import list_available_samples
from common.feature_store import list_versions, resolve_version
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


class TrainingCancelled(Exception):
    """進捗通知の区切りで学習を中止する。"""


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
        self._cancel_event = threading.Event()
        self.feature_versions = {}
        self.version_feature_set = None
        self.file_filter_rules = []

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
        self.sample_selection_summary = tk.StringVar(value="選択中（0件）: なし")
        self.samples = CheckList(left, height=10, on_change=self._samples_changed)
        self.samples.pack(fill="both", expand=True, pady=2)
        ttk.Label(left, textvariable=self.sample_selection_summary,
                  wraplength=340).pack(anchor="w", fill="x")
        ttk.Button(left, text="サンプルの選択をすべて解除",
                   command=self.samples.clear_selection).pack(fill="x", pady=2)
        ttk.Button(left, text="抽出結果の日時・条件を確認 / 使用する版を選択",
                   command=self._choose_versions).pack(fill="x", pady=2)
        self.version_summary = ttk.Label(left, text="未指定のサンプルは最新の完了版を使用",
                                         wraplength=340)
        self.version_summary.pack(anchor="w")
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

        ttk.Button(left, text="学習ファイルの絞り込み条件を設定",
                   command=self._choose_file_filters).pack(fill="x", pady=2)
        self.file_filter_summary = ttk.Label(left, text="ファイル絞り込み：なし", wraplength=400)
        self.file_filter_summary.pack(fill="x")
        self.run_button = ttk.Button(left, text="学習を実行", command=self._run)
        self.run_button.pack(fill="x", pady=4)
        self.cancel_button = ttk.Button(left, text="学習を中止", command=self._cancel,
                                        state="disabled")
        self.cancel_button.pack(fill="x", pady=(0, 4))
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
        runner.add_busy_listener(self._busy_changed)
        self._pipeline_changed()

    # --- 入力 ---
    def apply_review_selection(self, feature_set, samples, versions, rules):
        matching = [name for name, settings in PIPELINES.items()
                    if settings['feature_set'] == feature_set]
        if not matching:
            messagebox.showerror('未対応の特徴量セット', f'{feature_set} の学習パイプラインはまだありません。')
            return False
        if self.pipeline.get() not in matching:
            self.pipeline.set(matching[0])
            self._pipeline_changed()
        self.feature_versions = dict(versions)
        self.version_feature_set = feature_set
        self.version_summary.configure(text=f'精査で使用した版を固定: {len(versions)} サンプル')
        self.samples.clear_selection()
        for index, name in enumerate(self.samples.listbox.get(0, 'end')):
            if name in samples:
                self.samples.listbox.selection_set(index)
        self.samples._changed()
        self.combos.delete(0, 'end')
        self.combos.insert('end', ', '.join(samples))
        self._set_file_filters(rules)
        self.progress.log('\n精査画面のサンプル・抽出版・ファイル条件を反映しました。\n'
                          '組み合わせ一覧は今回のサンプルに置き換えました。\n'
                          '学習設定を確認して「学習を実行」を押してください。\n')
        return True

    def _samples_changed(self, selected):
        names = ", ".join(selected) if selected else "なし"
        self.sample_selection_summary.set(f"選択中（{len(selected)}件）: {names}")

    def _choose_file_filters(self):
        from common.file_review import load_events, file_statistics
        from training_filters import FileFilterDialog

        if self.runner.busy:
            return
        names = list(dict.fromkeys(self.samples.get_selected() +
                     [s.strip() for line in self.combos.get(0, "end")
                      for s in line.split(",") if s.strip()]))
        if not names:
            messagebox.showinfo("サンプルを選んでください", "サンプルを選ぶか、組み合わせを追加してください。")
            return
        feature_set = PIPELINES[self.pipeline.get()]["feature_set"]
        try:
            versions = {s: resolve_version(feature_set, s, self.feature_versions.get(s))['run_id']
                        for s in names}
        except (OSError, ValueError) as exc:
            messagebox.showerror("抽出結果を確認してください", str(exc))
            return

        def task(progress):
            return file_statistics(load_events(feature_set, names, progress, versions))

        def done(files):
            if files.empty:
                messagebox.showerror("入力エラー", "絞り込み対象のファイルがありません。")
                return
            FileFilterDialog(self, files, self.file_filter_rules, self._set_file_filters)

        self.runner.run(task, self.progress, on_done=done)

    def _set_file_filters(self, rules):
        self.file_filter_rules = list(rules)
        description = " AND ".join(f"{c} {op} {v}" for c, op, v in rules) or "なし"
        self.file_filter_summary.configure(text=f"ファイル絞り込み：{description}")

    def refresh_samples(self):
        feature_set = PIPELINES[self.pipeline.get()]["feature_set"]
        if self.version_feature_set != feature_set:
            self.feature_versions = {}
            self.version_feature_set = feature_set
            self._set_file_filters([])
        self.version_summary.configure(text=f"過去の版を指定: {len(self.feature_versions)} サンプル / その他は最新")
        self.samples.set_items(list_available_samples(feature_set))

    def _choose_versions(self):
        feature_set = PIPELINES[self.pipeline.get()]["feature_set"]
        # Limit the dialog to the classes selected for this training task.
        names = self.samples.get_selected()
        if not names:
            names = list(dict.fromkeys(s.strip() for line in self.combos.get(0, "end")
                                      for s in line.split(',') if s.strip()))
        if not names:
            messagebox.showinfo("クラスを選択してください",
                                "確認するクラスを選ぶか、学習の組み合わせを追加してください。")
            return
        dialog = tk.Toplevel(self)
        dialog.title("学習に使用する抽出結果")
        dialog.geometry("920x620")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        ttk.Label(dialog, text="サンプルごとに版を選択します。最新は実行開始時に確定します。"
                  "旧形式の抽出日時・条件は不明です。", wraplength=880).pack(anchor="w", padx=10, pady=8)
        toolbar = ttk.Frame(dialog)
        toolbar.pack(fill="x", padx=10)
        ttk.Label(toolbar, text="サンプル").pack(side="left")
        sample_var = tk.StringVar(value=names[0] if names else "")
        sample_combo = ttk.Combobox(toolbar, textvariable=sample_var, values=names, state="readonly", width=24)
        sample_combo.pack(side="left", padx=6)
        ttk.Label(toolbar, text="実験群で絞り込み").pack(side="left")
        experiment_var = tk.StringVar()
        experiment_entry = ttk.Entry(toolbar, textvariable=experiment_var, width=24)
        experiment_entry.pack(side="left", padx=6)
        condition_bar = ttk.Frame(dialog)
        condition_bar.pack(fill="x", padx=10, pady=4)
        ttk.Label(condition_bar, text="条件IDで絞り込み（空欄はすべて）").pack(side="left")
        condition_var = tk.StringVar()
        condition_entry = ttk.Entry(condition_bar, textvariable=condition_var, width=24)
        condition_entry.pack(side="left", padx=6)
        version_var = tk.StringVar()
        version_combo = ttk.Combobox(dialog, textvariable=version_var, state="readonly")
        version_combo.pack(fill="x", padx=10, pady=8)
        details = tk.Text(dialog, wrap="word", height=18)
        details.pack(fill="both", expand=True, padx=10)
        draft = dict(self.feature_versions)
        choices = {}

        def show(_event=None):
            selected = choices.get(version_var.get())
            details.configure(state="normal")
            details.delete("1.0", "end")
            if selected is not None:
                conditions = selected.get('conditions') or {}
                lines = [f"サンプル: {selected['sample']}",
                         f"抽出日時: {selected.get('extracted_at') or '不明（旧形式）'}",
                         f"手法: {selected['feature_set']}",
                         f"実験群: {selected.get('ex') or '不明（旧形式）'}",
                         f"イベント数: {selected.get('event_count', '不明')}",
                         f"条件ID: {selected.get('condition_id', '不明')}",
                         f"条件メモ: {conditions.get('settings', {}).get('note', '')}"]
                waveform = conditions.get('waveform')
                if waveform:
                    lines.extend([f"サンプリング周波数: {waveform['sample_rate_hz']:,} Hz",
                                  f"抽出する長さ: {waveform['duration_min_samples_exclusive']} 点より長いイベント",
                                  f"波形の分割数: {waveform['segments']}",
                                  f"正規化: {waveform['normalization']}"])
                for key in ('chronos_model', 'chronos_device'):
                    if key in conditions:
                        lines.append(f"{key}: {conditions[key]}")
                lines.extend([f"保存先: {selected['directory']}", "", "入力ファイル:"])
                for source in selected.get('inputs', []):
                    lines.append(f"  {source['path']} ({source['size']:,} bytes)")
                if selected['run_id'] == 'legacy':
                    lines.append("  旧形式では、元ファイルや抽出条件を確定できません。")
                if selected.get('warnings'):
                    lines.extend(['', '読めなかった入力（結果は読めたファイルのみ）:'])
                    lines.extend(f"  {w['reason']}: {w['path']}" for w in selected['warnings'])
                details.insert("end", '\n'.join(lines))
            details.configure(state="disabled")

        def refresh(_event=None):
            choices.clear()
            sample = sample_var.get()
            experiment = experiment_var.get().strip() or None
            condition = condition_var.get().strip() or None
            versions = list_versions(feature_set, sample, experiment=experiment,
                                     condition_id=condition) if sample else []
            if versions:
                # With a filter, selecting latest pins the latest matching result rather than
                # silently reverting to an unfiltered result at training time.
                choices["最新の完了版" if experiment is None and condition is None else
                        "この条件の最新（版を固定）"] = versions[0]
            for version in versions:
                label = ("旧形式の保存結果（日時・条件不明）" if version['run_id'] == 'legacy' else
                         f"{version['extracted_at']} | {version.get('ex', '')} | "
                         f"条件 {version.get('condition_id', '')} | {version.get('event_count', '?')} 件 | {version['run_id']}")
                choices[label] = version
            version_combo.configure(values=list(choices))
            chosen = next((label for label, v in choices.items()
                           if label != '最新の完了版' and v['run_id'] == draft.get(sample)),
                          next(iter(choices), ""))
            version_var.set(chosen)
            show()

        def use():
            sample = sample_var.get()
            version = choices.get(version_var.get())
            if version is None:
                return
            if version_var.get() == "最新の完了版":
                draft.pop(sample, None)
            else:
                draft[sample] = version['run_id']
            status.configure(text=f"{sample}: {version_var.get()} を設定（保存で反映）")

        def save():
            use()
            self.feature_versions = draft
            self.version_summary.configure(text=f"版を指定: {len(draft)} サンプル / その他は最新")
            dialog.destroy()

        sample_combo.bind("<<ComboboxSelected>>", refresh)
        experiment_entry.bind("<KeyRelease>", refresh)
        condition_entry.bind("<KeyRelease>", refresh)
        version_combo.bind("<<ComboboxSelected>>", lambda event: (show(event), use()))
        status = ttk.Label(dialog, text="実験群・条件・入力ファイルを確認して選択してください。", wraplength=880)
        status.pack(anchor="w", padx=10, pady=6)
        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=10, pady=8)
        ttk.Button(buttons, text="このサンプルに設定", command=use).pack(side="left")
        ttk.Button(buttons, text="保存して閉じる", command=save).pack(side="right")
        ttk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(side="right", padx=6)
        refresh()

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
    def _busy_changed(self, busy):
        if not busy:
            self.cancel_button.configure(state="disabled", text="学習を中止")

    def _cancel(self):
        self._cancel_event.set()
        self.cancel_button.configure(state="disabled", text="中止待ち…")
        self.progress.log("\n中止を要求しました。現在の計算の区切りまでお待ちください。\n")

    def _run(self):
        if self.runner.busy:
            return
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
        # Resolve on the UI thread once for the whole batch, before imports/calculation.
        try:
            selected_versions = {sample: resolve_version(pipeline['feature_set'], sample,
                                 self.feature_versions.get(sample))
                                 for sample in {s for group in combinations for s in group}}
        except (OSError, ValueError) as error:
            messagebox.showerror("抽出結果を確認してください", str(error))
            return
        feature_versions = {s: v['run_id'] for s, v in selected_versions.items()}
        file_filter_rules = list(self.file_filter_rules)
        for sample, version in selected_versions.items():
            self.progress.log(f"\n{sample}: {version['run_id']} / 実験群 {version.get('ex')} / "
                              f"条件 {version.get('condition_id', '不明')}\n")
        self._cancel_event.clear()

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
                    if self._cancel_event.is_set():
                        raise TrainingCancelled()
                    # 組み合わせ全体の中での進み具合に換算して表示する
                    frac = (idx + min(current, total) / max(total, 1)) / n
                    progress(int(frac * 1000), 1000,
                             f"[{idx + 1}/{n}] {'-'.join(smns)}: {message}")

                try:
                    inner(0, 1, "開始")
                    results.append((smns, module.run_analysis(smns, progress_callback=inner,
                                                             feature_versions=feature_versions,
                                                             file_filter_rules=file_filter_rules)))
                except TrainingCancelled:
                    return results, errors, True
                except Exception as e:  # noqa: BLE001 - 1件失敗しても残りは続ける
                    print(f"\n[ERROR] {smns}: {type(e).__name__}: {e}")
                    errors.append((smns, e))
            progress(1000, 1000, f"{len(results)}/{n} 件完了")
            return results, errors, self._cancel_event.is_set()

        if self.runner.run(task, self.progress, on_done=self._done):
            self.cancel_button.configure(state="normal", text="学習を中止")

    def _done(self, payload):
        results, errors, cancelled = payload
        if cancelled:
            self.progress.finish(f"学習を中止しました（{len(results)} 件完了）", ok=False)
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
