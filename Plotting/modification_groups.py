"""Group editor for modification-rate samples (experiment, sample)."""
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from bnal_modification import sample_label, validate_groups


def edit_groups(parent, sample_keys, groups, selected, on_apply):
    dialog = tk.Toplevel(parent)
    dialog.title('修飾率：グループ名・所属サンプル')
    dialog.geometry('720x640')
    dialog.transient(parent.winfo_toplevel())
    draft = [(name, list(members)) for name, members in groups.items()]
    name = tk.StringVar()
    ttk.Label(dialog, text='グループ名（空欄なら Group A、Group B…）').pack(anchor='w', padx=10)
    ttk.Entry(dialog, textvariable=name).pack(fill='x', padx=10)
    ttk.Label(dialog, text='所属サンプルを選択（Ctrl / Shift で複数選択）').pack(anchor='w', padx=10)
    frame = ttk.Frame(dialog)
    frame.pack(fill='both', expand=True, padx=10)
    samples = tk.Listbox(frame, selectmode='extended', exportselection=False)
    scroll = ttk.Scrollbar(frame, command=samples.yview)
    samples.configure(yscrollcommand=scroll.set)
    samples.pack(side='left', fill='both', expand=True)
    scroll.pack(side='right', fill='y')
    for i, key in enumerate(sample_keys):
        samples.insert('end', sample_label(key))
        if key in selected:
            samples.selection_set(i)
    listing = tk.Listbox(dialog, height=7, exportselection=False)
    listing.pack(fill='x', padx=10, pady=5)
    status = ttk.Label(dialog, text='グループを選択すると編集できます。追加・更新後に「適用」を押してください。', wraplength=680)
    status.pack(fill='x', padx=10)

    def refresh():
        listing.delete(0, 'end')
        for label, members in draft:
            listing.insert('end', f'{label}: {"; ".join(sample_label(k) for k in members)}')

    def put(update=False):
        selection = listing.curselection()
        if update and not selection:
            return
        members = [sample_keys[i] for i in samples.curselection()]
        label = name.get().strip()
        if not label:
            i = 0
            used = {n for j, (n, _) in enumerate(draft) if not update or j != selection[0]}
            while (label := f'Group {chr(65+i) if i < 26 else i+1}') in used:
                i += 1
        candidate = list(draft)
        if update:
            candidate[selection[0]] = (label, members)
        else:
            candidate.append((label, members))
        try:
            validate_groups(candidate)
        except ValueError as error:
            messagebox.showerror('設定エラー', str(error), parent=dialog)
            return
        draft[:] = candidate
        refresh()
        name.set('')
        samples.selection_clear(0, 'end')

    def select(_event):
        chosen = listing.curselection()
        if not chosen:
            return
        label, members = draft[chosen[0]]
        name.set(label)
        samples.selection_clear(0, 'end')
        for i, key in enumerate(sample_keys):
            if key in members:
                samples.selection_set(i)

    def remove():
        for i in reversed(listing.curselection()):
            draft.pop(i)
        refresh()

    def checked():
        result = validate_groups(draft)
        missing = {key for members in result.values() for key in members} - set(sample_keys)
        if missing:
            raise ValueError('未読込の所属サンプルがあります: ' + '; '.join(map(sample_label, sorted(missing))))
        return result

    def apply():
        try:
            result = checked()
        except ValueError as error:
            messagebox.showerror('設定エラー', str(error), parent=dialog)
            return
        on_apply(result)
        dialog.destroy()

    def save():
        try:
            result = checked()
            path = filedialog.asksaveasfilename(parent=dialog, defaultextension='.json', filetypes=[('JSON', '*.json')])
            if path:
                Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        except (ValueError, OSError) as error:
            messagebox.showerror('保存エラー', str(error), parent=dialog)

    def load():
        path = filedialog.askopenfilename(parent=dialog, filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(data, dict) or any(not isinstance(v, list) or any(
                    not isinstance(k, list) or len(k) != 2 or any(not isinstance(x, str) for x in k) for k in v) for v in data.values()):
                raise ValueError('修飾率のグループ設定JSONを指定してください。')
            draft[:] = list(validate_groups(data.items()).items())
            refresh()
        except (ValueError, OSError) as error:
            messagebox.showerror('読込エラー', str(error), parent=dialog)

    listing.bind('<<ListboxSelect>>', select)
    buttons = ttk.Frame(dialog)
    buttons.pack(fill='x', padx=10, pady=8)
    for text, command in [('追加', put), ('選択行を更新', lambda: put(True)), ('削除', remove), ('JSON保存', save), ('JSON読込', load)]:
        ttk.Button(buttons, text=text, command=command).pack(side='left', padx=2)
    ttk.Button(dialog, text='適用してグループ別に評価', command=apply).pack(fill='x', padx=10, pady=(0, 10))
    refresh()
    return dialog
