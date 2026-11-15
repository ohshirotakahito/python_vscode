"""Individual summary and asynchronous whole-library comparison views."""
import csv
from datetime import datetime
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from shared import ui_theme as theme
from chipdb.summary import COLUMNS, HISTOGRAMS, summarize, histogram, shares

ACCENT = '#dfb86b'
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


class AllView(ttk.Frame):
    def __init__(self, parent, open_chip, select_chip=None):
        super().__init__(parent, padding=12)
        self.open_chip = open_chip
        self.select_chip = select_chip
        self.selected_path = None
        self.paths = []
        self.records = []
        self.filtered = []
        self.errors = []
        self.version = 0
        self.messages = queue.Queue()
        self.reverse = False
        bar = ttk.Frame(self)
        bar.pack(fill='x')
        self.load_button = ttk.Button(bar, text='全チップを集計 / 更新', style='Accent.TButton', command=self.load)
        self.load_button.pack(side='left')
        ttk.Button(bar, text='表示一覧をCSV出力', command=self.export).pack(side='right')
        self.search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.search, width=25).pack(side='right', padx=8)
        ttk.Label(bar, text='全項目から絞り込み').pack(side='right')
        self.search.trace_add('write', lambda *_: self.refresh())
        self.state = ttk.Label(self, text='「全チップを集計 / 更新」で共有フォルダ内の全CSVを集計します。', style='Muted.TLabel')
        self.state.pack(anchor='w', pady=8)
        self.progress = ttk.Progressbar(self)
        self.progress.pack(fill='x', pady=(0, 8))
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True)
        listing, charts, errors = ttk.Frame(tabs), ttk.Frame(tabs, padding=10), ttk.Frame(tabs)
        tabs.add(listing, text='全チップ一覧')
        tabs.add(charts, text='分布・装置・担当者')
        tabs.add(errors, text='読み込みエラー')
        self.view = table(listing, COLUMNS)
        for col in COLUMNS:
            self.view.heading(col, command=lambda key=col: self.sort(key))
        self.view.bind('<Double-1>', self.open_selected)
        self.view.bind('<<TreeviewSelect>>', self.selection_changed)
        self.error_view = table(errors, ('ファイル', '理由'))
        self.error_view.column('理由', width=600, stretch=True)
        self.chart_kind = ttk.Combobox(charts, state='readonly', values=[h[0] for h in HISTOGRAMS] + ['装置別', '担当者別'], width=25)
        self.chart_kind.current(0)
        self.chart_kind.pack(anchor='w')
        self.chart_kind.bind('<<ComboboxSelected>>', self.draw)
        chart_frame = ttk.Frame(charts)
        chart_frame.pack(fill='both', expand=True, pady=8)
        self.chart = tk.Canvas(chart_frame, bg=theme.FIELD, highlightthickness=0)
        scroll = ttk.Scrollbar(chart_frame, orient='vertical', command=self.chart.yview)
        self.chart.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.chart.pack(fill='both', expand=True)
        self.chart.bind('<Configure>', self.draw)
        ttk.Label(self, text=RULES + '\nクリックで対象チップを選択、ダブルクリックで個別概要を開きます。分布・内訳は絞り込み後のチップが対象です。', style='Muted.TLabel', wraplength=820).pack(anchor='w', pady=8)
        self.after(100, self.poll)

    def reset(self, paths):
        self.version += 1
        self.paths = list(paths)
        self.records, self.errors = [], []
        self.load_button.configure(state='normal')
        self.progress.configure(value=0)
        self.state.configure(text=f'{len(paths)} チップ · 全チップを集計してください')
        self.error_view.delete(*self.error_view.get_children())
        self.refresh()

    def load(self):
        self.version += 1
        version, paths = self.version, list(self.paths)
        self.load_button.configure(state='disabled')
        self.progress.configure(maximum=max(len(paths), 1), value=0)
        self.state.configure(text=f'全チップを読み込み中 · 0 / {len(paths)}')

        def worker():
            records, errors = [], []
            for index, path in enumerate(paths):
                if version != self.version:
                    return
                try:
                    content = path.read_bytes().decode('cp932')
                    record = summarize([line.split(',') for line in content.splitlines()])
                    record['_path'] = str(path)
                    records.append(record)
                except (OSError, ValueError, UnicodeError) as exc:
                    errors.append((str(path), str(exc)))
                if index % 50 == 0:
                    self.messages.put((version, 'progress', index+1))
            self.messages.put((version, 'done', (records, errors)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                version, kind, payload = self.messages.get_nowait()
                if version != self.version:
                    continue
                if kind == 'progress':
                    self.progress.configure(value=payload)
                    self.state.configure(text=f'読み込み中 · {payload} / {len(self.paths)}')
                else:
                    self.records, self.errors = payload
                    self.progress.configure(value=len(self.paths))
                    self.load_button.configure(state='normal')
                    self.error_view.delete(*self.error_view.get_children())
                    for error in self.errors:
                        self.error_view.insert('', 'end', values=error)
                    self.refresh()
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def refresh(self):
        query = self.search.get().casefold()
        self.filtered = [r for r in self.records if not query or any(query in r[c].casefold() for c in COLUMNS)]
        self.view.delete(*self.view.get_children())
        for record in self.filtered:
            self.view.insert('', 'end', iid=record['_path'], values=[record[c] for c in COLUMNS])
        self.sync_selection(self.selected_path)
        if self.records or self.errors:
            self.state.configure(text=f'集計成功 {len(self.records)} / エラー {len(self.errors)} / 表示 {len(self.filtered)} チップ · {datetime.now():%H:%M:%S} · 個別編集後は集計を更新してください')
        self.draw()

    def sort(self, column):
        def key(record):
            try:
                return (0, float(record[column]))
            except ValueError:
                return (1, record[column].casefold())
        self.reverse = not self.reverse
        self.records.sort(key=key, reverse=self.reverse)
        self.refresh()

    def open_selected(self, event=None):
        selected = self.view.selection()
        if selected:
            self.open_chip(Path(selected[0]))

    def selection_changed(self, event=None):
        selected = self.view.selection()
        if selected and Path(selected[0]) != self.selected_path:
            self.selected_path = Path(selected[0])
            if self.select_chip:
                self.select_chip(self.selected_path)

    def sync_selection(self, path):
        self.selected_path = Path(path) if path is not None else None
        iid = str(path) if path is not None else ''
        selected = self.view.selection()
        if iid and self.view.exists(iid):
            if selected != (iid,):
                self.view.selection_set(iid)
            self.view.see(iid)
        elif selected:
            self.view.selection_remove(*selected)

    def draw(self, event=None):
        c = self.chart
        c.delete('all')
        width, height = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        if not self.filtered:
            c.create_text(24, 28, anchor='nw', text='集計後に分布・内訳を表示します', fill=theme.MUTED)
            return
        index = self.chart_kind.current()
        if index >= len(HISTOGRAMS):
            counts = shares(self.filtered, 'Machine' if index == len(HISTOGRAMS) else 'Operator')
            total_height = max(height, len(counts)*30+40)
            c.configure(scrollregion=(0, 0, width, total_height))
            maximum = max(n for _, n in counts)
            for i, (name, count) in enumerate(counts):
                y = 25+i*30
                c.create_text(12, y+8, anchor='w', text=name, fill=theme.TEXT)
                end = 160+(width-240)*count/maximum
                c.create_rectangle(160, y, end, y+18, fill=ACCENT, outline='')
                c.create_text(end+8, y+8, anchor='w', text=str(count), fill=theme.MUTED)
            return
        c.configure(scrollregion=(0, 0, width, height))
        _, column, maximum = HISTOGRAMS[index]
        edges, counts = histogram(self.filtered, column, maximum)
        if not counts:
            c.create_text(24, 28, anchor='nw', text='有効な数値がありません', fill=theme.MUTED)
            return
        peak = max(counts)
        c.create_text(20, 18, anchor='nw', text=f'{column} · 有効数 {sum(counts)} · 50区間 · ' + (f'0 < 値 ≤ {maximum}' if maximum else '全数値'), fill=ACCENT)
        for i in range(5):
            y = 55+(height-100)*i/4
            c.create_line(60, y, width-25, y, fill=theme.BORDER)
            c.create_text(50, y, anchor='e', text=f'{peak*(1-i/4):.0f}', fill=theme.MUTED)
        step = (width-85)/len(counts)
        for i, count in enumerate(counts):
            x = 60+i*step
            c.create_rectangle(x, height-45-(height-100)*count/peak, x+max(step-1, 1), height-45, fill=ACCENT, outline='')
        c.create_text(60, height-22, anchor='w', text=f'{edges[0]:.5g}', fill=theme.MUTED)
        c.create_text(width-25, height-22, anchor='e', text=f'{edges[-1]:.5g}', fill=theme.MUTED)

    def export(self):
        if not self.filtered:
            return
        name = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', initialfile='chip_summary.csv')
        if not name:
            return
        try:
            # Exports must never replace a source database CSV.
            if Path(name).resolve() in {p.resolve() for p in self.paths}:
                raise ValueError('共有元CSVと別の保存先を指定してください。')
            with open(name, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(COLUMNS)
                writer.writerows([r[c] for c in COLUMNS] for r in self.filtered)
        except (OSError, ValueError) as exc:
            messagebox.showerror('書き出し', str(exc), parent=self)
