# -*- coding: utf-8 -*-
"""同じクラスの組み合わせの学習器を、構成要素ごとに並べて比較して選ぶ画面（⑤ 混合予測タブ用）。"""
import tkinter as tk
from tkinter import ttk

import ui_theme
from common.trained_models import comparison_table


def column_title(index, model):
    created = (model.get("created_at") or "")[5:16]
    return f"{index + 1}. {created}  F1 {model['test_macro_f1']:.3f}"


class ModelCompareDialog(tk.Toplevel):
    """学習器を列、構成要素を行にした表。違いのある行は色付けし、絞り込み表示もできる。"""

    def __init__(self, master, models, on_choose, current=None):
        super().__init__(master)
        classes = ", ".join(models[0]["class_names"])
        self.title(f"学習器の比較：{classes}")
        self.geometry(f"{min(1500, 380 + 260 * len(models))}x720")
        self.transient(master.winfo_toplevel())
        self.models = models
        self.on_choose = on_choose
        self.table = comparison_table(models)

        best = max(range(len(models)), key=lambda i: models[i]["test_macro_f1"])
        ttk.Label(self, text=f"{classes}：学習器 {len(models)} 件。色付きの行は学習器ごとに値が違う項目です。"
                             f"テストF1が最も高いのは {best + 1} 番です。", wraplength=1200).pack(
            anchor="w", padx=10, pady=(10, 4))
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10)
        self.only_diff = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="違いのある項目だけ表示", variable=self.only_diff,
                        command=self._render).pack(side="left")

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=6)
        columns = ["item"] + [f"m{i}" for i in range(len(models))]
        self.tree = ttk.Treeview(frame, columns=columns, show="headings")
        self.tree.heading("item", text="区分 / 項目")
        self.tree.column("item", width=260, stretch=False)
        for i, model in enumerate(models):
            self.tree.heading(f"m{i}", text=column_title(i, model) + ("  ★" if i == best else ""))
            self.tree.column(f"m{i}", width=250, stretch=True)
        self.tree.tag_configure("diff", background=ui_theme.row("warning", 0.22))
        self.tree.tag_configure("section", foreground=ui_theme.MUTED)
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        choose = ttk.Frame(self)
        choose.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(choose, text="使う学習器：").pack(side="left")
        start = next((i for i, m in enumerate(models) if current and m["run_dir"] == current["run_dir"]), best)
        self.choice = tk.IntVar(value=start)
        for i, model in enumerate(models):
            ttk.Radiobutton(choose, text=f"{i + 1}", value=i, variable=self.choice).pack(side="left", padx=4)
        ttk.Button(choose, text="閉じる", command=self.destroy).pack(side="right")
        ttk.Button(choose, text="この学習器を使う", command=self._choose).pack(side="right", padx=6)
        self._render()

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        section = None
        for group, label, values, differs in self.table:
            if self.only_diff.get() and not differs:
                continue
            if group != section:
                section = group
                self.tree.insert("", "end", values=(f"【{group}】",), tags=("section",))
            self.tree.insert("", "end", values=(label, *values), tags=("diff",) if differs else ())

    def _choose(self):
        self.on_choose(self.models[self.choice.get()])
        self.destroy()
