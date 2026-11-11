"""Run: python database/app.py"""
from pathlib import Path, PureWindowsPath
import csv
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # リポジトリ直下（shared/ を読むため）
from shared import catalog, ui_theme  # noqa: E402

try:
    from . import inventory_views, recent_progress, sq_workflow, rackstation_view, bnal_progress, anal_progress, plan_view
except ImportError:
    import inventory_views
    import recent_progress
    import sq_workflow
    import rackstation_view
    import bnal_progress
    import anal_progress
    import plan_view
try:
    from .scan_lock import ScanLock
except ImportError:
    from scan_lock import ScanLock

BASE =Path(__file__).resolve().parent
CONFIG = BASE / 'sources.json'
DEFAULTS = [dict(server=name, root='\\\\' + name + '\\analysis', kind='analysis')
            for name in ('Rackstation', 'QTserver', 'QDserver', 'QKserver')]
DEFAULTS.append(dict(server='SQserver', root='', kind='experiment'))
# 「保存先の状態」表の列幅（px）
SOURCE_COLUMN_WIDTHS = {'server': 140, 'root': 300, 'status': 140, 'file_count': 140, 'bytes': 140,
                        'volume': 200, 'volume_rate': 80, 'folder_count': 140, 'last_success': 195,
                        'last_attempt': 208, 'error': 185}
# 「SQserver 実験一覧」の列（共有フォルダへ書き出す sq_experiments.csv も同じ列）
SQ_ENTRY_COLUMNS = ('group', 'container', 'machine', 'name', 'analysis_state', 'exsv_t_tdms', 'exzsv_tdms',
                    'experiment_at', 'analysis_started_at', 'last_success', 'status')
SQ_ENTRY_TITLES = ('グループ', 'backup / ANフォルダ', '装置', '元の名前', '解析着手', 'EXSV/T TDMS数', 'EXZSV TDMS数',
                   '実験日時', '解析着手日時', '最終取得 UTC', '取得状態')


def selected_source_kind(server, root):
    if server.casefold() != 'sqserver':
        return 'analysis'
    path = PureWindowsPath(root)
    name = path.name.casefold()
    if name.startswith('an') and name[2:].lstrip('#').isdigit():
        name = path.parent.name.casefold()
    return {'data': 'sq_data', 'data_stocked': 'sq_stocked'}.get(name, 'experiment')


