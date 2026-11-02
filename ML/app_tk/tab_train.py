# -*- coding: utf-8 -*-
"""③ 学習・評価タブ。

クラスの組み合わせを指定して train_xgboost_tsfresh / train_lightgbm_tsfresh /
train_xgboost_traditional の run_analysis() を実行し、レポート・混同行列・重要度を表示する。
"""

import importlib
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from lib.catalog import list_available_samples
from common.feature_store import list_versions, resolve_version
from common.fc_parameters import FC_PARAMETER_MODES
from common.training_core import BALANCE_METHODS, DEFAULT_SETTINGS
from tab_browser import RunResultView
from widgets import CheckList, ProgressPanel, ScrollableFrame

PIPELINES = {
    "rmc + XGBoost（tsfresh併用）": {
        "feature_set": "rmc", "module": "train_xgboost_tsfresh", "tsfresh": True,
    },
    "rmc + LightGBM（tsfresh併用）": {
        "feature_set": "rmc", "module": "train_lightgbm_tsfresh", "tsfresh": True,
    },
    "rmb + XGBoost（波形12点特徴量のみ）": {
        "feature_set": "rmb", "module": "train_xgboost_traditional", "tsfresh": False,
    },
}


class TrainingCancelled(Exception):
    """進捗通知の区切りで学習を中止する。"""


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
        self.sample_groups = {}

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # --- 左: 設定 ---
        left_panel = ttk.Frame(paned, padding=(0, 0, 8, 0))
        paned.add(left_panel, weight=1)
        actions = ttk.LabelFrame(left_panel, text="⑤ 学習を実行", padding=6)
        actions.pack(side="bottom", fill="x", pady=(6, 0))
        settings = ScrollableFrame(left_panel)
        settings.pack(fill="both", expand=True)
        left = settings.inner
        ttk.Label(left, text="パイプライン").pack(anchor="w")
        self.pipeline = tk.StringVar(value=list(PIPELINES)[0])
        combo = ttk.Combobox(left, textvariable=self.pipeline, values=list(PIPELINES),
                             state="readonly")
        combo.pack(fill="x", pady=(0, 6))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._pipeline_changed())

        ttk.Label(left, text="実験フォルダ（出どころ）").pack(anchor="w")
        self.experiment = tk.StringVar()
        self.experiment_combo = ttk.Combobox(left, state="readonly")
        self.experiment_combo.pack(fill="x")
        self.experiment_combo.bind("<<ComboboxSelected>>", self._filter_samples)
        self.source_hint = ttk.Label(left, wraplength=400)
        self.source_hint.pack(fill="x", pady=(2, 4))

        ttk.Label(left, text="① クラス（サンプル）を2つ以上選択（クリックで選択・解除）").pack(anchor="w")
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
        group_frame = ttk.LabelFrame(left, text="分類単位・グループ設定", padding=6)
        group_frame.pack(fill="x", pady=6)
        self.classification_mode = tk.StringVar(value="sample")
        ttk.Radiobutton(group_frame, text="サンプル別（従来）", variable=self.classification_mode,
                        value="sample").pack(anchor="w")
        ttk.Radiobutton(group_frame, text="グループ別", variable=self.classification_mode,
                        value="group").pack(anchor="w")
        ttk.Button(group_frame, text="グループ名・所属サンプルを設定 / 保存・読込",
                   command=self._choose_groups).pack(fill="x")
        self.group_summary = ttk.Label(group_frame, text="グループ未設定", wraplength=380)
        self.group_summary.pack(fill="x")
        self.split_mode = tk.StringVar(value="event")
        ttk.Radiobutton(group_frame, text="評価：イベント単位", variable=self.split_mode,
                        value="event").pack(anchor="w")
        ttk.Radiobutton(group_frame, text="評価：元サンプル単位（各群3サンプル以上）",
                        variable=self.split_mode, value="sample").pack(anchor="w")
        ttk.Label(group_frame, text="グループ別では登録した全所属サンプルで1回学習します。\n"
                  "実行する内容は下の一覧に表示します。", wraplength=380).pack(fill="x")
        self.add_combo_button = ttk.Button(left, text="② 選択中のクラスを組み合わせに追加 ↓",
                                          command=self._add_combo)
        self.add_combo_button.pack(fill="x", pady=2)

        self.execution_label = ttk.Label(left, text="実行する組み合わせ（1行につき1回の学習）", wraplength=380)
        self.execution_label.pack(anchor="w", pady=(6, 0))
        execution_frame = ttk.Frame(left)
        execution_frame.pack(fill="x", pady=2)
        self.combos = tk.Listbox(execution_frame, height=5, exportselection=False)
        self.combos.pack(fill="x", pady=2)
        self.group_preview = tk.Listbox(execution_frame, height=5, exportselection=False)
        self.remove_combo_button = ttk.Button(left, text="選択した組み合わせを削除", command=self._remove_combo)
        self.remove_combo_button.pack(fill="x")

        self.tsfresh_frame = ttk.LabelFrame(left, text="tsfresh設定", padding=6)
        self.tsfresh_frame.pack(fill="x", pady=6)
        self.fc_mode = tk.StringVar(value="minimal")
        for mode, label in FC_PARAMETER_MODES.items():
            ttk.Radiobutton(self.tsfresh_frame, text=label, value=mode,
                            variable=self.fc_mode).pack(anchor="w")
        self.use_selection = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.tsfresh_frame, text="tsfresh特徴量選択を使う（学習用データのみで選択）",
                        variable=self.use_selection).pack(anchor="w")

        # 全パイプライン共通の学習設定（common/training_core.py）
        training_frame = self.training_frame = ttk.LabelFrame(left, text="学習設定（全パイプライン共通）", padding=6)
        training_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(training_frame, text="クラスの偏りへの対処").pack(anchor="w")
        self.balance = tk.StringVar(value=BALANCE_METHODS[DEFAULT_SETTINGS["balance"]])
        ttk.Combobox(training_frame, textvariable=self.balance, values=list(BALANCE_METHODS.values()),
                     state="readonly").pack(fill="x", pady=(0, 4))
        tune_row = ttk.Frame(training_frame)
        tune_row.pack(fill="x")
        self.tune = tk.BooleanVar(value=DEFAULT_SETTINGS["tune"])
        ttk.Checkbutton(tune_row, text="ハイパーパラメータを探索する　候補数",
                        variable=self.tune).pack(side="left")
        self.n_trials = tk.StringVar(value=str(DEFAULT_SETTINGS["n_trials"]))
        ttk.Spinbox(tune_row, from_=1, to=200, textvariable=self.n_trials, width=5).pack(side="left", padx=4)
        ttk.Label(training_frame, text="イベント単位の評価では学習用/テスト用を8:2に分けます。"
                  "元サンプル単位の評価も上で選択できます。探索すると学習時間は約（候補数+1）倍になります。", wraplength=380).pack(anchor="w", pady=(4, 0))

        self.settings_end = ttk.Frame(left)
        self.settings_end.pack(fill="x")
        ttk.Button(left, text="③ 学習ファイルの絞り込みを設定（任意）",
                   command=self._choose_file_filters).pack(fill="x", pady=2)
        ttk.Label(left, text="distanceもここで指定します（例：distance.mean = 0.54）。", wraplength=400).pack(anchor="w")
        self.file_filter_summary = ttk.Label(left, text="ファイル絞り込み：なし", wraplength=400)
        self.file_filter_summary.pack(fill="x")
        self.run_hint = ttk.Label(actions, wraplength=400)
        self.run_hint.pack(anchor="w", pady=(0, 4))
        self.run_button = ttk.Button(actions, text="⑤ 学習を実行", command=self._run)
        self.run_button.pack(fill="x", pady=4)
        self.cancel_button = ttk.Button(actions, text="学習を中止", command=self._cancel,
                                        state="disabled")
        self.cancel_button.pack(fill="x", pady=(0, 4))
        self.progress = ProgressPanel(actions, log_height=3)
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
        self.classification_mode.trace_add('write', lambda *_: self._classification_changed())
        self._classification_changed()
        self._pipeline_changed()

    # --- 入力 ---
    def _classification_changed(self):
        grouped = self.classification_mode.get() == 'group'
        self.add_combo_button.configure(state='disabled' if grouped else 'normal')
        self.remove_combo_button.configure(state='disabled' if grouped else 'normal')
        if grouped:
            self.combos.pack_forget()
            self.group_preview.delete(0, 'end')
            for name, members in self.sample_groups.items():
                self.group_preview.insert('end', f'{name}: {", ".join(members)}')
            if not self.sample_groups:
                self.group_preview.insert('end', 'グループ未設定：上の設定ボタンから登録してください。')
            self.group_preview.pack(fill='x', pady=2)
            count = sum(len(members) for members in self.sample_groups.values())
            self.execution_label.configure(text=f'実行予定：グループ分類を1回（{len(self.sample_groups)}群・{count}サンプル）')
            self.run_hint.configure(text='上の実行予定に表示した全グループで1回学習します。\n'
                                    '所属・評価方法を確認し、「学習を実行」を押してください。\n'
                                    '②の組み合わせ追加は不要です。')
        else:
            self.group_preview.pack_forget()
            self.combos.pack(fill='x', pady=2)
            self.execution_label.configure(text='実行する組み合わせ（1行につき1回の学習）')
            self.run_hint.configure(text='絞り込み条件の保存後、このボタンで学習を開始します。\n'
                                    '組み合わせの登録があれば登録分を順番に実行します。\n'
                                    '未登録なら、現在選択中のサンプルで1回実行します。')

    def apply_review_selection(self, feature_set, samples, versions, rules):
        if hasattr(self, 'classification_mode'):
            self.classification_mode.set('sample')
        matching = [name for name, settings in PIPELINES.items()
                    if settings['feature_set'] == feature_set]
        if not matching:
            messagebox.showerror('未対応の特徴量セット', f'{feature_set} の学習パイプラインはまだありません。')
            return False
        if self.pipeline.get() not in matching:
            self.pipeline.set(matching[0])
            self._pipeline_changed()
        # Review may supply versions from a different source, or multiple sources.
        self.experiment.set('')
        self.refresh_samples()
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
        if getattr(self, 'classification_mode', None) is not None and self.classification_mode.get() == 'group':
            names = [s for members in self.sample_groups.values() for s in members]
        if not names:
            messagebox.showinfo("サンプルを選んでください", "サンプルを選ぶか、組み合わせを追加してください。")
            return
        feature_set = PIPELINES[self.pipeline.get()]["feature_set"]
        try:
            versions = {s: resolve_version(feature_set, s, self.feature_versions.get(s),
                                           experiment=self.experiment.get() or None)['run_id']
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
        changed = experiment != self.experiment.get()
        self.experiment.set(experiment)
        names = [name for name in self._sample_names if not experiment or any(
            v.get('ex') == experiment for v in self._source_versions[name])]
        if experiment:
            self.feature_versions = {name: run_id for name, run_id in self.feature_versions.items()
                                     if any(v['run_id'] == run_id and v.get('ex') == experiment
                                            for v in self._source_versions.get(name, []))}
        if changed:
            self._set_file_filters([])
        self.version_summary.configure(text=f'版を指定: {len(self.feature_versions)} サンプル / '
                                       'その他は選択した出どころの最新完了版')
        self.samples.set_items(names)
        self.source_hint.configure(text=(
            f'{len(names)} サンプル：組み合わせも選択した実験の抽出結果で学習します'
            if experiment else 'すべての出どころを表示。旧形式は出どころ不明です。'))

    def _choose_versions(self):
        feature_set = PIPELINES[self.pipeline.get()]["feature_set"]
        # Limit the dialog to the classes selected for this training task.
        names = self.samples.get_selected()
        if not names:
            names = list(dict.fromkeys(s.strip() for line in self.combos.get(0, "end")
                                      for s in line.split(',') if s.strip()))
        if getattr(self, 'classification_mode', None) is not None and self.classification_mode.get() == 'group':
            names = [s for members in self.sample_groups.values() for s in members]
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
        source_experiment = getattr(self, 'experiment', None)
        locked_experiment = source_experiment.get() if source_experiment is not None else ''
        experiment_var = tk.StringVar(value=locked_experiment)
        experiment_entry = ttk.Entry(toolbar, textvariable=experiment_var, width=24)
        experiment_entry.pack(side="left", padx=6)
        if locked_experiment:
            experiment_entry.configure(state="readonly")
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
            self.tsfresh_frame.pack(fill="x", pady=6, before=self.training_frame)
        else:
            self.tsfresh_frame.pack_forget()

    def _training_settings(self):
        """学習設定欄を common.training_core.configure() の引数に変換する。不正なら ValueError。"""
        balance = next(key for key, label in BALANCE_METHODS.items() if label == self.balance.get())
        try:
            n_trials = int(self.n_trials.get())
        except ValueError:
            raise ValueError(f"探索の候補数は整数で入力してください: {self.n_trials.get()}") from None
        if n_trials < 1:
            raise ValueError("探索の候補数は1以上にしてください。")
        return {"balance": balance, "tune": bool(self.tune.get()), "n_trials": n_trials,
                "classification_mode": self.classification_mode.get(),
                "sample_groups": {k: list(v) for k, v in self.sample_groups.items()},
                "split_mode": self.split_mode.get()}

    def _choose_groups(self):
        import json
        from tkinter import filedialog
        from common.training_core import normalize_groups
        if self.runner.busy:
            return
        dialog = tk.Toplevel(self)
        dialog.title("分類グループの設定")
        dialog.geometry("650x700")
        draft = [(name, list(members)) for name, members in self.sample_groups.items()]
        ttk.Label(dialog, text="グループ名（空欄なら Group A、Group B…）").pack(anchor="w", padx=10)
        name = tk.StringVar()
        ttk.Entry(dialog, textvariable=name).pack(fill="x", padx=10)
        ttk.Label(dialog, text="所属サンプルを選択").pack(anchor="w", padx=10)
        members = CheckList(dialog, height=12)
        members.pack(fill="both", expand=True, padx=10)
        members.set_items(list(self.samples.listbox.get(0, "end")))
        groups = tk.Listbox(dialog, height=7, exportselection=False)
        groups.pack(fill="x", padx=10, pady=6)

        def refresh():
            groups.delete(0, "end")
            for i, (label, items) in enumerate(draft):
                groups.insert("end", f"{label or ('Group ' + (chr(65+i) if i < 26 else str(i+1)))}: {', '.join(items)}")

        def put(edit=False):
            selected = members.get_selected()
            if not selected:
                messagebox.showerror("入力エラー", "所属サンプルを選択してください。", parent=dialog)
                return
            index = groups.curselection()
            if edit and not index:
                return
            entry = (name.get().strip(), selected)
            if edit:
                draft[index[0]] = entry
            else:
                draft.append(entry)
            refresh()
            name.set("")
            members.clear_selection()

        def select(_event):
            index = groups.curselection()
            if not index:
                return
            label, items = draft[index[0]]
            name.set(label)
            members.clear_selection()
            for i, sample in enumerate(members.listbox.get(0, 'end')):
                if sample in items:
                    members.listbox.selection_set(i)
            members._changed()

        def remove():
            for index in reversed(groups.curselection()):
                draft.pop(index)
            refresh()

        def checked():
            available = set(self.samples.listbox.get(0, 'end'))
            result = normalize_groups(draft)
            unknown = {s for items in result.values() for s in items} - available
            if unknown:
                raise ValueError(f'現在の実験フォルダにないサンプル: {sorted(unknown)}')
            return result

        def save_file():
            try:
                result = checked()
                path = filedialog.asksaveasfilename(parent=dialog, defaultextension='.json', filetypes=[('JSON', '*.json')])
                if path:
                    from pathlib import Path
                    Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            except (ValueError, OSError) as error:
                messagebox.showerror('設定エラー', str(error), parent=dialog)

        def load_file():
            path = filedialog.askopenfilename(parent=dialog, filetypes=[('JSON', '*.json')])
            if not path:
                return
            try:
                from pathlib import Path
                data = json.loads(Path(path).read_text(encoding='utf-8'))
                if not isinstance(data, dict) or any(not isinstance(v, list) or any(not isinstance(s, str) for s in v) for v in data.values()):
                    raise ValueError('グループ名と所属サンプル一覧のJSONを指定してください。')
                result = normalize_groups(data.items())
                draft[:] = list(result.items())
                refresh()
            except (ValueError, OSError) as error:
                messagebox.showerror('読込エラー', str(error), parent=dialog)

        def apply():
            try:
                self.sample_groups = checked()
            except ValueError as error:
                messagebox.showerror('設定エラー', str(error), parent=dialog)
                return
            self.group_summary.configure(text='\n'.join(f'{k}: {", ".join(v)}' for k, v in self.sample_groups.items()))
            self.classification_mode.set('group')
            dialog.destroy()

        groups.bind('<<ListboxSelect>>', select)
        row = ttk.Frame(dialog)
        row.pack(fill='x', padx=10, pady=4)
        for label, command in [('追加', lambda: put()), ('選択行を更新', lambda: put(True)), ('選択行を削除', remove),
                               ('JSON保存', save_file), ('JSON読込', load_file)]:
            ttk.Button(row, text=label, command=command).pack(side='left')
        ttk.Button(dialog, text='設定を適用', command=apply).pack(fill='x', padx=10, pady=8)
        refresh()

    def _add_combo(self):
        if getattr(self, 'classification_mode', None) is not None and self.classification_mode.get() == 'group':
            return
        selected = self.samples.get_selected()
        if len(selected) < 2:
            messagebox.showerror("入力エラー", "クラスを2つ以上選んでください。")
            return
        self.combos.insert("end", ", ".join(selected))
        self.samples.clear_selection()

    def _remove_combo(self):
        if getattr(self, 'classification_mode', None) is not None and self.classification_mode.get() == 'group':
            return
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
        if getattr(self, 'classification_mode', None) is not None and self.classification_mode.get() == 'group':
            from common.training_core import normalize_groups
            try:
                groups = normalize_groups(self.sample_groups.items())
                samples = [s for items in groups.values() for s in items]
                if set(samples) - set(self.samples.listbox.get(0, 'end')):
                    raise ValueError('現在の実験フォルダにない所属サンプルがあります。グループ設定を確認してください。')
                combinations = [samples]
            except ValueError as error:
                messagebox.showerror('グループ設定エラー', str(error))
                return
        if not combinations:
            # 組み合わせ未登録なら、今選んでいるサンプルを1件として実行する
            selected = self.samples.get_selected()
            if len(selected) < 2:
                messagebox.showerror("入力エラー",
                                     "クラスを2つ以上選ぶか、組み合わせを追加してください。")
                return
            combinations = [selected]

        pipeline = PIPELINES[self.pipeline.get()]
        try:
            training_settings = self._training_settings()
            if training_settings.get('split_mode') == 'sample':
                if training_settings.get('classification_mode') != 'group' or any(
                        len(items) < 3 for items in training_settings.get('sample_groups', {}).values()):
                    raise ValueError('元サンプル単位の評価にはグループ別分類で各群3サンプル以上を登録してください。')
        except ValueError as e:
            messagebox.showerror("入力エラー", str(e))
            return
        fc_mode = self.fc_mode.get()
        use_selection = self.use_selection.get()
        # Resolve on the UI thread once for the whole batch, before imports/calculation.
        try:
            selected_versions = {sample: resolve_version(pipeline['feature_set'], sample,
                                 self.feature_versions.get(sample),
                                 experiment=self.experiment.get() or None)
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
                from common.fc_parameters import fc_parameters_for

                fc_parameters = fc_parameters_for(fc_mode)
                # build_combined_dataset() などは common/data_pipeline.py 側のモジュール変数
                # (dp.FC_PARAMETERS) を参照するため、そちらも合わせて上書きする必要がある。
                module.FC_PARAMETERS = fc_parameters
                module.dp.FC_PARAMETERS = fc_parameters
                if hasattr(module, "FC_PARAMETERS_MODE"):
                    module.FC_PARAMETERS_MODE = fc_mode
                module.USE_TSFRESH_FEATURE_SELECTION = use_selection
            from common import training_core
            training_core.reset()
            training_core.configure(**training_settings)

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
