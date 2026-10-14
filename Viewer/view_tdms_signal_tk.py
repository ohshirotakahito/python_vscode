# -*- coding: utf-8 -*-
"""Tkinter front panel for the robust TDMS viewer.

Run: python Viewer/view_tdms_signal_tk.py
Waveforms and save previews are embedded in the application's tabs.
"""

import math
import os
import queue
import re
from pathlib import Path
import sys
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SIGNAL_COLORS = {
    "青": "#2563a6", "橙": "#c76b12", "緑": "#32804b", "赤": "#bf4040",
    "紫": "#7356a6", "茶": "#8a6240", "桃": "#ad5274", "黒": "#374151",
    "紺": "#253858", "青緑": "#007f86", "灰青": "#536878", "藍": "#4d58a6",
}

# Reuse the ML application's asynchronous server folder picker.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ML.app_tk.server_browse import (
    SERVERS, FolderPickerDialog, check_component, list_server_shares, list_subfolders,
)


def sample_t_path(server, share, experiment, sample):
    return Path(f"//{server}/{share}/{experiment}/{sample}/{sample}_10k_Sample/T")


def sample_inventory(selection, progress=None):
    inventory = []
    for sample in selection["samples"]:
        if progress:
            progress(f"フォルダ検索中: {sample}")
        base = sample_t_path(selection["server"], selection["share"], selection["experiment"], sample)
        try:
            with os.scandir(base) as entries:
                raw_files = sorted((Path(entry.path) for entry in entries
                                    if entry.is_file() and entry.name.lower().endswith(".tdms")))
        except FileNotFoundError:
            inventory.append(dict(sample=sample, base=base, raw=[], folders=[]))
            continue
        found = []
        def on_error(error):
            raise error
        for directory, _, files in os.walk(base, onerror=on_error):
            if "ANAL" in Path(directory).name and any(name.lower().endswith(".tdms") for name in files):
                found.append(directory)
        inventory.append(dict(sample=sample, base=base, raw=raw_files,
                              folders=sorted(found, key=str.casefold)))
    return inventory


def discover_anal_folders(selection):
    return list(dict.fromkeys(folder for item in sample_inventory(selection) for folder in item["folders"]))


def read_measurement_metadata(ar_table):
    """Read optional measurement identifiers without requiring every channel."""
    metadata = dict(date=None, machine=None, distance=None)
    def first_text(channel):
        value = ar_table[channel][:1][0]
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    try:
        ex_id = first_text("Ex ID").strip()
        date = re.match(r"^(\d{8}_\d{4})\b", ex_id)
        machine = re.search(r"\bAN#\d+", ex_id)
        metadata["date"] = date.group(1) if date else None
        metadata["machine"] = machine.group(0) if machine else None
    except (KeyError, IndexError, ValueError, UnicodeError):
        pass
    try:
        distance = float(first_text("Distance (nm)"))
        metadata["distance"] = distance if math.isfinite(distance) else None
    except (KeyError, IndexError, ValueError, UnicodeError):
        pass
    return metadata


def scan_file_counts(selection, progress=None):
    """Read saved counts and duration without loading the continuous waveform."""
    rows = []
    inventory = sample_inventory(selection, progress)
    for item in inventory:
        if not item["folders"]:
            if item["raw"]:
                rows.extend(dict(path=str(path), sample=item["sample"], name=path.name, count=None, time=None, freq=None,
                                 status="未解析（ANALなし）", analyzed=False) for path in item["raw"])
            else:
                rows.append(dict(path="", sample=item["sample"], name=None, count=None, time=None, freq=None,
                                 status="未測定（未アップロード）", analyzed=False))
    analyzed_folders = [(item["sample"], folder) for item in inventory for folder in item["folders"]]
    if analyzed_folders:
        from nptdms import TdmsFile
    checked = 0
    for sample, folder in analyzed_folders:
        directory = Path(folder)
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() != ".tdms":
                continue
            if progress:
                progress(f"シグナル数を確認中（{checked}ファイル確認済み）: {sample} / {path.name}")
            count, duration, frequency, source_name, status = None, None, None, None, ""
            metadata = dict(date=None, machine=None, distance=None)
            try:
                with TdmsFile.open(path) as tdms:
                    metadata = read_measurement_metadata(tdms["AR Table"])
                    values = tdms["AR Table"]["Signal#Sum"][:1]
                    count = int(values[0])
                    if count < 0:
                        raise ValueError("シグナル数が負です。")
                    # Each saved R Table row represents 1 ms, as in a_countpick.
                    duration = len(tdms["R Table"]["R # [n]"]) / 1000.0
                    frequency = count / duration if duration > 0 else None
                    try:
                        filename = tdms["AR Table"]["Filename"][:1][0]
                        if isinstance(filename, bytes):
                            filename = filename.decode("utf-8")
                        # Strip LabVIEW's analysis suffix, as in a_countpick.
                        source_name = str(filename).split(" D_", 1)[0]
                    except Exception as exc:
                        status = f"元ファイル名を取得できません: {exc}"
                if not status:
                    status = "確認済み"
                if duration == 0:
                    status = "全時間が0秒のため頻度を計算できません"
            except Exception as exc:
                status = f"解析情報を取得できません: {exc}"
            rows.append(dict(path=str(path), sample=sample, name=source_name, count=count, time=duration,
                             freq=frequency, status=status, analyzed=True, **metadata))
            checked += 1
    return sorted(rows, key=lambda row: (row["count"] is None, -(row["count"] or 0), row["path"]))


def candidate_file_rows(rows, folders):
    allowed = {os.path.normcase(os.path.normpath(folder)) for folder in folders}
    return sorted((row for row in rows if row["count"] is not None and row["count"] > 0
                   and os.path.normcase(os.path.normpath(str(Path(row["path"]).parent))) in allowed),
                  key=lambda row: (-row["count"], row["path"]))


FIELDS = (
    ("sampling_hz", "サンプリング周波数 (Hz)", "10000"),
    ("chunk_sec", "表示区間 (秒)", "1.0"),
    ("target_group", "TDMS グループ", "Data"),
    ("target_channel", "TDMS チャンネル", "Ch1"),
    ("baseline_window", "ベースライン窓 (点)", "2001"),
    ("baseline_percentile", "ベースライン百分位 (%)", "10.0"),
    ("sg_window", "平滑化窓 (点)", "51"),
    ("sg_poly", "平滑化の多項式次数", "3"),
    ("threshold_k1", "検出開始閾値 k1", "1.0"),
    ("threshold_k2", "ピーク閾値 k2", "5.0"),
    ("pad_for_std", "ノイズ推定の余白 (点)", "500"),
    ("min_width_ms", "最小信号幅 (ms)", "0.2"),
)


