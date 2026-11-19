"""Individual chip summary view and the shared table helpers (the all-chip comparison is in ledger_view)."""
from pathlib import Path
from tkinter import ttk

from shared import ui_theme as theme
from chipdb.summary import COLUMNS, summarize

ACCENT = '#dfb86b'
COMPARISON_COLUMNS = ('ChipID', 'CSV内ChipID', 'ID確認', *COLUMNS[1:])


def comparison_record(record):
    """Use the selected file's identity, while retaining its declared chip ID."""
    record = dict(record)
    declared = record.get('CSV内ChipID', record['ChipID'])
    record['ChipID'] = Path(record['_path']).stem
    record['CSV内ChipID'] = declared
    record['ID確認'] = '一致' if declared == record['ChipID'] else '不一致（要確認）'
    return record
RULES = 'AXN準拠: Info1の有効範囲 CAL (0,200) / FC (0,3) / MA・TA (0,100)。STDは標本標準偏差。回数は除外前。'


def table(parent, columns):
    frame = ttk.Frame(parent)
    frame.pack(fill='both', expand=True)
    view = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse')
    y = ttk.Scrollbar(frame, orient='vertical', command=view.yview)
    x = ttk.Scrollbar(frame, orient='horizontal', command=view.xview)
    view.configure(yscrollcommand=y.set, xscrollcommand=x.set)
    x.pack(side='bottom', fill='x')
    y.pack(side='right', fill='y')
    view.pack(fill='both', expand=True)
    for col in columns:
        view.heading(col, text=col)
        view.column(col, width=155, stretch=False)
    return view


class SummaryView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        ttk.Label(self, text='チップの全履歴を集計', font=(theme.UI_BOLD, 16)).pack(anchor='w', pady=(0, 10))
        ttk.Label(self, text='実験数 Ex# はGPSET件数、計測数 Run# はEXSV件数です。', style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
        self.view = table(self, ('項目', '値'))
        self.view.column('項目', width=240)
        self.view.column('値', width=440, stretch=True)
        ttk.Label(self, text=RULES, wraplength=800, style='Muted.TLabel').pack(anchor='w', pady=10)

    def clear(self):
        self.view.delete(*self.view.get_children())

    def show(self, rows):
        self.clear()
        try:
            record = summarize(rows)
        except ValueError as exc:
            self.view.insert('', 'end', values=('集計エラー', str(exc)))
            return
        for col, value in record.items():
            self.view.insert('', 'end', values=(col, value or '—'))
