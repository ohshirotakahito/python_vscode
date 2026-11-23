"""Cross-operation comparison with an always-visible ChipID selector. Tk/stdlib only."""
import csv
from datetime import date
from pathlib import Path
import math
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from chipdb.axis import nice_ticks, tick_label
from chipdb.operation_comparison import (KINDS, REPRESENTATIVES, chip_points, correlations, density_colour, density_grid,
                                          metric_label, observations, count_matches,
                                          representative_limits, within_representative_limits)
from chipdb.store import Document
from chipdb.trend_view import COLOURS, natural_key
from shared import ui_theme as theme

DISPLAY_MODES = ('散布図', '密度', '散布図＋密度')


class ComparisonView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.chosen, self.histories, self.cache = [], {}, {}
        self.messages, self.generation = queue.Queue(), 0
        self.records, self.record_page = [], 0
        self.errors, self.points = [], []
        self.valid_configuration = True
        split = ttk.Panedwindow(self, orient='horizontal')
        split.pack(fill='both', expand=True)
        left, right = ttk.Frame(split, width=320), ttk.Frame(split)
        split.add(left, weight=0)
        split.add(right, weight=1)
        ttk.Label(left, text='チップID（Ctrl / Shiftで複数選択）').pack(anchor='w')
        self.search = tk.StringVar()
        ttk.Entry(left, textvariable=self.search).pack(fill='x', pady=5)
        self.search.trace_add('write', lambda *_: self.refresh_list())
        listing = ttk.Frame(left)
        listing.pack(fill='both', expand=True)
        self.chips = ttk.Treeview(listing, columns=('chip', *KINDS), show='headings', selectmode='extended')
        for key in ('chip', *KINDS):
            self.chips.heading(key, text='チップID' if key == 'chip' else key,
                               command=lambda k=key: self.sort_list(k))
            self.chips.column(key, width=140 if key == 'chip' else 43, anchor='w' if key == 'chip' else 'e')
        self.chips.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.chips.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=self.chips.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.chips.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.sort = ('chip', False)
        self.chips.bind('<<TreeviewSelect>>', self.selected)
        # 左の一覧は複数選択専用（クリックで上部のチップは変えない）。ダブルクリックだけチップ概要へ
        self.chips.bind('<Double-1>', lambda e: self.open_row(self.chips.identify_row(e.y)))
        ttk.Button(left, text='選択をすべて解除', command=self.clear).pack(fill='x', pady=5)
        self.selection_text = ttk.Label(left, wraplength=310)
        self.selection_text.pack(fill='x')
        ttk.Button(left, text='件数条件に合うチップを選択', command=self.select_by_count).pack(fill='x', pady=4)
        jump = ttk.Frame(left)
        jump.pack(fill='x', pady=(6, 0))
        ttk.Button(jump, text='上部のチップの概要へ', command=lambda: self.go_to(self.app.overview)).pack(side='left', fill='x', expand=True)
        ttk.Button(jump, text='台帳へ', command=lambda: self.go_to(self.app.ledger)).pack(side='left', fill='x', expand=True, padx=(4, 0))
        ttk.Label(left, text='代表値一覧の行・散布図の点を選ぶと上部のチップになります。ダブルクリックでチップ概要へ。',
                  foreground=theme.MUTED, wraplength=310).pack(fill='x', pady=(4, 0))
        controls = ttk.Frame(right)
        controls.pack(fill='x', pady=5)
        self.xkind, self.ykind = tk.StringVar(value='FC'), tk.StringVar(value='MA')
        self.xmetric, self.ymetric = tk.StringVar(value='1'), tk.StringVar(value='1')
        self.method = tk.StringVar(value=REPRESENTATIVES[0])
        for title, kind, metric in [('X', self.xkind, self.xmetric), ('Y', self.ykind, self.ymetric)]:
            ttk.Label(controls, text=title).pack(side='left')
            ttk.Combobox(controls, textvariable=kind, values=KINDS, state='readonly', width=5).pack(side='left', padx=4)
            ttk.Label(controls, text='Info').pack(side='left')
            ttk.Combobox(controls, textvariable=metric, values=list(map(str, range(1, 7))),
                         state='readonly', width=3).pack(side='left', padx=(0, 10))
        ttk.Combobox(controls, textvariable=self.method, values=REPRESENTATIVES, state='readonly', width=12).pack(side='left')
        for variable in (self.xkind, self.ykind, self.xmetric, self.ymetric, self.method):
            variable.trace_add('write', lambda *_: self.plot())
        period = ttk.Frame(right)
        period.pack(fill='x')
        self.since, self.until = tk.StringVar(), tk.StringVar()
        self.valid_only = tk.BooleanVar(value=False)
        ttk.Label(period, text='期間 YYYY-MM-DD（空欄＝全履歴）').pack(side='left')
        for variable in (self.since, self.until):
            ttk.Entry(period, textvariable=variable, width=11).pack(side='left', padx=4)
        ttk.Checkbutton(period, text='Info1はAXN集計範囲のみ', variable=self.valid_only, command=self.plot).pack(side='left')
        ttk.Button(period, text='適用', command=self.plot).pack(side='left', padx=4)
        ttk.Button(period, text='CSV出力', command=self.export).pack(side='right')
        display = ttk.Frame(right)
        display.pack(fill='x', pady=5)
        self.display_mode = tk.StringVar(value=DISPLAY_MODES[0])
        ttk.Label(display, text='表示').pack(side='left')
        ttk.Combobox(display, textvariable=self.display_mode, values=DISPLAY_MODES, state='readonly',
                     width=12).pack(side='left', padx=(5, 4))
        self.density_bins = tk.StringVar(value='40')
        ttk.Label(display, text='密度のマス数').pack(side='left')
        ttk.Spinbox(display, textvariable=self.density_bins, from_=5, to=150, width=5).pack(side='left', padx=(5, 14))
        for variable in (self.display_mode, self.density_bins):
            variable.trace_add('write', lambda *_: self.plot())
        self.point_size = tk.StringVar(value='6')
        ttk.Label(display, text='点の直径 (px)').pack(side='left')
        ttk.Spinbox(display, textvariable=self.point_size, from_=2, to=30, width=5).pack(side='left', padx=5)
        self.point_size.trace_add('write', lambda *_: self.plot())
        self.count_enabled = tk.BooleanVar(value=False)
        self.count_scope = tk.StringVar(value='XとY両方')
        self.count_operator = tk.StringVar(value='>')
        self.count_threshold = tk.StringVar(value='5')
        ttk.Checkbutton(display, text='件数で絞る', variable=self.count_enabled, command=self.plot).pack(side='left', padx=5)
        ttk.Combobox(display, textvariable=self.count_scope, values=('XとY両方', 'Xのみ', 'Yのみ', 'XまたはY'),
                     state='readonly', width=12).pack(side='left')
        ttk.Combobox(display, textvariable=self.count_operator, values=('>', '>='), state='readonly', width=3).pack(side='left', padx=4)
        ttk.Spinbox(display, textvariable=self.count_threshold, from_=0, to=100000, width=6).pack(side='left')
        for variable in (self.count_scope, self.count_operator, self.count_threshold):
            variable.trace_add('write', lambda *_: self.plot())
        ttk.Label(display, text='一覧は全記録件数／散布図は条件適用後の有効件数', foreground=theme.MUTED).pack(side='left', padx=8)
        ranges = ttk.Frame(right)
        ranges.pack(fill='x', pady=(0, 5))
        ttk.Label(ranges, text='代表値の範囲').pack(side='left', padx=(0, 6))
        self.xmin, self.xmax, self.ymin, self.ymax = (tk.StringVar() for _ in range(4))
        for label, variable in (('X下限', self.xmin), ('X上限', self.xmax), ('Y下限', self.ymin), ('Y上限', self.ymax)):
            ttk.Label(ranges, text=label).pack(side='left')
            entry = ttk.Entry(ranges, textvariable=variable, width=9)
            entry.pack(side='left', padx=(3, 8))
            entry.bind('<Return>', self.plot)
        ttk.Button(ranges, text='範囲を適用', command=self.plot).pack(side='left')
        ttk.Button(ranges, text='範囲を解除', command=self.reset_ranges).pack(side='left', padx=5)
        ttk.Label(ranges, text='空欄＝制限なし・境界を含む', foreground=theme.MUTED).pack(side='left')
        self.status = ttk.Label(right, wraplength=1100, foreground=theme.MUTED)
        self.status.pack(fill='x', pady=5)
        self.legend = ttk.Frame(right)
        self.legend.pack(fill='x')
        content = ttk.Panedwindow(right, orient='horizontal')
        content.pack(fill='both', expand=True)
        graph = ttk.Frame(content, width=480)
        tables = ttk.Frame(content, width=440)
        graph.pack_propagate(False)
        tables.pack_propagate(False)
        content.add(graph, weight=1)
        content.add(tables, weight=1)
        self.chart = tk.Canvas(graph, background=theme.FIELD, highlightthickness=0, width=480, height=400)
        self.chart.pack(fill='both', expand=True)
        self.chart.bind('<Configure>', self.plot)
        tabs = ttk.Notebook(tables)
        tabs.pack(fill='both', expand=True)
        self.data_tabs = tabs
        summary_page, detail_page = ttk.Frame(tabs), ttk.Frame(tabs)
        tabs.add(summary_page, text='代表値一覧')
        tabs.add(detail_page, text='履歴明細（点をクリックして確認）')
        self.representatives = self.make_table(summary_page, ('チップID', 'X代表値', 'Y代表値', 'X件数', 'Y件数'))
        self.details = self.make_table(detail_page, ('チップID', '操作', '指標', '値', '日時', '日時の由来', '実験ID', 'GAP', '測定者', 'CSV行'))
        self.representatives.bind('<<TreeviewSelect>>', self.representative_selected)
        self.representatives.bind('<Double-1>', lambda e: self.open_point(self.representatives.identify_row(e.y)))
        pages = ttk.Frame(detail_page)
        pages.pack(fill='x')
        ttk.Button(pages, text='前の500件', command=lambda: self.page(-1)).pack(side='left')
        ttk.Button(pages, text='次の500件', command=lambda: self.page(1)).pack(side='left', padx=4)
        self.page_text = ttk.Label(pages)
        self.page_text.pack(side='left')
        ttk.Label(right, text='1点＝1チップの代表値。最初・最後は履歴の記録順。点をクリックすると右側に元記録を表示します。',
                  wraplength=1100).pack(fill='x', pady=4)
        self.after(100, self.poll)

    @staticmethod
    def make_table(parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        table = ttk.Treeview(frame, columns=columns, show='headings', height=5)
        for column in columns:
            table.heading(column, text=column)
            table.column(column, width=130 if column != '実験ID' else 320)
        table.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=table.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return table

    def refresh_list(self):
        self.chosen = [p for p in self.chosen if p in self.app.history_paths]
        text = self.search.get().casefold().strip()
        paths = [p for p in self.app.history_paths if text in p.stem.casefold()]
        column, reverse = self.sort
        def key(path):
            entry = self.app.entries.get(path)
            return natural_key(path) if column == 'chip' else getattr(entry, 'kinds', {}).get(column, 0)
        paths.sort(key=key, reverse=reverse)
        self.chips.delete(*self.chips.get_children())
        for path in paths:
            entry = self.app.entries.get(path)
            counts = getattr(entry, 'kinds', {})
            self.chips.insert('', 'end', iid=str(path), values=(path.stem, *(counts.get(k, 0) for k in KINDS)))
        keys = [str(p) for p in self.chosen if self.chips.exists(str(p))]
        self.chips.selection_set(keys)
        self.programmatic = set(keys)
        self.selection_text.configure(text=f'表示 {len(paths)} / 選択 {len(self.chosen)} チップ')
        if self.chosen:
            self.load()
        else:
            self.generation += 1
            self.histories = {}
            self.plot()

    def chip_changed(self, path):
        if not self.chosen and path in self.app.history_paths:
            self.chosen = [path]
            self.refresh_list()

    def sort_list(self, column):
        self.sort = column, not self.sort[1] if self.sort[0] == column else column != 'chip'
        self.refresh_list()

    def clear(self):
        self.chosen, self.histories = [], {}
        self.generation += 1
        self.chips.selection_set(())
        self.programmatic = set()
        self.selection_text.configure(text='選択 0 チップ')
        self.plot()

    def matches_count(self, nx, ny):
        return count_matches(nx, ny, int(self.count_threshold.get()), self.count_operator.get(), self.count_scope.get())

    def reset_ranges(self):
        for variable in (self.xmin, self.xmax, self.ymin, self.ymax):
            variable.set('')
        self.plot()

    def select_by_count(self):
        try:
            self.matches_count(0, 0)
            keys = [key for key in self.chips.get_children() if self.matches_count(
                int(self.chips.set(key, self.xkind.get())), int(self.chips.set(key, self.ykind.get())))]
        except ValueError as error:
            self.status.configure(text=f'件数条件を確認してください: {error}')
            return
        self.count_enabled.set(True)
        self.chips.selection_set(keys)
        self.programmatic = None
        self.selected()

    def selected(self, _event=None):
        keys = set(self.chips.selection())
        if keys == getattr(self, 'programmatic', None):
            return
        self.programmatic = None
        visible = set(self.chips.get_children())
        self.chosen = [p for p in self.chosen if str(p) not in visible or str(p) in keys]
        self.chosen += [Path(k) for k in self.chips.selection() if Path(k) not in self.chosen]
        self.selection_text.configure(text=f'選択 {len(self.chosen)} チップ（検索で隠れた選択も保持）')
        self.load()

    def load(self):
        self.generation += 1
        generation, paths, cache = self.generation, list(self.chosen), dict(self.cache)
        self.status.configure(text=f'選択した {len(paths)} チップの履歴を読み込み中…')
        def task():
            histories, errors = {}, []
            for path in paths:
                if generation != self.generation:
                    return
                try:
                    stat = path.stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                    old = cache.get(path)
                    if old is None or old[0] != signature:
                        rows = Document(path).rows
                        after = path.stat()
                        if (after.st_size, after.st_mtime_ns) != signature:
                            raise ValueError('読み込み中に更新されました。再読み込みしてください。')
                        cache[path] = signature, rows
                    histories[path.stem] = cache[path][1]
                except (OSError, UnicodeError, ValueError) as error:
                    errors.append(f'{path.stem}: {error}')
            self.messages.put((generation, histories, errors, cache))
        threading.Thread(target=task, daemon=True).start()

    def poll(self):
        try:
            while True:
                generation, histories, errors, cache = self.messages.get_nowait()
                if generation == self.generation:
                    self.histories, self.errors, self.cache = histories, errors, cache
                    self.plot()
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def options(self):
        since = date.fromisoformat(self.since.get().strip()) if self.since.get().strip() else None
        until = date.fromisoformat(self.until.get().strip()) if self.until.get().strip() else None
        if since and until and since > until:
            raise ValueError('期間の開始・終了を確認してください。')
        return dict(since=since, until=until, valid_only=self.valid_only.get())

    def plot(self, _event=None):
        if not hasattr(self, 'details'):
            return
        c = self.chart
        c.delete('all')
        self.valid_configuration = False
        try:
            size = float(self.point_size.get())
            if not 2 <= size <= 30:
                raise ValueError('点の直径は2〜30 pxで指定してください。')
            self.point_radius = size / 2
            bins = int(self.density_bins.get())
            if not 5 <= bins <= 150:
                raise ValueError('密度のマス数は5〜150で指定してください。')
            self.bins = bins
            limits = representative_limits(self.xmin.get(), self.xmax.get(), self.ymin.get(), self.ymax.get())
            options = self.options()
            xkind, ykind = self.xkind.get(), self.ykind.get()
            xm, ym = int(self.xmetric.get()), int(self.ymetric.get())
            all_records, omitted = [], 0
            for chip, rows in self.histories.items():
                for kind, metric in ((xkind, xm), (ykind, ym)):
                    points, skipped = observations(chip, rows, kind, metric, **options)
                    all_records.extend(points)
                    omitted += skipped
            all_records = list(dict.fromkeys(all_records))
            self.all_records = all_records
            self.points, missing = chip_points(self.histories, xkind, ykind, xm, ym, self.method.get(), **options)
            before_count = len(self.points)
            if self.count_enabled.get():
                self.matches_count(0, 0)
                self.points = [p for p in self.points if self.matches_count(p['nx'], p['ny'])]
            count_excluded = before_count - len(self.points)
            before_range = len(self.points)
            self.points = [p for p in self.points if within_representative_limits(p, limits)]
            range_excluded = before_range - len(self.points)
            self.applied_limits = limits
            self.representatives.delete(*self.representatives.get_children())
            for index, point in enumerate(self.points):
                self.representatives.insert('', 'end', iid=str(index), values=(point['chip'], f'{point["x"]:.6g}',
                    f'{point["y"]:.6g}', point['nx'], point['ny']))
            visible = {p['chip'] for p in self.points}
            self.show_records([p for p in all_records if p.chip in visible])
            colours = {chip: COLOURS[i % len(COLOURS)] for i, chip in enumerate(self.histories) if chip in visible}
            for child in self.legend.winfo_children():
                child.destroy()
            for chip in list(colours)[:4]:
                name = chip if len(chip) <= 16 else chip[:15] + '…'
                ttk.Label(self.legend, text=name, foreground=colours[chip]).pack(side='left', padx=(0, 10))
            if len(colours) > 4:
                ttk.Label(self.legend, text=f'ほか{len(colours)-4}チップ（点クリックでID確認）').pack(side='left')
            labels = metric_label(xkind, xm), metric_label(ykind, ym)
            self.draw_scatter(labels, colours)
            r, rho = correlations(self.points)
            fmt = lambda value: '算出不可（3チップ以上・値の変動が必要）' if value is None else f'{value:.3f}'
            text = f'1点＝1チップの{self.method.get()} | n={len(self.points)} | Pearson r={fmt(r)} | Spearman ρ={fmt(rho)}'
            self.status.configure(text=text + f' | 数値・期間・範囲による除外 {omitted}件 | 両操作が揃わない {len(missing)}チップ'
                                  + f' | 件数条件による除外 {count_excluded}チップ'
                                  + f' | 代表値範囲による除外 {range_excluded}チップ'
                                  + (f' | 読み込みエラー: {" / ".join(self.errors)[:200]}' if self.errors else ''))
            if not self.histories:
                c.create_text(30, 30, anchor='nw', text='左のチップID一覧から比較するチップを選択してください。', fill=theme.MUTED)
            self.valid_configuration = True
        except ValueError as error:
            self.status.configure(text=f'設定を確認してください: {error}')

    def axes(self, box, x_range, y_range, xlabel, ylabel):
        c = self.chart
        left, top, right, bottom = box
        yl, yh, step, yticks = nice_ticks(*y_range)
        for y in yticks:
            py = bottom - (y-yl)/(yh-yl)*(bottom-top)
            c.create_line(left, py, right, py, fill=theme.BORDER)
            c.create_text(left-7, py, anchor='e', text=tick_label(y, step), fill=theme.MUTED)
        xl, xh, xstep, xticks = nice_ticks(*x_range)
        xf = lambda x: left+(x-xl)/(xh-xl)*(right-left)
        for x in xticks:
            c.create_text(xf(x), bottom+5, anchor='n', text=tick_label(x, xstep), fill=theme.MUTED)
        c.create_rectangle(left, top, right, bottom, outline=theme.MUTED)
        c.create_text(left, top-10, anchor='sw', text=ylabel, fill=theme.TEXT)
        c.create_text(right, bottom+25, anchor='ne', text=xlabel, fill=theme.MUTED)
        self.scatter_ranges = (xl, xh), (yl, yh)
        return xf, lambda y: bottom-(y-yl)/(yh-yl)*(bottom-top)

    def draw_scatter(self, labels, colours):
        if not self.points:
            self.chart.create_text(25, 30, anchor='nw', text='両方の操作に数値があるチップを選択してください。', fill=theme.MUTED)
            return
        width, height = max(self.chart.winfo_width(), 160), max(self.chart.winfo_height(), 160)
        side = max(30, min(width-100, height-100))
        left = 75 + max(0, (width-100-side)/2)
        top = 30 + max(0, (height-100-side)/2)
        self.scatter_box = (left, top, left+side, top+side)
        xf, yf = self.axes(self.scatter_box,
            (min(p['x'] for p in self.points), max(p['x'] for p in self.points)),
            (min(p['y'] for p in self.points), max(p['y'] for p in self.points)), labels[0], labels[1])
        mode = self.display_mode.get()
        if mode != DISPLAY_MODES[0]:
            self.draw_density()
        if mode == DISPLAY_MODES[1]:
            return
        for index, point in enumerate(self.points):
            x, y = xf(point['x']), yf(point['y'])
            radius = self.point_radius
            item = self.chart.create_oval(x-radius, y-radius, x+radius, y+radius, fill=colours[point['chip']], outline=theme.TEXT)
            self.chart.tag_bind(item, '<Button-1>', lambda _, i=index: self.focus_chip(i))
            if len(self.points) <= 12:
                self.chart.create_text(x+7, y-7, anchor='sw', text=point['chip'], fill=colours[point['chip']])

    def draw_density(self):
        """マスごとの件数を色で塗る（対数スケール）。右にカラーバー。枠線・目盛りより手前、点より奥。"""
        c, bins = self.chart, self.bins
        left, top, right, bottom = self.scatter_box
        x_range, y_range = self.scatter_ranges
        grid = density_grid(self.points, x_range, y_range, bins)
        maximum = max(max(column) for column in grid)
        cw, ch = (right - left) / bins, (bottom - top) / bins
        for i, column in enumerate(grid):
            for j, count in enumerate(column):
                colour = density_colour(count, maximum)
                if colour:
                    item = c.create_rectangle(left + i*cw, bottom - (j+1)*ch, left + (i+1)*cw, bottom - j*ch,
                                              fill=colour, outline='', tags='density')
                    c.tag_bind(item, '<Enter>', lambda _, n=count: self.density_hint(n))
        c.tag_lower('density')                          # 目盛り線・枠は塗りの上に見せる
        bar_left, steps = right + 14, 60                # カラーバー: 1 … 最大件数（対数）
        if bar_left + 50 > max(c.winfo_width(), 160):
            return
        for k in range(steps):
            count = math.expm1(math.log1p(max(maximum, 1)) * (k + 1) / steps)
            y1 = bottom - k * (bottom - top) / steps
            c.create_rectangle(bar_left, y1 - (bottom - top) / steps, bar_left + 12, y1,
                               fill=density_colour(max(count, 1), maximum), outline='')
        c.create_text(bar_left + 16, top, anchor='nw', text=f'{maximum}', fill=theme.MUTED)
        c.create_text(bar_left + 16, bottom, anchor='sw', text='1', fill=theme.MUTED)
        c.create_text(bar_left, top - 10, anchor='sw', text='件/マス', fill=theme.MUTED)

    def density_hint(self, count):
        c = self.chart
        c.delete('density_hint')
        left, top, right, _ = self.scatter_box
        c.create_text(right, top - 10, anchor='se', text=f'このマス {count} チップ', fill=theme.TEXT, tags='density_hint')

    # --- 上部のチップ（アプリ全体の選択）との連動。比較の複数選択は変えない ---
    def path_of(self, chip):
        return next((p for p in self.chosen if p.stem == chip), None)

    def make_current(self, path):
        if path is not None and path != self.app.selected_path:
            self.app.select_path(path)

    def open_row(self, row):
        if row:
            self.app.open_chip(Path(row))

    def open_point(self, row):
        if row and int(row) < len(self.points):
            path = self.path_of(self.points[int(row)]['chip'])
            if path is not None:
                self.app.open_chip(path)

    def go_to(self, tab):
        self.app.tabs.select(tab)

    def focus_chip(self, index):
        self.representatives.selection_set(str(index))
        self.representatives.see(str(index))
        self.show_records(self.points[index]['x_records'] + self.points[index]['y_records'])
        self.data_tabs.select(1)

    def representative_selected(self, _event=None):
        selected = self.representatives.selection()
        if selected and int(selected[0]) < len(self.points):
            point = self.points[int(selected[0])]
            self.show_records(point['x_records'] + point['y_records'])
            self.make_current(self.path_of(point['chip']))

    def focus_record(self, point):
        records = [p for p in self.all_records if p.chip == point.chip]
        self.show_records(records)
        index = records.index(point)
        self.record_page = index // 500
        self.render_records()
        self.details.selection_set(str(index))
        self.details.see(str(index))
        self.data_tabs.select(1)

    def show_records(self, records):
        self.records, self.record_page = records, 0
        self.render_records()

    @staticmethod
    def record_values(p):
        return (p.chip, p.operation, metric_label(p.kind, p.metric), f'{p.value:.9g}', p.time.isoformat(' ') if p.time else '',
                p.time_source, p.experiment, p.gap, p.operator, p.line)

    def render_records(self):
        self.details.delete(*self.details.get_children())
        first = self.record_page*500
        last = min(first+500, len(self.records))
        for index in range(first, last):
            self.details.insert('', 'end', iid=str(index), values=self.record_values(self.records[index]))
        self.page_text.configure(text=f'{first+1 if last else 0}–{last} / {len(self.records)}')

    def page(self, direction):
        self.record_page = max(0, min(max(0, (len(self.records)-1)//500), self.record_page+direction))
        self.render_records()

    def export(self):
        if not self.histories or not self.valid_configuration:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['ChipID', metric_label(self.xkind.get(), int(self.xmetric.get())),
                    metric_label(self.ykind.get(), int(self.ymetric.get())), 'X件数', 'Y件数', '代表値'])
                writer.writerows((p['chip'], p['x'], p['y'], p['nx'], p['ny'], self.method.get()) for p in self.points)
        except OSError as error:
            messagebox.showerror('CSV出力', str(error), parent=self)
