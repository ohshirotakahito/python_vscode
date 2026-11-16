"""Burst/signal tables beside the waveform, with single-click time navigation."""
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from waveform_events import detect_file, jump_to_event, preview_bursts, preview_signals, saved_events


class EventPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, width=410, padding=(4, 0, 8, 0))
        self.browser = None
        self.path = None
        self.mode = 'auto'
        self.results = queue.Queue()
        self.cancelled = threading.Event()
        self.saved = None
        self.rows = {'bursts': [], 'signals': []}
        self.pages = {'bursts': 0, 'signals': 0}
        self.page_labels = {}
        self.signature = None
        self.status = tk.StringVar(value='ファイルの表を確認中…')
        ttk.Label(self, textvariable=self.status, wraplength=390).pack(fill='x')
        buttons = ttk.Frame(self)
        buttons.pack(fill='x', pady=5)
        ttk.Button(buttons, text='ANAL保存表', command=self.use_saved).pack(side='left')
        ttk.Button(buttons, text='表示区間の検出', command=self.use_view).pack(side='left', padx=4)
        ttk.Button(buttons, text='全体を検出', command=self.run_detection).pack(side='left')
        controls = ttk.Frame(self)
        controls.pack(fill='x', pady=(0, 5))
        self.gap = tk.StringVar(value='10')
        self.margin = tk.StringVar(value='20')
        for label, variable in [('ピーク間隔 ms', self.gap), ('前後余白 ms', self.margin)]:
            ttk.Label(controls, text=label).pack(side='left')
            ttk.Entry(controls, textvariable=variable, width=5).pack(side='left', padx=3)
        ttk.Label(self, text='プレビューのバースト＝指定ピーク間隔内の2信号以上。\nANAL保存表には保存時の判定を使用します。行クリックで移動・範囲を強調。',
                  wraplength=390).pack(fill='x')
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True, pady=6)
        self.tabs, self.tables = tabs, {}
        for kind, title in [('bursts', 'バースト'), ('signals', 'シグナル')]:
            page = ttk.Frame(tabs)
            tabs.add(page, text=title)
            table = ttk.Treeview(page, columns=('id', 'start', 'duration', 'value', 'baseline'),
                                 show='headings', selectmode='browse', height=13)
            for name, label, width in [('id', '#', 45), ('start', '開始 [s]', 85), ('duration', '長さ [ms]', 85),
                                       ('value', '信号数' if kind == 'bursts' else '信号 [pA]', 85), ('baseline', 'BL [pA]', 85)]:
                table.heading(name, text=label)
                table.column(name, width=width, anchor='e')
            table.grid(row=0, column=0, sticky='nsew')
            scroll = ttk.Scrollbar(page, orient='vertical', command=table.yview)
            scroll.grid(row=0, column=1, sticky='ns')
            horizontal = ttk.Scrollbar(page, orient='horizontal', command=table.xview)
            horizontal.grid(row=1, column=0, sticky='ew')
            table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
            navigation = ttk.Frame(page)
            navigation.grid(row=2, column=0, columnspan=2, sticky='ew')
            ttk.Button(navigation, text='前の1000件', command=lambda k=kind: self.page(k, -1)).pack(side='left')
            ttk.Button(navigation, text='次の1000件', command=lambda k=kind: self.page(k, 1)).pack(side='left', padx=4)
            label = ttk.Label(navigation)
            label.pack(side='left')
            self.page_labels[kind] = label
            page.columnconfigure(0, weight=1)
            page.rowconfigure(0, weight=1)
            table.bind('<<TreeviewSelect>>', lambda _, k=kind: self.select(k))
            self.tables[kind] = table
        self.bind('<Destroy>', self.destroyed, add='+')
        self.after(100, self.poll)

    def destroyed(self, event):
        if event.widget is self:
            self.cancelled.set()

    def fill(self, signals, bursts, text):
        self.signature = None
        self.rows = dict(signals=signals, bursts=bursts)
        for index, kind in enumerate(('bursts', 'signals')):
            self.pages[kind] = 0
            self.render_page(kind)
            self.tabs.tab(index, text=f'{"バースト" if kind == "bursts" else "シグナル"} ({len(self.rows[kind])})')
        self.status.set(text)

    def render_page(self, kind):
        table = self.tables[kind]
        table.delete(*table.get_children())
        first = self.pages[kind] * 1000
        last = min(first + 1000, len(self.rows[kind]))
        for i in range(first, last):
            row = self.rows[kind][i]
            values = [row.get(key) for key in ('id', 'start', 'duration_ms', 'value', 'baseline')]
            table.insert('', 'end', iid=str(i), values=['—' if v is None else f'{v:.6g}' for v in values])
        self.page_labels[kind].configure(text=f'{first + 1 if last else 0}–{last} / {len(self.rows[kind])}')

    def page(self, kind, direction):
        maximum = max(0, (len(self.rows[kind]) - 1) // 1000)
        self.pages[kind] = max(0, min(maximum, self.pages[kind] + direction))
        self.render_page(kind)

    def on_draw(self, browser, pulses, offset):
        self.browser = browser
        path = browser.current_tdms_path() if browser.tdms_files else None
        if path != self.path:
            self.cancelled.set()
            self.cancelled = threading.Event()
            token = self.cancelled
            self.path, self.saved, self.mode = path, None, 'auto'
            self.fill([], [], 'ANAL保存表を確認中…')
            def load():
                try:
                    self.results.put((token, 'saved', saved_events(path)))
                except Exception as error:
                    self.results.put((token, 'error', error))
            if path:
                threading.Thread(target=load, daemon=True).start()
        if self.mode not in ('auto', 'view') or self.saved is not None and self.mode == 'auto':
            return
        signature = (path, offset, browser.chunk_size, browser.detection_enabled,
                     tuple((p['start_index'], p['end_index']) for p in pulses), self.gap.get(), self.margin.get())
        if self.signature == signature:
            return
        if browser.detection_enabled:
            try:
                signals = preview_signals(pulses, browser.data, offset, browser.dt)
                bursts = preview_bursts(signals, float(self.gap.get()), float(self.margin.get()), browser.n*browser.dt)
                self.fill(signals, bursts, 'ビューア検出プレビュー：現在の表示区間（ANAL保存結果とは別）')
            except ValueError as error:
                self.status.set(str(error))
        else:
            self.fill([], [], '生波形：検出オフ。「表示区間の検出」または「全体を検出」で一覧を作成できます。')
        self.signature = signature

    def use_saved(self):
        if self.saved is None:
            self.status.set('このファイルにはANALのS/B Tableがありません。生TDMSでは検出プレビューを使用してください。')
            return
        self.mode = 'saved'
        self.cancelled.set()
        self.cancelled = threading.Event()
        self.fill(self.saved['signals'], self.saved['bursts'],
                  f'ANAL保存結果：ファイル全体（時間が不正な行 {self.saved["skipped"]}件を除外）')

    def use_view(self):
        if self.browser:
            self.cancelled.set()
            self.cancelled = threading.Event()
            self.mode, self.signature = 'view', None
            self.browser.detection_enabled = True
            if hasattr(self.browser, '_tk_detection_var'):
                self.browser._tk_detection_var.set(True)
            self.browser.draw()

    def run_detection(self):
        if self.browser is None:
            return
        try:
            gap, margin = float(self.gap.get()), float(self.margin.get())
            preview_bursts([], gap, margin)
        except ValueError as error:
            self.status.set(str(error))
            return
        self.cancelled.set()
        token = self.cancelled = threading.Event()
        browser = self.browser
        settings = {key: getattr(browser, key) for key in ('chunk_size', 'baseline_window', 'baseline_percentile',
            'sg_window', 'sg_poly', 'pad_for_std', 'noise_method', 'threshold_k1', 'threshold_k2', 'min_width_ms')}
        data, dt = browser.data, browser.dt
        self.mode = 'full'
        self.status.set('ファイル全体の検出プレビューを作成中…（ANAL処理・元データ保存なし）')
        def task():
            try:
                rows = detect_file(data, dt, settings, token,
                    lambda progress: self.results.put((token, 'progress', f'全体の検出プレビュー {progress:.0%}')))
                if rows is not None:
                    self.results.put((token, 'full', (rows, preview_bursts(rows, gap, margin, len(data)*dt))))
            except Exception as error:
                self.results.put((token, 'error', error))
        threading.Thread(target=task, daemon=True).start()

    def select(self, kind):
        selected = self.tables[kind].selection()
        if not selected or not self.browser:
            return
        try:
            jump_to_event(self.browser, self.rows[kind][int(selected[0])])
        except (ValueError, IndexError) as error:
            messagebox.showerror('イベントの時間範囲', str(error), parent=self)

    def poll(self):
        try:
            while True:
                token, kind, payload = self.results.get_nowait()
                if token is not self.cancelled or token.is_set():
                    continue
                if kind == 'saved':
                    self.saved = payload
                    if payload is not None and self.mode == 'auto':
                        self.use_saved()
                elif kind == 'full' and self.mode == 'full':
                    self.fill(*payload, 'ビューア検出プレビュー：ファイル全体（ANAL保存結果とは別）')
                else:
                    self.status.set(str(payload))
        except queue.Empty:
            pass
        self.after(100, self.poll)