def parse_settings(values):
    settings = {}
    integers = {"baseline_window", "sg_window", "sg_poly", "pad_for_std"}
    for key, label, _ in FIELDS:
        raw = values[key].strip()
        if key in {"target_group", "target_channel"}:
            if not raw:
                raise ValueError(f"{label}を入力してください。")
            settings[key] = raw
            continue
        try:
            number = int(raw) if key in integers else float(raw)
        except ValueError:
            raise ValueError(f"{label}に有効な数値を入力してください。") from None
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{label}は有限の0以上の数値にしてください。")
        settings[key] = number
    for key in ("sampling_hz", "chunk_sec", "threshold_k1", "threshold_k2"):
        if settings[key] <= 0:
            raise ValueError(f"{key}は0より大きい値にしてください。")
    if round(settings["sampling_hz"] * settings["chunk_sec"]) < 1:
        raise ValueError("表示区間には少なくとも1点のデータが必要です。")
    if not 0 <= settings["baseline_percentile"] <= 100:
        raise ValueError("ベースライン百分位は0〜100にしてください。")
    for key in ("baseline_window", "sg_window"):
        if settings[key] % 2 == 0:
            settings[key] += 1
        if settings[key] < 3:
            raise ValueError(f"{key}は3点以上にしてください。")
    if settings["sg_poly"] >= settings["sg_window"]:
        raise ValueError("多項式次数は平滑化窓より小さくしてください。")
    if settings["threshold_k1"] > settings["threshold_k2"]:
        raise ValueError("閾値は k1 ≤ k2 にしてください。")
    settings["dt"] = 1.0 / settings.pop("sampling_hz")
    return settings


