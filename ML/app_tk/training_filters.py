"""Training file-rule editor using the histogram's shared file statistics."""
import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd

from common.file_review import filter_files
from widgets import DataTable


class FileFilterDialog(tk.Toplevel):
    def __init__(self, master, files, rules, on_apply):
        super().__init__(master)
        self.title('学習ファイルの絞り込み（すべてAND）')
        self.geometry('1000x700')
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.files = files
        self.rules = list(rules)
        self.on_apply = on_apply
        ttk.Label(self, text='ファイルごとの統計で選び、そのファイルの全イベントを学習対象にします。'
                  '学習時には既存のイベント範囲条件も適用します。', wraplength=950).pack(padx=10, pady=8)
        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=10)
        self.column = tk.StringVar(value='events')
        columns = ttk.Combobox(bar, textvariable=self.column, values=list(files.columns), state='readonly', width=28)
        columns.pack(side='left')
        columns.bind('<<ComboboxSelected>>', lambda _: self._column_changed())
        self.operator = tk.StringVar(value='>=')
        self.operators = ttk.Combobox(bar, textvariable=self.operator, state='readonly', width=6)
        self.operators.pack(side='left', padx=4)
        self.value = tk.StringVar()
        ttk.Entry(bar, textvariable=self.value, width=20).pack(side='left')
        ttk.Button(bar, text='条件を追加', command=self._add).pack(side='left', padx=6)
        self.hint = ttk.Label(self)
        self.hint.pack(anchor='w', padx=10, pady=4)
        self.rule_list = tk.Listbox(self, height=5, exportselection=False)
        self.rule_list.pack(fill='x', padx=10)
        actions = ttk.Frame(self)
        actions.pack(fill='x', padx=10, pady=4)
        ttk.Button(actions, text='選択した条件を削除', command=self._remove).pack(side='left')
        ttk.Button(actions, text='絞り込みを解除', command=self._reset).pack(side='left', padx=4)
        self.summary = ttk.Label(self)
        self.summary.pack(anchor='w', padx=10, pady=4)
        self.table = DataTable(self, height=10)
        self.table.pack(fill='both', expand=True, padx=10)
        ttk.Button(self, text='この条件を学習に適用', command=self._apply).pack(pady=10)
        self._column_changed()
        self._refresh()

    def _column_changed(self):
        values = self.files[self.column.get()]
        numeric = pd.api.types.is_numeric_dtype(values)
        options = ['>=', '<=', '='] if numeric else ['含む', '=']
        self.operators.configure(values=options)
        self.operator.set(options[0])
        self.hint.configure(text=(f'値の範囲：{values.min():g} ～ {values.max():g}'
                                  if numeric and values.notna().any() else '条件値を入力してください'))

    def _add(self):
        rule = (self.column.get(), self.operator.get(), self.value.get())
        try:
            filter_files(self.files, [rule])
        except (ValueError, TypeError, KeyError) as exc:
            messagebox.showerror('入力エラー', str(exc), parent=self)
            return
        self.rules.append(rule)
        self._refresh()

    def _remove(self):
        for index in reversed(self.rule_list.curselection()):
            del self.rules[index]
        self._refresh()

    def _reset(self):
        self.rules.clear()
        self._refresh()

    def _refresh(self):
        self.rule_list.delete(0, 'end')
        for c, op, value in self.rules:
            self.rule_list.insert('end', f'{c} {op} {value}')
        try:
            selected = filter_files(self.files, self.rules)
        except (ValueError, TypeError, KeyError) as exc:
            self.summary.configure(text=f'条件を見直してください：{exc}')
            self.table.show(None)
            return
        counts = selected.groupby('sample')['events'].agg(['size', 'sum'])
        detail = ' / '.join(f'{name}: {int(row["size"])}ファイル・{int(row["sum"]):,}イベント'
                            for name, row in counts.iterrows())
        self.summary.configure(text=f'対象：{len(selected)} / {len(self.files)} ファイル　{detail}', wraplength=950)
        self.table.show(selected.round(6))

    def _apply(self):
        try:
            selected = filter_files(self.files, self.rules)
            missing = set(self.files['sample']) - set(selected['sample'])
            if missing:
                raise ValueError('条件に一致するファイルがないサンプル：' + '、'.join(sorted(missing)))
        except (ValueError, TypeError, KeyError) as exc:
            messagebox.showerror('入力エラー', str(exc), parent=self)
            return
        self.on_apply(self.rules)
        self.destroy()
