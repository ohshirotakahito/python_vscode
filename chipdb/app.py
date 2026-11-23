"""Run with python chipdb/app.py. Standard library only."""
import csv
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import ui_theme as theme
from chipdb.store import Document, db_folder, default_root, save_root
from chipdb.summary_view import SummaryView
from chipdb.search import date_query
from chipdb.ledger_view import LedgerView, ledger_rows, row_matches
from chipdb.library import Index
from chipdb.bulk_view import BulkEditor
from chipdb.trend_view import TrendView
from chipdb.comparison_view import ComparisonView
from chipdb.experiment_view import ExperimentView

ACCENT = '#dfb86b'
HEADINGS = ['種別', '状態', '日付', '測定者', '実験ID', '操作', 'GAP #']


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
        self.index = None             # library.Index: 履歴CSVの集計と使用日（変わったファイルだけ読み直す）
        self.entries = {}             # 画面が使う索引の写し（作業スレッドが書き換え中の辞書を直接読まない）
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
        ttk.Button(bar, text='＋ チェックイン', command=lambda: self.ledger.edit(new=True)).pack(side='left', padx=4)
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
        history = ttk.Frame(tabs, padding=12)
        tabs.add(history, text='操作履歴・編集')
        # 実験データ: 上部のチップの FC・MA・TA・CAL が SQserver のどこにあるか（逆引き）。開く・取り出す
        self.experiment_view = ExperimentView(tabs, self)
        tabs.add(self.experiment_view, text='実験データ')
        # 解析・トレンド: 左のチップ一覧から 1 つ・複数を選んで比べる（台帳に戻らずにチップを切り替えられる）
        self.trend = TrendView(tabs, self)
        tabs.add(self.trend, text='解析・トレンド')
        self.comparison = ComparisonView(tabs, self)
        tabs.add(self.comparison, text='操作間比較')
        self.history_tab, self.analysis_tab = history, self.trend
        # 台帳（管理情報）と全チップ比較（履歴CSVの集計）は 1 チップ 1 行の同じ表（2026-11-18 に統合）
        self.ledger = LedgerView(tabs, self)
        tabs.add(self.ledger, text='チップ台帳・比較')
        tabs.insert(0, self.ledger)
        tabs.select(self.ledger)
        self.search_state = self.ledger.search_state
        self.query.trace_add('write', lambda *_: self.filter())
        # 画面の切り替えは下のタブだけで行う（同じ移動先のボタン列は重複なので置かない）
        tools = ttk.Frame(history)
        tools.pack(fill='x', pady=(0, 10))
        ttk.Label(tools, text='行をダブルクリックして編集', style='Muted.TLabel').pack(side='left')
        ttk.Button(tools, text='選択行を編集', command=self.edit).pack(side='left', padx=8)
        ttk.Button(tools, text='表全体を編集', command=self.edit_table, style='Accent.TButton').pack(side='left', padx=4)
        ttk.Button(tools, text='チップ管理情報を編集', command=self.ledger.edit_current).pack(side='left')
        ttk.Button(tools, text='CSVを書き出す', command=self.export).pack(side='right')
        self.table = ttk.Treeview(history, show='headings', selectmode='browse')
        vertical = ttk.Scrollbar(history, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(history, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        horizontal.pack(side='bottom', fill='x')
        vertical.pack(side='right', fill='y')
        self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', self.edit)
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

    def scan(self, force=False):
        """共有フォルダのCSV一覧と台帳を読み直し、履歴CSVの索引を更新する。force=True なら全件を読み直す。"""
        previous = self.selected_path
        folder = db_folder(self.root_path)
        self.location.configure(text=str(folder))
        self.document = None
        self.selected_path = None
        self.table.delete(*self.table.get_children())
        self.overview.clear()
        self.title_label.configure(text='チップを選択')
        self.metrics.configure(text='共有CSVを直接閲覧・編集できます。')
        try:
            self.paths = sorted(folder.glob('*.csv')) if folder.is_dir() else []
            self.status.configure(text='接続済み · チップを選択してください' if self.paths else '未接続またはCSVなし · 「共有フォルダを選択」でAXNの共有データを指定してください')
        except OSError as exc:
            self.paths = []
            self.status.configure(text=str(exc))
        self.history_paths = set(self.paths)
        self.ledger.reload(folder)
        source_names = {p.stem.casefold() for p in self.paths}
        self.paths.extend(folder / (c['code'] + '.csv') for c in self.ledger.data['chips']
                          if c['code'].casefold() not in source_names)
        self.paths.sort(key=lambda p: p.stem.casefold())
        self.summary.configure(text=f'{len(self.paths):03d}  CHIPS     /     履歴あり {len(self.history_paths)} · 測定前 {len(self.paths)-len(self.history_paths)}')
        if self.index is None or self.index.db != folder:
            self.index = Index(folder)          # 前回の控えがあれば、それをすぐに表示する
        self.entries = dict(self.index.entries)
        self.update_index(force)
        self.filter()
        self.trend.refresh_list()
        self.comparison.refresh_list()
        if previous in self.paths:
            self.select_path(previous)
        self.plot()

    def update_index(self, force=False):
        """履歴CSVの索引を作業スレッドで更新する（変わったファイルだけ読む）。終わると表を描き直す。"""
        self.index_version += 1
        version, index, paths = self.index_version, self.index, sorted(self.history_paths)
        self.index_loading = True
        self.ledger.index_state.configure(text='履歴CSVの変更を確認中…' + ('（全件読み直し）' if force else ''))
        self.ledger.progress.configure(value=0, maximum=1)

        def worker():
            try:
                result = index.update(paths, force=force, cancelled=lambda: version != self.index_version,
                                      progress=lambda i, n: self.index_messages.put((version, 'progress', (i, n))))
            except Exception as exc:  # noqa: BLE001 - 読めない共有フォルダなどは画面に出す
                self.index_messages.put((version, 'failed', str(exc)))
                return
            if result is not None:
                self.index_messages.put((version, 'done', (dict(index.entries), result)))
        threading.Thread(target=worker, daemon=True).start()

    def filter(self):
        query = self.query.get()
        try:
            span = date_query(query)
        except ValueError as exc:
            self.search_state.configure(text=str(exc))
            self.ledger.show([])
            return
        self.ledger.rows = ledger_rows(self.paths, self.ledger.data['chips'], self.entries, self.history_paths,
                                       getattr(self.ledger, 'alias_table', None))
        visible = [row for row in self.ledger.rows if row_matches(row, query, span)]
        self.ledger.show(visible)
        detail = ''
        if span:
            detail = ' · 使用日を読み込み中…' if self.index_loading and not self.entries else ' · 履歴の実験日で検索'
        self.search_state.configure(text=f'{len(visible)} / {len(self.paths)} チップ{detail}')

    def poll_index(self):
        try:
            while True:
                version, kind, payload = self.index_messages.get_nowait()
                if version != self.index_version:
                    continue
                if kind == 'progress':
                    done, total = payload
                    self.ledger.progress.configure(maximum=max(total, 1), value=done)
                    self.ledger.index_state.configure(text=f'変更された履歴CSVを読み直し中 · {done} / {total}')
                    continue
                self.index_loading = False
                if kind == 'failed':
                    self.ledger.index_state.configure(text=f'履歴CSVを読めません: {payload}')
                    continue
                self.entries, result = payload
                self.ledger.progress.configure(maximum=1, value=1)
                self.ledger.show_errors(result.errors)
                self.ledger.index_state.configure(text=f'履歴CSV {len(self.entries)} 件 · {result.text} · '
                                                       f'{datetime.now():%H:%M:%S}')
                self.filter()
                self.trend.refresh_list()        # 一覧の「実験」「最終使用」を新しい集計で
                self.comparison.refresh_list()
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
        self.ledger.sync_selection(self.selected_path)
        self.trend.chip_changed(self.selected_path)
        self.comparison.chip_changed(self.selected_path)
        self.experiment_view.chip_changed(self.selected_path)

    def plot(self, event=None):
        self.trend.plot()
        self.comparison.plot()

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
        self.status.configure(text=f'{doc.path} · 保存時にバックアップを作成します')

    def open_chip(self, path):
        self.select_path(path)
        if self.selected_path == Path(path):
            self.tabs.select(self.overview)

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
            self.render()
            self.update_index()          # 保存したチップだけが変わっているので、その 1 件だけ読み直される
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