def sq_entry_tag(row):
    if row.get('analysis_state') == '着手済み':
        return 'sq_started'
    if row.get('analysis_state') == '未着手':
        if any(isinstance(row.get(key), (int, float))
               for key in ('exsv_t_tdms', 'exzsv_tdms')):
            return 'sq_needs_action'
        if all(row.get(key) is None for key in ('exsv_t_tdms', 'exzsv_tdms')):
            return 'sq_no_tdms'
    return 'sq_unknown'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('実験ファイル データベース')
        self.geometry('1450x950')
        style = ui_theme.apply(self, ui_theme.DATABASE)
        style.configure('Treeview', rowheight=26)
        ui_theme.header(self, '実験ファイル データベース', 'サーバーごとのファイル保存状況と解析着手状況',
                        '\uEE94', ui_theme.DATABASE, on_close=lambda: self.close()).pack(fill='x')
        self.db_path = catalog.DEFAULT_DB
        catalog.initialize(self.db_path)
        self.configured = DEFAULTS
        if CONFIG.exists():
            try:
                data = json.loads(CONFIG.read_text(encoding='utf-8'))
                if not isinstance(data, list) or not all(
                    isinstance(s, dict) and all(isinstance(s.get(k), str) for k in ('server', 'root', 'kind'))
                    and s['kind'] in catalog.SOURCE_KINDS for s in data
                ):
                    raise ValueError('保存先設定の形式が不正です。')
                self.configured = data
            except (OSError, ValueError) as exc:
                messagebox.showerror('設定の読み込み', str(exc))
        self.events = queue.Queue()
        self.refresh_version = 0
        self.cancel = threading.Event()
        self.busy = False
        self.main_tabs = ttk.Notebook(self)
        self.main_tabs.pack(fill='both', expand=True)
        self.sq_panel = ttk.Frame(self.main_tabs)
        self.analysis_panel = ttk.Frame(self.main_tabs)
        self.main_tabs.add(self.sq_panel, text='Data_server')
        self.main_tabs.add(self.analysis_panel, text='Analysis_server')
        self.anal_panel = ttk.Frame(self.main_tabs)
        self.main_tabs.add(self.anal_panel, text='ANAL進捗')
        self.bnal_panel = ttk.Frame(self.main_tabs)
        self.main_tabs.add(self.bnal_panel, text='BNAL進捗')
        self.plan_panel = ttk.Frame(self.main_tabs)
        self.main_tabs.add(self.plan_panel, text='サンプル台帳')
        self.buttons = []
        target = ttk.LabelFrame(self.sq_panel, text='更新する範囲を選択（ほかの範囲の記録は保持します）', padding=8)
        target.pack(fill='x', padx=8)
        self.target_server = tk.StringVar()
        self.target_root = tk.StringVar()
        self.target_scope = tk.StringVar()
        ttk.Label(target, text='サーバー').grid(row=0, column=0, sticky='w')
        self.server_box = ttk.Combobox(target, textvariable=self.target_server, state='readonly', width=18)
        self.server_box.grid(row=0, column=1, padx=5)
        self.server_box.bind('<<ComboboxSelected>>', lambda _: self.select_server())
        # サーバー選択欄の右に、段階ごとの更新ボタン（Recent → Archived → Stocked）を並べる。
        # SQserverの「サーバーの更新」は Recent_Data（backup_01〜08）の更新にあたる。
        # 色はアプリ共通の役割色: 青＝通常の更新、橙＝範囲を絞った更新、灰（既定）＝設定・状況確認。
        recent = ttk.Button(target, text='Recent_Data を更新', command=self.update_server,
                            style='Primary.TButton')
        recent.grid(row=0, column=2, padx=4)
        self.buttons.append(recent)
        for col, stage in [(3, 'Archived_Data'), (4, 'Stocked_Data')]:
            button = ttk.Button(target, text=stage + ' を更新', style='Primary.TButton',
                                command=lambda stage=stage: self.run_jobs(sq_workflow.jobs_for_stage(self.configured, stage)))
            button.grid(row=0, column=col, padx=4)
            self.buttons.append(button)
        ttk.Label(target, text='共有').grid(row=0, column=5, padx=(16, 0))
        self.root_box = ttk.Combobox(target, textvariable=self.target_root, state='readonly', width=48)
        self.root_box.grid(row=0, column=6, sticky='w', padx=5)
        self.root_box.bind('<<ComboboxSelected>>', self.change_root)
        root_button = ttk.Button(target, text='この共有を更新', command=self.update_root,
                                 style='Conditional.TButton')
        root_button.grid(row=0, column=7, padx=4)
        self.buttons.append(root_button)
        # 更新中だけ押せる中止ボタンは、更新ボタンのすぐ右に置く
        self.stop_button = ttk.Button(target, text='更新を中止', command=self.cancel.set, state='disabled')
        self.stop_button.grid(row=0, column=8, padx=4)
        settings_button = ttk.Button(target, text='保存先設定', command=self.settings)
        settings_button.grid(row=0, column=9, padx=(20, 4))
        self.buttons.append(settings_button)
        ttk.Button(target, text='Recent_Data 着手状況',
                   command=lambda: recent_progress.open_window(self)).grid(row=0, column=10, padx=4)
        ttk.Button(target, text='台帳の共有', command=self.publish_settings).grid(row=0, column=11, padx=4)
        target.columnconfigure(12, weight=1)  # 入力欄は固定幅。余白は右端へ
        # 2本のバーを見出しと色で区別する（サーバー更新＝アプリの紫、表示の読み込み＝紫寄りの青）
        style = ttk.Style(self)
        bars = ttk.Frame(self.sq_panel)
        bars.pack(fill='x', padx=8, pady=(4, 0))
        bars.columnconfigure(1, weight=1)
        for row, (name, title, color) in enumerate((
                ('Update', 'サーバー更新', ui_theme.DATABASE),
                ('Read', '表示の読み込み', ui_theme.mix(ui_theme.DATABASE, '#5b9dff', 0.6)))):
            style.configure(f'{name}.Horizontal.TProgressbar', background=color, lightcolor=color,
                            darkcolor=color, troughcolor=ui_theme.FIELD, bordercolor=ui_theme.BORDER)
            style.configure(f'{name}.TLabel', foreground=color)
            ttk.Label(bars, text=title, style=f'{name}.TLabel', width=12).grid(
                row=row, column=0, sticky='w', pady=2)
            bar = ttk.Progressbar(bars, mode='indeterminate', style=f'{name}.Horizontal.TProgressbar')
            bar.grid(row=row, column=1, sticky='ew', pady=2)
            if name == 'Update':
                self.progress_bar = bar
            else:
                self.read_progress = bar
        self.read_status = tk.StringVar(value='')
        ttk.Label(self.sq_panel, textvariable=self.read_status, padding=(8, 2)).pack(fill='x')
        self.reading = False
        self.live_jobs = {}
        self.recent_counts = {}
        self.db_label = ttk.Label(self.sq_panel, padding=8)
        self.db_label.pack(anchor='w')
        ttk.Label(self.sq_panel, text='保存先の状態（日時はUTC／接続失敗・中止時は前回の記録を表示）').pack(anchor='w', padx=8)
        self.source_tree = self.table(('server', 'root', 'status', 'file_count', 'bytes', 'volume', 'volume_rate', 'folder_count', 'last_success', 'last_attempt', 'error'),
                                      ('サーバー', '保存先', '状態', '件数', '容量 (GB / TB)', 'ボリューム使用量 / 全体', '使用率', 'フォルダ数', '最終全体取得 UTC', '最終試行 UTC', 'エラー・除外'), 8)
        rackstation_view.tag_volume_rows(self.source_tree)
        ttk.Label(self.sq_panel, text=rackstation_view.VOLUME_LEGEND, style='Muted.TLabel',
                  padding=(8, 2)).pack(anchor='w')
        # 保存先の状態の列幅は固定（ドラッグでも変えない）
        for col, width in SOURCE_COLUMN_WIDTHS.items():
            self.source_tree.column(col, width=width, minwidth=width, stretch=False)
        self.source_tree.bind('<Button-1>', lambda event: 'break'
                              if self.source_tree.identify_region(event.x, event.y) == 'separator' else None)
        search_bar = ttk.Frame(self.sq_panel, padding=8)
        search_bar.pack(fill='x')
        ttk.Label(search_bar, text='パス・実験・サンプル名で検索').pack(side='left')
        self.search = tk.StringVar()
        entry = ttk.Entry(search_bar, textvariable=self.search, width=50)
        entry.pack(side='left', padx=5)
        entry.bind('<Return>', lambda _: self.refresh())
        ttk.Button(search_bar, text='表示を更新', command=self.refresh).pack(side='left')
        ttk.Button(search_bar, text='ファイル一覧', command=self.show_files).pack(side='left', padx=5)
        self.total = ttk.Label(search_bar)
        self.total.pack(side='left', padx=10)
        self.columns = ('server', 'root', 'experiment', 'sample', 'frequency', 'specimen', 'category', 'file_count', 'bytes', 'last_success')
        self.notebook = ttk.Notebook(self.sq_panel)
        self.notebook.pack(fill='both', expand=True, padx=8)
        self.views = {}
        specifications = [
            ('sq_groups', 'SQserver グループ集計',
             ('server', 'group', 'container', 'folder_count', 'archive_count', 'started_count', 'pending_count', 'started_rate', 'history_note', 'root', 'last_success', 'status'),
             ('サーバー', 'グループ', 'backup / ANフォルダ', '直下フォルダ数', '圧縮ファイル数', '着手数', '未着手数', '着手率', '履歴照合の確認事項', '保存先', '最終取得 UTC', '取得状態')),
            ('sq_entries', 'SQserver 実験一覧', SQ_ENTRY_COLUMNS, SQ_ENTRY_TITLES),
            ('updates', '更新状況・履歴',
             ('server', 'share_name', 'status', 'direct_folders', 'analysis_started', 'analysis_pending', 'analysis_rate', 'folder_count', 'elapsed', 'started', 'finished', 'error', 'count_note', 'root'),
             ('サーバー', '共有名', '状態', '直下実験フォルダ総数', '解析着手数', '未着手数', '着手率', '確認フォルダ数（全階層）', '経過秒', '開始 UTC', '終了 UTC', '処理中の場所・エラー', '着手照合の確認事項', '共有')),
        ]
        for key, title, columns, titles in specifications:
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text=title)
            if key == 'sq_entries':
                ttk.Label(tab, text='赤：未着手・TDMS件数あり（優先処理）　灰：未着手・TDMS未作成　緑：着手済み　無地：確認不可・対象外',
                          padding=(8, 4)).pack(fill='x')
            tree = self.table(columns, titles, 14, True, parent=tab)
            if key == 'sq_entries':
                for tag, color in [('sq_needs_action', ui_theme.row('danger', 0.3)),
                                   ('sq_no_tdms', ui_theme.row('neutral', 0.16)),
                                   ('sq_started', ui_theme.row('success')), ('sq_unknown', ui_theme.FIELD)]:
                    tree.tag_configure(tag, background=color)
            self.views[key] = dict(tab=tab, tree=tree, columns=columns, rows=[])
            tree.bind('<Double-1>', lambda event, current=tree: self.show_row_details(current))
        self.notebook.bind('<<NotebookTabChanged>>', lambda _: self.update_total())
        ttk.Label(self.sq_panel, text='未作成＝フォルダなし / 0＝存在して0件。TDMS・stockedは各フォルダ直下のみ。行をダブルクリックすると詳細を表示します。').pack(anchor='w', padx=8)
        self.status = tk.StringVar(value='サーバー・共有・実験を選んで個別に更新できます。閲覧のみなら接続しません。')
        ttk.Label(self.sq_panel, textvariable=self.status, padding=8, wraplength=1300).pack(fill='x')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(150, self.poll)
        self.select_server()
        rackstation_view.create_panel(self, self.analysis_panel)
        anal_progress.create_panel(self, self.anal_panel)
        bnal_progress.create_panel(self, self.bnal_panel)
        plan_view.create_panel(self, self.plan_panel)
        self.refresh()

    def table(self, columns, titles, height, expand=False, parent=None):
        frame = ttk.Frame(parent if parent is not None else self.sq_panel, padding=(8, 0))
        frame.pack(fill='both' if expand else 'x', expand=expand)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=height)
        tree.tag_configure('even', background=ui_theme.STRIPE)
        tree.tag_configure('完了', background=ui_theme.row('success'))
        tree.tag_configure('実行中', background=ui_theme.row('info'))
        tree.tag_configure('失敗', background=ui_theme.row('danger'))
        tree.tag_configure('中止', background=ui_theme.row('warning'))
        for col, title in zip(columns, titles):
            tree.heading(col, text=title)
            tree.column(col, width=300 if col in ('root', 'error', 'scope') else 140, stretch=False)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return tree

    def refresh(self):
        self.refresh_version += 1
        version = self.refresh_version
        path, search = self.db_path, self.search.get()
        configured = [dict(source) for source in self.configured]
        self.db_label.config(text=f'読み込み中: {path}')
        self.reading = True
        self.read_started = time.monotonic()
        self.read_phase = '前回の表示データを読み込み中'
        self.read_status.set('読み込み中：前回の表示データを確認しています…')
        self.read_progress.start(15)

        def worker():
            try:
                cache = path.with_suffix('.view.json')
                try:
                    saved = json.loads(cache.read_text(encoding='utf-8'))
                    inventory = saved.get('inventory')
                    if (saved.get('format') == 1 and saved.get('search') == search
                            and isinstance(inventory, list) and len(inventory) == 7
                            and all(isinstance(rows, list) for rows in inventory[:6])
                            and isinstance(inventory[6], dict)):
                        self.events.put(('cached_refresh', (version, saved['inventory'], None)))
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
                # Show acquisition dates immediately, even before large file tables
                # have finished aggregating on the first launch without a cache.
                with catalog.connect(path, readonly=True) as db:
                    sources = [dict(r) for r in db.execute(
                        "SELECT * FROM sources WHERE lower(server)='sqserver' ORDER BY root")]
                self.events.put(('saved_sources', (version, sources)))
                result = self.read_inventory(path, search, configured,
                    lambda phase: self.events.put(('read_phase', (version, phase))))
                self.events.put(('refresh', (version, result, None)))
                if path == catalog.DEFAULT_DB and not search and version == self.refresh_version:
                    try:
                        temporary = cache.with_suffix('.json.tmp')
                        temporary.write_text(json.dumps(dict(format=1, search=search, inventory=result),
                                                        ensure_ascii=False), encoding='utf-8')
                        temporary.replace(cache)
                    except OSError:
                        pass
            except Exception as exc:
                self.events.put(('refresh', (version, None, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def read_inventory(path, search, configured, progress=lambda phase: None):
        progress('保存済みDBの件数・容量を集計中')
        source_rows = catalog.sources(path)
        if path == catalog.DEFAULT_DB:
            for source in configured:
                if not any(Path(r['root']) == Path(source['root']) for r in source_rows):
                    source_rows.append(dict(source, status='未取得' if source['root'] else '共有パス未設定',
                                            file_count='', bytes='', folder_count='', last_success=None,
                                            last_attempt=None, error=''))
        rows, analysis = [], []
        source_rows = [r for r in source_rows if r['server'].casefold() == 'sqserver']
        progress('保存済みDBのグループ・実験一覧を集計中')
        groups, entries = inventory_views.sq_inventory(path)
        groups = [r for r in groups if r['server'].casefold() == 'sqserver']
        entries = [r for r in entries if r['server'].casefold() == 'sqserver']
        progress('コピー履歴と解析着手状況を照合中')
        sq_workflow.enrich(groups, entries)
        counts = recent_progress.inventory_counts(path)
        by_root = {}
        for group in groups:
            by_root.setdefault(group['root'], []).append(group)
        for root, root_groups in by_root.items():
            valid = [g for g in root_groups if g['folder_count'] is not None]
            total = sum(g['folder_count'] for g in valid)
            counted = valid and all(isinstance(g['started_count'], int) for g in valid)
            started = sum(g['started_count'] for g in valid) if counted else None
            stocked = all(g['group'] == 'Stocked_Data' for g in root_groups)
            counts[str(PureWindowsPath(root)).casefold()] = dict(
                direct_folders=total if valid else '未取得',
                analysis_started='' if stocked else started if counted else '確認不可',
                analysis_pending='' if stocked else total-started if counted else '確認不可',
                analysis_rate=f'{started / total:.1%}' if counted and total else '',
                count_note='; '.join(sorted({g['history_note'] for g in root_groups if g['history_note']})))
        if search:
            groups = [r for r in groups if search.casefold() in ' '.join(str(v) for v in r.values()).casefold()]
            entries = [r for r in entries if search.casefold() in ' '.join(str(v) for v in r.values()).casefold()]
        history = [r for r in catalog.update_history(path) if r['server'].casefold() == 'sqserver']
        return source_rows, rows, analysis, groups, entries, history, counts

    def finish_refresh(self, version, result, error, final=True):
        if version != self.refresh_version:
            return
        if final:
            self.reading = False
            self.read_progress.stop()
            elapsed = time.monotonic() - self.read_started
            self.read_status.set(f'読み込み失敗（{elapsed:.1f}秒）' if error else
                                 f'読み込み完了（{elapsed:.1f}秒）')
        else:
            self.read_phase = '前回の一覧を表示済み／保存済みDBを再集計中'
        if error is not None:
            self.db_label.config(text=f'読み込み失敗: {self.db_path}')
            messagebox.showerror('データベースを読めません', error)
            return
        source_rows, rows, analysis, groups, entries, history, self.recent_counts = result
        if not self.busy:
            for row in history:
                if row['status'] == '実行中':
                    row['status'] = '実行中／終了未確認'
        self.display_rows = rows
        datasets = dict(sq_groups=groups, sq_entries=entries, updates=history)
        if self.busy:
            datasets['updates'] = self.views['updates']['rows']
        else:
            datasets['updates'] = [r for r in self.live_jobs.values() if r['status'] == '未実行'] + history
        for key, records in datasets.items():
            self.views[key]['rows'] = records
        for row in datasets['updates']:
            self.decorate_update(row)
        for tree, records in [(self.source_tree, source_rows)] + [(v['tree'], v['rows']) for v in self.views.values()]:
            if self.busy and tree is self.views['updates']['tree']:
                continue
            tree.delete(*tree.get_children())
            for index, row in enumerate(records):
                volume_tag = rackstation_view.volume_rate(row)[1] if tree is self.source_tree else ''
                tag = (sq_entry_tag(row) if tree is self.views['sq_entries']['tree'] else
                       row.get('status') if row.get('status') in ('完了', '失敗', '中止', '実行中') else
                       volume_tag or ('even' if index % 2 == 0 else ''))
                tree.insert('', 'end', iid=str(index), values=[self.cell(row, key) for key in tree['columns']],
                            tags=(tag,))
        dates = sorted({r['last_success'] for r in source_rows + groups + entries if r.get('last_success')})
        acquired = f'{dates[0]} ～ {dates[-1]}' if len(dates) > 1 else dates[0] if dates else '未取得'
        self.db_label.config(text=f'保存済み情報を表示中: {self.db_path}\n取得日時 UTC: {acquired}（対象ごとの日時は一覧で確認できます）')
        self.fit_sq_columns()
        self.update_total()
        self.scope_candidates()

    def show_saved_sources(self, version, records):
        if version != self.refresh_version or self.source_tree.get_children():
            return
        for index, row in enumerate(records):
            values = [self.cell(row, key) if key in row or key in ('volume', 'volume_rate') else '集計中'
                      for key in self.source_tree['columns']]
            self.source_tree.insert('', 'end', iid=str(index), values=values)
        self.db_label.config(text=f'保存済みの取得日時を表示中: {self.db_path}（一覧を読み込み中）')

    def fit_sq_columns(self):
        view = self.views['sq_entries']
        tree = view['tree']
        font = tkfont.nametofont('TkDefaultFont')
        heading_font = tkfont.nametofont('TkHeadingFont')
        for column in view['columns']:
            width = max([heading_font.measure(tree.heading(column, 'text'))] +
                        [font.measure(str(self.cell(row, column))) for row in view['rows']]) + 20
            tree.column(column, width=width, minwidth=width, stretch=column == 'name',
                        anchor='e' if column in ('exsv_t_tdms', 'exzsv_tdms') else 'w')

    def decorate_update(self, row):
        root = str(PureWindowsPath(row['root'])).casefold()
        row['share_name'] = row['root'].replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
        row.update(direct_folders='対象外', analysis_started='', analysis_pending='', analysis_rate='', count_note='')
        row.update(self.recent_counts.get(root, {}))

    @staticmethod
    def cell(row, key):
        if key == 'volume':
            # 共有が載っているディスク全体（同じディスク上の共有は同じ値になる）
            if row.get('volume_total') is None:
                return '未取得（要更新）'
            return f"{App.cell(dict(bytes=row['volume_used']), 'bytes')} / {App.cell(dict(bytes=row['volume_total']), 'bytes')}"
        if key == 'volume_rate':
            return rackstation_view.volume_rate(row)[0]
        value = row.get(key)
        if key == 'bytes' and isinstance(value, (int, float)):
            unit, divisor = ('TB', 10**12) if value >= 10**12 else ('GB', 10**9)
            if 0 < value < divisor / 1000:
                return f'<0.001 {unit}'
            return f'{value / divisor:,.3f} {unit}'
        if key == 'elapsed' and value is not None:
            return f'{value:.1f}'
        if value is not None:
            return value
        if key in ('last_success', 'last_attempt', 'finished'):
            return '未取得'
        if key == 'elapsed':
            return ''
        if key in ('folder_count', 'archive_count'):
            return '未確定'
        return '未作成'

    def active_view(self):
        return next(v for v in self.views.values() if str(v['tab']) == self.notebook.select())

    def show_row_details(self, tree):
        selection = tree.selection()
        if not selection:
            return
        dialog = tk.Toplevel(self)
        dialog.title('記録の詳細（選択してコピーできます）')
        dialog.geometry('1000x600')
        text = tk.Text(dialog, wrap='word')
        scroll = ttk.Scrollbar(dialog, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        for column, value in zip(tree['columns'], tree.item(selection[0], 'values')):
            text.insert('end', f"{tree.heading(column, 'text')}:\n{value}\n\n")
        text.config(state='disabled')

    def update_total(self):
        view = self.active_view()
        rows = view['rows']
        text = f'{len(rows):,}'
        self.total.config(text=text)

    def settings(self):
        dialog = tk.Toplevel(self)
        dialog.title('保存先設定（1共有につき1行）')
        dialog.transient(self)
        dialog.grab_set()
        picker = ttk.Frame(dialog)
        picker.pack(fill='x', padx=10, pady=10)
        ttk.Label(picker, text='サーバー').pack(side='left')
        selected_server = tk.StringVar(value=self.target_server.get() or 'SQserver')
        ttk.Combobox(picker, textvariable=selected_server,
                     values=list(dict.fromkeys(s['server'] for s in self.configured)),
                     state='readonly', width=18).pack(side='left', padx=6)

        def browse():
            server = selected_server.get()
            root = filedialog.askdirectory(parent=dialog, title=f'{server}：登録するフォルダを選択',
                                           initialdir='\\\\' + server + '\\', mustexist=True)
            if not root:
                return
            root = str(Path(root))
            lines = editor.get('1.0', 'end').splitlines()
            # Replace the empty default; keep other shares when adding another folder.
            kept = []
            for line in lines:
                parts = [part.strip() for part in line.split('|')]
                if len(parts) == 3 and parts[0] == server:
                    if not parts[2] or Path(parts[2]) == Path(root):
                        continue
                if line.strip():
                    kept.append(line)
            kept.append(f'{server} | {selected_source_kind(server, root)} | {root}')
            editor.delete('1.0', 'end')
            editor.insert('1.0', '\n'.join(kept))

        ttk.Button(picker, text='フォルダを選んで追加', command=browse).pack(side='left')
        ttk.Label(dialog, text='フォルダを選ぶと下の設定に追加されます。複数ある場合は繰り返し選び、最後に「保存」を押してください。\nSQserverはbackup_AN1、data、data_stocked、data内のAN1などを選べます。').pack(padx=10)
        ttk.Label(dialog, text='サーバー名 | 種別 | 絶対パス\n種別: analysis（解析）、experiment（SQ自動分類）、sq_recent、sq_data、sq_stocked\nSQserverはbackup_*・data・data_stockedを含む共有、または各グループのパスを指定します。\n共有名で分類できない場合はsq_recent等を選択。backup_*が別共有なら各共有を1行ずつ登録。\n空欄は更新対象外。解析用は実験フォルダを直接含む共有を指定。親子で重なるパスは登録しないでください。').pack(padx=10, pady=10)
        editor_frame = ttk.Frame(dialog)
        editor_frame.pack(fill='both', expand=True, padx=10)
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(0, weight=1)
        editor = tk.Text(editor_frame, width=110, height=15, wrap='none')
        vertical_scroll = ttk.Scrollbar(editor_frame, orient='vertical', command=editor.yview)
        horizontal_scroll = ttk.Scrollbar(editor_frame, orient='horizontal', command=editor.xview)
        editor.configure(yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set)
        editor.grid(row=0, column=0, sticky='nsew')
        vertical_scroll.grid(row=0, column=1, sticky='ns')
        horizontal_scroll.grid(row=1, column=0, sticky='ew')
        editor.insert('1.0', '\n'.join(f"{s['server']} | {s['kind']} | {s['root']}" for s in self.configured))

        def save():
            try:
                records = []
                roots = []
                for line in editor.get('1.0', 'end').splitlines():
                    if not line.strip():
                        continue
                    server, kind, root = [part.strip() for part in line.split('|')]
                    if not server or kind not in catalog.SOURCE_KINDS:
                        raise ValueError('サーバー名と種別を確認してください。')
                    if root:
                        candidate = Path(root)
                        if not candidate.is_absolute():
                            raise ValueError('共有を含む絶対パスを入力してください。')
                        if any(candidate == p or candidate in p.parents or p in candidate.parents for p in roots):
                            raise ValueError('保存先に重複または親子の重なりがあります。')
                        roots.append(candidate)
                    records.append(dict(server=server, kind=kind, root=root))
                temporary = CONFIG.with_suffix('.tmp')
                temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(CONFIG)
                self.configured = records
                self.select_server()
                dialog.destroy()
            except (ValueError, OSError) as exc:
                messagebox.showerror('設定エラー', str(exc), parent=dialog)
        ttk.Button(dialog, text='保存', command=save).pack(pady=10)

    def select_server(self):
        names = list(dict.fromkeys(s['server'] for s in self.configured if s['server'].casefold() == 'sqserver'))
        self.server_box['values'] = names
        if self.target_server.get() not in names:
            self.target_server.set(names[0] if names else '')
        # 保存先設定に登録した共有をすべて選べるようにする（backup_*・data・data_stocked など、設定の順）
        roots = [s['root'] for s in self.configured if s['server'] == self.target_server.get() and s['root']]
        self.root_box['values'] = roots
        if self.target_root.get() not in roots:
            self.target_root.set(roots[0] if roots else '')
        self.target_scope.set('')
        self.scope_candidates()

    def scope_candidates(self):
        if not hasattr(self, 'views'):
            return
        root = Path(self.target_root.get())
        candidates = set()
        for row in self.views.get('analysis', {'rows': []})['rows']:
            if Path(row['root']) == root:
                candidates.add(row['experiment'])
        for row in self.views['sq_entries']['rows']:
            if Path(row['root']) == root:
                candidates.add(row['relative_path'])

    def change_root(self, event=None):
        self.target_scope.set('')
        self.scope_candidates()

    def configured_source(self, root):
        return next((dict(s) for s in self.configured if s['root'] and Path(s['root']) == Path(root)), None)

    def update_server(self):
        if self.target_server.get().casefold() == 'sqserver':
            self.run_jobs(sq_workflow.jobs_for_stage(self.configured, 'Recent_Data'))
            return
        if any(s['server'] == self.target_server.get() and not s['root'].strip() for s in self.configured):
            messagebox.showinfo('共有パス未設定', '選択サーバーにパス未設定の行があります。保存先設定を確認してください。')
            return
        jobs = [(dict(s), '.') for s in self.configured
                if s['server'] == self.target_server.get() and s['root'].strip()]
        self.run_jobs(jobs)

    def update_root(self):
        source = self.configured_source(self.target_root.get())
        self.run_jobs([(source, '.')] if source else [])

    def update_scope(self):
        source = self.configured_source(self.target_root.get())
        try:
            scope = catalog.normalize_scope(self.target_scope.get())
            if scope == '.':
                raise ValueError('更新する実験の相対パスを選択または入力してください。')
        except ValueError as exc:
            messagebox.showerror('更新対象', str(exc))
            return
        self.run_jobs([(source, scope)] if source else [])

    def update_selected(self):
        if self.db_path != catalog.DEFAULT_DB:
            messagebox.showinfo('更新対象', 'ローカルDBを表示してから実験を選択してください。')
            return
        view = self.active_view()
        if view not in (self.views['analysis'], self.views['sq_entries']):
            messagebox.showinfo('更新対象', '解析用4台またはSQserver実験一覧で更新する行を選択してください。')
            return
        jobs = []
        for item in view['tree'].selection():
            row = view['rows'][int(item)]
            source = self.configured_source(row['root'])
            if source is None:
                messagebox.showerror('未設定の共有', f"保存先設定に登録してください: {row['root']}")
                return
            scope = row['experiment'] if view is self.views['analysis'] else row['relative_path']
            if not any(Path(s['root']) == Path(source['root']) and p == scope for s, p in jobs):
                jobs.append((source, scope))
        self.run_jobs(jobs)

    def run_jobs(self, jobs):
        if self.busy or getattr(self, 'rack_busy', False):
            return
        if not jobs:
            messagebox.showinfo('更新対象なし', '保存先の共有パスを設定し、更新するサーバー・共有・実験を選んでください。')
            return
        lock = ScanLock('実験ファイルDB（Data_server の更新）')
        if not lock.acquire():
            messagebox.showinfo('走査中', f'ほかの走査が実行中です。終わってから実行してください。\n{lock.holder()}')
            return
        self.db_path = catalog.DEFAULT_DB
        self.busy = True
        self.cancel.clear()
        self.batch_started = time.monotonic()
        self.live_jobs = {str(i): dict(server=s['server'], root=s['root'], scope=p, status='待機中',
                                     file_count=0, folder_count=0, elapsed=0, started='', finished='', error='')
                          for i, (s, p) in enumerate(jobs)}
        for button in self.buttons:
            button.config(state='disabled')
        for box in (self.server_box, self.root_box):
            box.config(state='disabled')
        self.stop_button.config(state='normal')
        self.progress_bar.start(12)
        self.notebook.select(self.views['updates']['tab'])
        tree = self.views['updates']['tree']
        tree.delete(*tree.get_children())
        self.status.set(f'{len(jobs)}対象の更新を開始します。ネットワーク応答待ちは中止まで時間がかかる場合があります。')
        self.render_live_jobs()
        self.update_total()

        def worker():
            try:
                scan()
            finally:
                lock.release()

        def scan():
            failures = 0
            for i, (source, scope) in enumerate(jobs):
                key = str(i)
                if self.cancel.is_set():
                    self.events.put(('job', (key, dict(status='未実行', error='中止要求により未実行'))))
                    continue
                started = time.monotonic()
                self.events.put(('job', (key, dict(status='実行中', started=catalog.now(), _started=started))))
                try:
                    count = catalog.scan_source(source, scope=scope, cancel=self.cancel,
                        on_progress=lambda value, key=key: self.events.put(('job', (key, dict(
                            file_count=value['file_count'], folder_count=value['folder_count'],
                            elapsed=value['elapsed'], error=value['current'])))))
                    result = dict(status='完了', file_count=count, error='')
                except catalog.ScanCancelled as exc:
                    result = dict(status='中止', error=str(exc))
                except Exception as exc:
                    failures += 1
                    result = dict(status='失敗', error=str(exc))
                result.update(finished=catalog.now(), elapsed=time.monotonic()-started)
                self.events.put(('job', (key, result)))
            self.events.put(('done', f'更新終了: {len(jobs)}対象 / 失敗 {failures}対象。' +
                             ('中止・未実行の対象は前回記録を保持しました。' if self.cancel.is_set() else '')))
        threading.Thread(target=worker, daemon=True).start()

    def render_live_jobs(self):
        view = self.views['updates']
        view['rows'] = list(self.live_jobs.values())
        tree = view['tree']
        # Update in place so scrolling and selection remain stable during a scan.
        for key, row in self.live_jobs.items():
            self.decorate_update(row)
            values = [self.cell(row, col) for col in tree['columns']]
            if tree.exists('job_' + key):
                tree.item('job_' + key, values=values, tags=(row['status'],))
            else:
                tree.insert('', 'end', iid='job_' + key, values=values, tags=(row['status'],))

    def poll(self):
        try:
            while True:
                event, text = self.events.get_nowait()
                if event == 'read_phase':
                    version, phase = text
                    if version == self.refresh_version:
                        self.read_phase = phase
                    continue
                if event == 'cached_refresh':
                    self.finish_refresh(*text, final=False)
                    continue
                if event == 'saved_sources':
                    self.show_saved_sources(*text)
                    continue
                if event == 'refresh':
                    self.finish_refresh(*text)
                    continue
                if event == 'job':
                    key, changes = text
                    self.live_jobs[key].update(changes)
                    self.render_live_jobs()
                    continue
                self.status.set(text)
                if event == 'done':
                    self.busy = False
                    for button in self.buttons:
                        button.config(state='normal')
                    self.stop_button.config(state='disabled')
                    self.progress_bar.stop()
                    self.server_box.config(state='readonly')
                    self.root_box.config(state='readonly')
                    self.refresh()
        except queue.Empty:
            pass
        if self.reading:
            elapsed = time.monotonic() - self.read_started
            self.read_status.set(f'読み込み中：{self.read_phase}… ／ 経過 {elapsed:.0f}秒（サーバーの再取得は行っていません）')
        if self.busy:
            for row in self.live_jobs.values():
                if row['status'] == '実行中' and '_started' in row:
                    row['elapsed'] = time.monotonic() - row['_started']
            self.render_live_jobs()
            elapsed = time.monotonic() - self.batch_started
            finished = sum(r['status'] in ('完了', '失敗', '中止', '未実行') for r in self.live_jobs.values())
            self.status.set(f"対象完了 {finished}/{len(self.live_jobs)} / 経過 {elapsed:.0f}秒 — 中止ボタンで更新を停止できます。")
        self.after(150, self.poll)

    def local(self):
        self.db_path = catalog.DEFAULT_DB
        self.refresh()

    def show_files(self):
        dialog = tk.Toplevel(self)
        dialog.title('ファイル一覧（検索条件を適用・500件ずつ表示）')
        dialog.geometry('1150x520')
        columns = ('server', 'root', 'relative_path', 'size', 'modified_ns')
        frame = ttk.Frame(dialog)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings')
        for key, title, width in zip(columns, ('サーバー', '共有パス', '相対パス', 'サイズ(bytes)', '更新日時(epoch ns)'), (120, 230, 520, 130, 180)):
            tree.heading(key, text=title)
            tree.column(key, width=width, stretch=False)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        controls = ttk.Frame(dialog, padding=8)
        controls.pack(fill='x')
        page_label = ttk.Label(controls)
        page_label.pack(side='left', padx=8)
        offset = 0
        search, db_path = self.search.get(), self.db_path

        def load(delta=0):
            nonlocal offset
            offset = max(0, offset + delta)
            try:
                records = catalog.find_files(db_path, search, limit=501, offset=offset)
            except Exception as exc:
                messagebox.showerror('読み込み失敗', str(exc), parent=dialog)
                return
            tree.delete(*tree.get_children())
            for row in records[:500]:
                tree.insert('', 'end', values=[row[key] for key in columns])
            page_label.config(text=f'{offset + 1}件目から {min(len(records), 500)}件表示')
            previous.config(state='normal' if offset else 'disabled')
            following.config(state='normal' if len(records) > 500 else 'disabled')

        def copy_path():
            selected = tree.selection()
            if selected:
                values = tree.item(selected[0], 'values')
                dialog.clipboard_clear()
                dialog.clipboard_append(str(Path(values[1]) / values[2]))

        previous = ttk.Button(controls, text='前へ', command=lambda: load(-500))
        previous.pack(side='left')
        following = ttk.Button(controls, text='次へ', command=lambda: load(500))
        following.pack(side='left', padx=5)
        ttk.Button(controls, text='選択ファイルのパスをコピー', command=copy_path).pack(side='left')
        load()

    def open_db(self):
        filename = filedialog.askopenfilename(filetypes=[('SQLite', '*.sqlite3'), ('All', '*.*')])
        if filename:
            try:
                catalog.sources(filename)
                catalog.summary(filename)
            except Exception as exc:
                messagebox.showerror('DBを開けません', str(exc))
                return
            self.db_path = Path(filename)
            self.refresh()

    def backup(self):
        filename = filedialog.asksaveasfilename(defaultextension='.sqlite3', initialfile='inventory_snapshot.sqlite3')
        if filename:
            try:
                catalog.export_snapshot(filename, self.db_path)
                self.status.set(f'配布用DBを保存しました: {filename}')
            except Exception as exc:
                messagebox.showerror('保存失敗', str(exc))

    def export_csv(self):
        filename = filedialog.asksaveasfilename(defaultextension='.csv', initialfile='inventory_summary.csv')
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8-sig', newline='') as stream:
                    view = self.active_view()
                    writer = csv.DictWriter(stream, fieldnames=view['columns'], extrasaction='ignore')
                    writer.writeheader()
                    writer.writerows({key: self.cell(row, key) for key in view['columns']} for row in view['rows'])
                self.status.set(f'表示中の集計を保存しました: {filename}')
            except OSError as exc:
                messagebox.showerror('保存失敗', str(exc))

    def publish_settings(self):
        """台帳を共有フォルダへ書き出す処理（publish.py）の有効・無効と手動実行。"""
        try:
            from . import publish
        except ImportError:
            import publish
        window = tk.Toplevel(self)
        window.title('台帳の共有')
        window.configure(bg=ui_theme.BG)
        window.transient(self)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=f'書き出し先: {publish.PUBLISH_DIR}\n'
                              f'台帳: {", ".join(publish.FILES.values())}\n'
                              '計測PCはこのフォルダの同期から、実験のアップロード後の進捗（解析着手・ANAL・BNAL）を確認します。',
                  wraplength=640, justify='left').pack(anchor='w')
        settings = publish.load_settings()
        enabled = tk.BooleanVar(value=settings['enabled'])
        task_state = tk.StringVar()
        last_run = tk.StringVar()

        def describe():
            registered = publish.task_registered()
            task_state.set(('タスク スケジューラ: 登録済み（毎時 5 分に起動）' if registered else
                            'タスク スケジューラ: 未登録（「タスクを登録」を押してください）') +
                           ('' if enabled.get() else '　※自動実行は無効のため、起動しても何もしません'))
            status = publish.load_status()
            if not status:
                last_run.set('前回の実行: なし')
                return
            exports = '　'.join(f"{publish.FILES[name]} {'%s行' % r['rows'] if r['ok'] else '失敗'}"
                               for name, r in status.get('exports', {}).items())
            failed = [f'{name}: {r["result"]}' for name, r in status.get('scans', {}).items()
                      if not r['result'].startswith('完了')]
            last_run.set(f"前回の実行（UTC）: {status.get('started')} ～ {status.get('finished')}"
                         f"{'（走査なし）' if not status.get('scanned') else ''}\n{exports}" +
                         ('\n走査の注意: ' + ' ／ '.join(failed) if failed else ''))

        def toggle():
            current = publish.load_settings()
            current['enabled'] = enabled.get()
            try:
                publish.save_settings(current)
            except OSError as exc:
                messagebox.showerror('保存失敗', str(exc), parent=window)
            describe()

        def install():
            result = publish.install_task()
            if result.returncode != 0:
                messagebox.showerror('タスクの登録', (result.stderr or result.stdout).strip(), parent=window)
            describe()

        def start(*args):
            publish.start_background(*args)
            messagebox.showinfo('台帳の共有', 'バックグラウンドで開始しました。終わったら「表示を更新」で結果を確認できます。'
                                f'\n経過はログに記録されます: {publish.LOG_PATH}', parent=window)

        ttk.Checkbutton(frame, text='1時間ごとの自動実行を有効にする（走査してから書き出す）', variable=enabled,
                        command=toggle).pack(anchor='w', pady=(10, 0))
        ttk.Label(frame, textvariable=task_state, style='Muted.TLabel').pack(anchor='w')
        ttk.Label(frame, text=f"毎時: SQserver Recent_Data と解析サーバーを走査。{settings['nightly_hour']}時台: "
                              'Archived/Stocked と BNAL TDMS 数も確認。ほかの走査が実行中なら走査を省き、保存済みDBから書き出します。',
                  style='Muted.TLabel', wraplength=640, justify='left').pack(anchor='w')
        ttk.Label(frame, textvariable=last_run, wraplength=640, justify='left').pack(anchor='w', pady=(10, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=(12, 0))
        ttk.Button(buttons, text='今すぐ実行（走査＋書き出し）', style='Primary.TButton',
                   command=lambda: start()).pack(side='left')
        ttk.Button(buttons, text='書き出しのみ', command=lambda: start('--no-scan')).pack(side='left', padx=6)
        ttk.Button(buttons, text='表示を更新', command=describe).pack(side='left')
        ttk.Button(buttons, text='タスクを登録', command=install).pack(side='left', padx=6)
        ttk.Button(buttons, text='ログを開く',
                   command=lambda: publish.LOG_PATH.exists() and os.startfile(publish.LOG_PATH)).pack(side='left')
        ttk.Button(buttons, text='書き出し先を開く',
                   command=lambda: publish.PUBLISH_DIR.exists() and os.startfile(publish.PUBLISH_DIR)).pack(side='left', padx=6)
        describe()

    def close(self):
        if getattr(self, 'rack_busy', False):
            self.rack_cancel.set()
            self.main_tabs.select(self.active_analysis_panel)
            self.status.set('中止を要求しました。Analysis_serverの処理終了後に閉じてください。')
            return
        if self.busy:
            self.cancel.set()
            self.status.set('中止を要求しました。ネットワークの応答と終了を待ってから閉じてください。')
            return
        self.destroy()


if __name__ == '__main__':
    App().mainloop()
