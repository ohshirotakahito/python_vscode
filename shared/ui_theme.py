# -*- coding: utf-8 -*-
"""プロジェクト共通のダークテーマ(launcher.py と各Tkinterアプリで共有)。

標準ライブラリのみ使用する。各アプリは起動直後(ウィジェット作成前)に
apply(root, accent) を呼び、必要に応じて header() で上部バナーを置く。
"""

import tkinter as tk
from tkinter import ttk

BG = '#0f1417'          # ウィンドウ背景
PANEL = '#141b20'       # 枠・タブ内の背景
FIELD = '#182129'       # 入力欄・表・テキスト
HOVER = '#1f2b34'
BORDER = '#2a3640'
TEXT = '#e6efe9'
MUTED = '#8b9a92'
DIM = '#5d6b64'
DANGER = '#ff7b72'
HEADER_FROM = '#12241a'
HEADER_TO = '#24452f'

# アプリごとのアクセント色(ランチャーのカードと同じ)
EXMANAGER = '#4fc1b5'
ML = '#8fd18b'
VIEWER = '#6aa8ff'
DATABASE = '#c58af9'
PLOTTING = '#f08c6c'
ANALYZER = '#f27fb2'

UI = 'Yu Gothic UI'
UI_BOLD = 'Yu Gothic UI Semibold'
ICON = 'Segoe Fluent Icons'
MONO = 'Consolas'

# 表の行の色分け(意味ごとの色相をダーク背景になじませる)
_ROW_HUES = {
    'success': '#5fbf77', 'info': '#5b9dff', 'warning': '#e8b45e', 'danger': '#ff6b62',
    'violet': '#b68cf5', 'neutral': '#8b9a92', 'teal': '#4fc1b5',
}


HUES = _ROW_HUES  # 意味ごとの色相(状態の文字色などに使う)


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(a, b))


def row(kind, strength=0.24):
    """Treeview の tag_configure 用の背景色。kind は success/info/warning/danger/violet/neutral/teal。"""
    return mix(FIELD, _ROW_HUES[kind], strength)


STRIPE = mix(FIELD, '#ffffff', 0.025)


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


