# -*- coding: utf-8 -*-
"""プロジェクト内のTkinterアプリを起動するランチャー。

起動: launch_apps.bat をダブルクリック、または python launcher.py
カードをクリック(または数字キー 1〜7)で各アプリを別プロセスとして起動する。
ランチャーを閉じても起動済みのアプリは終了しない。
"""

import os
import subprocess
import sys
import tempfile
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent

from shared.ui_theme import (BG, BORDER, DANGER, DIM, HEADER_FROM, HEADER_TO, ICON, MONO, MUTED,  # noqa: E402
                             TEXT, UI, UI_BOLD, mix, round_rect)
from shared import ui_theme  # noqa: E402

CARD = '#172027'
CARD_HOVER = '#1d2932'


@dataclass
class App:
    key: str
    title: str
    description: str
    script: str  # 作業フォルダからの相対パス
    cwd: str     # ROOT からの相対パス
    icon: str
    accent: str

    @property
    def display_path(self):
        return str(Path(self.cwd) / self.script).lstrip('.\\')


# データの流れの順: 転送・整理 → 保存状況の記録 → 解析（ANAL・BNAL）→ ANAL の確認・機械学習 → BNAL の可視化
APPS = [
    App('1', '実験データ整理', '元実験の転送セット作成・照合・コピーと履歴管理',
        r'exmanager\app.py', '.', '\uE8AB', ui_theme.EXMANAGER),
    App('2', 'ファイル管理', 'フォルダ作成・転送・stocked 取り出し・リネーム',
        r'filemanager\app.py', '.', '\uE8B7', ui_theme.FILEMANAGER),
    App('3', '実験ファイルDB', 'サーバーごとのファイル保存状況と解析着手状況',
        r'database\app.py', '.', '\uEE94', ui_theme.DATABASE),
    App('4', '\u30B7\u30B0\u30CA\u30EB\u89E3\u6790', '1st Step\uFF08ANAL\uFF09\u30FB2nd Step\uFF08BNAL\uFF09\u306E\u89E3\u6790\u3068\u7D50\u679C\u30D3\u30E5\u30FC\u30A2',
        r'python\gui_app.py', 'Analyzer', '\uE9F5', ui_theme.ANALYZER),
    App('5', 'TDMSビューア', 'TDMS 波形の表示・ノイズ推定・PDF 出力',
        r'Viewer\view_tdms_signal_tk.py', '.', '\uE9D9', ui_theme.VIEWER),
    App('6', 'MLパネル', '特徴量抽出・統計・学習・混合予測・クラスタリング',
        r'app_tk\main.py', 'ML', '\uEBD2', ui_theme.ML),
    App('7', '\u914D\u5217\u30A2\u30BB\u30F3\u30D6\u30EA', 'BNAL \u306E\u4F4D\u7F6E\u3054\u3068\u306E\u5206\u5E03\u30FB\u30A2\u30BB\u30F3\u30D6\u30EA\u30FBq\u5024\u30FB\u4FEE\u98FE\u7387\u306E\u53EF\u8996\u5316',
        r'Plotting\app.py', '.', '\uE9D2', ui_theme.PLOTTING),
]


def python_for_gui():
    """コンソール窓を出さない pythonw.exe を優先して使う。"""
    exe = Path(sys.executable)
    windowed = exe.with_name('pythonw.exe')
    return str(windowed if windowed.exists() else exe)


