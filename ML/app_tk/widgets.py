# -*- coding: utf-8 -*-
"""ML/app_tk/widgets.py

Tkinter版MLパネルの各タブで共通に使う部品。

- TaskRunner    : 重い処理（抽出・学習・予測）を別スレッドで実行し、
                  進捗・ログ・結果をメインスレッドへ安全に受け渡す
- ProgressPanel : 進捗バー＋メッセージ＋ログ表示
- CheckList     : 複数選択できるリスト（サンプル選択用）
- DataTable     : pandas.DataFrame を表示する表
- ImageGallery  : 結果フォルダ内の画像のサムネイル一覧（クリックで拡大表示）
"""

import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import pandas as pd
from PIL import Image, ImageTk

JP_FONT = ("Meiryo UI", 10)


# =====================
# バックグラウンド実行
# =====================
class _QueueWriter:
    """print() の出力をキューに流す（処理中のログをウィンドウに表示するため）。"""

    def __init__(self, q, original):
        self._q = q
        self._original = original

    def write(self, text):
        if text:
            self._q.put(("log", text))
        try:
            self._original.write(text)
        except Exception:  # noqa: BLE001 - コンソールが無い(pythonw)場合など
            pass

    def flush(self):
        try:
            self._original.flush()
        except Exception:  # noqa: BLE001
            pass


class TaskRunner:
    """同時に1つだけ重い処理を実行するためのランナー（アプリ全体で1つ共有）。

    Tkinterのウィジェットはメインスレッドからしか触れないため、作業スレッドからは
    キューにメッセージを積むだけにし、メインスレッドが after() で定期的に取り出して
    画面に反映する。
    """

    POLL_MS = 100

    def __init__(self, root: tk.Tk):
        self.root = root
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._panel = None
        self._on_done = None
        self._busy_listeners = []
        self.root.after(self.POLL_MS, self._poll)

    @property
    def busy(self) -> bool:
        return self._busy

    def add_busy_listener(self, callback):
        """実行中/待機中が切り替わるたびに callback(busy: bool) を呼ぶ。"""
        self._busy_listeners.append(callback)

    def run(self, func, panel: "ProgressPanel", on_done=None) -> bool:
        """func(progress_callback) を別スレッドで実行する。

        func         : progress_callback(current, total, message) を受け取る関数
        panel        : 進捗・ログを表示する ProgressPanel
        on_done      : 成功時に結果を受け取る関数（メインスレッドで呼ばれる）
        """
        if self._busy:
            messagebox.showwarning("実行中", "別の処理が実行中です。終わるまでお待ちください。")
            return False
        self._set_busy(True)
        self._panel = panel
        self._on_done = on_done
        panel.start()

        def progress(current, total, message=""):
            self._q.put(("progress", (current, total, message)))

        def worker():
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = _QueueWriter(self._q, old_out)
            sys.stderr = _QueueWriter(self._q, old_err)
            try:
                result = func(progress)
            except Exception as e:  # noqa: BLE001 - エラー内容はそのまま画面に出す
                self._q.put(("error", (e, traceback.format_exc())))
            else:
                self._q.put(("done", result))
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _set_busy(self, busy):
        self._busy = busy
        for callback in self._busy_listeners:
            callback(busy)

    def _poll(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                panel = self._panel
                if kind == "log" and panel:
                    panel.log(payload)
                elif kind == "progress" and panel:
                    panel.set_progress(*payload)
                elif kind == "error":
                    exc, tb = payload
                    if panel:
                        panel.log("\n" + tb)
                        panel.finish(f"エラー: {exc}", ok=False)
                    self._set_busy(False)
                    messagebox.showerror("エラー", f"{type(exc).__name__}: {exc}")
                elif kind == "done":
                    if panel:
                        panel.finish("完了しました", ok=True)
                    self._set_busy(False)
                    if self._on_done:
                        self._on_done(payload)
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self._poll)


class ProgressPanel(ttk.LabelFrame):
    """進捗バー・現在のメッセージ・ログをまとめた枠。"""

    def __init__(self, master, log_height=8):
        super().__init__(master, text="進捗")
        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=6, pady=(6, 2))
        self.message = ttk.Label(self, text="待機中")
        self.message.pack(anchor="w", padx=6)
        self.text = ScrolledText(self, height=log_height, font=("Consolas", 9), wrap="none")
        self.text.pack(fill="both", expand=True, padx=6, pady=6)
        self.text.configure(state="disabled")

    def start(self):
        self.bar.configure(mode="indeterminate")
        self.bar.start(15)
        self.message.configure(text="実行中...", foreground="")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def set_progress(self, current, total, message=""):
        total = max(int(total), 1)
        current = min(max(int(current), 0), total)
        self.bar.stop()
        self.bar.configure(mode="determinate", value=current / total * 100)
        self.message.configure(text=f"{current}/{total}  {message}")

    def log(self, text):
        self.text.configure(state="normal")
        # tqdm の \r 上書きは改行に置き換えず、最後の状態だけ残す
        for part in text.replace("\r\n", "\n").split("\r"):
            self.text.insert("end", part)
        self.text.see("end")
        self.text.configure(state="disabled")

    def finish(self, message, ok=True):
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100 if ok else 0)
        self.message.configure(text=message, foreground="" if ok else "#c0392b")


