"""Muted sage palette shared by the desktop panel and classic Tk widgets."""
from tkinter import ttk

BACKGROUND = '#e9f0e9'
SURFACE = '#f8fbf7'
TEXT = '#283e32'
ACCENT = '#416b50'
SELECTED = '#d3e5d3'
BORDER = '#b7cbb9'


def apply_theme(root, font):
    style = ttk.Style(root)
    # Clam allows custom backgrounds on Windows buttons and notebook tabs.
    style.theme_use('clam')
    root.configure(background=BACKGROUND)
    for option, value in {
        '*Font': font, '*Background': BACKGROUND, '*Foreground': TEXT,
        '*Text.background': SURFACE, '*Listbox.background': SURFACE,
        '*Canvas.background': BACKGROUND, '*selectBackground': ACCENT,
        '*selectForeground': '#ffffff', '*insertBackground': TEXT,
        '*highlightBackground': BORDER,
    }.items():
        root.option_add(option, value)
    style.configure('.', font=font, background=BACKGROUND, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=SURFACE, darkcolor=BORDER,
                    troughcolor='#dce6dc', selectbackground=ACCENT,
                    selectforeground='#ffffff')
    style.configure('TButton', background='#dce9dc', padding=(8, 4))
    style.map('TButton', background=[('disabled', BACKGROUND), ('pressed', '#bfd5c2'),
                                    ('active', '#cddfce')],
              foreground=[('disabled', '#87988a')])
    style.configure('TNotebook', background=BACKGROUND, borderwidth=0)
    style.configure('TNotebook.Tab', background='#dce7dc', padding=(14, 6))
    style.map('TNotebook.Tab', background=[('selected', SURFACE), ('active', '#ccdfcf')],
              foreground=[('selected', '#2d573b')])
    style.configure('TLabelframe', bordercolor=BORDER)
    style.configure('TLabelframe.Label', foreground=ACCENT)
    for name in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(name, fieldbackground=SURFACE, arrowcolor=ACCENT)
        style.map(name, fieldbackground=[('disabled', BACKGROUND), ('readonly', SURFACE)],
                  foreground=[('disabled', '#87988a'), ('readonly', TEXT)],
                  selectbackground=[('!disabled', ACCENT)],
                  selectforeground=[('!disabled', '#ffffff')])
    style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE, rowheight=22)
    style.map('Treeview', background=[('selected', ACCENT)],
              foreground=[('selected', '#ffffff')])
    style.configure('Treeview.Heading', background='#dce9dc', foreground=TEXT)
    style.map('Treeview.Heading', background=[('active', SELECTED)])
    style.configure('Horizontal.TProgressbar', background='#608b69', troughcolor='#dce6dc')
    style.configure('TScrollbar', background='#c7d9c9', arrowcolor=ACCENT)
    return style