class Card(tk.Canvas):
    HEIGHT = 86

    def __init__(self, master, app, on_launch, width):
        super().__init__(master, width=width, height=self.HEIGHT, bg=BG,
                         highlightthickness=0, cursor='hand2')
        self.app, self.on_launch, self.running = app, on_launch, 0
        w, h, a = width, self.HEIGHT, app.accent
        self.shape = round_rect(self, 1, 1, w - 1, h - 1, 16, fill=CARD, outline=BORDER)
        self.bar = round_rect(self, 1, 14, 6, h - 14, 3, fill=a, outline=a)
        round_rect(self, 22, 17, 74, 69, 14, fill=mix(CARD, a, 0.16), outline='')
        self.create_text(48, 43, text=app.icon, font=(ICON, 22), fill=a)
        self.create_text(92, 27, text=app.title, anchor='w', font=(UI_BOLD, 14), fill=TEXT)
        self.create_text(92, 50, text=app.description, anchor='w', font=(UI, 10), fill=MUTED)
        self.create_text(92, 69, text=app.display_path, anchor='w', font=(MONO, 9), fill=DIM)
        self.badge = round_rect(self, w - 172, 30, w - 74, 56, 12, fill=CARD, outline='')
        self.dot = self.create_oval(w - 160, 39, w - 152, 47, fill=CARD, outline='')
        self.badge_text = self.create_text(w - 146, 43, anchor='w', font=(UI, 9), fill=CARD)
        self.key_text = self.create_text(w - 123, 43, text=app.key, font=(MONO, 11), fill=DIM)
        self.arrow = self.create_text(w - 40, 43, text='\uE768', font=(ICON, 16), fill=DIM)
        self.bind('<Enter>', lambda e: self._hover(True))
        self.bind('<Leave>', lambda e: self._hover(False))
        self.bind('<ButtonRelease-1>', lambda e: self.on_launch(self.app))

    def _hover(self, on):
        fill = CARD_HOVER if on else CARD
        self.itemconfigure(self.shape, fill=fill, outline=self.app.accent if on else BORDER)
        self.itemconfigure(self.arrow, fill=self.app.accent if on else DIM)
        if not self.running:
            self.itemconfigure(self.badge, fill=fill)

    def flash(self):
        self.itemconfigure(self.shape, fill=mix(CARD_HOVER, self.app.accent, 0.22))
        self.after(160, lambda: self.itemconfigure(self.shape, fill=CARD_HOVER))

    def set_running(self, count, pulse):
        self.running = count
        a = self.app.accent
        if count:
            label = '起動中' if count == 1 else f'起動中 ×{count}'
            self.itemconfigure(self.badge, fill=mix(CARD, a, 0.18))
            self.itemconfigure(self.dot, fill=a if pulse else mix(CARD, a, 0.45))
            self.itemconfigure(self.badge_text, text=label, fill=a)
            self.itemconfigure(self.key_text, state='hidden')
        else:
            for item in (self.badge, self.dot):
                self.itemconfigure(item, fill=self.itemcget(self.shape, 'fill'))
            self.itemconfigure(self.badge_text, text='')
            self.itemconfigure(self.key_text, state='normal')


