"""Version selection shared by feature inspection tabs."""
import tkinter as tk
from tkinter import messagebox, ttk
from common.feature_store import list_versions


class InspectionVersions:
    def _build_version_picker(self, parent):
        self.feature_versions = {}
        self.version_feature_set = self.feature_set.get()
        ttk.Button(parent, text="抽出結果の日時・条件を確認 / 使用する版を選択",
                   command=self._choose_versions).pack(fill="x", pady=4)
        self.version_summary = ttk.Label(parent, text="未指定のサンプルは最新の完了版を使用", wraplength=400)
        self.version_summary.pack(fill="x", pady=(0, 4))

    def _refresh_version_set(self):
        if self.version_feature_set != self.feature_set.get():
            self.feature_versions = {}
            self.version_feature_set = self.feature_set.get()
            self.version_summary.configure(text="未指定のサンプルは最新の完了版を使用")

    def _choose_versions(self):
        feature_set = self.feature_set.get()
        names = self.samples.get_selected()
        if not names:
            messagebox.showinfo("サンプルを選択してください", "確認するサンプルを選んでください。")
            return
        dialog = tk.Toplevel(self)
        dialog.title("確認に使用する抽出結果")
        dialog.geometry("920x620")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        ttk.Label(dialog, text="サンプルごとに版を選択します。最新は読み込み開始時に確定します。"
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
