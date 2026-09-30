# -*- coding: utf-8 -*-
"""① 特徴量抽出タブ。

1. サーバー・共有フォルダ・実験フォルダ・サンプルを選ぶ（filemanager と同じ「一覧から選択」）
2. 「ファイルを確認」で、サンプルごとに 測定ファイル／ANAL済みファイル／抽出済みかどうか と、
   ファイル名から読める 測定日時・Gap・装置 を一覧表示（common/extract_catalog.scan_sample）
3. 抽出するファイルをチェックで選ぶ（全選択・未抽出のみ・Gapで絞り込み など）
4. 「抽出を実行」で、選んだファイルだけを
   extract_features_traditional / extract_features_tsfresh の run_extraction() で抽出
5. 「抽出履歴」タブで、いつ・どのファイルから・何件抽出したかを確認
"""

import importlib
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import messagebox, ttk

import pandas as pd

import common.paths as paths
from common.extract_catalog import load_history, scan_sample
from lib.catalog import list_available_samples
from server_browse import SERVERS, FolderPickerDialog, check_component, list_server_shares, list_subfolders
from widgets import DataTable, ProgressPanel, open_path

METHODS = {
    "traditional（波形12点特徴量 → data/features/rmb）": {
        "module": "extract_features_traditional", "feature_set": "rmb",
    },
    "tsfresh（→ data/features/rmc）": {
        "module": "extract_features_tsfresh", "feature_set": "rmc",
    },
}

SELECTABLE = ("未抽出", "未抽出（同名の別実験あり）", "抽出済み")
STATUS_COLORS = {
    "未抽出": "#1a5fb4", "未抽出（同名の別実験あり）": "#1a5fb4", "抽出済み": "#2e7d32",
    "ANAL未処理": "#b35c00", "測定ファイルなし": "#b35c00", "Tフォルダなし": "#c0392b",
}
CHECKED, UNCHECKED, NA = "☑", "☐", "－"
ALL_GAPS = "すべてのGap"


def _fmt_time(value, fmt="%Y-%m-%d %H:%M"):
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return ""
    return value.strftime(fmt) if hasattr(value, "strftime") else str(value)


def _fmt_value(value, fmt="{}"):
    return "" if value is None or pd.isna(value) else fmt.format(value)