class FrontPanel(ttk.Frame):
    def __init__(self, root, workspace, waveform_frame, preview_frame):
        super().__init__(root, padding=16)
        self.pack(fill="both", expand=True)
        self.columnconfigure(1, weight=1)
        self.workspace = workspace
        self.waveform_frame = waveform_frame
        self.preview_frame = preview_frame
        self.browser = None
        self.results = queue.Queue()
        self.file_results = queue.Queue()
        self.file_rows = {}
        self.catalog_key = None
        self.scanning = False
        self.scan_progress = queue.Queue()
        self.scan_started = None
        self.scan_message = ""
        self.loading = False
        self.server = tk.StringVar(value=SERVERS[0])
        self.share = tk.StringVar(value="analysis")
        self.experiment = tk.StringVar()
        self.samples = tk.StringVar()
        self.source_preview = tk.StringVar()
        self.output = tk.StringVar(value=str(Path(__file__).resolve().parent / "clips_pick"))
        self.status = tk.StringVar(value="サーバー・共有フォルダ・実験フォルダ・サンプル名を選択してください。")
        self.values = {key: tk.StringVar(value=default) for key, _, default in FIELDS}
        self.noise_method = tk.StringVar(value="現方式（MAD）")
        ttk.Label(self, text="サーバー").grid(row=0, column=0, sticky="w")
        server_box = ttk.Combobox(self, textvariable=self.server, values=SERVERS)
        server_box.grid(row=0, column=1, sticky="ew", padx=8)
        for row, (label, variable, key) in enumerate((
            ("共有フォルダ", self.share, "share"),
            ("実験フォルダ名", self.experiment, "experiment"),
            ("サンプル名（カンマ区切り）", self.samples, "samples"),
        ), start=1):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(self, textvariable=variable, width=48).grid(row=row, column=1, sticky="ew", padx=8)
            ttk.Button(self, text="一覧から選択 / 更新", command=lambda key=key: self.pick(key)).grid(row=row, column=2)
        ttk.Label(self, textvariable=self.source_preview, wraplength=650).grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Label(self, text="保存先").grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(self, textvariable=self.output).grid(row=5, column=1, sticky="ew", padx=8)
        ttk.Button(self, text="参照…", command=self.choose_output).grid(row=5, column=2)
        self.server.trace_add("write", lambda *_: self.share.set(""))
        self.share.trace_add("write", lambda *_: self.experiment.set(""))
        self.experiment.trace_add("write", lambda *_: self.samples.set(""))
        for variable in (self.server, self.share, self.experiment, self.samples):
            variable.trace_add("write", self.update_source_preview)
        self.update_source_preview()
        tabs = ttk.Notebook(self)
        tabs.grid(row=6, column=0, columnspan=3, sticky="ew", pady=12)
        basic = ttk.Frame(tabs, padding=12)
        detection = ttk.Frame(tabs, padding=12)
        tabs.add(basic, text="波形表示")
        tabs.add(detection, text="信号検出")
        ttk.Label(detection, text="ノイズσ推定方式").grid(row=8, column=0, sticky="w", pady=4)
        ttk.Combobox(detection, textvariable=self.noise_method, state="readonly",
                     values=("現方式（MAD）", "新方式（低振幅側の背景成分）"), width=36
                     ).grid(row=8, column=1, sticky="ew", padx=12)
        ttk.Label(detection, text="新方式は上向き信号を想定。背景成分を分離できない場合は現方式に戻ります。",
                  wraplength=750).grid(row=9, column=0, columnspan=2, sticky="w", pady=4)
        for index, (key, label, _) in enumerate(FIELDS):
            frame = basic if index < 4 else detection
            row = index if index < 4 else index - 4
            frame.columnconfigure(1, weight=1)
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(frame, textvariable=self.values[key], width=24).grid(row=row, column=1, sticky="ew", padx=12)
        actions = ttk.Frame(self)
        actions.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.scan_button = ttk.Button(actions, text="ファイル一覧を確認 / 更新", command=self.scan_files)
        self.scan_button.pack(side="left", padx=(0, 8))
        self.open_button = ttk.Button(actions, text="波形を読み込む / 設定を適用", command=self.open_viewer)
        self.open_button.pack(side="left")
        ttk.Button(actions, text="設定を初期値に戻す", command=self.reset).pack(side="left", padx=10)
        ttk.Label(self, text="波形の移動・範囲選択は「波形」タブ、保存確認は「保存プレビュー」タブで操作します。",
                  wraplength=560).grid(row=8, column=0, columnspan=3, sticky="w", pady=(14, 6))
        status_frame = ttk.Frame(self, padding=(8, 6))
        status_frame.grid(row=9, column=0, columnspan=3, sticky="ew")
        status_frame.columnconfigure(0, weight=1)
        ttk.Label(status_frame, textvariable=self.status, wraplength=1200,
                  style="Status.TLabel", padding=6).grid(row=0, column=0, sticky="ew")
        self.scan_bar = ttk.Progressbar(status_frame, mode="indeterminate")
        self.scan_bar.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.scan_bar.grid_remove()
        table_frame = ttk.LabelFrame(self, text="ANALファイル一覧 — シグナル数の多い順", padding=8)
        table_frame.grid(row=10, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        self.rowconfigure(10, weight=1)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(1, weight=1)
        ttk.Label(table_frame, text="行を選択して「波形を読み込む」を押すか、ダブルクリックで表示します。数・全時間・頻度はLabVIEWの保存済み解析結果から取得します。"
                  ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.file_table = ttk.Treeview(table_frame, columns=("sample", "file", "name", "date", "machine", "distance", "count", "time", "freq", "status"),
                                      show="headings", selectmode="browse", height=8)
        for key, title, width in (("sample", "サンプル", 120), ("file", "ファイル名", 580),
                                  ("name", "元ファイル名", 300),
                                  ("date", "Date", 140), ("machine", "Machine", 90),
                                  ("distance", "Distance (nm)", 110),
                                  ("count", "シグナル数", 100), ("time", "全時間 (s)", 100),
                                  ("freq", "頻度 (/s)", 100), ("status", "状態", 240)):
            self.file_table.heading(key, text=title)
            self.file_table.column(key, width=width, anchor="e" if key in {"count", "time", "freq", "distance"} else "w")
        self.file_table.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.file_table.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.file_table.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.file_table.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.file_table.configure(xscrollcommand=horizontal.set)
        self.file_table.bind("<Double-1>", lambda _: self.open_viewer())
        root.after(500, self.poll)

    def selection_key(self):
        return (self.server.get().strip(), self.share.get().strip(), self.experiment.get().strip(), tuple(self.sample_names()))

    def validated_selection(self):
        selection = {key: check_component(variable.get(), label) for key, variable, label in (
            ("server", self.server, "サーバー"), ("share", self.share, "共有フォルダ"),
            ("experiment", self.experiment, "実験フォルダ名"),
        )}
        selection["samples"] = [check_component(name, "サンプル名") for name in self.sample_names()]
        if not selection["samples"]:
            raise ValueError("サンプル名を1つ以上選択してください。")
        return selection

    def scan_files(self):
        if self.scanning:
            return
        try:
            selection = self.validated_selection()
        except ValueError as exc:
            messagebox.showerror("入力を確認してください", str(exc), parent=self)
            return
        key = self.selection_key()
        self.scanning = True
        self.scan_started = time.monotonic()
        self.scan_message = "サーバー上のフォルダを検索しています…"
        self.scan_button.state(["disabled"])
        self.open_button.state(["disabled"])
        self.scan_bar.grid()
        self.scan_bar.start(15)
        self.configure(cursor="watch")
        self.status.set("● 読み込み中 — " + self.scan_message)
        self.update_idletasks()
        def scan():
            try:
                self.file_results.put((key, scan_file_counts(selection, self.scan_progress.put), None))
            except Exception as exc:
                self.file_results.put((key, [], exc))
        threading.Thread(target=scan, daemon=True).start()

    def sample_names(self):
        return list(dict.fromkeys(name.strip() for name in self.samples.get().split(",") if name.strip()))

    def update_source_preview(self, *_):
        if hasattr(self, "file_table"):
            self.file_table.delete(*self.file_table.get_children())
            self.file_rows.clear()
            self.catalog_key = None
        sample = next(iter(self.sample_names()), "<サンプル>")
        path = sample_t_path(self.server.get(), self.share.get() or "<共有フォルダ>",
                             self.experiment.get() or "<実験フォルダ>", sample)
        self.source_preview.set(f"読み込み元: {path}/…ANAL…（複数選択時は各サンプルを検索）")

    def pick(self, key):
        try:
            server = check_component(self.server.get(), "サーバー")
            if key == "share":
                FolderPickerDialog(self, "共有フォルダを選択", f"//{server}",
                                   loader=lambda: list_server_shares(server), on_select=self.share.set,
                                   preselected=[self.share.get()])
                return
            share = check_component(self.share.get(), "共有フォルダ")
            parent = f"//{server}/{share}"
            if key == "experiment":
                FolderPickerDialog(self, "実験フォルダを選択", parent,
                                   loader=lambda: list_subfolders(parent), on_select=self.experiment.set,
                                   browse_dir=parent, preselected=[self.experiment.get()])
            else:
                experiment = check_component(self.experiment.get(), "実験フォルダ名")
                parent += f"/{experiment}"
                FolderPickerDialog(self, "サンプルを選択", parent,
                                   loader=lambda: list_subfolders(parent), multiple=True,
                                   on_select=lambda names: self.samples.set(", ".join(names)),
                                   browse_dir=parent, preselected=self.sample_names())
        except ValueError as exc:
            messagebox.showerror("入力を確認してください", str(exc), parent=self)

    def choose_output(self):
        selected = filedialog.askdirectory(parent=self, title="保存先を選択")
        if selected:
            self.output.set(selected)

    def reset(self):
        self.noise_method.set("現方式（MAD）")
        for key, _, default in FIELDS:
            self.values[key].set(default)

    def open_viewer(self):
        if self.loading or self.scanning:
            return
        try:
            settings = parse_settings({key: value.get() for key, value in self.values.items()})
            settings["noise_method"] = "lower_mode" if self.noise_method.get().startswith("新方式") else "mad"
            for key in ("baseline_window", "sg_window"):
                self.values[key].set(str(settings[key]))
            selection = self.validated_selection()
            if not self.output.get().strip():
                raise ValueError("保存先を指定してください。")
            settings.update(out_root=str(Path(self.output.get()).resolve()))
            selected = self.file_table.selection()
            if selected and self.catalog_key == self.selection_key():
                chosen = self.file_rows[selected[0]]
                if not chosen.get("analyzed", True):
                    self.status.set(f"{chosen['sample']}: {chosen['status']}。ANAL済みのファイルを選択してください。")
                    return
                settings["initial_path"] = chosen["path"]
                self.embed_viewer(settings, list(dict.fromkeys(str(Path(row["path"]).parent)
                                  for row in self.file_rows.values() if row.get("analyzed", True))))
                self.status.set("選択したファイルを表示しています。")
                return
            self.loading = True
            self.open_button.state(["disabled"])
            self.status.set("サーバー上のANALフォルダを検索しています…")
            def search():
                try:
                    self.results.put((settings, discover_anal_folders(selection), None))
                except Exception as exc:
                    self.results.put((settings, None, exc))
            threading.Thread(target=search, daemon=True).start()
        except (ValueError, OSError) as exc:
            messagebox.showerror("起動できません", str(exc), parent=self)

    def poll(self):
        while True:
            try:
                self.scan_message = self.scan_progress.get_nowait()
            except queue.Empty:
                break
        if self.scanning:
            elapsed = int(time.monotonic() - self.scan_started)
            self.status.set(f"● 読み込み中（{elapsed}秒経過）— {self.scan_message}")
        try:
            key, rows, error = self.file_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.scanning = False
            self.scan_bar.stop()
            self.scan_bar.grid_remove()
            self.configure(cursor="")
            self.scan_button.state(["!disabled"])
            if not self.loading:
                self.open_button.state(["!disabled"])
            if key == self.selection_key():
                if error is not None:
                    self.status.set(f"一覧取得失敗: {error}")
                    messagebox.showerror("一覧を取得できません", str(error), parent=self)
                else:
                    self.file_table.delete(*self.file_table.get_children())
                    self.file_rows.clear()
                    self.catalog_key = key
                    for index, row in enumerate(rows):
                        iid = str(index)
                        self.file_rows[iid] = row
                        self.file_table.insert("", "end", iid=iid, values=(row["sample"], Path(row["path"]).name if row["path"] else "—",
                                                row["name"] or "—",
                                                row.get("date") or "—", row.get("machine") or "—",
                                                f"{row['distance']:g}" if row.get("distance") is not None else "—",
                                                row["count"] if row["count"] is not None else ("不明" if row.get("analyzed", True) else "—"),
                                                f"{row['time']:.3f}" if row["time"] is not None else "—",
                                                f"{row['freq']:.2f}" if row["freq"] is not None else "—", row["status"]),
                                                tags=("analyzed" if row.get("analyzed", True) else "pending",))
                    if rows:
                        self.file_table.selection_set("0")
                    analyzed = sum(row.get("analyzed", True) for row in rows)
                    elapsed = time.monotonic() - self.scan_started
                    self.status.set(f"✓ 読み込み完了（{elapsed:.1f}秒）— 解析済み {analyzed}ファイル / "
                                    f"未解析・未測定 {len(rows)-analyzed}行。シグナル数はLabVIEW解析値です。")
            else:
                self.status.set("検索条件が変更されたため、結果を反映しませんでした。一覧を更新してください。")
        try:
            settings, folders, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            try:
                if error is not None:
                    raise error
                if not folders:
                    self.status.set("ANAL済みのファイルがありません。測定・解析状態を一覧で確認してください。")
                    self.scan_files()
                else:
                    self.status.set("波形を読み込み・解析しています…")
                    self.update_idletasks()
                    self.embed_viewer(settings, folders)
                    self.status.set("読み込み完了。波形タブで操作できます。")
            except Exception as exc:
                self.status.set(f"読み込み失敗: {exc}")
                messagebox.showerror("読み込みできません", str(exc), parent=self)
            finally:
                self.loading = False
                self.open_button.state(["!disabled"])
        self.after(500, self.poll)

    def clear_viewer(self):
        import matplotlib.pyplot as plt
        if self.browser is not None:
            if self.browser._preview_fig is not None:
                plt.close(self.browser._preview_fig)
            plt.close(self.browser.fig)
            self.browser = None
        for frame in (self.waveform_frame, self.preview_frame):
            for widget in frame.winfo_children():
                widget.destroy()

    def embed_viewer(self, settings, folders):
        settings = dict(settings)
        initial_path = settings.pop("initial_path", None)
        import matplotlib
        # No pyplot window managers: Tk canvases are attached before widgets
        # connect their mouse/keyboard callbacks.
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        from tdms_signal_core import RobustTdmsMultiFolderBrowser

        self.clear_viewer()
        panel = self
        candidates = candidate_file_rows(self.file_rows.values(), folders) if self.catalog_key == self.selection_key() else []
        candidate_paths = [row["path"] for row in candidates]
        created_figures = []
        header = ttk.Frame(self.waveform_frame, padding=(12, 10))
        header.pack(fill="x")
        info = tk.StringVar(value="読み込み中…")
        ttk.Label(header, textvariable=info, style="Heading.TLabel").pack(anchor="w")
        file_row = ttk.Frame(header)
        file_row.pack(fill="x", pady=(8, 0))
        ttk.Label(file_row, text="表示ファイル").pack(side="left", padx=(0, 8))
        file_choice = ttk.Combobox(file_row, state="readonly")
        file_choice.pack(side="left", fill="x", expand=True)
        body = ttk.Frame(self.waveform_frame, padding=(12, 0, 12, 8))
        body.pack(fill="both", expand=True)
        sidebar_host = ttk.Frame(body)
        sidebar_host.pack(side="left", fill="y", padx=(0, 12))
        side_canvas = tk.Canvas(sidebar_host, width=255, highlightthickness=0)
        side_scroll = ttk.Scrollbar(sidebar_host, orient="vertical", command=side_canvas.yview)
        side_canvas.configure(yscrollcommand=side_scroll.set)
        side_scroll.pack(side="right", fill="y")
        side_canvas.pack(side="left", fill="y", expand=True)
        sidebar = ttk.Frame(side_canvas)
        side_window = side_canvas.create_window((0, 0), window=sidebar, anchor="nw")
        sidebar.bind("<Configure>", lambda _: side_canvas.configure(scrollregion=side_canvas.bbox("all")))
        side_canvas.bind("<Configure>", lambda event: side_canvas.itemconfigure(side_window, width=event.width))
        plot_frame = ttk.Frame(body)
        plot_frame.pack(side="left", fill="both", expand=True)
        summary = tk.StringVar(value="波形上をドラッグして保存範囲を選択できます。")
        ttk.Label(self.waveform_frame, textvariable=summary, padding=(12, 8),
                  style="Status.TLabel").pack(fill="x")

        def attach(figure, frame):
            created_figures.append(figure)
            canvas = FigureCanvasTkAgg(figure, master=frame)
            toolbar = NavigationToolbar2Tk(canvas, frame, pack_toolbar=False)
            toolbar.pack(side="bottom", fill="x")
            canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
            return canvas

        def figure_factory():
            figure, axes = plt.subplots(figsize=(14, 7))
            attach(figure, plot_frame)
            return figure, axes

        class EmbeddedBrowser(RobustTdmsMultiFolderBrowser):
            def __init__(self, *args, **kwargs):
                self.tight_time_edges = True
                super().__init__(*args, **kwargs)

            def apply_plot_style(self):
                from matplotlib.ticker import FuncFormatter, ScalarFormatter
                for line in self.ax.lines:
                    if line.get_label() == "Signal":
                        line.set_color(getattr(self, "signal_color", "#2563a6"))
                if getattr(self, "relative_time", False):
                    origin = (self._view_start_override if self._view_start_override is not None
                              else self.chunk_idx*self.chunk_size)*self.dt
                    self.ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{(value-origin)*1000:g}"))
                    self.ax.set_xlabel("Time from view start (ms)")
                else:
                    self.ax.xaxis.set_major_formatter(ScalarFormatter(useOffset=False))
                    self.ax.set_xlabel("Measurement time (s)")

            def navigate_candidate_file(self, direction):
                if not candidates:
                    messagebox.showinfo("候補ファイル", "候補ファイルがありません。読み込みタブで一覧を更新してください。\n"
                                        "シグナル数が1以上のファイルを、多い順に移動します。", parent=panel)
                    return
                current = self.current_tdms_path()
                if current in candidate_paths:
                    index = (candidate_paths.index(current) + direction) % len(candidates)
                else:
                    index = 0 if direction > 0 else len(candidates) - 1
                target = candidates[index]
                if target["path"] == current:
                    messagebox.showinfo("候補ファイル", "ほかの候補ファイルはありません。", parent=panel)
                    return
                folder = str(Path(target["path"]).parent)
                try:
                    if folder != self.current_anal_folder():
                        self.load_folder(self.anal_folders.index(folder))
                    self.load_file(self.tdms_files.index(target["path"]))
                    self.draw()
                    if self.n == 0:
                        raise ValueError("候補ファイルの波形を読み込めません。")
                    panel.status.set(f"候補ファイル {index + 1}/{len(candidates)} · シグナル数 {target['count']:,}")
                except (ValueError, OSError) as exc:
                    messagebox.showerror("候補ファイル", str(exc), parent=panel)

            def on_clear(self, event):
                super().on_clear(event)
                summary.set("波形上をドラッグして保存範囲を選択できます。")

            def draw(self):
                super().draw()
                self.apply_plot_style()
                if getattr(self, "_tk_controls_ready", False):
                    self.ax.set_title("")
                    info.set(f"ファイル {self.file_idx + 1} / {len(self.tdms_files)}   ·   "
                             f"表示区間 {self.chunk_idx + 1} / {self.num_chunks}")
                    info.set(info.get() + "   ·   " + self.noise_estimation_status)
                    current = self.current_tdms_path()
                    if current in candidate_paths:
                        rank = candidate_paths.index(current)
                        info.set(info.get() + f"   ·   候補ファイル {rank + 1}/{len(candidates)}"
                                 f"（シグナル数 {candidates[rank]['count']:,}）")
                    file_choice.configure(values=[Path(p).name for p in self.tdms_files])
                    file_choice.current(self.file_idx)
                    window_var.set(f"{self.chunk_sec:g}")
                    if self.sel_tmin is not None and self.sel_tmax is not None:
                        summary.set(f"選択範囲: {self.sel_tmin:.6g} ～ {self.sel_tmax:.6g} 秒   "
                                    f"（{(self.sel_tmax - self.sel_tmin)*1000:.6g} ms）")
                    else:
                        summary.set("波形上をドラッグして保存範囲を選択できます。全体波形をクリックすると移動します。")

            def on_select(self, xmin, xmax):
                super().on_select(xmin, xmax)
                if getattr(self, "_tk_controls_ready", False):
                    start_var.set(f"{self.sel_tmin:.9g}")
                    duration_var.set(f"{self.sel_tmax - self.sel_tmin:.9g}")
                    summary.set(f"選択範囲: {self.sel_tmin:.6g} ～ {self.sel_tmax:.6g} 秒 / "
                                f"長さ {(self.sel_tmax-self.sel_tmin)*1000:.6g} ms")

            def _open_save_preview(self, i0, i1):
                super()._open_save_preview(i0, i1)
                from matplotlib.widgets import Widget
                for widget in self.__dict__.values():
                    if isinstance(widget, Widget) and getattr(widget, "ax", None) in self._preview_fig.axes and widget.ax is not self._preview_ax:
                        widget.disconnect_events()
                        # CheckButtons.set_active(index) toggles a checkbox;
                        # use the base Widget method to disable interaction.
                        Widget.set_active(widget, False)
                for axes in self._preview_fig.axes:
                    if axes is not self._preview_ax:
                        axes.set_visible(False)
                self._preview_ax.set_position([0.08, 0.12, 0.88, 0.78])
                self._preview_fig.canvas.draw_idle()

            def _create_preview_figure(self):
                for widget in panel.preview_frame.winfo_children():
                    widget.destroy()
                figure = plt.figure(figsize=(12, 6.6))
                controls = ttk.Frame(panel.preview_frame, padding=12)
                controls.pack(fill="x")
                ttk.Label(controls, text="保存内容の確認", style="Heading.TLabel").pack(anchor="w")
                ttk.Label(controls, text=f"保存先: {self.out_dir()}\n内容: 時刻・波形のCSV / 波形のNPY / 波形図のPNG",
                          wraplength=1000).pack(anchor="w", pady=8)
                region_info = tk.StringVar()
                self._preview_region_info = region_info
                region_row = ttk.Frame(controls)
                region_row.pack(fill="x", pady=4)
                region_start = tk.StringVar(value=f"{self._preview_i0*self.dt*1000:.9g}")
                region_length = tk.StringVar(value=f"{(self._preview_i1-self._preview_i0)*self.dt*1000:.9g}")
                step = tk.StringVar(value="1")
                def refresh_region():
                    region_start.set(f"{self._preview_i0*self.dt*1000:.9g}")
                    region_length.set(f"{(self._preview_i1-self._preview_i0)*self.dt*1000:.9g}")
                    region_info.set(f"領域の長さ: {region_length.get()} ms   /   全体時刻: "
                                    f"{self._preview_i0*self.dt:.9g} ～ {self._preview_i1*self.dt:.9g} 秒")
                    self._preview_render()
                    figure.canvas.draw_idle()
                def apply_region(round_out=False):
                    try:
                        start, length = float(region_start.get()), float(region_length.get())
                        if not all(math.isfinite(v) for v in (start,length)) or start < 0 or length <= 0:
                            raise ValueError
                        end = start + length
                        if round_out:
                            unit = float(step.get())
                            if not math.isfinite(unit) or unit <= 0:
                                raise ValueError
                            start = math.floor(start/unit)*unit
                            end = math.ceil(end/unit)*unit
                        i0 = max(0, int(math.floor(start/(self.dt*1000)+1e-9)))
                        i1 = min(self.n, int(math.ceil(end/(self.dt*1000)-1e-9)))
                        if i1-i0 < 2:
                            raise ValueError
                    except ValueError:
                        messagebox.showerror("保存範囲", "測定内の開始位置と正の長さ・時間刻みを指定してください。", parent=panel)
                        return
                    self._preview_i0, self._preview_i1 = i0, i1
                    self.on_select(i0*self.dt, i1*self.dt)
                    self._preview_sel = (self.sel_tmin, self.sel_tmax, self.chunk_idx)
                    refresh_region()
                for label, variable in (("全体開始 (ms)", region_start), ("長さ (ms)", region_length), ("丸め刻み (ms)", step)):
                    ttk.Label(region_row, text=label).pack(side="left", padx=(0,4))
                    ttk.Entry(region_row, textvariable=variable, width=13).pack(side="left", padx=(0,10))
                ttk.Button(region_row, text="範囲を再表示", command=apply_region).pack(side="left")
                ttk.Button(region_row, text="刻みに合わせて広げる", command=lambda: apply_region(True)).pack(side="left", padx=6)
                region_info.set(f"領域の長さ: {region_length.get()} ms / 開始: {region_start.get()} ms（全体時刻）")
                ttk.Label(controls, textvariable=region_info, style="Heading.TLabel").pack(anchor="w", pady=4)
                self._preview_control_vars = [region_start, region_length, step, region_info]
                self.make_style_controls(controls, lambda: (self._preview_render(), figure.canvas.draw_idle(), self.draw()))
                row = ttk.Frame(controls)
                row.pack(fill="x")
                peaks = tk.BooleanVar(value=self._preview_show_pv)
                self._preview_control_vars.append(peaks)
                def toggle_peaks():
                    self._preview_show_pv = peaks.get()
                    self._preview_render()
                    figure.canvas.draw_idle()
                ttk.Checkbutton(row, text="ピーク値を図に表示", variable=peaks,
                                command=toggle_peaks).pack(side="left")
                ttk.Button(row, text="CSV・NPY・PNGを保存", style="Primary.TButton",
                           command=lambda: self._on_preview_confirm(None)).pack(side="right")
                ttk.Button(row, text="波形に戻る", command=lambda: self._on_preview_cancel(None)).pack(side="right", padx=8)
                attach(figure, panel.preview_frame)
                panel.workspace.select(panel.preview_frame)
                return figure

            def _on_preview_cancel(self, event):
                super()._on_preview_cancel(event)
                self.close_preview()

            def _on_preview_confirm(self, event):
                try:
                    self.on_select(self._preview_i0*self.dt, self._preview_i1*self.dt)
                    self._save_indices_override = (self._preview_i0, self._preview_i1)
                    super()._on_preview_confirm(event)
                except Exception as exc:
                    messagebox.showerror("保存できません", str(exc), parent=panel)
                    return
                finally:
                    self._save_indices_override = None
                panel.status.set("選択範囲をCSV・NPY・PNGで保存しました。")
                panel.saved_panel.use_output(panel.output)
                self.close_preview()

            def close_preview(self):
                for widget in panel.preview_frame.winfo_children():
                    widget.destroy()
                panel.workspace.select(panel.waveform_frame)

            def on_stop(self, event):
                panel.clear_viewer()
                panel.workspace.select(panel)
                panel.status.set("波形表示を終了しました。")

            def make_style_controls(self, frame, redraw):
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=6)
                if not hasattr(self, "_tk_time_mode"):
                    self._tk_time_mode = tk.StringVar(value="全体時刻 (s)")
                    self._tk_edge_mode = tk.StringVar(value="枠に合わせる")
                    self._tk_color_name = tk.StringVar(value="青")
                time_mode = self._tk_time_mode
                ttk.Label(row, text="時刻表示").pack(side="left", padx=(0,4))
                mode_box = ttk.Combobox(row, textvariable=time_mode, values=("全体時刻 (s)", "領域内 (ms)"), state="readonly", width=14)
                mode_box.pack(side="left", padx=(0,10))
                ttk.Label(row, text="左右の余白").pack(side="left", padx=(0,4))
                edges = ttk.Combobox(row, textvariable=self._tk_edge_mode,
                                     values=("余白あり", "枠に合わせる"), state="readonly", width=12)
                edges.pack(side="left", padx=(0,10))
                def update(_):
                    self.relative_time = time_mode.get() == "領域内 (ms)"
                    self.tight_time_edges = self._tk_edge_mode.get() == "枠に合わせる"
                    redraw()
                mode_box.bind("<<ComboboxSelected>>", update)
                edges.bind("<<ComboboxSelected>>", update)
                colors = ttk.Frame(frame)
                colors.pack(fill="x", pady=(0,6))
                ttk.Label(colors, text="波形色").pack(side="left", padx=(0,8))
                def choose_color(name):
                    self.signal_color = SIGNAL_COLORS[name]
                    self._tk_color_name.set(name)
                    redraw()
                for name, value in SIGNAL_COLORS.items():
                    tk.Button(colors, text=name, bg=value, fg="white", activebackground=value,
                              activeforeground="white", width=3, relief="flat", cursor="hand2",
                              padx=3, pady=5, command=lambda name=name: choose_color(name)).pack(side="left", padx=2)
                ttk.Button(colors, text="標準色に戻す", command=lambda: choose_color("青")).pack(side="left", padx=8)
                ttk.Label(colors, textvariable=self._tk_color_name).pack(side="left")

        try:
            self.browser = EmbeddedBrowser(
                **settings, anal_folders=folders, anal_folder_samples=None,
                recursive_tdms=False, sanitize_filename=True, save_subfolder_per_aa=True,
                figure_factory=figure_factory,
            )
            browser = self.browser
            if initial_path is not None:
                browser.load_folder(folders.index(str(Path(initial_path).parent)))
                browser.load_file(browser.tdms_files.index(initial_path))
                if browser.n == 0:
                    raise ValueError("選択したファイルの波形を読み込めません。")
            elif browser.n == 0:
                raise ValueError("TDMS波形を読み込めません。グループとチャンネルを確認してください。")
            # Keep the legacy widgets as callback adapters, but place all
            # visible controls in Tk so the figure can use the available space.
            from matplotlib.widgets import Widget
            for widget in browser.__dict__.values():
                if isinstance(widget, Widget) and getattr(widget, "ax", None) in browser.fig.axes and widget.ax not in (browser.ax, browser.ov_ax):
                    widget.disconnect_events()
                    Widget.set_active(widget, False)
            for axes in browser.fig.axes:
                if axes not in (browser.ax, browser.ov_ax):
                    axes.set_visible(False)
            for text in browser.fig.texts:
                text.set_visible(False)
            browser.fig.subplots_adjust(left=0.075, right=0.98, top=0.95, bottom=0.28)
            browser.ov_ax.set_position([0.075, 0.075, 0.905, 0.085])
            browser.ov_ax.set_title("Whole file · click to move · green: candidates", fontsize=9, pad=8)
            browser.fig.set_facecolor("#ffffff")

            def group(title):
                frame = ttk.LabelFrame(sidebar, text=title, padding=10)
                frame.pack(fill="x", pady=(0, 10))
                return frame

            def button_row(frame, buttons):
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=3)
                for label, callback in buttons:
                    ttk.Button(row, text=label, command=lambda cb=callback: cb(None)).pack(side="left", expand=True, fill="x", padx=2)

            navigation = group("波形の移動")
            button_row(navigation, [("前のファイル", browser.on_prev_file), ("次のファイル", browser.on_next_file)])
            button_row(navigation, [("前の候補ファイル", lambda _: browser.navigate_candidate_file(-1)),
                                    ("次の候補ファイル", lambda _: browser.navigate_candidate_file(1))])
            ttk.Label(navigation, text=f"候補: {len(candidates)}ファイル / シグナル数の多い順",
                      wraplength=220).pack(anchor="w", pady=(2, 6))
            button_row(navigation, [("前の区間", browser.on_prev_chunk), ("次の区間", browser.on_next_chunk)])
            button_row(navigation, [("前の候補区間", browser.on_prev_candidate), ("次の候補区間", browser.on_next_candidate)])
            window_var = tk.StringVar(value=str(browser.chunk_sec))

            def entry_row(frame, label, variable):
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=3)
                ttk.Label(row, text=label, width=12).pack(side="left")
                ttk.Entry(row, textvariable=variable, width=12).pack(side="left", fill="x", expand=True)

            entry_row(navigation, "表示幅 (秒)", window_var)
            def apply_window():
                try:
                    value = float(window_var.get())
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("表示幅", "0より大きい数値を入力してください。", parent=panel)
                    return
                browser.on_window_size_submit(str(value))
            ttk.Button(navigation, text="表示幅を適用", command=apply_window).pack(fill="x", pady=4)
            visibility = group("表示する情報")
            browser._tk_visibility_vars = []
            for label, key in [("波形", "Signal"), ("ベースライン", "Baseline"),
                               ("検出閾値", "Threshold"), ("検出信号", "Pulse"), ("ピーク値", "Peak value")]:
                variable = tk.BooleanVar(value=True)
                browser._tk_visibility_vars.append(variable)
                ttk.Checkbutton(visibility, text=label, variable=variable,
                                command=lambda key=key: browser.on_check(key)).pack(anchor="w", pady=2)
            browser.make_style_controls(header, browser.draw)
            selection_group = group("保存する範囲")
            start_var = tk.StringVar(value="0")
            duration_var = tk.StringVar(value="1")
            entry_row(selection_group, "開始 (秒)", start_var)
            entry_row(selection_group, "長さ (秒)", duration_var)
            def select_range():
                try:
                    start, duration = float(start_var.get()), float(duration_var.get())
                    if not all(math.isfinite(v) for v in (start, duration)) or start < 0 or duration <= 0 or start >= browser.n * browser.dt:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("保存範囲", "測定内の開始時刻と、0より大きい長さを入力してください。", parent=panel)
                    return
                browser.selection_start_box.set_val(str(start))
                browser.selection_duration_box.set_val(str(duration))
                browser.on_timed_selection(None)
            ttk.Button(selection_group, text="範囲を選択", command=select_range).pack(fill="x", pady=4)
            button_row(selection_group, [("選択解除", browser.on_clear)])
            def preview():
                if browser.sel_tmin is None:
                    messagebox.showinfo("保存範囲", "先に波形の保存範囲を選択してください。", parent=panel)
                    return
                browser.on_save(None)
            ttk.Button(selection_group, text="保存プレビューへ", style="Primary.TButton", command=preview).pack(fill="x", pady=(8, 0))
            axes_group = group("軸の表示範囲")
            axis_vars = [tk.StringVar(value="") for _ in range(4)]
            for label, variable in zip(("Y 最小", "Y 最大", "X 開始 (秒)", "X 終了 (秒)"), axis_vars):
                entry_row(axes_group, label, variable)
            def apply_axes():
                try:
                    for variable in axis_vars:
                        if variable.get().strip() and not math.isfinite(float(variable.get())):
                            raise ValueError
                    for low, high in (axis_vars[:2], axis_vars[2:]):
                        if low.get().strip() and high.get().strip() and float(low.get()) >= float(high.get()):
                            raise ValueError
                except ValueError:
                    messagebox.showerror("表示範囲", "最小 < 最大となる数値を入力してください。空欄は自動です。", parent=panel)
                    return
                for box, variable in zip((browser.ymin_box, browser.ymax_box, browser.xmin_box, browser.xmax_box), axis_vars):
                    box.eventson = False
                    box.set_val(variable.get())
                    box.eventson = True
                browser.on_axis_range_submit()
            ttk.Button(axes_group, text="軸範囲を適用", command=apply_axes).pack(fill="x", pady=4)
            def auto_axes():
                for variable in axis_vars:
                    variable.set("")
                browser.on_auto_axes(None)
            ttk.Button(axes_group, text="自動表示に戻す", command=auto_axes).pack(fill="x")
            file_choice.bind("<<ComboboxSelected>>", lambda _: (browser.load_file(file_choice.current()), browser.draw()))
            browser._tk_controls_ready = True
            browser.draw()
            self.browser.fig.canvas.draw_idle()
            self.workspace.select(self.waveform_frame)
        except Exception:
            self.clear_viewer()
            for figure in created_figures:
                plt.close(figure)
            raise