class Launcher(tk.Tk):
    WIDTH = 660

    def __init__(self):
        super().__init__()
        self.title('Quantum Workbench ランチャー')
        self.configure(bg=BG)
        self.resizable(False, False)
        self.python = python_for_gui()
        self.processes = {app.key: [] for app in APPS}
        self.pulse = False
        self._build()
        self._center()
        for app in APPS:
            self.bind(app.key, lambda e, a=app: self.launch(a))
        self.bind('a', lambda e: self.launch_all())
        self.bind('<Escape>', lambda e: self.destroy())
        self.after(500, self._poll)

    def _build(self):
        w = self.WIDTH
        header = tk.Canvas(self, width=w, height=112, highlightthickness=0, bg=BG)
        header.pack()
        for x in range(w):
            header.create_line(x, 0, x, 112, fill=mix(HEADER_FROM, HEADER_TO, x / w))
        header.create_text(32, 40, text='Quantum Workbench', anchor='w',
                           font=('Segoe UI Semibold', 20), fill=TEXT)
        header.create_text(34, 76, text='アプリランチャー  ·  カードをクリック、または数字キーで起動',
                           anchor='w', font=(UI, 10), fill='#a9c4b2')
        btn = round_rect(header, w - 268, 38, w - 132, 74, 18, fill='#3c6b4b', outline='#5d9a6f')
        txt = header.create_text(w - 200, 56, text='すべて起動  (A)', font=(UI_BOLD, 10), fill=TEXT)
        for item in (btn, txt):
            header.tag_bind(item, '<Enter>', lambda e: (header.itemconfigure(btn, fill='#4a8159'),
                                                         header.configure(cursor='hand2')))
            header.tag_bind(item, '<Leave>', lambda e: (header.itemconfigure(btn, fill='#3c6b4b'),
                                                         header.configure(cursor='')))
            header.tag_bind(item, '<ButtonRelease-1>', lambda e: self.launch_all())
        ui_theme.close_button(header, w - 24, 56, self.destroy)

        body = tk.Frame(self, bg=BG)
        body.pack(padx=24, pady=(20, 8))
        self.cards = {}
        for app in APPS:
            card = Card(body, app, self.launch, w - 48)
            card.pack(pady=6)
            self.cards[app.key] = card

        footer = tk.Frame(self, bg=BG)
        footer.pack(fill='x', padx=28, pady=(6, 18))
        self.status = tk.Label(footer, text='準備完了', bg=BG, fg=MUTED, font=(UI, 9), anchor='w')
        self.status.pack(side='left')
        tk.Label(footer, text=Path(self.python).name + '  ·  Esc で閉じる', bg=BG, fg=DIM,
                 font=(UI, 9)).pack(side='right')

    def _center(self):
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_width()) // 2
        y = (self.winfo_screenheight() - self.winfo_height()) // 3
        self.geometry(f'+{x}+{y}')

    def launch(self, app):
        if any(proc.poll() is None for proc, _ in self.processes[app.key]):
            self._set_status(f'{app.title} はすでに起動しています', app.accent)
            return
        script = ROOT / app.cwd / app.script
        if not script.exists():
            messagebox.showerror('起動できません', f'ファイルが見つかりません:\n{script}', parent=self)
            return
        log = Path(tempfile.gettempdir()) / f'pyvscode_launcher_{app.key}_{os.getpid()}_{len(self.processes[app.key])}.log'
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        try:
            with open(log, 'wb') as out:
                proc = subprocess.Popen([self.python, str(script)], cwd=ROOT / app.cwd,
                                        stdout=out, stderr=subprocess.STDOUT, env=env)
        except OSError as exc:
            messagebox.showerror('起動できません', str(exc), parent=self)
            return
        self.processes[app.key].append((proc, log))
        self.cards[app.key].flash()
        self._set_status(f'{app.title} を起動しました', app.accent)
        self._refresh()

    def launch_all(self):
        for i, app in enumerate(APPS):
            self.after(i * 300, lambda a=app: self.launch(a))

    def _set_status(self, text, color=MUTED):
        self.status.configure(text=text, fg=color)

    def _poll(self):
        for app in APPS:
            alive = []
            for proc, log in self.processes[app.key]:
                code = proc.poll()
                if code is None:
                    alive.append((proc, log))
                elif code:
                    self._report_failure(app, code, log)
                else:
                    self._set_status(f'{app.title} を終了しました')
                    log.unlink(missing_ok=True)
            self.processes[app.key] = alive
        self.pulse = not self.pulse
        self._refresh()
        self.after(600, self._poll)

    def _report_failure(self, app, code, log):
        try:
            tail = log.read_text(encoding='utf-8', errors='replace').splitlines()[-25:]
        except OSError:
            tail = []
        self._set_status(f'{app.title} がエラーで終了しました (code {code})', DANGER)
        messagebox.showerror(f'{app.title} がエラーで終了しました',
                             '\n'.join(tail or [f'終了コード {code}']) + f'\n\nログ: {log}',
                             parent=self)

    def _refresh(self):
        for key, card in self.cards.items():
            card.set_running(len(self.processes[key]), self.pulse)


def main():
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    Launcher().mainloop()


if __name__ == '__main__':
    main()
