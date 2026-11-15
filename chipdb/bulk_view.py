"""Spreadsheet-style editing of one complete chip history with save preview."""
import tkinter as tk
from tkinter import ttk, messagebox

from chipdb.bulk import Draft
from chipdb.summary_view import table
from shared import ui_theme as theme

HEADINGS = ['種別', '状態', '日付', '担当者', '実験 ID', '操作', 'GAP #']


class BulkEditor(tk.Toplevel):
    def __init__(self, app, document):
        super().__init__(app)
        self.app, self.document = app, document
        self.draft = Draft(document.rows)
        self.anchor = (0, 6)
        self.title(f'表全体を編集 · {document.path.stem}')
        self.geometry('1250x780')
        self.minsize(900, 600)
        self.transient(app)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.columns = [HEADINGS[i] if i < 7 else f'Info {i-6}' for i in range(max(map(len, document.rows), default=7))]
        ttk.Label(self, text=f'{document.path.stem}  /  表全体を編集', font=(theme.UI_BOLD, 18), padding=16).pack(anchor='w')
        ttk.Label(self, text='セルをダブルクリックで編集。行をShift/Ctrlで複数選択し、列の一括置換・同じ値の設定ができます。\n貼り付けはクリックした行と「対象列」を起点に、Excel等のタブ区切りデータを反映します。先頭行のGAPも表示しています。', padding=(16, 0), foreground=theme.MUTED).pack(anchor='w')
        tools = ttk.Frame(self, padding=12)
        tools.pack(fill='x')
        ttk.Label(tools, text='対象列').pack(side='left')
        self.column = ttk.Combobox(tools, values=self.columns, state='readonly', width=13)
        self.column.current(6)
        self.column.pack(side='left', padx=6)
        self.scope = ttk.Combobox(tools, values=['選択行', '全行'], state='readonly', width=10)
        self.scope.current(0)
        self.scope.pack(side='left', padx=6)
        ttk.Label(tools, text='元の値').pack(side='left')
        self.old = ttk.Entry(tools, width=15)
        self.old.pack(side='left', padx=6)
        ttk.Label(tools, text='新しい値').pack(side='left')
        self.new = ttk.Entry(tools, width=15)
        self.new.pack(side='left', padx=6)
        ttk.Button(tools, text='完全一致で置換', command=self.replace).pack(side='left', padx=4)
        ttk.Button(tools, text='同じ値にする', command=lambda: self.replace(fill=True)).pack(side='left')
        bar = ttk.Frame(self, padding=(12, 0))
        bar.pack(fill='x', pady=(0, 8))
        ttk.Button(bar, text='選択列をコピー', command=self.copy).pack(side='left')
        ttk.Button(bar, text='貼り付け (Ctrl+V)', command=self.paste).pack(side='left', padx=6)
        ttk.Button(bar, text='元に戻す', command=self.undo).pack(side='left')
        self.state = ttk.Label(bar, foreground=theme.MUTED)
        self.state.pack(side='right')
        body = ttk.Frame(self, padding=(12, 0))
        body.pack(fill='both', expand=True)
        self.view = table(body, tuple(str(i) for i in range(len(self.columns))))
        self.view.configure(selectmode='extended', show='tree headings')
        self.view.heading('#0', text='行')
        self.view.column('#0', width=55, stretch=False)
        for i, label in enumerate(self.columns):
            self.view.heading(str(i), text=label)
            self.view.column(str(i), width=130, stretch=False)
        self.view.tag_configure('changed', background=theme.mix(theme.FIELD, '#dfb86b', .18))
        self.view.bind('<ButtonRelease-1>', self.point)
        self.view.bind('<Double-1>', self.edit_cell)
        self.view.bind('<Control-v>', self.paste)
        self.view.bind('<Control-c>', self.copy)
        self.view.bind('<Control-z>', lambda e: self.undo())
        footer = ttk.Frame(self, padding=12)
        footer.pack(fill='x')
        ttk.Label(footer, text='変更はまだ下書きです。正しいGAP値を確認してから保存してください。自動採番・推測はしません。', foreground=theme.MUTED).pack(side='left')
        ttk.Button(footer, text='変更内容を確認して保存', style='Accent.TButton', command=self.preview).pack(side='right')
        self.refresh()
        self.grab_set()

    def refresh(self):
        selection = self.view.selection()
        position = self.view.yview()[0]
        self.view.delete(*self.view.get_children())
        changed_rows = {r for r, _, _, _ in self.draft.changes()}
        for i, row in enumerate(self.draft.rows):
            self.view.insert('', 'end', iid=str(i), text=str(i+1), values=row, tags=('changed',) if i in changed_rows else ())
        if selection:
            self.view.selection_set(selection)
        self.view.yview_moveto(position)
        self.state.configure(text=f'変更 {len(self.draft.changes())} セル · 貼付開始 行{self.anchor[0]+1}')

    def point(self, event):
        row, column = self.view.identify_row(event.y), self.view.identify_column(event.x)
        if row and column and column != '#0':
            self.anchor = (int(row), int(column[1:])-1)
            self.column.current(self.anchor[1])
            self.state.configure(text=f'変更 {len(self.draft.changes())} セル · 貼付開始 行{self.anchor[0]+1}')

    def run(self, action):
        try:
            action()
        except (ValueError, UnicodeError, tk.TclError) as exc:
            messagebox.showerror('編集できません', str(exc), parent=self)
            return False
        self.refresh()
        return True

    def target_rows(self):
        if self.scope.current() == 1:
            return list(range(len(self.draft.rows)))
        selected = sorted(int(i) for i in self.view.selection())
        if not selected:
            raise ValueError('対象の行を選択してください。Shiftで範囲選択できます。')
        return selected

    def replace(self, fill=False):
        self.run(lambda: self.draft.replace(self.target_rows(), self.column.current(), self.old.get(), self.new.get(), fill=fill))

    def paste(self, event=None):
        self.run(lambda: self.draft.paste(self.anchor[0], self.column.current(), self.clipboard_get()))
        return 'break'

    def copy(self, event=None):
        def action():
            column = self.column.current()
            rows = self.target_rows()
            self.clipboard_clear()
            self.clipboard_append('\n'.join(self.draft.rows[i][column] if column < len(self.draft.rows[i]) else '' for i in rows))
        self.run(action)
        return 'break'

    def undo(self):
        self.draft.undo()
        self.refresh()

    def edit_cell(self, event):
        self.point(event)
        row, column = self.anchor
        if not self.view.identify_row(event.y) or self.view.identify_column(event.x) == '#0' or column >= len(self.draft.rows[row]):
            return
        dialog = tk.Toplevel(self)
        dialog.title(f'行{row+1} · {self.columns[column]}')
        dialog.transient(self)
        entry = ttk.Entry(dialog, width=65)
        entry.insert(0, self.draft.rows[row][column])
        entry.pack(padx=18, pady=18)
        entry.select_range(0, 'end')
        entry.focus_set()

        def commit(event=None):
            if self.run(lambda: self.draft.apply([((row, column), entry.get())])):
                dialog.destroy()
                self.grab_set()
        ttk.Button(dialog, text='下書きに反映', command=commit).pack(pady=(0, 12))
        entry.bind('<Return>', commit)
        dialog.protocol('WM_DELETE_WINDOW', lambda: (dialog.destroy(), self.grab_set()))
        dialog.grab_set()

    def preview(self):
        changes = self.draft.changes()
        if not changes:
            messagebox.showinfo('変更内容', '変更はありません。', parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title(f'保存前の確認 · {len(changes)}セル')
        dialog.geometry('850x550')
        dialog.transient(self)
        ttk.Label(dialog, text=f'{self.document.path}\n変更前のCSVをバックアップしてから、共有元へ保存します。', padding=12).pack(anchor='w')
        view = table(dialog, ('行', '列', '変更前', '変更後'))
        for row, column, old, new in changes:
            view.insert('', 'end', values=(row+1, self.columns[column], old, new))

        def save():
            try:
                backup = self.document.save(self.draft.rows)
            except (OSError, ValueError, UnicodeError) as exc:
                messagebox.showerror('保存できません', str(exc), parent=dialog)
                return
            dialog.destroy()
            self.app.document = self.document
            self.app.selected_path = self.document.path
            self.app.render()
            self.app.start_index()
            self.app.sync_chip_selection()
            self.app.status.configure(text=f'{len(changes)}セルを保存 · バックアップ: {backup} · 全チップ比較は集計を更新してください')
            self.destroy()
        ttk.Button(dialog, text=f'{len(changes)}セルの変更を保存', style='Accent.TButton', command=save).pack(side='right', padx=12, pady=12)
        ttk.Button(dialog, text='編集に戻る', command=lambda: (dialog.destroy(), self.grab_set())).pack(side='right', pady=12)
        dialog.protocol('WM_DELETE_WINDOW', lambda: (dialog.destroy(), self.grab_set()))
        dialog.grab_set()

    def close(self):
        if not self.draft.changes() or messagebox.askyesno('下書きを破棄', '保存していない変更を破棄して閉じますか？', parent=self):
            self.destroy()