class SavedFilesPanel(ttk.Frame):
    """Inspect saved clips independently of the source TDMS viewer."""

    def __init__(self, workspace, output):
        super().__init__(workspace, padding=12)
        self.directory = tk.StringVar(value=output.get())
        self.mode = tk.StringVar(value="波形")
        self.status = tk.StringVar(value="保存先を指定して「一覧を更新」を押してください。")
        self.rows = {}
        self.results = queue.Queue()
        self.generation = 0
        self.canvas = None
        self.figure = None
        self.details = tk.StringVar(value="保存ファイルを選択すると内容を表示します。")
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Label(bar, text="保存ファイルのフォルダ").pack(side="left", padx=(0, 8))
        ttk.Entry(bar, textvariable=self.directory).pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="参照…", command=self.browse).pack(side="left", padx=6)
        ttk.Button(bar, text="現在の保存先", command=lambda: self.use_output(output)).pack(side="left")
        ttk.Button(bar, text="一覧を更新", command=self.refresh).pack(side="left", padx=6)
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        files_frame = ttk.Frame(panes)
        view = ttk.Frame(panes, padding=(12, 0, 0, 0))
        panes.add(files_frame, weight=1)
        panes.add(view, weight=3)
        ttk.Label(files_frame, text="保存済みの範囲（新しい順）", style="Heading.TLabel").pack(anchor="w", pady=(0, 8))
        table_box = ttk.Frame(files_frame)
        table_box.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_box, columns=("name", "formats", "modified"), show="headings", selectmode="browse")
        for key, title, width in (("name", "保存名", 320), ("formats", "形式", 120), ("modified", "更新日時", 150)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width)
        self.table.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_box, orient="vertical", command=self.table.yview)
        scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<<TreeviewSelect>>", lambda _: self.show_selected(auto=True))
        ttk.Label(view, textvariable=self.details, wraplength=850).pack(anchor="w", pady=(0, 8))
        modes = ttk.Frame(view)
        modes.pack(fill="x", pady=(0, 8))
        for label in ("波形", "保存画像 (PNG)"):
            ttk.Radiobutton(modes, text=label, variable=self.mode, value=label,
                            command=self.show_selected).pack(side="left", padx=(0, 16))
        self.plot_frame = ttk.Frame(view)
        self.plot_frame.pack(fill="both", expand=True)
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", padding=8).pack(fill="x", pady=(10, 0))
        self.after(200, self.poll)
        self.after_idle(self.refresh)

    def browse(self):
        folder = filedialog.askdirectory(parent=self, title="保存ファイルのフォルダ")
        if folder:
            self.directory.set(folder)
            self.refresh()

    def use_output(self, output):
        self.directory.set(output.get())
        self.refresh()

    def refresh(self):
        raw = self.directory.get().strip()
        if not raw:
            messagebox.showerror("保存フォルダ", "保存フォルダを指定してください。", parent=self)
            return
        self.generation += 1
        generation = self.generation
        self.status.set("保存ファイルを検索しています…")
        def scan():
            try:
                folder = Path(raw)
                if not folder.is_dir():
                    raise ValueError(f"フォルダが見つかりません: {folder}")
                groups = {}
                def on_error(error):
                    raise error
                for directory, _, names in os.walk(folder, onerror=on_error):
                    for name in names:
                        path = Path(directory) / name
                        suffix = path.suffix.lower()
                        if suffix not in (".csv", ".npy", ".png"):
                            continue
                        key = str(path.with_suffix(""))
                        item = groups.setdefault(key, dict(paths={}, modified=0, name=str(path.with_suffix("").relative_to(folder))))
                        item["paths"][suffix] = path
                        item["modified"] = max(item["modified"], path.stat().st_mtime)
                self.results.put((generation, sorted(groups.values(), key=lambda row: row["modified"], reverse=True), None))
            except Exception as exc:
                self.results.put((generation, [], exc))
        threading.Thread(target=scan, daemon=True).start()

    def poll(self):
        try:
            generation, rows, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            if generation == self.generation:
                if error:
                    self.status.set(f"一覧取得失敗: {error}")
                else:
                    self.table.delete(*self.table.get_children())
                    self.rows.clear()
                    self.details.set("保存ファイルを選択すると内容を表示します。")
                    if self.figure is not None:
                        self.figure.clear()
                        self.canvas.draw_idle()
                    for index, row in enumerate(rows):
                        key = str(index)
                        self.rows[key] = row
                        self.table.insert("", "end", iid=key, values=(row["name"],
                            " / ".join(ext[1:].upper() for ext in sorted(row["paths"])),
                            datetime.fromtimestamp(row["modified"]).strftime("%Y-%m-%d %H:%M")))
                    self.status.set(f"{len(rows)}件の保存範囲があります。同名のCSV・NPY・PNGを1行にまとめています。")
        self.after(200, self.poll)

    def show_selected(self, auto=False):
        selected = self.table.selection()
        if not selected or selected[0] not in self.rows:
            return
        row = self.rows[selected[0]]
        paths = row["paths"]
        if auto:
            self.mode.set("保存画像 (PNG)" if ".png" in paths else "波形")
        try:
            import numpy as np
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
            if self.figure is None:
                self.figure = Figure(figsize=(10, 6), facecolor="white")
                self.canvas = FigureCanvasTkAgg(self.figure, master=self.plot_frame)
                toolbar = NavigationToolbar2Tk(self.canvas, self.plot_frame, pack_toolbar=False)
                toolbar.pack(side="bottom", fill="x")
                self.canvas.get_tk_widget().pack(fill="both", expand=True)
            self.figure.clear()
            axes = self.figure.add_subplot(111)
            if self.mode.get() == "保存画像 (PNG)":
                if ".png" not in paths:
                    raise ValueError("この保存範囲にはPNG画像がありません。")
                from matplotlib.image import imread
                axes.imshow(imread(paths[".png"]))
                axes.axis("off")
                detail = "保存されたPNGを表示しています。"
            else:
                if ".csv" in paths:
                    data = np.loadtxt(paths[".csv"], delimiter=",", skiprows=1, ndmin=2)
                    if data.shape[1] != 2 or not len(data):
                        raise ValueError("CSVは時刻・波形の2列である必要があります。")
                    x, y = data[:, 0], data[:, 1]
                    xlabel = "Time (s)"
                    detail = f"CSV: {len(y):,}点 / 時刻 {x[0]:.6g} ～ {x[-1]:.6g} 秒"
                elif ".npy" in paths:
                    y = np.load(paths[".npy"], allow_pickle=False)
                    if y.ndim != 1 or not len(y):
                        raise ValueError("NPYは空でない1次元の波形である必要があります。")
                    x = np.arange(len(y))
                    xlabel = "Sample index"
                    detail = f"NPY: {len(y):,}点 / 時刻情報がないためサンプル番号で表示"
                else:
                    raise ValueError("波形データがありません。「保存画像 (PNG)」を選択してください。")
                axes.plot(x, y, color="#2563a6", linewidth=0.8)
                axes.set_xlabel(xlabel)
                axes.set_ylabel("Signal amplitude (nA)")
                axes.grid(alpha=0.2)
            self.figure.tight_layout()
            self.canvas.draw_idle()
            self.details.set(f"{Path(next(iter(paths.values()))).parent}\n{row['name']}\n{detail}")
            self.status.set("保存済みファイルを表示しています。元TDMSへの接続は不要です。")
        except Exception as exc:
            if self.canvas is not None:
                self.canvas.draw_idle()
            self.status.set(f"表示できません: {exc}")


