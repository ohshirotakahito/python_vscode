"""Run with python chipdb/app.py. Standard library only."""
import csv
import math
import queue
import sys
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import ui_theme as theme
from chipdb.store import Document, db_folder, default_root, save_root
from chipdb.summary_view import SummaryView, AllView
from chipdb.search import date_query, usage_dates, matches
from chipdb.registry_view import RegistryView
from chipdb.bulk_view import BulkEditor

ACCENT = '#dfb86b'
HEADINGS = ['種別', '状態', '日付', '担当者', '実験 ID', '操作', 'GAP #']


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Chip Atlas · Quantum Workbench')
        from shared.app_release import configure_tk
        configure_tk(self, 'chipdb')
        self.geometry('1440x900')
        self.minsize(1050, 700)
        theme.apply(self, ACCENT)
        self.document = None
        self.selected_path = None
        self.paths = []
        self.history_paths = set()
        self.date_index = {}
        self.date_errors = 0
        self.index_version = 0
        self.index_loading = False
        self.index_messages = queue.Queue()
        self.root_path = default_root()
        theme.header(self, 'Chip Atlas', 'AXN SHARED DATABASE  /  チップの履歴を、次の発見へ。',
                     '\uE950', ACCENT, height=82, on_close=self.destroy).pack(fill='x')
        bar = ttk.Frame(self, padding=(20, 12))
        bar.pack(fill='x')
        ttk.Button(bar, text='共有フォルダを選択', command=self.choose).pack(side='left')
        ttk.Button(bar, text='一覧・履歴・集計を更新', command=self.scan).pack(side='left', padx=8)
        ttk.Button(bar, text='＋ チェックイン', command=lambda: self.registry_view.edit(new=True)).pack(side='left', padx=4)
        from chipdb.cleanup_view import open_cleanup
        ttk.Button(bar, text='重複・IDを整理…', command=lambda: open_cleanup(self)).pack(side='left', padx=4)
        self.location = ttk.Label(bar, foreground=theme.MUTED)
        self.location.pack(side='left', padx=12)
        self.summary = ttk.Label(self, text='', font=(theme.UI_BOLD, 13), padding=(24, 12))
        self.summary.pack(fill='x')
        self.query = tk.StringVar()
        right = ttk.Frame(self, padding=(24, 8))
        right.pack(fill='both', expand=True)
        self.title_label = ttk.Label(right, text='チップを選択', font=(theme.UI_BOLD, 24))
        self.title_label.pack(anchor='w')
        self.metrics = ttk.Label(right, text='共有フォルダを接続すると、AXNの履歴を表示します。', foreground=theme.MUTED)
        self.metrics.pack(anchor='w', pady=(6, 18))
        tabs = ttk.Notebook(right)
        self.tabs = tabs
        tabs.pack(fill='both', expand=True)
        self.overview = SummaryView(tabs)
        tabs.add(self.overview, text='チップ概要')
        history, analysis = ttk.Frame(tabs, padding=12), ttk.Frame(tabs, padding=12)
        tabs.add(history, text='操作履歴・編集')
        tabs.add(analysis, text='解析・トレンド')
        self.history_tab, self.analysis_tab = history, analysis
        self.all_view = AllView(tabs, self.open_chip, self.select_path, refresh_sources=self.scan)
        tabs.add(self.all_view, text='全チップ比較')
        self.registry_view = RegistryView(tabs, self)
        tabs.add(self.registry_view, text='チップ台帳・チェックイン')
        tabs.insert(0, self.registry_view)
        tabs.insert(1, self.all_view)
        tabs.select(self.registry_view)
        self.search_state = self.registry_view.search_state
        self.query.trace_add('write', lambda *_: self.filter())
        # 画面の切り替えは下のタブだけで行う（同じ移動先のボタン列は重複なので置かない）
        tools = ttk.Frame(history)
        tools.pack(fill='x', pady=(0, 10))
        ttk.Label(tools, text='行をダブルクリックして編集', style='Muted.TLabel').pack(side='left')
        ttk.Button(tools, text='選択行を編集', command=self.edit).pack(side='left', padx=8)
        ttk.Button(tools, text='表全体を編集', command=self.edit_table, style='Accent.TButton').pack(side='left', padx=4)
        ttk.Button(tools, text='チップ管理情報を編集', command=self.registry_view.edit_current).pack(side='left')
        ttk.Button(tools, text='CSVを書き出す', command=self.export).pack(side='right')
        self.table = ttk.Treeview(history, show='headings', selectmode='browse')
        vertical = ttk.Scrollbar(history, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(history, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        horizontal.pack(side='bottom', fill='x')
        vertical.pack(side='right', fill='y')
        self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', self.edit)
        options = ttk.Frame(analysis)
        options.pack(fill='x')
        ttk.Label(options, text='操作').pack(side='left')
        self.kind = ttk.Combobox(options, state='readonly', width=15)
        self.kind.pack(side='left', padx=10)
        self.metric = ttk.Combobox(options, state='readonly', values=['Info 1', 'Info 2', 'Info 3', 'Info 4', 'Info 5', 'Info 6'], width=12)
        self.metric.current(0)
        self.metric.pack(side='left')
        for combo in (self.kind, self.metric):
            combo.bind('<<ComboboxSelected>>', self.plot)
        self.chart = tk.Canvas(analysis, bg=theme.FIELD, highlightthickness=0)
        self.chart.pack(fill='both', expand=True, pady=15)
        self.chart.bind('<Configure>', self.plot)
        ttk.Label(analysis, text='選択した操作の数値を記録順で表示。Infoの意味・単位はAXNの操作ごとに異なります。', style='Muted.TLabel').pack(anchor='w')
        self.status = ttk.Label(self, padding=(24, 10), style='Status.TLabel')
        self.status.pack(fill='x')
        self.after(100, self.scan)
        self.after(100, self.poll_index)

    def choose(self):
        folder = filedialog.askdirectory(title='AXN共有ルート / ChipDB / DB を選択',
                                        initialdir=str(self.root_path), parent=self)
        if folder:
            try:
                save_root(folder)
            except OSError as exc:
                messagebox.showerror('設定を保存できません', str(exc), parent=self)
                return
            self.root_path = Path(folder)
            self.scan()

    def scan(self):
        previous = self.selected_path
        folder = db_folder(self.root_path)
        self.location.configure(text=str(folder))
        self.document = None
        self.selected_path = None
        self.all_view.sync_selection(None)
        self.table.delete(*self.table.get_children())
        self.overview.clear()
        self.title_label.configure(text='チップを選択')
        self.metrics.configure(text='共有CSVを直接閲覧・編集できます。')
        try:
            self.paths = sorted(folder.glob('*.csv')) if folder.is_dir() else []
            self.summary.configure(text=f'{len(self.paths):03d}  CHIPS     /     AXN 共有ライブラリ')
            self.status.configure(text='接続済み · チップを選択してください' if self.paths else '未接続またはCSVなし · 「共有フォルダを選択」でAXNの共有データを指定してください')
        except OSError as exc:
            self.paths = []
            self.status.configure(text=str(exc))
        self.history_paths = set(self.paths)
        self.registry_view.reload(folder)
        source_names = {p.stem.casefold() for p in self.paths}
        self.paths.extend(folder / (c['code'] + '.csv') for c in self.registry_view.data['chips']
                          if c['code'].casefold() not in source_names)
        self.paths.sort(key=lambda p: p.stem.casefold())
        self.summary.configure(text=f'{len(self.paths):03d}  CHIPS     /     履歴あり {len(self.history_paths)} · 測定前 {len(self.paths)-len(self.history_paths)}')
        self.start_index()
        self.filter()
        self.all_view.reset(sorted(self.history_paths))
        self.all_view.load()
        if previous in self.paths:
            self.select_path(previous)
        self.plot()

    def filter(self):
        try:
            span = date_query(self.query.get())
        except ValueError as exc:
            self.search_state.configure(text=str(exc))
            self.registry_view.refresh([])
            return
        visible_paths = []
        chips = {c['code'].casefold(): c for c in self.registry_view.data['chips']}
        for path in self.paths:
            chip = chips.get(path.stem.casefold(), {})
            text = ' '.join(str(chip.get(key, '未登録' if key == 'state' else ''))
                            for key in ('code', 'state', 'location', 'user', 'owner', 'origin', 'description'))
            metadata_match = span is None and self.query.get().strip().casefold() in text.casefold()
            if matches(path.stem, self.date_index.get(path, ()), self.query.get(), span) or metadata_match:
                visible_paths.append(path)
        self.registry_view.refresh(visible_paths)
        self.sync_chip_selection()
        detail = ' · 使用日を読み込み中…' if span and self.index_loading else ''
        if span and not self.index_loading:
            detail = ' · 履歴の実験日で検索'
            if self.date_errors:
                detail += f'（{self.date_errors}件は読込不可）'
        self.search_state.configure(text=f'{len(visible_paths)} / {len(self.paths)} チップ{detail}')

    def start_index(self):
        self.index_version += 1
        version, paths = self.index_version, list(self.history_paths)
        self.date_index = {}
        self.date_errors = 0
        self.index_loading = bool(paths)

        def worker():
            dates, errors = {}, 0
            for path in paths:
                if version != self.index_version:
                    return
                try:
                    dates[path] = usage_dates(Document(path).rows)
                except (OSError, UnicodeError):
                    errors += 1
            self.index_messages.put((version, dates, errors))
        if paths:
            threading.Thread(target=worker, daemon=True).start()

    def poll_index(self):
        try:
            while True:
                version, dates, errors = self.index_messages.get_nowait()
                if version == self.index_version:
                    self.date_index = dates
                    self.date_errors = errors
                    self.index_loading = False
                    self.filter()
        except queue.Empty:
            pass
        self.after(100, self.poll_index)

    def select_path(self, path):
        path = Path(path)
        if self.selected_path == path:
            self.sync_chip_selection()
            return
        if path not in self.history_paths:
            self.document = None
            self.table.delete(*self.table.get_children())
            self.overview.clear()
            self.overview.view.insert('', 'end', values=('チップID', path.stem))
            self.overview.view.insert('', 'end', values=('測定履歴', 'なし（測定前）'))
            self.title_label.configure(text=path.stem)
            self.metrics.configure(text='チェックイン済み · 測定前（履歴なし） · チップ台帳で管理情報を編集できます')
            self.plot()
            self.selected_path = path
            self.sync_chip_selection()
            return
        try:
            document = Document(path)
        except (OSError, UnicodeError) as exc:
            messagebox.showerror('読み込み', str(exc), parent=self)
            self.sync_chip_selection()
            return
        self.document = document
        self.selected_path = path
        self.render()
        self.sync_chip_selection()

    def sync_chip_selection(self):
        self.registry_view.sync_selection(self.selected_path)
        self.all_view.sync_selection(self.selected_path)

    def render(self):
        doc = self.document
        self.overview.show(doc.rows)
        self.title_label.configure(text=doc.path.stem)
        experiments = doc.experiments
        count = len({r[4] for r in experiments})
        gap = doc.rows[0][6] if doc.rows and len(doc.rows[0]) > 6 else '—'
        self.metrics.configure(text=f'{count} 実験    /    {len(experiments)} 操作記録    /    GAP {gap}    /    CP932 · AXN CSV')
        declared_id = doc.rows[0][0] if doc.rows and doc.rows[0] else ''
        if declared_id != doc.path.stem:
            self.metrics.configure(text=self.metrics.cget('text') +
                f'    /    注意: CSV内IDは {declared_id or "空欄"}（ファイル名と不一致）')
        self.table.delete(*self.table.get_children())
        width = max((len(r) for r in doc.rows), default=7)
        self.table.configure(columns=tuple(str(i) for i in range(width)))
        for i in range(width):
            name = HEADINGS[i] if i < 7 else f'Info {i - 6}'
            self.table.heading(str(i), text=name)
            self.table.column(str(i), width=140 if i in (2, 4) else 105, stretch=False)
        for i, row in enumerate(doc.rows):
            self.table.insert('', 'end', iid=str(i), values=row, tags=('operation' if row and row[0] == 'Experiment' else 'meta',))
        self.table.tag_configure('meta', foreground=theme.MUTED)
        kinds = Counter(r[5].split('#')[0] for r in experiments)
        selected_kind = self.kind.get()
        choices = list(kinds)
        if selected_kind and selected_kind not in choices:
            choices.append(selected_kind)
        self.kind.configure(values=choices)
        self.kind.set(selected_kind or next(iter(kinds), ''))
        self.status.configure(text=f'{doc.path} · 保存時にバックアップを作成します')
        self.plot()

    def open_chip(self, path):
        self.select_path(path)
        if self.selected_path == Path(path):
            self.tabs.select(self.overview)

    def plot(self, event=None):
        c = self.chart
        c.delete('all')
        points = []
        if self.document:
            column = 7 + max(self.metric.current(), 0)
            for row in self.document.experiments:
                if row[5].split('#')[0] == self.kind.get() and len(row) > column:
                    try:
                        value = float(row[column])
                        if math.isfinite(value):
                            points.append(value)
                    except ValueError:
                        pass
        if not points:
            c.create_text(35, 40, anchor='nw', text='数値データがある操作を選択してください', fill=theme.MUTED, font=(theme.UI, 12))
            return
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        low, high = min(points), max(points)
        c.create_text(24, 20, anchor='nw', text=f'{self.kind.get()} / {self.metric.get()}     n={len(points)}     min {low:.5g}     max {high:.5g}     mean {sum(points)/len(points):.5g}', fill=ACCENT)
        for i in range(5):
            y = 60 + (h - 100) * i / 4
            c.create_line(75, y, w - 25, y, fill=theme.BORDER)
            c.create_text(65, y, anchor='e', text=f'{high - (high-low)*i/4:.4g}', fill=theme.MUTED)
        coords = [(75 + i * (w - 110) / max(len(points)-1, 1), h - 40 - (v-low) / (high-low or 1) * (h-100)) for i, v in enumerate(points)]
        if len(coords) > 1:
            c.create_line(*[n for p in coords for n in p], fill=ACCENT, width=2)
        for x, y in coords:
            c.create_oval(x-3, y-3, x+3, y+3, fill=ACCENT, outline=theme.FIELD)
        c.create_text(w-25, h-16, anchor='e', text='記録順 →', fill=theme.MUTED)

    def edit_table(self):
        if self.document is None:
            messagebox.showinfo('表全体を編集', '履歴のあるチップを選択してください。', parent=self)
            return
        BulkEditor(self, self.document)

    def edit(self, event=None):
        selected = self.table.selection()
        if not selected or self.document is None:
            messagebox.showinfo('履歴を編集', '編集する履歴行を選択してください。', parent=self)
            return
        index = int(selected[0])
        row = self.document.rows[index]
        dialog = tk.Toplevel(self)
        dialog.title('履歴行を編集')
        dialog.transient(self)
        entries = []
        for i, value in enumerate(row):
            ttk.Label(dialog, text=HEADINGS[i] if i < 7 else f'Info {i-6}').grid(row=i, column=0, padx=15, pady=4, sticky='w')
            entry = ttk.Entry(dialog, width=65)
            entry.insert(0, value)
            entry.grid(row=i, column=1, padx=15, pady=4)
            if i == 0:
                entry.configure(state='readonly')
            entries.append(entry)

        def save():
            rows = [list(r) for r in self.document.rows]
            rows[index] = [e.get() for e in entries]
            try:
                backup = self.document.save(rows)
            except (OSError, ValueError, UnicodeError) as exc:
                messagebox.showerror('保存できません', str(exc), parent=dialog)
                return
            dialog.destroy()
            self.start_index()
            self.render()
            self.all_view.mark_stale()
            self.status.configure(text=f'保存完了 · バックアップ: {backup}')

        ttk.Label(dialog, text='AXNの共有CSVに保存します。変更前のファイルは自動バックアップされます。', style='Muted.TLabel').grid(row=len(row), columnspan=2, padx=15, pady=12)
        ttk.Button(dialog, text='変更を保存', style='Accent.TButton', command=save).grid(row=len(row)+1, column=1, sticky='e', padx=15, pady=12)
        dialog.grab_set()

    def export(self):
        if not self.document:
            return
        target = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', initialfile=f'{self.document.path.stem}_export.csv')
        if target:
            try:
                if Path(target).resolve() == self.document.path.resolve():
                    raise ValueError('共有元と別の保存先を指定してください。')
                with open(target, 'w', encoding='utf-8-sig', newline='') as stream:
                    csv.writer(stream).writerows(self.document.rows)
                self.status.configure(text=f'書き出しました: {target}')
            except (OSError, ValueError) as exc:
                messagebox.showerror('書き出し', str(exc), parent=self)


if __name__ == '__main__':
    App().mainloop()
