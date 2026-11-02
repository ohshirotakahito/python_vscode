"""Saved inventory references and explicit, journaled ANAL archive/restore operations."""
from collections import Counter
from datetime import datetime
import os
from pathlib import Path
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from shared import catalog, progress, rack_store, seq_targets
# 履歴つきの移動・取り消しは shared/file_moves.py（Analyzer と共有）。既存の呼び出し側のため同じ名前で使えるようにしている。
from shared.file_moves import (  # noqa: F401
    journal_dir, scan_anal, checksum, validate_operations, result_folder,
    deletion_plan, undo_plan, move_plan, replace_retrying, execute_moves,
)
from filemanager import bnal_stock

BNAL_MODE = 'BNAL用の退避・復帰（ANAL ⇄ stocked）'
UI_FONT = 'Yu Gothic UI'
STAGE_COLORS = dict(todo='#f0c060', restore='#f0c060', wait='#7ec8e3', done='#7fd88f')


def load_inventory(path):
    with catalog.connect(path, readonly=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rack_samples'").fetchone():
            raise ValueError('保存済みのAnalysis_server調査がありません。実験データベースでAnalysis_serverを更新してください。')
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM rack_samples ORDER BY root, experiment, sample')]
    for row in rows:
        count, distinct = row.get('anal_tdms'), row.get('anal_distinct')
        row['mixed'] = max(0, count - distinct) if count is not None and distinct is not None else None
        original = row.get('t_tdms')
        row['expected'] = original + (row.get('t_stocked_tdms') or 0) if original is not None else None
        row['anal_path'] = str(Path(row['root']) / row['experiment'] / row['sample'] /
                               (row['sample'] + '_10k_Sample') / 'T' / 'ANAL')
    return rows


def analysis_groups(rows):
    """Dates are comparison aids, never an automatic decision to remove files."""
    dates = sorted({r['group'] for r in rows})
    groups = []
    for date in dates:
        files = [r for r in rows if r['group'] == date]
        groups.append(dict(group=date, age=('新しい' if date == dates[-1] else '古い' if date == dates[0] else '中間')
                           if len(dates) > 1 else '単一日', count=len(files),
                           anal=sum(r['location'] != 'stocked' for r in files),
                           stocked=sum(r['location'] == 'stocked' for r in files),
                           duplicates=sum(r['candidate'] for r in files),
                           unique=sum(not r['candidate'] for r in files),
                           basis=' / '.join(sorted({r['date_source'] for r in files}))))
    return groups


def bnal_row_state(row):
    """(要対応か, 表示) for one BNAL@ row: unfinished against ANAL+stocked, or ANAL files still set aside.

    The state itself is shared.progress.bnal_state (the same as the database's BNAL進捗 and Analyzer)."""
    tdms, expected, stocked = row.get('anal_tdms'), row.get('expected'), row.get('sample_stocked') or 0
    if tdms is None or expected is None:
        return True, '未計数・要DB更新'
    _, _, tag = progress.bnal_state(tdms, expected)
    text = {'unknown': 'ANALなし・要確認', 'complete': 'BNAL完了', 'unstarted': f'BNAL未着手 0/{expected}（BNAL@ 0件）',
            'partial': f'BNAL未完了 {tdms}/{expected}（残り {expected - tdms}）',
            'excess': f'超過 {tdms - expected}件・要確認'}[tag]
    if stocked:
        text += f'／ANAL/stocked {stocked}件' + ('（復帰待ち）' if tag == 'complete' else '')
    return tag != 'complete' or bool(stocked), text


def refresh_db(path, keys):
    """Re-read each (root, experiment, sample) on its share and save it, as the database app's scan would."""
    for root, experiment, sample in keys:
        rack_store.refresh_sample(path, root, experiment, sample)
    return len(keys)


def scan_warning(path, roots):
    """Warning text when the database app has recorded a running Analysis_server scan on any of these shares.

    Only a warning: a crashed database app leaves '実行中' behind, so nothing is ever blocked by it."""
    try:
        with catalog.connect(path, readonly=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rack_roots'").fetchone():
                return ''
            running = [dict(r) for r in db.execute("SELECT root, last_attempt FROM rack_roots WHERE status='実行中'")]
    except sqlite3.Error:
        return ''
    wanted = {catalog.root_identity(r) for r in roots}
    hits = [r for r in running if catalog.root_identity(r['root']) in wanted]
    if not hits:
        return ''
    return ('⚠ 実験データベースでこの共有を調査中と記録されています（'
            + '、'.join(f"{r['root']} 開始 {r['last_attempt'] or '不明'}" for r in hits) + '）。\n'
            '調査中にファイルを動かすと、その調査結果が中途半端になることがあります。可能なら調査の終了を待ってください。'
            '移動後はこのサンプルのDBを自動更新します。\n'
            '（データベースのアプリが途中で終了した場合は、調査していなくてもこの記録が残ることがあります。）')


def row_identity(row):
    return tuple(row.get(k) for k in ('root', 'experiment', 'sample', 'target'))


def extract_clashes(files, taken_out):
    """Numbers that would appear more than once in ANAL after taking these stocked files out."""
    staying = [r['number'] for r in files if r['location'] != 'stocked']
    moving = [r['number'] for r in taken_out]
    counts = Counter(n for n in staying + moving if n is not None)
    return sorted(n for n in set(moving) if n is not None and counts[n] > 1)


def matches_inventory_query(row, query, exact_experiment=False):
    """Space-separated words must all appear (share, experiment, sample, target); exact: the experiment name."""
    query = query.casefold().strip()
    if not query:
        return True
    if exact_experiment:
        return query == str(row['experiment']).casefold().strip()
    text = f"{row['root']} {row['experiment']} {row['sample']} {row.get('target', '')}".casefold()
    return all(word in text for word in query.split())


ALL_SHARES = 'すべての共有'


def share_label(root):
    return str(root).rstrip('\\/')


def add_share_filter(panel, parent):
    """'共有' drop-down next to the search box; its choices come from the loaded inventory."""
    panel.share = tk.StringVar(value=ALL_SHARES)
    ttk.Label(parent, text='共有').pack(side='left', padx=(6, 4))
    panel.share_box = ttk.Combobox(parent, textvariable=panel.share, state='readonly', width=26, values=(ALL_SHARES,))
    panel.share_box.pack(side='left', padx=(0, 6))
    panel.share_box.bind('<<ComboboxSelected>>', lambda _: panel.render())


def update_shares(panel):
    shares = sorted({share_label(r['root']) for r in panel.inventory}, key=str.casefold)
    panel.share_box.configure(values=(ALL_SHARES, *shares))
    if panel.share.get() not in shares:
        panel.share.set(ALL_SHARES)


def share_matches(panel, row):
    return panel.share.get() == ALL_SHARES or share_label(row['root']) == panel.share.get()


class AnalPanel(ttk.Frame):
    def __init__(self, owner, entity='ANAL', loader=load_inventory):
        super().__init__(owner.tabs, padding=10)
        self.owner, self.busy, self.plan = owner, False, None
        self.entity, self.loader = entity, loader
        self.log_dir = journal_dir(entity)
        self.events = queue.Queue()
        self.inventory, self.files = [], []
        self.marked = set()
        # BNAL tab opens on the resume workflow: unfinished targets are what is usually looked for there.
        self.mode = tk.StringVar(value=BNAL_MODE if entity == 'BNAL' else f'退避の整理（stocked → {entity}）')
        self.db = tk.StringVar(value=str(catalog.DEFAULT_DB))
        self.query = tk.StringVar()
        self.exact_experiment = tk.BooleanVar(value=False)
        self.only_flags = tk.BooleanVar(value=True)
        top = ttk.Frame(self)
        top.pack(fill='x')
        ttk.Label(top, text='データベース').pack(side='left')
        ttk.Entry(top, textvariable=self.db).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(top, text='DBを選択', command=self.choose_db).pack(side='left')
        ttk.Button(top, text='保存済み調査を読み込む', command=self.load).pack(side='left', padx=6)
        filters = self.filters = ttk.Frame(self)
        filters.pack(fill='x', pady=6)
        ttk.Label(filters, text='実験・サンプル・共有を検索').pack(side='left')
        ttk.Entry(filters, textvariable=self.query).pack(side='left', fill='x', expand=True, padx=6)
        add_share_filter(self, filters)
        ttk.Checkbutton(filters, text='実験名を完全一致', variable=self.exact_experiment,
                        command=self.render).pack(side='left', padx=(0, 6))
        self.flags_check = ttk.Checkbutton(filters, text='重複候補・超過・退避ありのみ', variable=self.only_flags,
                                           command=self.render)
        self.flags_check.pack(side='left')
        self.stocked_only = tk.BooleanVar(value=False)
        if entity == 'ANAL':
            # Samples whose ANAL files are set aside in ANAL/stocked, most first (BNAL waiting or forgotten).
            ttk.Checkbutton(filters, text='ANAL退避中のみ（多い順）', variable=self.stocked_only,
                            command=self.render).pack(side='left', padx=(6, 0))
        self.query.trace_add('write', lambda *_: self.render())
        self.samples = self.tree(('root', 'experiment', 'sample', 'target', 'anal', 'expected', 'mixed', 'stocked', 'state', 'date'),
                                 ('共有', '実験', 'サンプル', '対象フォルダ' if entity == 'BNAL' else 'T直下TDMS', f'{entity}件数', '基準ANAL件数' if entity == 'BNAL' else '基準件数', '同番号候補', '退避件数', '状態', 'DB調査日時（UTC）'), 4)
        for tag, color in [('stocked', '#25464b'), ('duplicate', '#58432a'), ('both', '#4b365e')]:
            self.samples.tag_configure(tag, background=color, foreground='#ffffff')
        self.samples.bind('<<TreeviewSelect>>', lambda _: self.sample_selected())
        controls = ttk.Frame(self)
        controls.pack(fill='x', pady=4)
        self.scan_button = ttk.Button(controls, text='選択した保存先を再調査', command=self.scan)
        self.scan_button.pack(side='left')
        ttk.Button(controls, text='選択サンプルのDBを更新', command=self.refresh_selected).pack(side='left', padx=(6, 0))
        ttk.Label(controls, text='操作:').pack(side='left', padx=(16, 4))
        mode = ttk.Combobox(controls, textvariable=self.mode, state='readonly', width=36,
                           values=(f'退避の整理（stocked → {entity}）', '重複の整理（比較して削除）')
                           + ((BNAL_MODE,) if entity == 'BNAL' else ()))
        mode.pack(side='left')
        mode.bind('<<ComboboxSelected>>', self.mode_changed)
        self.activity = tk.StringVar(value='待機中')
        self.activity_bar = ttk.Progressbar(controls, mode='indeterminate', length=140)
        self.activity_bar.pack(side='left', padx=(16, 6))
        ttk.Label(controls, textvariable=self.activity).pack(side='left')
        self.mode_help = tk.StringVar()
        ttk.Label(self, textvariable=self.mode_help).pack(anchor='w')
        self.targets, self.bnal_built = [], None
        self.bnal_box = ttk.Frame(self)
        ttk.Label(self.bnal_box, text='targetリスト（CSV・複数可）').pack(side='left', anchor='n')
        self.csv_dir = tk.StringVar(value=seq_targets.DEFAULT_TARGET_DIR)
        ttk.Entry(self.bnal_box, textvariable=self.csv_dir, width=40).pack(side='left', anchor='n', padx=6)
        ttk.Button(self.bnal_box, text='参照', command=self.choose_csv_dir).pack(side='left', anchor='n')
        ttk.Button(self.bnal_box, text='再読込', command=self.list_csvs).pack(side='left', anchor='n', padx=6)
        ttk.Button(self.bnal_box, text='CSV選択を解除', command=self.clear_csvs).pack(side='left', anchor='n', padx=(0, 6))
        self.csv_list = tk.Listbox(self.bnal_box, selectmode='extended', height=4, exportselection=False)
        self.csv_list.pack(side='left', fill='x', expand=True)
        self.csv_list.bind('<<ListboxSelect>>', lambda _: self.csv_changed())
        self.groups = self.tree(('date', 'age', 'count', 'anal', 'stocked', 'duplicates', 'unique', 'basis'),
                                ('解析日グループ', '新旧', '総件数', entity, 'stocked', '同番号候補', '固有番号等', '日付の根拠'), 3)
        self.groups.tag_configure('old', background='#43405e', foreground='#ffffff')
        self.groups.tag_configure('new', background='#245046', foreground='#ffffff')
        self.detail = self.tree(('action', 'location', 'group', 'number', 'candidate', 'name', 'size', 'date'),
                               ('操作対象', '場所', '解析日', '番号', '同番号候補', 'ファイル名', 'バイト数', '更新日時'), 6)
        for tag, color in [('stocked', '#25464b'), ('old', '#43405e'), ('new', '#245046'),
                           ('duplicate', '#58432a'), ('delete', '#703b42')]:
            self.detail.tag_configure(tag, background=color, foreground='#ffffff')
        self.detail.bind('<<TreeviewSelect>>', lambda _: setattr(self, 'plan', None))
        self.bnal_info = ttk.Frame(self)
        self.bnal_stage = tk.StringVar()
        self.stage_label = ttk.Label(self.bnal_info, textvariable=self.bnal_stage, wraplength=1400, font=(UI_FONT, 12, 'bold'))
        self.stage_label.pack(anchor='w')
        self.bnal_summary = tk.StringVar()
        ttk.Label(self.bnal_info, textvariable=self.bnal_summary, wraplength=1400).pack(anchor='w')
        self.completion = None  # result of the last run, shown once the automatic re-scan has finished
        self.bnal_targets = self.tree(('target', 'folder', 'done', 'empty', 'delete'),
                                      ('target', 'BNAL@フォルダ', '済み番号', '0バイト', '削除予定'), 3)
        self.bnal_detail = self.tree(('plan', 'location', 'number', 'name', 'items', 'size'),
                                     ('予定', '場所', '番号', 'ファイル名（.tdms／結果フォルダ）', 'まとめて移動', 'バイト数'), 8)
        for tag, color in [('archive', '#25464b'), ('restore', '#593d2d'), ('delete', '#703b42'),
                           ('stay', '#58432a'), ('skip', '#4b365e')]:
            self.bnal_detail.tag_configure(tag, background=color, foreground='#ffffff')
        self.bnal_targets.tag_configure('delete', background='#703b42', foreground='#ffffff')
        self.normal_frames = [self.groups.master, self.detail.master]
        self.bnal_frames = [self.bnal_box, self.bnal_info, self.bnal_targets.master, self.bnal_detail.master]
        if entity == 'BNAL':
            # BNAL tab: the CSV narrows the sample list to its target folders, in every mode.
            self.bnal_frames.remove(self.bnal_box)
            self.bnal_box.pack(after=self.filters, fill='x', pady=(0, 6))
        actions = self.actions = ttk.Frame(self)
        actions.pack(fill='x', pady=4)
        self.extract_button = ttk.Button(actions, text='選択したstockedの取り出し予定', command=lambda: self.preview(True))
        self.extract_button.pack(side='left', padx=(0, 6))
        self.extract_all_button = ttk.Button(actions, text='stockedを全部取り出す予定', command=self.preview_all_stocked)
        self.extract_all_button.pack(side='left', padx=(0, 6))
        self.group_button = ttk.Button(actions, text='選択グループを削除対象に指定', command=self.mark_groups)
        self.group_button.pack(side='left', padx=(0, 6))
        self.mark_button = ttk.Button(actions, text='選択ファイルの削除指定を切替', command=self.mark_files)
        self.mark_button.pack(side='left', padx=(0, 6))
        ttk.Button(actions, text='削除指定を解除', command=self.clear_marks).pack(side='left', padx=(0, 6))
        self.execute_button = ttk.Button(actions, text='取り出しを実行', command=self.execute)
        self.execute_button.pack(side='left', padx=(0, 6))
        ttk.Button(actions, text='履歴から元に戻す', command=self.undo).pack(side='left', padx=(0, 6))
        ttk.Button(actions, text='実行履歴を開く', command=self.history).pack(side='left', padx=(0, 6))
        self.notice = tk.StringVar(value='DBの保存済み調査を参照します。共有全体の再走査は行いません。')
        ttk.Label(self, textvariable=self.notice, wraplength=1400).pack(anchor='w')
        ttk.Label(self, text='色: 青緑＝stocked／緑＝新しい解析日／紫＝古い解析日／橙＝同番号候補／赤＝削除対象。'
                  '\n解析日はファイル名の@@年月日（取得できない場合は更新日）です。同日内の世代はまとめて表示します。'
                  '\n内容・解析条件の一致は未確認です。実行前に解析を停止してください。削除対象は復元用保管場所へ移し、履歴から戻せます。').pack(anchor='w')
        self.list_csvs(quiet=True)
        self.mode_changed()
        self.after(100, self.poll)

    def tree(self, columns, titles, height):
        frame = ttk.Frame(self)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=height)
        for col, title in zip(columns, titles):
            tree.heading(col, text=title)
            tree.column(col, width=150 if col != 'name' else 500)
        y = ttk.Scrollbar(frame, command=tree.yview)
        x = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        tree.grid(row=0, column=0, sticky='nsew')
        y.grid(row=0, column=1, sticky='ns')
        x.grid(row=1, column=0, sticky='ew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return tree

    def run(self, work, done, activity='処理中…'):
        if self.busy or self.owner.busy or any(getattr(getattr(self.owner, key, None), 'busy', False)
                                              for key in ('anal_panel', 'bnal_panel', 't_stock_panel')):
            return
        self.busy = True
        self.notice.set(activity)
        self.activity.set(activity)
        self.activity_bar.start(12)
        self.scan_button.configure(state='disabled', text='再調査中…' if activity.startswith('再調査中') else '処理中…')
        def worker():
            try:
                self.events.put((done, work(), None))
            except Exception as exc:
                self.events.put((done, None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            done, value, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.activity_bar.stop()
            self.activity_bar.configure(value=0)
            self.scan_button.configure(state='normal', text='選択した保存先を再調査')
            self.activity.set('エラー' if error else '完了')
            if error:
                self.plan, self.completion = None, None
                self.notice.set(error)
                messagebox.showerror(f'{self.entity}整理', error, parent=self)
            else:
                done(value)
        self.after(100, self.poll)

    def choose_db(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('SQLite', '*.sqlite3 *.db'), ('すべて', '*')])
        if path:
            self.db.set(path)

    def load(self):
        path = self.db.get()
        def done(rows):
            self.inventory = rows
            self.render()
            self.notice.set(f'DBから{len(rows)}件取得。件数は保存済み調査時点です。操作対象を再調査してください。'
                            '未計数・候補なしも表示する場合は絞り込みを外してください。')
        self.run(lambda: self.loader(path), done)

    def row_for(self, folder):
        """Inventory row whose sample a plan folder (T, ANAL or BNAL@) belongs to; an exact folder match first."""
        folder = Path(folder)
        rows = [r for r in self.inventory if folder in (Path(r['anal_path']), Path(r['anal_path']).parent)
                or folder.parent == Path(r['anal_path']).parent]
        return next((r for r in rows if Path(r['anal_path']) == folder), rows[0] if rows else None)

    def refresh_selected(self, rows=None, rescan=False):
        """Update the saved counts of the selected samples from the share, then reload and keep the selection."""
        if self.busy or self.owner.busy:
            return
        rows = rows or [self.inventory[int(i)] for i in self.samples.selection()]
        if not rows:
            messagebox.showinfo(f'{self.entity}整理', 'DBを更新するサンプルの行を選択してください（複数可）。', parent=self)
            return
        keys = list(dict.fromkeys((r['root'], r['experiment'], r['sample']) for r in rows))
        selected = {row_identity(r) for r in rows}
        path = self.db.get()
        def done(inventory):
            self.inventory = inventory
            self.render()
            again = [i for i in self.samples.get_children() if row_identity(self.inventory[int(i)]) in selected]
            self.samples.selection_set(again)
            if again:
                self.samples.see(again[0])
            warning = scan_warning(path, [root for root, _, _ in keys])
            self.notice.set(f'{len(keys)}サンプルのDBを更新しました（' + '、'.join(f'{e}/{s}' for _, e, s in keys[:5])
                            + ('…' if len(keys) > 5 else '') + '）。' + ('' if again else '絞り込み条件から外れたため一覧に表示されていません。')
                            + (' ' + warning.split('\n')[0] + '。データベース側の調査が同じサンプルを古い件数で上書きした場合は、もう一度更新してください。'
                               if warning else ''))
            if rescan and again and self.one_sample(again):
                self.after(100, self.scan)  # show the real files after the move
            elif self.completion:
                # The sample left the filtered list (e.g. complete after a restore): announce the run here.
                finished, self.completion = self.completion, None
                fresh = [r for r in self.inventory if row_identity(r) in selected]
                state = (f'現在の状態: {bnal_row_state(fresh[0])[1]}（一覧の絞り込み条件から外れました）'
                         if fresh and self.is_bnal_mode() else '')
                self.notice.set(finished + '　' + state)
                messagebox.showinfo('実行完了', finished + ('\n\n' + state if state else ''), parent=self)
        self.run(lambda: (refresh_db(path, keys), self.loader(path))[1], done,
                 activity=f'DB更新中… {len(keys)}サンプル（共有を読み直しています）')

    def invalidate(self):
        self.plan, self.files, self.bnal_built = None, [], None
        self.bnal_stage.set('')
        if self.is_bnal_mode():
            self.execute_button.configure(text='予定を実行（元に戻せます）')
        self.bnal_detail.delete(*self.bnal_detail.get_children())
        self.bnal_targets.delete(*self.bnal_targets.get_children())
        self.bnal_summary.set('保存先（共有・サンプルの行）を選択し、再調査してください。BNAL・ANAL解析ソフトは停止しておいてください。')
        self.marked.clear()
        self.detail.delete(*self.detail.get_children())
        self.groups.delete(*self.groups.get_children())

    def render(self):
        if self.busy:
            return
        self.invalidate()
        self.samples.delete(*self.samples.get_children())
        update_shares(self)
        query = self.query.get().casefold().strip()
        shown = []  # (index, row, state, flagged)
        for i, row in enumerate(self.inventory):
            if not matches_inventory_query(row, query, self.exact_experiment.get()) or not share_matches(self, row):
                continue
            state = row.get('state', '')
            if self.entity == 'BNAL' and self.targets and row['target'][len('BNAL@'):] not in self.targets:
                continue
            if self.is_bnal_mode():
                flagged, state = bnal_row_state(row)
            else:
                unfinished, progress = bnal_row_state(row) if self.entity == 'BNAL' else (False, '')
                if unfinished:
                    state = f'{state}／{progress}' if state else progress
                flagged = bool(row['mixed'] or row.get('anal_stocked_tdms') or row.get('excess') or unfinished)
            shown.append((i, row, state, flagged))
        if self.stocked_only.get():
            shown = sorted((item for item in shown if item[1].get('anal_stocked_tdms')),
                           key=lambda item: -item[1]['anal_stocked_tdms'])
        elif self.only_flags.get():
            # BNAL runs every target of the CSV together, so one flagged target brings its whole sample group.
            key = (lambda r: (r['root'], r['experiment'], r['sample'])) if self.entity == 'BNAL' else id
            groups = {key(row) for _, row, _, flagged in shown if flagged}
            shown = [item for item in shown if key(item[1]) in groups]
        for i, row, state, _ in shown:
            values = [row['root'], row['experiment'], row['sample'],
                      # ANAL is always the target folder; its originals in T are the useful number here.
                      row.get('target') if self.entity == 'BNAL' else row.get('t_tdms'), row.get('anal_tdms'), row.get('expected'),
                      row['mixed'], row.get('anal_stocked_tdms'), state, row.get('last_success')]
            tag = 'both' if row['mixed'] and row.get('anal_stocked_tdms') else 'duplicate' if row['mixed'] or row.get('excess') else 'stocked' if row.get('anal_stocked_tdms') else ''
            self.samples.insert('', 'end', iid=str(i), values=['未取得' if v is None else v for v in values], tags=(tag,))

    def sample_key(self, iid):
        row = self.inventory[int(iid)]
        return row['root'], row['experiment'], row['sample']

    def one_sample(self, iids):
        """One row, or (BNAL用の退避・復帰) several target rows of the same share/experiment/sample."""
        return len(iids) == 1 or (self.is_bnal_mode() and len({self.sample_key(i) for i in iids}) == 1)

    def sample_selected(self):
        if self.is_bnal_mode():
            # Every CSV target of the sample is examined together, so show them selected together.
            keys = {self.sample_key(i) for i in self.samples.selection()}
            group = tuple(i for i in self.samples.get_children() if self.sample_key(i) in keys)
            if set(group) != set(self.samples.selection()):
                self.samples.selection_set(group)  # fires this handler once more with the full group
                return
        self.invalidate()

    def scan(self):
        if self.busy or self.owner.busy:
            return
        selected = self.samples.selection()
        several = not self.is_bnal_mode() and not self.is_duplicate_mode() and len(selected) > 1
        if not selected or not (several or self.one_sample(selected)):
            messagebox.showinfo(f'{self.entity}整理', '同じ共有・実験・サンプルの行を選択してください。'
                                if self.is_bnal_mode() else '重複の整理は保存先を1件選択してください。', parent=self)
            return
        folder = self.inventory[int(selected[0])]['anal_path']
        self.invalidate()
        if self.is_bnal_mode():
            self.scan_bnal(selected, self.inventory[int(selected[0])])
            return
        rows = [self.inventory[int(i)] for i in selected]
        entity = self.entity
        def work():
            if not several:
                return scan_anal(folder, entity), []
            files, skipped = [], []
            for row in rows:
                try:
                    found = scan_anal(row['anal_path'], entity)
                except (OSError, ValueError) as exc:
                    skipped.append(f"{row['sample']}（{exc}）")
                    continue
                for r in found:
                    # The sample in the group keeps dates of different samples apart (新旧 is per sample).
                    r.update(sample=row['sample'], group=f"{row['sample']} {r['group']}")
                files += found
            return files, skipped
        def done(result):
            if self.samples.selection() != selected:
                self.invalidate()
                self.notice.set('選択先が変わりました。保存先を再調査してください。')
                return
            files, skipped = result
            self.folder = tuple(r['anal_path'] for r in rows) if several else folder
            self.files = files
            self.show_files()
            self.activity.set(f'再調査完了：{len(files):,}件')
            stocked = sum(r['location'] == 'stocked' for r in files)
            self.notice.set((f'{len(rows)}サンプルの実ファイル: {len(files)}件（stocked {stocked}件）。'
                             'stockedの行を選んで「選択したstockedの取り出し予定」、または「stockedを全部取り出す予定」。'
                             if several else f'現在の実ファイル: {len(files)}件 / {folder}')
                            + '（DBの保存済み件数は変更していません）'
                            + ('　調べられなかったサンプル: ' + '、'.join(skipped) if skipped else ''))
        row = rows[0]
        self.run(work, done, activity=f'再調査中… {len(rows)}サンプル' if several
                 else f"再調査中… {row['experiment']} / {row['sample']}")

    def preview_all_stocked(self):
        """Select every stocked file of the re-scanned folder(s) and plan taking all of them out."""
        if self.busy or self.owner.busy or self.is_duplicate_mode() or self.is_bnal_mode():
            return
        selected = self.samples.selection()
        if selected and not self.files:
            self.preview_samples_stocked(selected)  # not re-scanned yet: scan and plan in one go
            return
        stocked = [str(i) for i, r in enumerate(self.files) if r['location'] == 'stocked']
        if not stocked:
            messagebox.showinfo(f'{self.entity}整理', '再調査した保存先のstockedにファイルがありません。'
                                if self.files else '保存先を選択して再調査してください。', parent=self)
            return
        self.detail.selection_set(stocked)
        self.detail.see(stocked[0])
        self.preview(True)

    def preview_samples_stocked(self, selected):
        """Re-scan every selected sample and plan taking all of its stocked files out, one plan per sample."""
        rows = [self.inventory[int(i)] for i in selected]
        entity = self.entity
        def work():
            plans, skipped = [], []
            for row in rows:
                try:
                    files = scan_anal(row['anal_path'], entity)
                    stocked = [r for r in files if r['location'] == 'stocked']
                    if not stocked:
                        skipped.append((row, 'stockedなし'))
                        continue
                    plan = move_plan(row['anal_path'], stocked, True)
                    plan.update(action='extract', clashes=extract_clashes(files, stocked))
                    plans.append((row, plan))
                except (OSError, ValueError) as exc:
                    skipped.append((row, str(exc)))
            return plans, skipped
        def done(result):
            if self.samples.selection() != selected or self.is_duplicate_mode() or self.is_bnal_mode():
                self.plan = None
                self.notice.set('選択が変わりました。取り出し予定を作成し直してください。')
                return
            self.set_samples_plan(*result)
        self.run(work, done, activity=f'再調査中… {len(rows)}サンプルのstocked')

    def set_samples_plan(self, plans, skipped):
        """Keep per-sample extract plans as one plan; '取り出しを実行' runs them sample by sample."""
        skip_text = '、'.join(f"{r['sample']}（{why}）" for r, why in skipped)
        if not plans:
            self.plan = None
            self.notice.set('取り出すstockedがありません。' + skip_text)
            messagebox.showinfo(f'{self.entity}整理', '選択したサンプルに取り出せるstockedがありません。\n\n' + skip_text, parent=self)
            return
        operations = [o for _, p in plans for o in p['operations']]
        self.plan = dict(action='extract', folder=plans[0][1]['folder'], samples=plans, operations=operations)
        clashes = [f"{r['sample']} {len(p['clashes'])}件" for r, p in plans if p['clashes']]
        self.notice.set(f'{len(plans)}サンプル・{len(operations)}件の取り出し予定を作成しました。「取り出しを実行」で確認画面が開きます。'
                        + (f'　⚠ 同じ番号が重なります: ' + '、'.join(clashes) + '。' if clashes else '')
                        + (f'　対象外: {skip_text}' if skipped else ''))
        self.write_log(f'{self.entity}移動予定（{len(plans)}サンプル）', operations)

    def write_log(self, title, operations):
        self.owner.log.configure(state='normal')
        self.owner.log.insert('end', f'\n=== {title} ===\n' + '\n'.join(
            f'{o["source"]}\n  → {o["target"]}' for o in operations) + '\n')
        self.owner.log.configure(state='disabled')
        self.owner.log.see('end')

    def execute_samples(self, plan):
        """Run each sample's plan in turn (one undo history per sample); stop at the first failure."""
        samples = plan['samples']
        text = '\n\n'.join(
            f"■ {row['experiment']} / {row['sample']}：{len(p['operations'])}件"
            + (f"　⚠ {self.entity}で同じ番号が重なります " + ', '.join(f'#{n:03d}' for n in p['clashes'][:10])
               + ('…' if len(p['clashes']) > 10 else '') if p['clashes'] else '')
            + '\n' + '\n'.join(f'{o["source"]}\n→ {o["target"]}' for o in p['operations'])
            for row, p in samples)
        warning = scan_warning(self.db.get(), list(dict.fromkeys(row['root'] for row, _ in samples)))
        if warning:
            text = warning + '\n\n' + text
        if not self.confirm(f'取り出し（{len(samples)}サンプル・{len(plan["operations"])}件）', text):
            return
        self.plan = None
        log_dir = self.log_dir
        def work():
            logs, error = [], None
            for row, p in samples:
                try:
                    logs.append((row, p, execute_moves(p, log_dir)))
                except Exception as exc:
                    error = f"{row['experiment']} / {row['sample']}: {exc}"
                    break
            return logs, error
        def done(result):
            logs, error = result
            self.invalidate()
            moved = sum(len(p['operations']) for _, p, _ in logs)
            text = (f"✔ 取り出し完了（{datetime.now():%H:%M:%S}）：{len(logs)}/{len(samples)}サンプル・{moved}件。"
                    f"履歴 {len(logs)}件（サンプルごとに元に戻せます）。")
            if error:
                text += f'\n⚠ 途中で停止しました（以降のサンプルは未実行）。{error}'
            self.notice.set(text.replace('\n', '　') + '　DBを更新しています…')
            (messagebox.showwarning if error else messagebox.showinfo)(f'{self.entity} 取り出し', text, parent=self)
            # The stopped sample may be partly moved, so its counts are refreshed too.
            self.after(100, lambda: self.refresh_selected([row for row, _ in samples]))
        self.run(work, done, activity=f'取り出し中… {len(samples)}サンプル')

    def preview(self, restore):
        if self.busy or self.owner.busy or self.is_duplicate_mode():
            return
        if self.is_bnal_mode():
            self.scan()  # the plan is always built from a fresh look at ANAL and every BNAL@
            return
        rows = [self.files[int(i)] for i in self.detail.selection()]
        if not rows:
            messagebox.showinfo(f'{self.entity}整理', 'ファイルを選択してください。', parent=self)
            return
        folder = self.folder
        selection = self.detail.selection()
        if isinstance(folder, tuple):
            # Several samples were re-scanned together: one plan per sample folder.
            files = list(self.files)
            def anal_of(r):
                path = Path(r['path'])
                return path.parent.parent if r['location'] == 'stocked' else path.parent
            def work():
                plans, by_folder = [], {}
                for r in rows:
                    by_folder.setdefault(anal_of(r), []).append(r)
                for anal, picked in by_folder.items():
                    plan = move_plan(anal, picked, restore)
                    here = [f for f in files if anal_of(f) == anal]
                    plan.update(action='extract', clashes=extract_clashes(here, picked))
                    plans.append((self.row_for(anal) or dict(experiment='', sample=picked[0].get('sample'), root=''), plan))
                return plans, []
            def done_many(result):
                if self.detail.selection() != selection or self.folder != folder or self.is_duplicate_mode():
                    self.plan = None
                    self.notice.set('選択が変わりました。移動予定を作成し直してください。')
                    return
                self.set_samples_plan(*result)
            self.run(work, done_many)
            return
        def done(plan):
            if self.detail.selection() != selection or not self.files or self.folder != folder or self.is_duplicate_mode():
                self.plan = None
                self.notice.set('選択が変わりました。移動予定を作成し直してください。')
                return
            self.plan = plan
            self.plan['action'] = 'extract'
            clash = extract_clashes(self.files, rows)
            self.notice.set(f'{len(plan["operations"])}件の移動予定を作成しました。下の実行ログで移動元・先を確認してください。'
                            + (f'　⚠ 取り出すと{self.entity}で同じ番号が重なります {len(clash)}件（'
                               + ', '.join(f'#{n:03d}' for n in clash[:10]) + ('…' if len(clash) > 10 else '')
                               + '）。世代の混在に注意してください。' if clash else ''))
            self.write_log(f'{self.entity}移動予定', plan['operations'])
        self.run(lambda: move_plan(folder, rows, restore), done)

    def execute(self):
        if self.busy or self.owner.busy:
            return
        if self.is_duplicate_mode() and (not self.plan or self.plan.get('action') not in ('undo', 'delete')):
            if not self.marked and self.detail.selection():
                # Selected but not yet marked: take the selected files; the confirmation still lists everything.
                self.marked = {self.files[int(i)]['path'] for i in self.detail.selection()}
                self.show_files()
            rows = [r for r in self.files if r['path'] in self.marked]
            if not rows:
                messagebox.showinfo('削除対象', '明細でファイルを選択するか、グループまたはファイルを削除対象に指定してください。',
                                    parent=self)
                return
            folder = self.folder
            selection = set(self.marked)
            def ready(plan):
                if self.marked != selection or not self.is_duplicate_mode() or self.folder != folder:
                    self.notice.set('対象が変わりました。削除対象を指定し直してください。')
                    return
                self.plan = plan
                self.execute()
            self.run(lambda: deletion_plan(folder, rows, with_results=self.entity == 'BNAL'), ready)
            return
        if not self.plan or not self.plan['operations']:
            messagebox.showinfo(BNAL_MODE if self.is_bnal_mode() else '取り出し予定',
                                '移動・削除する対象がありません。保存先を再調査してください。' if self.is_bnal_mode() else
                                'stocked内のファイルを選択し、取り出し予定を作成してください。', parent=self)
            return
        plan = self.plan
        if plan.get('samples'):
            self.execute_samples(plan)
            return
        action = {'delete': '削除', 'extract': '取り出し', 'undo': '元に戻す',
                  'bnal_stock': 'BNAL用の退避'}.get(plan.get('action'), '移動')
        if plan.get('action') == 'bnal_stock':
            action = bnal_stock.action_label(self.bnal_built)  # 退避 or 復帰, from what the plan really does
        text = '\n\n'.join(f'{o["source"]}\n→ {o["target"]}' for o in plan['operations'])
        if plan.get('action') == 'bnal_stock':
            text = bnal_stock.describe(self.bnal_built)
        if plan.get('action') == 'delete':
            unique = sum(not r['candidate'] for r in self.files if r['path'] in self.marked)
            removed = {r['number'] for r in self.files if r['path'] in self.marked and r['number'] is not None}
            remaining = {r['number'] for r in self.files if r['path'] not in self.marked and r['number'] is not None}
            lost = ', '.join(f'#{n:03d}' for n in sorted(removed - remaining)) or 'なし'
            folders = sum(bool(o.get('dir')) for o in plan['operations'])
            text = (f'削除対象: {len(plan["operations"]) - folders}件（他に同番号がないファイル: {unique}件）'
                    + (f'＋同じ日時・番号の結果フォルダ {folders}件' if folders else '') + '。\n'
                    f'{self.entity}とstockedの両方からなくなる番号: {lost}\n'
                    f'復元用に対象{self.entity}/.整理履歴へ保管します。\n\n') + text
        scanned = self.row_for(plan['folder'])
        warning = scan_warning(self.db.get(), [scanned['root']] if scanned else [])
        if warning:
            text = warning + '\n\n' + text
        if not self.confirm(action, text, require_stop=plan.get('action') == 'bnal_stock' or self.is_bnal_mode()):
            return
        self.plan = None
        def done(log):
            self.invalidate()
            result = f"✔ 実行完了（{datetime.now():%H:%M:%S}）：{bnal_stock.result_text(plan)}。履歴: {Path(log).name}"
            self.notice.set(result + '　DBを更新して再調査しています…')
            if scanned is not None and self.is_bnal_mode():
                self.completion = result  # announced with the new stage after the re-scan
            else:
                messagebox.showinfo(f'{self.entity} {action}', result, parent=self)
            if scanned is not None:
                # Keep the saved counts in step with the move, then show the real files again.
                self.after(100, lambda: self.refresh_selected([scanned], rescan=True))
        self.run(lambda: execute_moves(plan, self.log_dir), done)

    def confirm(self, action, text, require_stop=False):
        window = tk.Toplevel(self)
        window.title(f'{action}の確認' if action.startswith(self.entity) else f'{self.entity} {action}の確認')
        window.geometry(f'{min(1100, self.winfo_screenwidth() - 80)}x{min(650, self.winfo_screenheight() - 100)}')
        window.transient(self.winfo_toplevel())
        ttk.Label(window, text=f'{action}の全対象・移動先を確認してください。解析処理を停止してから実行してください。',
                  padding=10).pack(anchor='w')
        content = ScrolledText(window, wrap='none')
        from shared import ui_theme
        ui_theme.style_text(content)
        content.insert('1.0', text)
        content.configure(state='disabled')
        content.pack(fill='both', expand=True, padx=10)
        result = [False]
        def approve():
            result[0] = True
            window.destroy()
        buttons = ttk.Frame(window, padding=10)
        buttons.pack(fill='x')
        ok = ttk.Button(buttons, text=f'確認して{action}を実行', command=approve,
                        state='disabled' if require_stop else 'normal')
        if require_stop:
            stopped = tk.BooleanVar(value=False)
            ttk.Checkbutton(buttons, text='このサンプルのBNAL・ANAL解析ソフトを停止しました', variable=stopped,
                            command=lambda: ok.configure(state='normal' if stopped.get() else 'disabled')).pack(side='left')
        ttk.Button(buttons, text='キャンセル', command=window.destroy).pack(side='right', padx=6)
        ok.pack(side='right')
        window.grab_set()
        self.wait_window(window)
        return result[0]

    def is_duplicate_mode(self):
        return self.mode.get().startswith('重複')

    def is_bnal_mode(self):
        return self.mode.get() == BNAL_MODE

    def mode_changed(self, _=None):
        self.plan = None
        self.marked.clear()
        duplicate, bnal = self.is_duplicate_mode(), self.is_bnal_mode()
        if duplicate and isinstance(getattr(self, 'folder', None), tuple):
            self.invalidate()  # deletion works on one sample; several re-scanned samples must be re-scanned singly
        self.was_bnal, self.bnal_filter = getattr(self, 'bnal_filter', False), bnal
        for frame in self.normal_frames + self.bnal_frames:
            frame.pack_forget()
        for frame in self.bnal_frames if bnal else self.normal_frames:
            frame.pack(before=self.actions, **(dict(fill='x', pady=4) if frame in (self.bnal_box, self.bnal_info)
                                               else dict(fill='both', expand=True)))
        self.flags_check.configure(text='BNAL未完了・ANAL退避中のみ' if bnal else
                                   '重複候補・超過・未完了ありのみ' if self.entity == 'BNAL' else '重複候補・超過・退避ありのみ')
        self.extract_all_button.configure(state='disabled' if duplicate or bnal else 'normal')
        self.extract_button.configure(state='disabled' if duplicate else 'normal',
                                      text='BNAL用の退避・復帰予定を作成（再調査）' if bnal else '選択したstockedの取り出し予定')
        self.group_button.configure(state='normal' if duplicate else 'disabled')
        self.mark_button.configure(state='normal' if duplicate else 'disabled')
        self.execute_button.configure(text='削除を実行（元に戻せます）' if duplicate else
                                      '予定を実行（元に戻せます）' if bnal else '取り出しを実行')
        if bnal:
            self.mode_help.set('BNAL用の退避・復帰: CSVを選択 → targetの行を1件選択して再調査（そのサンプルのCSVの全targetを調べます）→ '
                               'BNAL未完了なら、全targetで済んだ番号のANALをstockedへ・先行分と0バイトのBNAL（結果フォルダ含む）を削除。'
                               '全target完了ならstockedをANALへ復帰。→ 予定を実行。')
            self.render()
            return
        self.mode_help.set('重複の整理: 新旧の件数を比較 → 不要なグループまたはファイルを削除対象に指定 → 削除を実行。'
                           if duplicate else f'退避の整理: 青緑のstocked行を選択 → 取り出し予定 → stockedから直上の{self.entity}へ移動。同名があれば停止します。'
                           '（複数サンプル: Ctrl／Shiftで選択 → 再調査 → stockedを選んで取り出し予定、または全部取り出す予定 → 取り出しを実行）')
        if self.was_bnal:
            self.render()  # the sample list was filtered for BNAL; restore the normal filter
        else:
            self.show_files()

    def choose_csv_dir(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.csv_dir.get() or None)
        if folder:
            self.csv_dir.set(folder)
            self.list_csvs()

    def list_csvs(self, quiet=False):
        self.csv_list.delete(0, 'end')
        try:
            names = seq_targets.list_target_csvs(self.csv_dir.get())
        except OSError as exc:
            if not quiet:
                messagebox.showerror(BNAL_MODE, f'CSVフォルダを読めません: {exc}', parent=self)
            names = []
        for name in names:
            self.csv_list.insert('end', name)
        self.csv_changed()

    def csv_changed(self):
        targets = []
        for index in self.csv_list.curselection():
            path = Path(self.csv_dir.get()) / self.csv_list.get(index)
            try:
                targets += seq_targets.read_targets(path)
            except (OSError, ValueError) as exc:
                messagebox.showerror(BNAL_MODE, f'CSVを読めません: {path}\n{exc}', parent=self)
        self.targets = list(dict.fromkeys(targets))
        if self.is_bnal_mode() or self.entity == 'BNAL':
            self.render()
            self.notice.set(f'CSVのtarget {len(self.targets)}件：' + '、'.join(self.targets[:12])
                            + ('…' if len(self.targets) > 12 else '') if self.targets else
                            '全targetを表示しています。CSVを選ぶとそのtargetの対象フォルダだけに絞り込みます。'
                            if self.entity == 'BNAL' else 'targetリスト（CSV）を選択してください。')

    def clear_csvs(self):
        self.csv_list.selection_clear(0, 'end')
        self.csv_changed()

    def scan_bnal(self, selected, row):
        if not self.targets:
            messagebox.showinfo(BNAL_MODE, 'targetリスト（CSV）を選択してください。', parent=self)
            return
        t, targets = Path(row['anal_path']).parent, list(self.targets)
        def done(built):
            if self.samples.selection() != selected or not self.is_bnal_mode() or targets != self.targets:
                self.invalidate()
                self.notice.set('選択が変わりました。保存先を再調査してください。')
                return
            self.bnal_built = built
            self.plan = built['plan']
            self.show_bnal()
        self.run(lambda: bnal_stock.plan(bnal_stock.scan(t, targets)), done,
                 activity=f"再調査中… {row['experiment']} / {row['sample']}（ANALとBNAL@ {len(targets)}target）")

    def show_bnal(self):
        built = self.bnal_built
        s = built['summary']
        self.bnal_targets.delete(*self.bnal_targets.get_children())
        for i, t in enumerate(s['targets']):
            self.bnal_targets.insert('', 'end', iid=str(i), tags=('delete',) if t['delete'] else (), values=(
                t['target'], 'あり' if t['exists'] else 'なし（未着手）', t['done'] if t['exists'] else 0, t['empty'], t['delete']))
        self.bnal_detail.delete(*self.bnal_detail.get_children())
        order = {'ANAL': 0, 'ANAL/stocked': 1}
        rows = sorted(built['rows'], key=lambda r: (order.get(r['location'], 2), r['location'],
                                                    r['run']['number'] if r['run']['number'] is not None else -1))
        for i, r in enumerate(rows):
            run = r['run']
            names = [Path(p['path']).name for p in run['paths']]
            self.bnal_detail.insert('', 'end', iid=str(i), tags=(r['tag'],), values=(
                r['plan'], r['location'], '不明' if run['number'] is None else f"#{run['number']:03d}",
                run['name'] or names[0], '、'.join(n for n in names if n != run['name']),
                '' if run['size'] is None else f"{run['size']:,}"))
        lines = [f"全targetで済んだ番号 {s['common']}件（{s['common_text']}）→ ANAL/stockedへ {s['archive']}件"
                 + (f"、stockedからANALへ {s['restore']}件" if s['restore'] else '')
                 + f"。ANALに残す欠番 {s['missing']}件（{s['missing_text']}）。BNAL削除 {s['delete']}件。"]
        lines += ['⚠ ' + w for w in built['warnings']]
        self.bnal_summary.set('\n'.join(lines))
        kind, text = bnal_stock.stage(built)
        self.execute_button.configure(text={'todo': bnal_stock.action_label(built).replace('BNAL用の', '') + 'を実行（元に戻せます）',
                                            'restore': '復帰を実行（stocked → ANAL・元に戻せます）'}.get(kind, '予定を実行（元に戻せます）'))
        self.bnal_stage.set(text)
        self.stage_label.configure(foreground=STAGE_COLORS[kind])
        if self.completion:
            done, self.completion = self.completion, None
            self.notice.set(done + '　' + text)
            messagebox.showinfo('実行完了', done + '\n\n現在の状態:\n' + text, parent=self)
        else:
            self.notice.set(text)

    def show_files(self):
        selected = self.detail.selection()
        self.detail.delete(*self.detail.get_children())
        self.groups.delete(*self.groups.get_children())
        # Several samples re-scanned together: 新旧 is judged within each sample.
        samples = dict.fromkeys(r.get('sample') for r in self.files)
        groups = [g for s in samples for g in analysis_groups([r for r in self.files if r.get('sample') == s])]
        for g in groups:
            self.groups.insert('', 'end', iid=g['group'], values=tuple(g[k] for k in
                ('group', 'age', 'count', 'anal', 'stocked', 'duplicates', 'unique', 'basis')),
                tags=('new' if g['age'] == '新しい' else 'old' if g['age'] == '古い' else '',))
        ages = {g['group']: g['age'] for g in groups}
        for i in sorted(range(len(self.files)), key=lambda i: (self.files[i]['group'], self.files[i]['number'] or -1, self.files[i]['path'])):
            r = self.files[i]
            marked = r['path'] in self.marked
            tag = 'delete' if marked else 'stocked' if not self.is_duplicate_mode() and r['location'] == 'stocked' else (
                'new' if ages[r['group']] == '新しい' else 'old' if ages[r['group']] == '古い' else 'duplicate' if r['candidate'] else '')
            self.detail.insert('', 'end', iid=str(i), tags=(tag,), values=('削除対象' if marked else '',
                f"{r['sample']} {r['location']}" if r.get('sample') else r['location'], r['group'],
                r['number'] if r['number'] is not None else '不明', '候補' if r['candidate'] else '', Path(r['path']).name, r['size'],
                datetime.fromtimestamp(r['modified_ns'] / 1e9).astimezone().isoformat(timespec='seconds')))
        self.detail.selection_set([i for i in selected if self.detail.exists(i)])

    def mark_groups(self):
        if self.busy or self.owner.busy or not self.is_duplicate_mode():
            return
        selected = set(self.groups.selection())
        self.marked = {r['path'] for r in self.files if r['group'] in selected}
        self.plan = None
        self.show_files()
        self.notice.set(f'削除対象 {len(self.marked)}件（赤）。指定したグループ全体が対象です。固有番号の件数も確認してください。')

    def mark_files(self):
        if self.busy or self.owner.busy or not self.is_duplicate_mode():
            return
        for iid in self.detail.selection():
            path = self.files[int(iid)]['path']
            if path in self.marked:
                self.marked.remove(path)
            else:
                self.marked.add(path)
        self.plan = None
        self.show_files()
        self.notice.set(f'削除対象 {len(self.marked)}件（赤）。')

    def clear_marks(self):
        if self.busy:
            return
        self.marked.clear()
        self.plan = None
        self.show_files()

    def undo(self):
        if self.busy or self.owner.busy:
            return
        history = filedialog.askopenfilename(parent=self, initialdir=str(self.log_dir),
                                              filetypes=[(f'{self.entity}操作履歴', '*.json')])
        if not history:
            return
        def done(plan):
            self.plan = plan
            self.execute()  # Always shows the complete reverse mapping for confirmation.
        self.run(lambda: undo_plan(history), done)

    def history(self):
        folder = self.log_dir
        folder.mkdir(exist_ok=True)
        os.startfile(folder)
