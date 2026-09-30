# -*- coding: utf-8 -*-
"""ML/app_tk/server_browse.py

計測サーバー上のフォルダを一覧から選ぶための部品（filemanager/app.py と同じ操作感）。

    //<サーバー>/<共有フォルダ>/<実験フォルダ>/<サンプル>/<サンプル>_10k_Sample/T/...ANAL...

- サーバー     : SERVERS から選択
- 共有フォルダ : Windows の共有列挙API（NetShareEnum）でサーバー直下の共有を取得
- 実験フォルダ・サンプル : 1つ上の階層の直下フォルダを os.scandir で取得
ネットワーク越しの読み込みは時間がかかることがあるため、別スレッドで行う。
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SERVERS = ("Rackstation", "QTserver", "QDserver", "QKserver")
_INVALID_CHARS = '<>:"/\\|?*'


def list_server_shares(server):
    """Windowsの共有列挙APIでサーバー直下のディスク共有を取得する（filemanager/app.py と同じ）。"""
    import ctypes
    from ctypes import wintypes

    class ShareInfo(ctypes.Structure):
        _fields_ = [("name", wintypes.LPWSTR), ("type", wintypes.DWORD),
                    ("remark", wintypes.LPWSTR)]

    api = ctypes.WinDLL("Netapi32.dll")
    enum = api.NetShareEnum
    enum.argtypes = [wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    enum.restype = wintypes.DWORD
    api.NetApiBufferFree.argtypes = [ctypes.c_void_p]
    api.NetApiBufferFree.restype = wintypes.DWORD
    resume = wintypes.DWORD()
    names = []
    while True:
        buffer = ctypes.c_void_p()
        count, total = wintypes.DWORD(), wintypes.DWORD()
        result = enum("\\\\" + server, 1, ctypes.byref(buffer), 0xFFFFFFFF,
                      ctypes.byref(count), ctypes.byref(total), ctypes.byref(resume))
        try:
            if result not in (0, 234):  # 234 = ERROR_MORE_DATA（続きがある）
                raise ctypes.WinError(result)
            entries = ctypes.cast(buffer, ctypes.POINTER(ShareInfo))
            names.extend(entries[i].name for i in range(count.value)
                         if entries[i].type & 0xFFFF == 0)  # ディスク共有のみ
        finally:
            if buffer:
                api.NetApiBufferFree(buffer)
        if result == 0:
            return sorted(set(names), key=str.casefold)


def list_subfolders(parent):
    with os.scandir(parent) as entries:
        return sorted((e.name for e in entries if e.is_dir()), key=str.casefold)


def check_component(value, label):
    """フォルダ名として使えない値（空・区切り文字入り等）なら ValueError。"""
    value = value.strip()
    if not value or value in (".", "..") or any(c in value for c in _INVALID_CHARS):
        raise ValueError(f"{label}を正しく入力してください（空欄や / \\ : * ? などは使えません）。")
    return value


class FolderPickerDialog(tk.Toplevel):
    """フォルダ名の一覧から1つ（または複数）を選ぶダイアログ。

    loader     : フォルダ名リストを返す関数（別スレッドで呼ばれる）
    on_select  : 選択結果（multiple=True なら名前のリスト、False なら名前1つ）を受け取る関数
    annotate   : 名前 → 一覧に添える注記（例: "抽出済み"）を返す関数（任意）
    browse_dir : 「フォルダ参照…」で開く初期フォルダ（任意）
    """

    def __init__(self, master, title, location, loader, on_select, multiple=False,
                 annotate=None, browse_dir=None, preselected=()):
        super().__init__(master)
        self.title(title)
        self.geometry("680x520")
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self._loader = loader
        self._on_select = on_select
        self._multiple = multiple
        self._annotate = annotate or (lambda _name: "")
        self._browse_dir = browse_dir
        self._preselected = set(preselected)
        self._names: list[str] = []
        self._shown: list[str] = []
        self._results: queue.Queue = queue.Queue()

        panel = ttk.Frame(self, padding=12)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=location, wraplength=640).pack(anchor="w")
        ttk.Label(panel, text="直下のフォルダ名から選択してください。更新ボタンで再読み込みできます。"
                  ).pack(anchor="w", pady=(4, 8))

        bar = ttk.Frame(panel)
        bar.pack(fill="x")
        if browse_dir:
            ttk.Button(bar, text="フォルダ参照…", command=self._browse).pack(side="left", padx=(0, 8))
        self.load_button = ttk.Button(bar, text="一覧から選択 / 更新", command=self._load)
        self.load_button.pack(side="left")
        ttk.Label(bar, text="  絞り込み").pack(side="left")
        self.filter_text = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.filter_text, width=24)
        entry.pack(side="left", padx=4)
        entry.bind("<KeyRelease>", lambda _e: self._fill())

        self.status = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.status, wraplength=640).pack(anchor="w", pady=6)
        if multiple:
            ttk.Label(panel, text="Ctrl / Shift で複数選択できます。選択結果でサンプル名欄を置き換えます。"
                      ).pack(anchor="w")

        list_frame = ttk.Frame(panel)
        list_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.listbox = tk.Listbox(list_frame, exportselection=False, activestyle="none",
                                  selectmode="extended" if multiple else "browse")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", self._choose)
        self.listbox.bind("<Return>", self._choose)

        bottom = ttk.Frame(panel)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="選択したフォルダを使用", command=self._choose).pack(side="left")
        ttk.Button(bottom, text="キャンセル", command=self.destroy).pack(side="right")

        self._load()

    def _load(self):
        self.load_button.state(["disabled"])
        self.status.set("フォルダ一覧を読み込み中…")

        def worker():
            try:
                self._results.put((self._loader(), None))
            except OSError as exc:
                self._results.put(([], str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._poll)

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            names, error = self._results.get_nowait()
        except queue.Empty:
            self.after(80, self._poll)
            return
        self.load_button.state(["!disabled"])
        self._names = names
        self._fill()
        self.status.set(f"読み込めませんでした: {error}" if error else
                        f"{len(names)}件 — ダブルクリック または「選択したフォルダを使用」で反映します。")

    def _fill(self):
        keyword = self.filter_text.get().strip().casefold()
        selected = set(self._selected_names()) or self._preselected
        self._shown = [n for n in self._names if keyword in n.casefold()]
        self.listbox.delete(0, "end")
        for i, name in enumerate(self._shown):
            note = self._annotate(name)
            self.listbox.insert("end", f"{name}    （{note}）" if note else name)
            if name in selected:
                self.listbox.selection_set(i)
                self.listbox.see(i)

    def _selected_names(self):
        return [self._shown[i] for i in self.listbox.curselection() if i < len(self._shown)]

    def _choose(self, _event=None):
        names = self._selected_names()
        if not names:
            return
        if self._on_select(names if self._multiple else names[0]) is not False:
            self.destroy()

    def _browse(self):
        selected = filedialog.askdirectory(parent=self, initialdir=self._browse_dir, mustexist=True)
        if not selected:
            return
        parent, _, name = selected.replace("\\", "/").rstrip("/").rpartition("/")
        if parent.casefold() != self._browse_dir.replace("\\", "/").rstrip("/").casefold():
            messagebox.showerror("選択先を確認してください",
                                 f"{self._browse_dir} の直下のフォルダを選択してください。", parent=self)
            return
        if self._on_select([name] if self._multiple else name) is not False:
            self.destroy()