def apply(root, accent, font=(UI, 10)):
    """ttk と従来の tk ウィジェットにダークテーマを適用し、Style を返す。"""
    select = mix(accent, '#000000', 0.45)
    soft = mix(PANEL, accent, 0.18)
    root.configure(background=PANEL)
    for option, value in {
        '*Font': font, '*Background': PANEL, '*Foreground': TEXT,
        '*activeBackground': HOVER, '*activeForeground': TEXT,
        '*highlightBackground': PANEL, '*highlightColor': accent,
        '*selectBackground': select, '*selectForeground': '#ffffff',
        '*insertBackground': TEXT, '*disabledForeground': DIM,
        '*Text.background': FIELD, '*Listbox.background': FIELD,
        '*Entry.background': FIELD, '*Spinbox.background': FIELD,
        '*Text.relief': 'flat', '*Listbox.relief': 'flat',
        '*Canvas.background': PANEL, '*Toplevel.background': PANEL,
        '*Panedwindow.background': BORDER, '*Menu.background': FIELD,
        '*TCombobox*Listbox.background': FIELD, '*TCombobox*Listbox.foreground': TEXT,
        '*TCombobox*Listbox.selectBackground': select,
        '*TCombobox*Listbox.selectForeground': '#ffffff',
    }.items():
        root.option_add(option, value)

    style = ttk.Style(root)
    # clam はWindowsでもボタン・タブの背景色を変えられる
    style.theme_use('clam')
    style.configure('.', font=font, background=PANEL, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=PANEL, darkcolor=PANEL, troughcolor=FIELD,
                    fieldbackground=FIELD, insertcolor=TEXT, arrowcolor=MUTED,
                    selectbackground=select, selectforeground='#ffffff', focuscolor=accent)
    style.map('.', foreground=[('disabled', DIM)])
    for name in ('TFrame', 'TLabel', 'TCheckbutton', 'TRadiobutton', 'TLabelframe',
                 'TLabelframe.Label', 'TPanedwindow'):
        style.configure(name, background=PANEL)
    style.configure('TLabelframe', bordercolor=BORDER, relief='solid', borderwidth=1)
    style.configure('TLabelframe.Label', foreground=accent, font=(UI_BOLD, font[1]))
    style.configure('Sash', sashthickness=6, gripcount=0, background=BORDER)

    def button(name, normal, active, pressed, fg=TEXT, border=BORDER):
        style.configure(name, background=normal, foreground=fg, bordercolor=border,
                        lightcolor=normal, darkcolor=normal, padding=(10, 4), focusthickness=0)
        style.map(name, background=[('disabled', PANEL), ('pressed', pressed), ('active', active)],
                  lightcolor=[('disabled', PANEL), ('pressed', pressed), ('active', active)],
                  darkcolor=[('disabled', PANEL), ('pressed', pressed), ('active', active)],
                  bordercolor=[('disabled', BORDER), ('active', accent)],
                  foreground=[('disabled', DIM)])

    button('TButton', '#1d2730', HOVER, mix(HOVER, accent, 0.25))
    button('Accent.TButton', mix(PANEL, accent, 0.32), mix(PANEL, accent, 0.45),
           mix(PANEL, accent, 0.55), border=mix(PANEL, accent, 0.6))
    for name, hue in (('Primary.TButton', _ROW_HUES['info']),
                      ('Execute.TButton', _ROW_HUES['success']),
                      ('Conditional.TButton', _ROW_HUES['warning'])):
        button(name, mix(PANEL, hue, 0.22), mix(PANEL, hue, 0.34), mix(PANEL, hue, 0.45),
               border=mix(PANEL, hue, 0.5))

    for name in ('TCheckbutton', 'TRadiobutton'):
        style.configure(name, indicatorbackground=FIELD, indicatorforeground=BG,
                        indicatorcolor=FIELD, focuscolor=PANEL)
        style.map(name, background=[('active', PANEL)],
                  indicatorbackground=[('selected', accent), ('pressed', soft)],
                  indicatorcolor=[('selected', accent), ('pressed', soft)],
                  foreground=[('disabled', DIM)])

    for name in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(name, fieldbackground=FIELD, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=FIELD, darkcolor=FIELD, arrowcolor=accent,
                        background='#1d2730', padding=3)
        style.map(name, fieldbackground=[('disabled', PANEL), ('readonly', FIELD)],
                  foreground=[('disabled', DIM), ('readonly', TEXT)],
                  bordercolor=[('focus', accent)], lightcolor=[('focus', FIELD)],
                  background=[('active', HOVER)],
                  selectbackground=[('!disabled', select)],
                  selectforeground=[('!disabled', '#ffffff')])

    style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(8, 6, 8, 0))
    style.configure('TNotebook.Tab', background=BG, foreground=MUTED, padding=(16, 7),
                    bordercolor=BG, lightcolor=BG)
    style.map('TNotebook.Tab', background=[('selected', PANEL), ('active', HOVER)],
              foreground=[('selected', TEXT), ('active', TEXT)],
              lightcolor=[('selected', accent)], bordercolor=[('selected', BORDER)])

    style.configure('Treeview', background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD, rowheight=24)
    style.map('Treeview', background=[('selected', select)], foreground=[('selected', '#ffffff')])
    style.configure('Treeview.Heading', background='#1b252d', foreground=MUTED,
                    bordercolor=BORDER, lightcolor='#1b252d', darkcolor='#1b252d',
                    font=(UI_BOLD, font[1]), padding=(6, 4))
    style.map('Treeview.Heading', background=[('active', HOVER)], foreground=[('active', TEXT)])

    for orient in ('Horizontal', 'Vertical'):
        style.configure(f'{orient}.TScrollbar', background='#26323b', troughcolor=PANEL,
                        bordercolor=PANEL, lightcolor='#26323b', darkcolor='#26323b',
                        arrowcolor=MUTED, gripcount=0)
        style.map(f'{orient}.TScrollbar', background=[('active', '#33424d')],
                  lightcolor=[('active', '#33424d')], darkcolor=[('active', '#33424d')])
        style.configure(f'{orient}.TProgressbar', background=accent, troughcolor=FIELD,
                        bordercolor=BORDER, lightcolor=accent, darkcolor=accent)
        style.configure(f'{orient}.TScale', background='#26323b', troughcolor=FIELD)

    style.configure('TSeparator', background=BORDER)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Accent.TLabel', foreground=accent)
    style.configure('Danger.TLabel', foreground=DANGER)
    style.configure('Heading.TLabel', font=(UI_BOLD, font[1] + 2))
    style.configure('Status.TLabel', background=FIELD, foreground=MUTED)
    return style