def main():
    root = tk.Tk()
    root.title("TDMS 波形ビューア")
    root.geometry("1400x900")
    root.minsize(900, 650)
    style = ttk.Style(root)
    style.configure("TButton", padding=(10, 6))
    style.configure("TNotebook.Tab", padding=(18, 8))
    style.configure("TLabelframe.Label", font=("Yu Gothic UI", 10, "bold"))
    style.configure("Heading.TLabel", font=("Yu Gothic UI", 12, "bold"))
    style.configure("Primary.TButton", font=("Yu Gothic UI", 10, "bold"))
    style.configure("Status.TLabel", background="#eaf1f8", foreground="#244361")
    workspace = ttk.Notebook(root)
    workspace.pack(fill="both", expand=True)
    waveform_frame = ttk.Frame(workspace)
    preview_frame = ttk.Frame(workspace)
    panel = FrontPanel(workspace, workspace, waveform_frame, preview_frame)
    panel.pack_forget()
    workspace.add(panel, text="読み込み・設定")
    workspace.add(waveform_frame, text="波形")
    workspace.add(preview_frame, text="保存プレビュー")
    saved_panel = SavedFilesPanel(workspace, panel.output)
    panel.saved_panel = saved_panel
    workspace.add(saved_panel, text="保存ファイル")
    def close():
        if panel.browser is not None:
            panel.clear_viewer()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()


if __name__ == "__main__":
    main()
