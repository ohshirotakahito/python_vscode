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
    if app_id != 'workbench':
        root.configure(menu=menu)
    state = None
    buttons = {}
    status_text = tk.StringVar(root, value='最新版は未確認です')

    def set_enabled(index, enabled):
        value = 'normal' if enabled else 'disabled'
        menu.entryconfigure(index, state=value)
        if index in buttons:
            buttons[index].set_enabled(enabled)
            if index == 2:
                if enabled:
                    buttons[index].pack(side='left', padx=5)
                else:
                    buttons[index].pack_forget()

    def build_panel(parent):
        from shared.ui_theme import UI, UI_BOLD, round_rect, BG, BORDER, TEXT, MUTED, DIM
        panel = tk.Frame(parent, bg='#172027', highlightbackground=BORDER,
                         highlightthickness=1)
        panel.pack(fill='x', padx=28, pady=(8, 12))
        actions = tk.Frame(panel, bg='#172027')
        actions.pack(side='right', padx=14, pady=16)
        info = tk.Frame(panel, bg='#172027')
        info.pack(side='left', fill='x', expand=True, padx=18, pady=14)
        tk.Label(info, text=f'ソフトウェアの更新   ·   version {running}',
                 bg='#172027', fg=TEXT,
                 font=(UI_BOLD, 10), anchor='w').pack(anchor='w')
        tk.Label(info, textvariable=status_text, bg='#172027', fg=MUTED,
                 font=(UI, 9), anchor='w', wraplength=420,
                 justify='left').pack(anchor='w', pady=(4, 0))

        class RoundedButton(tk.Canvas):
            def __init__(self, label, command, enabled):
                super().__init__(actions, width=144, height=38, bg='#172027',
                                 highlightthickness=0, takefocus=enabled)
                self.enabled = enabled
                self.command = command
                self.shape = round_rect(self, 1, 1, 143, 37, 10,
                                        fill='#203b39', outline='#40746c')
                self.label = self.create_text(72, 19, text=label,
                                             font=(UI_BOLD, 10), fill=TEXT)
                self.bind('<ButtonRelease-1>', lambda _: self.invoke())
                self.bind('<Return>', lambda _: self.invoke())
                self.bind('<space>', lambda _: self.invoke())
                self.bind('<Enter>', lambda _: self.paint(True))
                self.bind('<Leave>', lambda _: self.paint())
                self.bind('<FocusIn>', lambda _: self.paint(True))
                self.bind('<FocusOut>', lambda _: self.paint())
                self.paint()

            def paint(self, hover=False):
                self.itemconfigure(self.shape,
                                   fill=('#2c514c' if hover else '#203b39') if self.enabled else '#1a242b',
                                   outline='#4fc1b5' if self.enabled else BORDER)
                self.itemconfigure(self.label, fill=TEXT if self.enabled else DIM)
                self.configure(cursor='hand2' if self.enabled else '')

            def set_enabled(self, enabled):
                self.enabled = enabled
                self.configure(takefocus=enabled)
                self.paint()

            def invoke(self):
                if self.enabled:
                    self.command()

        for index, label, command in (
                (0, '最新版を確認', perform),
                (1, '更新する', lambda: perform(True)),
                (2, '再起動', restart)):
            button = RoundedButton(label, command, index == 0)
            if index < 2:
                button.pack(side='left', padx=5)
            buttons[index] = button

    if app_id == 'workbench':
        root.build_update_panel = build_panel

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
        set_enabled(0, False)
        set_enabled(1, False)
        set_enabled(2, False)
        status_text.set('更新中です…' if update else '最新版を確認しています…')
        future = pool.submit(app_update.install, state) if update else pool.submit(app_update.check, app_id, running)

        def poll():
            nonlocal state
            if not future.done():
                root.after(100, poll)
                return
            set_enabled(0, True)
            try:
                result = future.result()
                if update:
                    state = None
                    status_text.set('更新が完了しました。再起動してください')
                    set_enabled(2, True)
                    messagebox.showinfo('更新完了', '更新しました。実行中の各アプリを再起動してください。', parent=root)
                else:
                    state = result
                    if state.available is None:
                        status_text.set('配布先のバージョン情報は未公開です')
                    elif state.behind:
                        status_text.set(f'最新版 version {state.available} に更新できます')
                    elif state.restart_needed:
                        status_text.set('最新版は適用済みです。再起動してください')
                    else:
                        status_text.set('最新版を使用しています')
                    set_enabled(1, bool(state.behind))
                    set_enabled(2, state.restart_needed)
                    if app_id != 'workbench':
                        messagebox.showinfo('更新チェック', description(state), parent=root)
            except Exception as exc:
                state = None
                status_text.set('更新に失敗しました' if update else '最新版を確認できませんでした')
                if version(app_id) != running:
                    set_enabled(2, True)
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