def style_text(widget):
    """tk.Text / ScrolledText など、作成後に色を揃えたい従来ウィジェット用。"""
    widget.configure(background=FIELD, foreground=TEXT, insertbackground=TEXT,
                     selectbackground=BORDER, selectforeground='#ffffff',
                     relief='flat', borderwidth=0, highlightthickness=1,
                     highlightbackground=BORDER)


def close_button(canvas, x2, cy, on_close):
    """Canvas の右端(x2)・縦中央(cy)に「終了」ボタンを描き、クリックで on_close を呼ぶ。"""
    normal, hot = mix(HEADER_FROM, DANGER, 0.18), mix(HEADER_FROM, DANGER, 0.38)
    canvas.delete('close')
    round_rect(canvas, x2 - 96, cy - 16, x2, cy + 16, 16, fill=normal,
               outline=mix(HEADER_FROM, DANGER, 0.55), tags=('close', 'close_bg'))
    canvas.create_text(x2 - 72, cy, text='\uE7E8', font=(ICON, 11), fill=DANGER, tags='close')
    canvas.create_text(x2 - 42, cy, text='終了', font=(UI_BOLD, 10), fill=TEXT, tags='close')

    def hover(on):
        canvas.itemconfigure('close_bg', fill=hot if on else normal)
        canvas.configure(cursor='hand2' if on else '')
    canvas.tag_bind('close', '<Enter>', lambda e: hover(True))
    canvas.tag_bind('close', '<Leave>', lambda e: hover(False))
    # クリック処理を抜けてから閉じる(バインド中にウィンドウを破棄しない)
    canvas.tag_bind('close', '<ButtonRelease-1>', lambda e: canvas.after_idle(on_close))


def header(parent, title, subtitle, icon, accent, height=64, on_close=None):
    """ランチャーと同じグラデーションの上部バナー(Canvas)を作って返す。pack/grid は呼び出し側で行う。

    on_close を渡すと右端に「終了」ボタンを描き、クリックでそれを呼ぶ。
    """
    canvas = tk.Canvas(parent, height=height, highlightthickness=0, background=BG)
    end = mix(HEADER_FROM, accent, 0.22)

    def draw(event=None):
        canvas.delete('all')
        width = max(canvas.winfo_width(), 1)
        for x in range(0, width, 2):
            canvas.create_line(x, 0, x, height, width=2,
                               fill=mix(HEADER_FROM, end, min(x / max(width - 1, 1) * 1.6, 1)))
        canvas.create_line(0, height - 1, width, height - 1, fill=mix(BORDER, accent, 0.35))
        round_rect(canvas, 18, 12, 18 + height - 24, height - 12, 10,
                   fill=mix(HEADER_FROM, accent, 0.22), outline='')
        canvas.create_text(18 + (height - 24) / 2, height / 2, text=icon,
                           font=(ICON, 17), fill=accent)
        canvas.create_text(height + 14, height / 2 - 9, text=title, anchor='w',
                           font=(UI_BOLD, 15), fill=TEXT)
        canvas.create_text(height + 15, height / 2 + 13, text=subtitle, anchor='w',
                           font=(UI, 9), fill='#a9c4b2')
        if on_close is not None:
            close_button(canvas, width - 18, height / 2, on_close)

    canvas.bind('<Configure>', draw)
    return canvas
