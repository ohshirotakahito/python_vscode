"""Searchable feature selection dialog; edits are applied only on save."""
import tkinter as tk
from tkinter import messagebox, ttk

from common.training_features import feature_catalog
from widgets import CheckList


def choose_training_features(parent, feature_set, fc_mode, selected, on_save):
    try:
        columns = feature_catalog(feature_set, fc_mode)
    except (ImportError, ValueError) as error:
        messagebox.showerror('特徴量一覧を取得できません', str(error), parent=parent)
        return
    draft = set(columns if selected is None else selected)
    dialog = tk.Toplevel(parent)
    dialog.title('学習に使う特徴量を選択 / 除外')
    dialog.geometry('850x650')
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()
    ttk.Label(dialog, text='クリックで使用 / 除外を切り替えます。検索しても他の項目の選択は保持します。\n'
              'value__ で始まる項目はtsfresh特徴量です。自動選択が有効な場合、選択した候補からさらに絞り込みます。',
              wraplength=800).pack(fill='x', padx=10, pady=8)
    query = tk.StringVar()
    ttk.Label(dialog, text='特徴量名で検索').pack(anchor='w', padx=10)
    ttk.Entry(dialog, textvariable=query).pack(fill='x', padx=10)
    count = ttk.Label(dialog)
    count.pack(anchor='w', padx=10, pady=4)
    visible = []

    def changed(names):
        draft.difference_update(visible)
        draft.update(names)
        count.configure(text=f'使用する特徴量: {len(draft)} / {len(columns)}')

    choices = CheckList(dialog, height=17, on_change=changed)
    choices.pack(fill='both', expand=True, padx=10, pady=6)

    def refresh(*_args):
        nonlocal visible
        visible = [c for c in columns if query.get().casefold() in c.casefold()]
        # Populate before notifying so hidden selections remain intact.
        callback = choices._on_change
        choices._on_change = None
        choices.set_items(visible, keep_selection=False)
        for index, name in enumerate(visible):
            if name in draft:
                choices.listbox.selection_set(index)
        choices._on_change = callback
        choices._changed()

    def select_visible(use):
        draft.update(visible) if use else draft.difference_update(visible)
        refresh()

    def save():
        if not draft:
            messagebox.showerror('特徴量を選択してください', '学習に使う特徴量を1つ以上選択してください。', parent=dialog)
            return
        on_save(None if draft == set(columns) else [c for c in columns if c in draft])
        dialog.destroy()

    bar = ttk.Frame(dialog)
    bar.pack(fill='x', padx=10, pady=10)
    ttk.Button(bar, text='表示中をすべて使用', command=lambda: select_visible(True)).pack(side='left')
    ttk.Button(bar, text='表示中をすべて除外', command=lambda: select_visible(False)).pack(side='left', padx=5)
    ttk.Button(bar, text='保存して閉じる', command=save).pack(side='right')
    ttk.Button(bar, text='キャンセル', command=dialog.destroy).pack(side='right', padx=5)
    query.trace_add('write', refresh)
    refresh()
