"""Check-in and metadata editing against the AXN shared chip master."""
import tkinter as tk
from tkinter import ttk, messagebox

from chipdb.registry import Store, FIELDS, STATES, find, normalize_code
from chipdb.summary_view import table
from shared import ui_theme as theme


class RegistryView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.data = dict(chips=[], revision=0)
        self.store = None
        self.rows = []
        bar = ttk.Frame(self)
        bar.pack(fill='x', pady=(0, 10))
        ttk.Button(bar, text='＋ チップをチェックイン', style='Accent.TButton', command=lambda: self.edit(new=True)).pack(side='left')
        ttk.Button(bar, text='選択チップの管理情報を編集', command=self.edit).pack(side='left', padx=8)
        self.state = ttk.Label(self, style='Muted.TLabel')
        self.state.pack(anchor='w', pady=(0, 8))
        self.view = table(self, ('チップID', '状態', '保管場所', '使用者', '作製日', '使用開始日', '履歴'))
        self.view.bind('<Double-1>', lambda e: self.edit())
        ttk.Label(self, text='チェックインは測定前のチップ登録です。左の検索欄で台帳も絞り込めます。\n測定履歴は「操作履歴・編集」で行を選び「選択行を編集」。管理情報はAXNと同じ共有台帳に保存します。', style='Muted.TLabel', wraplength=800).pack(anchor='w', pady=12)

    def reload(self, db):
        self.store = Store(db)
        try:
            self.data = self.store.load()
            self.state.configure(text=f'AXN共有台帳 · revision {self.data["revision"]}')
            self.enabled = True
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            self.data = dict(chips=[], revision=0)
            self.enabled = False
            self.state.configure(text=f'台帳を読み込めません（編集停止）: {exc}')

    def refresh(self, paths):
        self.rows = []
        self.view.delete(*self.view.get_children())
        chips = {c['code'].casefold(): c for c in self.data['chips']}
        for path in paths:
            chip = chips.get(path.stem.casefold(), dict(code=path.stem))
            self.rows.append(chip)
            self.view.insert('', 'end', iid=str(len(self.rows)-1), values=(chip['code'], chip.get('state', '未登録'),
                chip.get('location', ''), chip.get('user', chip.get('owner', '')), chip.get('fabricated_on', ''),
                chip.get('use_started_on', ''), 'あり' if path in self.app.history_paths else '測定前・履歴なし'))

    def edit_current(self):
        code = self.app.document.path.stem if self.app.document else None
        self.edit(code=code)

    def edit(self, new=False, code=None):
        if not self.store or not self.enabled:
            messagebox.showerror('チップ台帳', '共有フォルダと台帳を再読み込みしてください。', parent=self)
            return
        if not new and code is None:
            selected = self.view.selection()
            if not selected:
                messagebox.showinfo('チップ台帳', '台帳から編集するチップを選択してください。', parent=self)
                return
            code = self.rows[int(selected[0])]['code']
        try:
            chip = find(self.data, code) if code else None
        except ValueError as exc:
            messagebox.showerror('チップ台帳', str(exc), parent=self)
            return
        chip = chip or dict(code=code or '', state='保管中')
        store, revision = self.store, self.data['revision']
        dialog = tk.Toplevel(self)
        dialog.title('チップをチェックイン' if new else 'チップの管理情報を編集')
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        fields = {}
        for index, (key, label) in enumerate({'code': 'チップID', **FIELDS}.items()):
            ttk.Label(dialog, text=label).grid(row=index, column=0, sticky='w', padx=18, pady=6)
            value = chip.get(key, '')
            widget = ttk.Combobox(dialog, state='readonly', values=STATES, width=48) if key == 'state' else ttk.Entry(dialog, width=50)
            if key == 'state':
                widget.set(value)
            else:
                widget.insert(0, value)
                if key == 'code' and not new:
                    widget.configure(state='readonly')
            widget.grid(row=index, column=1, padx=18, pady=6)
            fields[key] = widget
        ttk.Label(dialog, text='日付は YYYY-MM-DD（未定は空欄）。測定履歴やGPSETは作成しません。\n新規IDの区切り文字（_・=など）はAXNと同じく「-」に統一します。', style='Muted.TLabel').grid(row=9, columnspan=2, padx=18, pady=10)

        def save():
            try:
                store.save_chip(fields['code'].get(), {k: fields[k].get().strip() for k in FIELDS}, revision, new=new)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
                messagebox.showerror('保存できません', str(exc), parent=dialog)
                return
            saved_code = normalize_code(fields['code'].get()) if new else fields['code'].get().strip()
            dialog.destroy()
            self.app.scan()
            self.app.query.set(saved_code)
            self.app.tabs.select(self)
            self.app.status.configure(text=f'{saved_code} · チップ台帳を保存しました')
        buttons = ttk.Frame(dialog)
        buttons.grid(row=10, columnspan=2, sticky='e', padx=18, pady=12)
        ttk.Button(buttons, text='キャンセル', command=dialog.destroy).pack(side='left', padx=8)
        ttk.Button(buttons, text='チェックイン' if new else '変更を保存', style='Accent.TButton', command=save).pack(side='left')
        dialog.grab_set()
