"""Update controls for Tk and Qt applications."""
from concurrent.futures import ThreadPoolExecutor

from shared import app_update
from shared.app_release import version


def description(state):
    if state.available is None:
        action = ('配布元のバージョン情報はまだ公開されていません。\n'
                  '更新の有無は確認できません。手元のアプリは引き続き使用できます。')
    else:
        action = ('更新があります（共通コードを含む）' if state.behind else '配布元との差分はありません')
    if state.restart_needed:
        action += '\n手元の更新を反映するには再起動してください。'
    available = state.available if state.available is not None else '未公開'
    return (f'起動中: {state.running}\n手元: {state.installed}\n配布元: {available}\n\n{action}')


def tk_controls(root, app_id):
    import tkinter as tk
    from tkinter import messagebox
    running = version(app_id)
    pool = ThreadPoolExecutor(max_workers=1)
    menu = tk.Menu(root, tearoff=False)
    root.configure(menu=menu)
    state = None

    def close():
        handler = root.protocol('WM_DELETE_WINDOW')
        if handler:
            root.tk.call(handler)
        else:
            root.destroy()
        try:
            return not bool(root.winfo_exists())
        except tk.TclError:
            return True

    def restart():
        if messagebox.askyesno('再起動', '作業を保存してから再起動してください。再起動しますか？', parent=root):
            app_update.restart(close)

    def perform(update=False):
        menu.entryconfigure(0, state='disabled')
        menu.entryconfigure(1, state='disabled')
        future = pool.submit(app_update.install, state) if update else pool.submit(app_update.check, app_id, running)

        def poll():
            nonlocal state
            if not future.done():
                root.after(100, poll)
                return
            menu.entryconfigure(0, state='normal')
            try:
                result = future.result()
                if update:
                    menu.entryconfigure(2, state='normal')
                    messagebox.showinfo('更新完了', '更新しました。実行中の各アプリを再起動してください。', parent=root)
                else:
                    state = result
                    menu.entryconfigure(1, state='normal' if state.behind else 'disabled')
                    menu.entryconfigure(2, state='normal' if state.restart_needed else 'disabled')
                    messagebox.showinfo('更新チェック', description(state), parent=root)
            except Exception as exc:
                if version(app_id) != running:
                    menu.entryconfigure(2, state='normal')
                messagebox.showerror('更新できません', str(exc), parent=root)
        root.after(100, poll)

    menu.add_command(label='更新チェック', command=perform)
    menu.add_command(label='更新する', command=lambda: perform(True), state='disabled')
    menu.add_command(label='再起動', command=restart, state='disabled')


def qt_controls(window, app_id):
    from shared.terminal_profile import require
    require(app_id)
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QMessageBox, QPushButton, QToolBar
    running = version(app_id)
    pool = ThreadPoolExecutor(max_workers=1)
    toolbar = QToolBar('アプリの更新', window)
    window.addToolBar(toolbar)
    check = QPushButton('更新チェック')
    update = QPushButton('更新する')
    restart = QPushButton('再起動')
    for button in (check, update, restart):
        toolbar.addWidget(button)
    update.setEnabled(False)
    restart.setEnabled(False)
    state = None
    timer = QTimer(window)

    def perform(install=False):
        check.setEnabled(False)
        update.setEnabled(False)
        future = pool.submit(app_update.install, state) if install else pool.submit(app_update.check, app_id, running)

        def poll():
            nonlocal state
            if not future.done():
                return
            timer.stop()
            timer.timeout.disconnect(poll)
            check.setEnabled(True)
            try:
                result = future.result()
                if install:
                    restart.setEnabled(True)
                    QMessageBox.information(window, '更新完了', '実行中の各アプリを再起動してください。')
                else:
                    state = result
                    update.setEnabled(bool(state.behind))
                    restart.setEnabled(state.restart_needed)
                    QMessageBox.information(window, '更新チェック', description(state))
            except Exception as exc:
                if version(app_id) != running:
                    restart.setEnabled(True)
                QMessageBox.warning(window, '更新できません', str(exc))
        timer.timeout.connect(poll)
        timer.start(100)

    def reboot():
        if QMessageBox.question(window, '再起動', '作業を保存してから再起動してください。再起動しますか？') == QMessageBox.StandardButton.Yes:
            app_update.restart(window.close)
    check.clicked.connect(lambda: perform())
    update.clicked.connect(lambda: perform(True))
    restart.clicked.connect(reboot)