# =====================
# 入力部品
# =====================
class CheckList(ttk.Frame):
    """複数選択リスト。クリックで選択/解除が切り替わる。"""

    def __init__(self, master, height=8, on_change=None):
        super().__init__(master)
        self._on_change = on_change
        self.listbox = tk.Listbox(self, selectmode="multiple", exportselection=False,
                                  height=height, activestyle="none", font=JP_FONT)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._changed())
        self._items: list[str] = []

    def set_items(self, items, keep_selection=True):
        selected = set(self.get_selected()) if keep_selection else set()
        self._items = list(items)
        self.listbox.delete(0, "end")
        for i, item in enumerate(self._items):
            self.listbox.insert("end", item)
            if item in selected:
                self.listbox.selection_set(i)
        self._changed()

    def get_selected(self) -> list[str]:
        return [self._items[i] for i in self.listbox.curselection()]

    def clear_selection(self):
        self.listbox.selection_clear(0, "end")
        self._changed()

    def _changed(self):
        if self._on_change:
            self._on_change(self.get_selected())


# =====================
# 表示部品
# =====================
class DataTable(ttk.Frame):
    """pandas.DataFrame を Treeview で表示する。"""

    MAX_ROWS = 5000

    def __init__(self, master, height=10):
        super().__init__(master)
        self.tree = ttk.Treeview(self, show="headings", height=height)
        vs = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.note = ttk.Label(self, text="", foreground="#666")
        self.note.grid(row=2, column=0, sticky="w")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

    def show(self, df: pd.DataFrame | None):
        self.tree.delete(*self.tree.get_children())
        if df is None or df.empty:
            self.tree["columns"] = ()
            self.note.configure(text="（データなし）")
            return
        columns = [str(c) for c in df.columns]
        self.tree["columns"] = columns
        sample = df.head(200).astype(str)

        def text_width(text):  # 全角文字は半角2文字分として数える
            return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)

        for col, src in zip(columns, df.columns):
            longest = max([text_width(col)] + [text_width(v) for v in sample[src]])
            width = max(60, min(320, 8 * longest + 16))
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor="w" if col in ("file", "mix_sample") else "e",
                             stretch=False)
        for row in df.head(self.MAX_ROWS).itertuples(index=False):
            self.tree.insert("", "end", values=["" if pd.isna(v) else v for v in row])
        extra = f"（先頭 {self.MAX_ROWS} 行のみ表示）" if len(df) > self.MAX_ROWS else ""
        self.note.configure(text=f"{len(df)} 行 {extra}")


class ScrollableFrame(ttk.Frame):
    """縦スクロールできるフレーム。中身は self.inner に配置する。"""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        # マウスホイールはポインタが上にあるときだけスクロールさせる
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ImageGallery(ScrollableFrame):
    """画像のサムネイル一覧。クリックすると既定のビューアで開く。"""

    THUMB = 360

    def __init__(self, master, columns=2, **kwargs):
        super().__init__(master, **kwargs)
        self.columns = columns
        self._photos = []  # PhotoImage の参照を保持しないと画像が消える

    def show(self, image_paths):
        for child in self.inner.winfo_children():
            child.destroy()
        self._photos.clear()
        if not image_paths:
            ttk.Label(self.inner, text="（画像なし）", foreground="#666").grid(row=0, column=0)
            return
        for i, path in enumerate(image_paths):
            try:
                img = Image.open(path)
                img.thumbnail((self.THUMB, self.THUMB))
                photo = ImageTk.PhotoImage(img)
            except Exception:  # noqa: BLE001 - 壊れた画像はスキップ
                continue
            self._photos.append(photo)
            cell = ttk.Frame(self.inner)
            cell.grid(row=i // self.columns, column=i % self.columns, padx=6, pady=6, sticky="n")
            label = ttk.Label(cell, image=photo, cursor="hand2")
            label.pack()
            label.bind("<Button-1>", lambda _e, p=path: open_path(p))
            ttk.Label(cell, text=Path(path).name, foreground="#555").pack()


# =====================
# ファイル操作
# =====================
def open_path(path):
    """フォルダ・ファイルを Windows の既定アプリ（エクスプローラー等）で開く。"""
    path = Path(path)
    if not path.exists():
        messagebox.showwarning("見つかりません", str(path))
        return
    os.startfile(str(path))  # noqa: S606 - Windows専用アプリ


def save_bytes_dialog(data: bytes, default_name: str, filetypes):
    """保存先を選ばせて data を書き込む。"""
    ext = Path(default_name).suffix
    target = filedialog.asksaveasfilename(initialfile=default_name, defaultextension=ext,
                                          filetypes=filetypes)
    if target:
        Path(target).write_bytes(data)
        messagebox.showinfo("保存しました", target)


def save_zip_dialog(folder: Path):
    from lib.runs import make_zip_bytes, run_zip_path

    zip_path = run_zip_path(folder) if folder.parent.parent.parent.name == "results" else None
    data = zip_path.read_bytes() if zip_path else make_zip_bytes(folder)
    save_bytes_dialog(data, f"{folder.name}.zip", [("ZIP", "*.zip")])


def save_csv_dialog(df: pd.DataFrame, default_name: str):
    save_bytes_dialog(df.to_csv(index=False).encode("utf-8-sig"), default_name, [("CSV", "*.csv")])


def labeled(master, text, widget_factory, row, column=0, **grid):
    """「ラベル＋入力欄」を1行で配置するヘルパー。作ったウィジェットを返す。"""
    ttk.Label(master, text=text).grid(row=row, column=column, sticky="w", padx=4, pady=3)
    widget = widget_factory(master)
    widget.grid(row=row, column=column + 1, sticky="ew", padx=4, pady=3, **grid)
    return widget
