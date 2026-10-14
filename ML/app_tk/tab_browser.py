# -*- coding: utf-8 -*-
"""④ 結果ブラウザタブ と、学習結果フォルダの表示部品 RunResultView。

results/ 配下の run_manifest.json を持つ実行結果（UI経由・CLI経由どちらも）を一覧し、
レポート・混同行列・解析画像を再表示、フォルダを開く／ZIPで保存できる。
"""

import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import pandas as pd

from lib.runs import list_runs
from widgets import DataTable, ImageGallery, open_path, save_zip_dialog


class RunResultView(ttk.Frame):
    """1つの学習結果フォルダ（run_dir）の中身を表示する。"""

    def __init__(self, master):
        super().__init__(master)
        self.run_dir = None

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.title = ttk.Label(bar, text="結果を選んでください", foreground="#666")
        self.zip_button = ttk.Button(bar, text="ZIPで保存", state="disabled",
                                     command=lambda: save_zip_dialog(self.run_dir))
        self.zip_button.pack(side="right")
        self.open_button = ttk.Button(bar, text="フォルダを開く", state="disabled",
                                      command=lambda: open_path(self.run_dir))
        self.open_button.pack(side="right", padx=4)
        self.title.pack(side="left", fill="x", expand=True)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, pady=4)

        summary = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(summary, text="レポート")
        self.report = ScrolledText(summary, height=12, font=("Consolas", 9), wrap="none")
        self.report.pack(fill="both", expand=True)
        self.comparison = DataTable(summary, height=5)
        self.comparison.pack(fill="x", pady=4)

        cm_frame = ttk.Frame(self.notebook)
        self.notebook.add(cm_frame, text="混同行列")
        cm_bar = ttk.Frame(cm_frame)
        cm_bar.pack(fill="x")
        self.cm_order_button = ttk.Button(cm_bar, text="サンプルの表示順を変更",
                                         command=self._choose_cm_order, state="disabled")
        self.cm_order_button.pack(side="left", padx=4, pady=4)
        self.cm_order_summary = ttk.Label(cm_bar, text="")
        self.cm_order_summary.pack(side="left", fill="x", expand=True)
        self.cm_gallery = ImageGallery(cm_frame, columns=2)
        self.cm_gallery.pack(fill="both", expand=True)
        self.other_gallery = ImageGallery(self.notebook, columns=3)
        self.notebook.add(self.other_gallery, text="重要度・解析画像")

        manifest_frame = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(manifest_frame, text="実行条件")
        self.manifest = ScrolledText(manifest_frame, font=("Consolas", 9))
        self.manifest.pack(fill="both", expand=True)

    def show(self, run_dir: Path):
        from common.eval_viz import redraw_confusion_matrices

        self.run_dir = Path(run_dir)
        self.title.configure(text=str(self.run_dir), foreground="")
        self.zip_button.configure(state="normal")
        self.open_button.configure(state="normal")

        report_path = self.run_dir / "classification_report.txt"
        self._set_text(self.report, report_path.read_text(encoding="utf-8")
                       if report_path.exists() else "（classification_report.txt がありません）")
        comparison_path = self.run_dir / "step1_feature_set_comparison.csv"
        self.comparison.show(pd.read_csv(comparison_path) if comparison_path.exists() else None)

        cm_names = ("confusion_matrix.png", "confusion_matrix_normalized.png")
        if (not (self.run_dir / cm_names[0]).exists()
                and (self.run_dir / "confusion_matrix.csv").exists()):
            redraw_confusion_matrices(self.run_dir)  # CSVだけ残っている古い結果は画像を再生成
        cm_paths = [self.run_dir / n for n in cm_names if (self.run_dir / n).exists()]
        self.cm_gallery.show(cm_paths)
        self.cm_class_order = []
        csv_path = self.run_dir / "confusion_matrix.csv"
        if csv_path.exists() and (self.run_dir / "confusion_matrix_normalized.csv").exists():
            self.cm_class_order = [str(label).removeprefix("Actual_")
                                   for label in pd.read_csv(csv_path, index_col=0).index]
        self.cm_order_button.configure(state="normal" if self.cm_class_order else "disabled")
        self.cm_order_summary.configure(text="CSVの元の順番" if self.cm_class_order
                                        else "順番の変更には混同行列のCSVが必要です")
        self.other_gallery.show(sorted(p for p in self.run_dir.glob("*.png") if p not in cm_paths))

        manifest_path = self.run_dir / "run_manifest.json"
        text = ""
        if manifest_path.exists():
            text = json.dumps(json.loads(manifest_path.read_text(encoding="utf-8")),
                              ensure_ascii=False, indent=2)
        self._set_text(self.manifest, text or "（run_manifest.json がありません）")

    def _choose_cm_order(self):
        dialog = tk.Toplevel(self)
        dialog.title("混同行列の表示順")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        ttk.Label(dialog, text="サンプルを選んで上下に移動してください。行・列を同じ順番で変更します。",
                  wraplength=400).pack(padx=10, pady=10)
        items = tk.Listbox(dialog, exportselection=False, height=12, width=40)
        items.pack(fill="both", expand=True, padx=10)
        for name in self.cm_class_order:
            items.insert("end", name)
        controls = ttk.Frame(dialog)
        controls.pack(fill="x", padx=10, pady=10)

        def move(offset):
            selected = items.curselection()
            if not selected:
                return
            index = selected[0]
            target = index + offset
            if 0 <= target < items.size():
                name = items.get(index)
                items.delete(index)
                items.insert(target, name)
                items.selection_clear(0, "end")
                items.selection_set(target)
                items.see(target)

        def reset():
            names = pd.read_csv(self.run_dir / "confusion_matrix.csv", index_col=0).index
            items.delete(0, "end")
            for name in names:
                items.insert("end", str(name).removeprefix("Actual_"))

        def apply():
            from common.eval_viz import redraw_confusion_matrices

            order = list(items.get(0, "end"))
            output = self.run_dir / "confusion_matrix_display"
            try:
                redraw_confusion_matrices(self.run_dir, class_order=order, output_dir=output)
            except Exception as exc:
                messagebox.showerror("混同行列の表示順を変更できません", str(exc), parent=dialog)
                return
            self.cm_class_order = order
            self.cm_order_summary.configure(text=" → ".join(order), wraplength=500)
            self.cm_gallery.show([output / "confusion_matrix.png",
                                  output / "confusion_matrix_normalized.png"])
            dialog.destroy()

        ttk.Button(controls, text="↑ 上へ", command=lambda: move(-1)).pack(side="left")
        ttk.Button(controls, text="↓ 下へ", command=lambda: move(1)).pack(side="left", padx=4)
        ttk.Button(controls, text="元の順番", command=reset).pack(side="left")
        ttk.Button(controls, text="適用", command=apply).pack(side="right")

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")


class BrowserTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        paned.add(left, weight=1)
        top = ttk.Frame(left)
        top.pack(fill="x")
        ttk.Label(top, text="過去の実行結果").pack(side="left")
        ttk.Button(top, text="再読み込み", command=self.refresh).pack(side="right")
        self.filter_text = tk.StringVar()
        entry = ttk.Entry(left, textvariable=self.filter_text)
        entry.pack(fill="x", pady=4)
        entry.bind("<KeyRelease>", lambda _e: self._fill())
        ttk.Label(left, text="↑ サンプル名などで絞り込み", foreground="#666").pack(anchor="w")

        self.tree = ttk.Treeview(left, columns=("time", "kind", "smns"), show="headings")
        for col, text, width in (("time", "日時", 130), ("kind", "特徴量/手法", 110),
                                 ("smns", "クラス", 260)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, stretch=col == "smns")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._selected)

        self.view = RunResultView(paned)
        paned.add(self.view, weight=3)

        self._runs = []
        self.refresh()

    def refresh(self):
        self._runs = list_runs()
        self._fill()

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        keyword = self.filter_text.get().strip().lower()
        for i, r in enumerate(self._runs):
            smns = "-".join(r["manifest"].get("smns", []))
            values = (r["manifest"].get("run_timestamp", "?"),
                      f"{r['feature_set']}/{r['algorithm']}", smns)
            if keyword and keyword not in " ".join(values).lower() + r["run_dir"].name.lower():
                continue
            self.tree.insert("", "end", iid=str(i), values=values)

    def _selected(self, _event):
        selection = self.tree.selection()
        if selection:
            self.view.show(self._runs[int(selection[0])]["run_dir"])
