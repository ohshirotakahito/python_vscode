"""Preview duplicate merges before updating the shared ChipDB."""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from chipdb import cleanup
from chipdb.store import db_folder
from chipdb.summary_view import table


def open_cleanup(app):
    window = tk.Toplevel(app)
    window.title('チップ履歴の重複・ID整理')
    window.geometry('1100x640')
    window.transient(app)
    ttk.Label(window, text='同名_DESKTOP…・(2)・(3)の履歴を比較します。列の空欄を補完し、固有履歴を残して正規ファイルに統合します。\n'
              '非空欄の値が食い違う組は自動統合しません。バックアップ後に重複CSVを削除し、チップ台帳の重複登録も統合します。', padding=10).pack(fill='x')
    status = ttk.Label(window, text='比較中…', padding=10)
    status.pack(fill='x')
    view = table(window, ('保存するID', '入力ファイル数', '削除する重複数', '結果'))
    view.configure(selectmode='extended')
    view.column('結果', width=550)
    detail = tk.Text(window, height=7, wrap='word')
    detail.pack(fill='x', padx=10, pady=4)
    toolbar = ttk.Frame(window, padding=10)
    toolbar.pack(fill='x')
    plans, events = [], queue.Queue()
    state = {'busy': True}
    folder = db_folder(app.root_path)

    def details(*_):
        detail.configure(state='normal')
        detail.delete('1.0', 'end')
        for key in view.selection():
            plan = plans[int(key)]
            detail.insert('end', f"保存先: {plan['target']}\n入力: " + '\n'.join(plan['sources']) + '\n' +
                '\n'.join(plan['notes']) + ('\n' + plan['error'] if plan['error'] else '') + '\n\n')
        detail.configure(state='disabled')
    view.bind('<<TreeviewSelect>>', details)

    def execute():
        selected = [plans[int(key)] for key in view.selection() if not plans[int(key)]['error']]
        if state['busy'] or not selected:
            return
        count = sum(len(p['remove']) for p in selected)
        if not messagebox.askyesno('統合して重複を削除',
                f'{len(selected)}組を統合し、重複ファイル{count}個を削除します。\n'
                'チップ台帳chip_info.jsonの重複登録も正規IDへ統合します。\n'
                '元CSV・元台帳のバックアップと処理記録をWorkbenchBackupsに保存します。実行しますか？', parent=window):
            return
        state['busy'] = True
        button.configure(state='disabled')
        status.configure(text='バックアップ・統合を実行中…')

        def worker():
            try:
                events.put(('applied', cleanup.apply(folder, selected)))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    button = ttk.Button(toolbar, text='選択した統合を実行', command=execute, state='disabled')
    button.pack(side='left')
    ttk.Button(toolbar, text='統合可能な組をすべて選択', command=lambda: view.selection_set(
        [str(i) for i, plan in enumerate(plans) if not plan['error']])).pack(side='left', padx=8)

    def close():
        if state['busy']:
            messagebox.showinfo('処理中', '比較・統合が終了してから閉じてください。', parent=window)
        else:
            window.after_cancel(state['poll'])
            window.destroy()
    ttk.Button(toolbar, text='閉じる', command=close).pack(side='right')
    window.protocol('WM_DELETE_WINDOW', close)

    def poll():
        try:
            kind, payload = events.get_nowait()
            state['busy'] = False
            if kind == 'preview':
                plans.extend(payload)
                for i, plan in enumerate(plans):
                    view.insert('', 'end', iid=str(i), values=(plan['code'], len(plan['sources']),
                        len(plan.get('remove', [])), plan['error'] or '統合・修正可能'))
                status.configure(text=f'{len(plans)}組 / 要確認 {sum(bool(p["error"]) for p in plans)}組。対象を選んで実行してください。')
                button.configure(state='normal')
            elif kind == 'applied':
                status.configure(text=f'統合完了。バックアップ: {payload}')
                app.scan()
            else:
                status.configure(text='停止: ' + str(payload))
                messagebox.showerror('重複整理', str(payload), parent=window)
        except queue.Empty:
            pass
        state['poll'] = window.after(100, poll)

    def compare():
        try:
            events.put(('preview', cleanup.preview(folder)))
        except Exception as exc:
            events.put(('error', str(exc)))
    threading.Thread(target=compare, daemon=True).start()
    state['poll'] = window.after(100, poll)
    window.grab_set()