class ExtractTab(ttk.Frame):
    def __init__(self, master, runner, on_features_changed=None):
        super().__init__(master, padding=10)
        self.runner = runner
        self.on_features_changed = on_features_changed
        self.output_dir = None
        self.scan_df = pd.DataFrame()
        self.scan_key = None
        self.checked: set[int] = set()

        self._build_form()
        self._build_actions()

        self.sub = ttk.Notebook(self)
        self.sub.pack(fill="both", expand=True, pady=(6, 0))
        self._build_file_tab()
        self._build_history_tab()
        self.progress = ProgressPanel(self.sub, log_height=16)
        self.sub.add(self.progress, text="進捗・ログ")

        runner.add_busy_listener(self._on_busy)
        self.refresh_history()

    # =====================
    # 設定欄
    # =====================
    def _build_form(self):
        ttk.Label(self, text="計測サーバー上のANAL済みtdmsファイルから特徴量を計算し、"
                             "data/features/ 以下に保存します。").pack(anchor="w")
        form = ttk.LabelFrame(self, text="設定", padding=8)
        form.pack(fill="x", pady=(6, 0))
        form.columnconfigure(1, weight=1)

        self.method = tk.StringVar(value=list(METHODS)[0])
        ttk.Label(form, text="抽出手法").grid(row=0, column=0, sticky="nw", padx=(4, 12), pady=3)
        method_box = ttk.Frame(form)
        method_box.grid(row=0, column=1, sticky="w", pady=3)
        for label in METHODS:
            ttk.Radiobutton(method_box, text=label, value=label, variable=self.method).pack(
                side="left", padx=(0, 16))

        self.server = tk.StringVar(value=SERVERS[0])
        self.share = tk.StringVar(value="analysis")
        self.ex = tk.StringVar()
        self.samples = tk.StringVar()
        self._row(form, 1, "サーバー", ttk.Combobox(form, textvariable=self.server, values=SERVERS,
                                                  state="readonly"))
        self._row(form, 2, "共有フォルダ", ttk.Entry(form, textvariable=self.share),
                  lambda: self._pick("share"))
        self._row(form, 3, "実験フォルダ名", ttk.Entry(form, textvariable=self.ex),
                  lambda: self._pick("ex"))
        self._row(form, 4, "サンプル名（カンマ区切り）", ttk.Entry(form, textvariable=self.samples),
                  lambda: self._pick("samples"))
        self.path_label = ttk.Label(form, text="", foreground="#666")
        self.path_label.grid(row=5, column=1, columnspan=2, sticky="w")
        for var in (self.method, self.server, self.share, self.ex, self.samples):
            var.trace_add("write", lambda *_: self._settings_changed())
        self._update_path()

    @staticmethod
    def _row(form, row, label, widget, browse=None):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(4, 12), pady=2)
        widget.grid(row=row, column=1, sticky="ew", pady=2)
        if browse:
            ttk.Button(form, text="一覧から選択 / 更新", command=browse).grid(row=row, column=2, padx=5)

    def _update_path(self):
        parts = [self.server.get(), self.share.get().strip(), self.ex.get().strip()]
        path = "//" + "/".join(p for p in parts if p)
        self.path_label.configure(text=f"読み込み元: {path}/<サンプル>/<サンプル>_10k_Sample/T/…ANAL…")

    def _settings_changed(self):
        self._update_path()
        if self.scan_key is not None and self.scan_key != self._current_key():
            self.scan_status.configure(
                text="設定が変わりました。「ファイルを確認」を押し直してください。", foreground="#b35c00")

    def _build_actions(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(6, 0))
        self.scan_button = ttk.Button(bar, text="① ファイルを確認", command=self._scan)
        self.scan_button.pack(side="left")
        self.run_button = ttk.Button(bar, text="② 抽出を実行", command=self._run)
        self.run_button.pack(side="left", padx=6)
        self.open_button = ttk.Button(bar, text="保存先フォルダを開く",
                                      command=lambda: open_path(self._feature_dir()))
        self.open_button.pack(side="left")
        self.scan_status = ttk.Label(bar, text="サンプルを選んで「ファイルを確認」を押すと、"
                                               "抽出できるファイルと抽出済みかどうかを一覧表示します。",
                                     foreground="#555")
        self.scan_status.pack(side="left", padx=12)

    def _on_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.run_button.configure(state=state)
        self.scan_button.configure(state=state)

    def _feature_set(self):
        return METHODS[self.method.get()]["feature_set"]

    def _feature_dir(self):
        return paths.feature_dir(self._feature_set())

    def _sample_list(self):
        return [s.strip() for s in self.samples.get().split(",") if s.strip()]

    def _current_key(self):
        return (self._feature_set(), self.server.get(), self.share.get().strip(),
                self.ex.get().strip(), tuple(self._sample_list()))

    def _validated_settings(self):
        share = check_component(self.share.get(), "共有フォルダ")
        ex = check_component(self.ex.get(), "実験フォルダ名")
        samples = self._sample_list()
        if not samples:
            raise ValueError("サンプル名を1つ以上入力するか、一覧から選択してください。")
        for sample in samples:
            check_component(sample, "サンプル名")
        return self.server.get(), share, ex, samples

    # --- 一覧から選択 ---
    def _pick(self, key):
        server = self.server.get()
        try:
            share = check_component(self.share.get(), "共有フォルダ") if key != "share" else ""
            ex = check_component(self.ex.get(), "実験フォルダ名") if key == "samples" else ""
        except ValueError as e:
            messagebox.showerror("入力を確認してください", str(e))
            return

        if key == "share":
            FolderPickerDialog(
                self, "共有フォルダの一覧から選択", f"//{server}",
                loader=lambda: list_server_shares(server),
                on_select=self._set_share, preselected=[self.share.get().strip()])
        elif key == "ex":
            parent = f"//{server}/{share}"
            FolderPickerDialog(
                self, "実験フォルダの一覧から選択", parent,
                loader=lambda: list_subfolders(parent), browse_dir=parent,
                on_select=self._set_ex, preselected=[self.ex.get().strip()])
        else:
            parent = f"//{server}/{share}/{ex}"
            feature_set = self._feature_set()
            extracted = set(list_available_samples(feature_set))
            FolderPickerDialog(
                self, "サンプルの一覧から選択", parent,
                loader=lambda: list_subfolders(parent), browse_dir=parent, multiple=True,
                annotate=lambda name: f"{feature_set} に同名の特徴量あり" if name in extracted else "",
                on_select=lambda names: self.samples.set(", ".join(dict.fromkeys(names))),
                preselected=self._sample_list())

    def _set_share(self, name):
        if name != self.share.get():
            self.share.set(name)
            self.ex.set("")
            self.samples.set("")

    def _set_ex(self, name):
        if name != self.ex.get():
            self.ex.set(name)
            self.samples.set("")

    # =====================
    # ファイル確認・選択
    # =====================
    FILE_COLUMNS = (
        ("check", "抽出", 44, "center"), ("sample", "サンプル", 90, "w"), ("no", "No", 44, "e"),
        ("status", "状態", 170, "w"), ("measured", "測定日時", 125, "w"), ("gap", "Gap(nm)", 62, "e"),
        ("machine", "装置", 48, "center"), ("analyzed", "ANAL解析日時", 125, "w"),
        ("events", "抽出済み件数", 88, "e"), ("last", "最終抽出（履歴）", 130, "w"),
        ("experiment", "実験ID", 110, "w"),
    )

    def _build_file_tab(self):
        frame = ttk.Frame(self.sub, padding=6)
        self.sub.add(frame, text="ファイル確認・選択")

        self.summary = DataTable(frame, height=3)
        self.summary.pack(fill="x")

        tools = ttk.Frame(frame)
        tools.pack(fill="x", pady=(6, 4))
        ttk.Label(tools, text="選択:").pack(side="left")
        ttk.Button(tools, text="すべて", command=lambda: self._check_where(lambda r: True)).pack(side="left", padx=2)
        ttk.Button(tools, text="未抽出のみ",
                   command=lambda: self._check_where(lambda r: r["status"].startswith("未抽出"))).pack(side="left", padx=2)
        ttk.Button(tools, text="すべて解除", command=lambda: self._check_where(lambda r: False)).pack(side="left", padx=2)
        ttk.Label(tools, text="  Gap").pack(side="left")
        self.gap_choice = ttk.Combobox(tools, values=[ALL_GAPS], state="readonly", width=12)
        self.gap_choice.set(ALL_GAPS)
        self.gap_choice.pack(side="left", padx=2)
        ttk.Button(tools, text="このGapだけ選択", command=self._check_gap).pack(side="left", padx=2)
        self.selection_label = ttk.Label(tools, text="", foreground="#333")
        self.selection_label.pack(side="right")

        ttk.Label(frame, text="行をクリックすると抽出する／しないが切り替わります（Shift/Ctrlで複数行を選んで"
                              "Spaceキーでまとめて切り替え）。ANALが無いファイルは選べません。",
                  foreground="#666").pack(anchor="w")

        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True, pady=(4, 0))
        self.files = ttk.Treeview(table, columns=[c[0] for c in self.FILE_COLUMNS], show="headings",
                                  selectmode="extended")
        for key, text, width, anchor in self.FILE_COLUMNS:
            self.files.heading(key, text=text)
            self.files.column(key, width=width, anchor=anchor, stretch=key == "status")
        vs = ttk.Scrollbar(table, orient="vertical", command=self.files.yview)
        self.files.configure(yscrollcommand=vs.set)
        self.files.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        for status, color in STATUS_COLORS.items():
            self.files.tag_configure(status, foreground=color)
        self.files.bind("<ButtonRelease-1>", self._on_click)
        self.files.bind("<space>", lambda _e: self._toggle(self.files.selection()))

    def _scan(self, auto=False):
        try:
            server, share, ex, samples = self._validated_settings()
        except ValueError as e:
            messagebox.showerror("入力を確認してください", str(e))
            return
        feature_set = self._feature_set()
        key = self._current_key()
        if not auto:
            self.sub.select(2)

        def task(progress):
            frames = []
            progress(0, len(samples), "サーバー上のファイルを確認中…")
            # サーバーへの問い合わせ待ちが主なので、サンプルごとに並列で確認する
            with ThreadPoolExecutor(max_workers=min(4, len(samples))) as pool:
                futures = {pool.submit(scan_sample, server, share, ex, s, feature_set): s
                           for s in samples}
                for done, future in enumerate(as_completed(futures), start=1):
                    sample = futures[future]
                    df = future.result()
                    print(f"[{sample}] {len(df)} 件: {df['status'].value_counts().to_dict()}")
                    frames.append(df)
                    progress(done, len(samples), f"[{sample}] 確認完了")
            order = {s: i for i, s in enumerate(samples)}
            result = pd.concat(frames, ignore_index=True)
            result["_order"] = result["sample"].map(order)
            return result.sort_values(["_order", "file_no"], ignore_index=True).drop(columns="_order")

        def done(df):
            previous = self._checked_keys()
            self.scan_df, self.scan_key = df, key
            self._fill_files(previous if auto else None)
            self.sub.select(0)

        self.runner.run(task, self.progress, on_done=done)

    def _checked_keys(self):
        if self.scan_df.empty:
            return set()
        return {(self.scan_df.at[i, "sample"], self.scan_df.at[i, "file_key"]) for i in self.checked}

    def _fill_files(self, keep_keys=None):
        df = self.scan_df
        self.files.delete(*self.files.get_children())
        if keep_keys is None:
            # 初期状態: まだ抽出していないファイルだけにチェックを付ける
            self.checked = {i for i, r in df.iterrows() if r["status"].startswith("未抽出")}
        else:
            self.checked = {i for i, r in df.iterrows()
                            if (r["sample"], r["file_key"]) in keep_keys and r["status"] in SELECTABLE}
        for i, r in df.iterrows():
            self.files.insert("", "end", iid=str(i), tags=(r["status"],), values=(
                self._mark(i), r["sample"], _fmt_value(r["file_no"], "{:03.0f}"),
                r["status"] if r["status"] != "Tフォルダなし" else f"Tフォルダなし: {r['raw_name']}",
                _fmt_time(r["measured_at"]), _fmt_value(r["gap_nm"], "{:.3f}"),
                _fmt_value(r["machine"], "AN#{:.0f}"), _fmt_time(r["analyzed_at"], "%Y-%m-%d %H:%M:%S"),
                _fmt_value(r["extracted_events"], "{:.0f}") if r["extracted_events"] else "",
                _fmt_value(r["last_extracted_at"]), _fmt_value(r["experiment_id"]),
            ))
        gaps = sorted(g for g in df["gap_nm"].dropna().unique())
        self.gap_choice.configure(values=[ALL_GAPS] + [f"{g:.3f}" for g in gaps])
        self.gap_choice.set(ALL_GAPS)
        self._fill_summary()
        self._update_selection_label()
        self.scan_status.configure(text="確認済み（下の一覧で抽出するファイルを選んでください）",
                                   foreground="#2e7d32")

    def _fill_summary(self):
        rows = []
        for sample, g in self.scan_df.groupby("sample", sort=False):
            counts = g["status"].value_counts()
            if counts.get("Tフォルダなし"):
                rows.append({"サンプル": sample, "測定ファイル": 0, "ANAL済み": 0, "抽出済み": 0,
                             "未抽出": 0, "ANAL未処理": 0, "Gap(nm)": "", "測定日": "",
                             "備考": "測定データ（Tフォルダ）がありません"})
                continue
            measured = g["measured_at"].dropna()
            days = sorted({d.strftime("%Y-%m-%d") for d in measured})
            rows.append({
                "サンプル": sample,
                "測定ファイル": int(g["raw_name"].notna().sum()),
                "ANAL済み": int(g["anal_path"].notna().sum()),
                "抽出済み": int(counts.get("抽出済み", 0)),
                "未抽出": int(counts.get("未抽出", 0) + counts.get("未抽出（同名の別実験あり）", 0)),
                "ANAL未処理": int(counts.get("ANAL未処理", 0)),
                "Gap(nm)": ", ".join(f"{x:.3f}" for x in sorted(g["gap_nm"].dropna().unique())),
                "測定日": ", ".join(days),
                "備考": ("同名の別実験データあり" if counts.get("未抽出（同名の別実験あり）") else ""),
            })
        self.summary.show(pd.DataFrame(rows))

    def _mark(self, i):
        if self.scan_df.at[i, "status"] not in SELECTABLE:
            return NA
        return CHECKED if i in self.checked else UNCHECKED

    def _on_click(self, event):
        if self.files.identify_region(event.x, event.y) != "cell":
            return
        row = self.files.identify_row(event.y)
        # Shift/Ctrl を押しながらのクリックは複数行の選択に使う（切り替えはSpaceキー）
        if row and not (event.state & 0x0005):
            self._toggle([row])

    def _toggle(self, iids):
        indices = [int(i) for i in iids if self.scan_df.at[int(i), "status"] in SELECTABLE]
        if not indices:
            return
        # まとめて切り替えるときは「1つでも未チェックなら全部チェック」
        check = any(i not in self.checked for i in indices)
        for i in indices:
            (self.checked.add if check else self.checked.discard)(i)
            self.files.set(str(i), "check", self._mark(i))
        self._update_selection_label()

    def _check_where(self, predicate):
        if self.scan_df.empty:
            return
        self.checked = {i for i, r in self.scan_df.iterrows()
                        if r["status"] in SELECTABLE and predicate(r)}
        for i in self.scan_df.index:
            self.files.set(str(i), "check", self._mark(i))
        self._update_selection_label()

    def _check_gap(self):
        choice = self.gap_choice.get()
        if choice == ALL_GAPS:
            self._check_where(lambda r: True)
        else:
            gap = float(choice)
            self._check_where(lambda r: pd.notna(r["gap_nm"]) and abs(r["gap_nm"] - gap) < 1e-9)

    def _update_selection_label(self):
        df = self.scan_df
        if df.empty:
            self.selection_label.configure(text="")
            return
        chosen = df.loc[sorted(self.checked)]
        n_done = int((chosen["status"] == "抽出済み").sum())
        text = f"抽出するファイル: {len(chosen)} 件"
        if n_done:
            text += f"（うち抽出済み {n_done} 件）"
        self.selection_label.configure(text=text)

    # =====================
    # 抽出の実行
    # =====================
    def _run(self):
        try:
            server, share, ex, samples = self._validated_settings()
        except ValueError as e:
            messagebox.showerror("入力を確認してください", str(e))
            return
        module_name = METHODS[self.method.get()]["module"]

        if self.scan_key == self._current_key() and not self.scan_df.empty:
            chosen = self.scan_df.loc[sorted(self.checked)]
            if chosen.empty:
                messagebox.showerror("ファイルが選ばれていません", "抽出するファイルにチェックを付けてください。")
                return
            files_by_sample = {s: g["anal_path"].tolist() for s, g in chosen.groupby("sample")}
            samples = [s for s in samples if s in files_by_sample]
            n_done = int((chosen["status"] == "抽出済み").sum())
            message = f"{len(samples)} サンプル・{len(chosen)} ファイルから特徴量を抽出します。"
            if n_done:
                message += (f"\n\nうち {n_done} ファイルは抽出済みです"
                            "（同じイベントは重複として自動でスキップされます）。")
            if not messagebox.askyesno("抽出の確認", message + "\n\n実行しますか？"):
                return
        else:
            if not messagebox.askyesno(
                    "ファイル確認をしていません",
                    "「ファイルを確認」をしていない（または設定を変更した）ため、"
                    "選んだサンプルの全ANALファイルを抽出します。\n\n実行しますか？"):
                return
            files_by_sample = None

        def task(progress):
            module = importlib.import_module(module_name)
            return module.run_extraction(samples, server=server, keyfolder=share, ex=ex,
                                         progress_callback=progress, files_by_sample=files_by_sample)

        def done(output_dir):
            self.output_dir = output_dir
            self.progress.log(f"\n保存先: {output_dir}\n")
            self.refresh_history()
            if self.on_features_changed:
                self.on_features_changed()
            if files_by_sample is not None:
                # 抽出後の状態（抽出済み件数・最終抽出日時）に更新する
                self.after(300, lambda: self._scan(auto=True))

        self.sub.select(2)
        self.runner.run(task, self.progress, on_done=done)

    # =====================
    # 抽出履歴
    # =====================
    RUN_COLUMNS = (
        ("extracted_at", "抽出日時", 140), ("feature_set", "手法", 50), ("location", "サーバー/共有/実験", 230),
        ("sample", "サンプル", 90), ("n_files", "ファイル数", 70), ("n_files_ok", "読込OK", 60),
        ("events_read", "読込イベント", 90), ("added", "新規追加", 80),
        ("skipped_duplicates", "重複スキップ", 90), ("total_after", "保存後の合計", 90),
    )

    def _build_history_tab(self):
        frame = ttk.Frame(self.sub, padding=6)
        self.sub.add(frame, text="抽出履歴")

        tools = ttk.Frame(frame)
        tools.pack(fill="x")
        ttk.Label(tools, text="手法").pack(side="left")
        self.history_set = ttk.Combobox(tools, values=["すべて", "rmb", "rmc"], state="readonly", width=8)
        self.history_set.set("すべて")
        self.history_set.pack(side="left", padx=4)
        self.history_set.bind("<<ComboboxSelected>>", lambda _e: self._fill_history())
        ttk.Label(tools, text="絞り込み").pack(side="left", padx=(8, 0))
        self.history_filter = tk.StringVar()
        entry = ttk.Entry(tools, textvariable=self.history_filter, width=24)
        entry.pack(side="left", padx=4)
        entry.bind("<KeyRelease>", lambda _e: self._fill_history())
        ttk.Button(tools, text="再読み込み", command=self.refresh_history).pack(side="left", padx=4)
        ttk.Button(tools, text="履歴CSVのフォルダを開く",
                   command=lambda: open_path(self._feature_dir())).pack(side="left")
        ttk.Label(frame, text="抽出を実行するたびに data/features/<rmb|rmc>/extraction_runs.csv・"
                              "extraction_files.csv に記録されます（この機能を入れる前の抽出は記録されていません。"
                              "過去分の有無は「ファイル確認・選択」の抽出済み件数で確認できます）。",
                  foreground="#666", wraplength=1200).pack(anchor="w", pady=(4, 4))

        paned = ttk.PanedWindow(frame, orient="vertical")
        paned.pack(fill="both", expand=True)
        upper = ttk.Frame(paned)
        paned.add(upper, weight=1)
        self.runs_tree = ttk.Treeview(upper, columns=[c[0] for c in self.RUN_COLUMNS], show="headings",
                                      height=8)
        for key, text, width in self.RUN_COLUMNS:
            self.runs_tree.heading(key, text=text)
            self.runs_tree.column(key, width=width, anchor="w" if key in ("location", "sample") else "e",
                                  stretch=key == "location")
        vs = ttk.Scrollbar(upper, orient="vertical", command=self.runs_tree.yview)
        self.runs_tree.configure(yscrollcommand=vs.set)
        self.runs_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.runs_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_run_files())

        lower = ttk.Frame(paned)
        paned.add(lower, weight=1)
        ttk.Label(lower, text="選んだ実行で読み込んだファイル").pack(anchor="w", pady=(4, 0))
        self.run_files = DataTable(lower, height=8)
        self.run_files.pack(fill="both", expand=True)

        self.history_runs = pd.DataFrame()
        self.history_files = pd.DataFrame()

    def refresh_history(self):
        try:
            self.history_runs, self.history_files = load_history()
        except Exception as e:  # noqa: BLE001 - 壊れたCSVでもタブ全体は動かす
            self.history_runs, self.history_files = pd.DataFrame(), pd.DataFrame()
            print(f"[WARN] 抽出履歴を読めませんでした: {e}")
        self._fill_history()

    def _fill_history(self):
        self.runs_tree.delete(*self.runs_tree.get_children())
        self.run_files.show(None)
        runs = self.history_runs
        if runs.empty:
            return
        chosen_set = self.history_set.get()
        if chosen_set != "すべて":
            runs = runs[runs["feature_set"] == chosen_set]
        keyword = self.history_filter.get().strip().casefold()
        for i, r in runs.iterrows():
            location = f"//{r['server']}/{r['share']}/{r['ex']}"
            values = [r["extracted_at"], r["feature_set"], location, r["sample"]] + [
                _fmt_value(r[c], "{:.0f}") for c in ("n_files", "n_files_ok", "events_read", "added",
                                                   "skipped_duplicates", "total_after")]
            if keyword and keyword not in " ".join(map(str, values)).casefold():
                continue
            self.runs_tree.insert("", "end", iid=str(i), values=values)

    def _show_run_files(self):
        selection = self.runs_tree.selection()
        if not selection or self.history_files.empty:
            self.run_files.show(None)
            return
        run_id = self.history_runs.at[int(selection[0]), "run_id"]
        files = self.history_files[self.history_files["run_id"] == run_id]
        self.run_files.show(files[["file_key", "check", "events", "anal_path"]].rename(columns={
            "file_key": "ファイル", "check": "読込結果", "events": "イベント数", "anal_path": "ANALファイル"}))

    def refresh_samples(self):
        """他タブとの互換用（抽出タブはサーバーから直接選ぶので何もしない）。"""
