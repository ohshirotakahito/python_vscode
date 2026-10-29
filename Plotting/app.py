# -*- coding: utf-8 -*-
"""BNALプロット: 実験ファイルDBの BNAL@ 一覧から target を選び、TDMS（LAP Table）を読み込んで可視化する。

一覧は DB を読むだけでサーバーにはアクセスしない。読み込みはスレッド1本で順番に行う
（サーバー負荷を軽く保つため）。読んだ内容は cache/ に保存し、次回は差分だけ読む。
"""
from __future__ import annotations

import math
import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use('Agg')  # 画面のグラフは FigureCanvasTkAgg で描く
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

import bnal_assembly as asm  # noqa: E402
import bnal_modification as mod  # noqa: E402
import bnal_plots as plots  # noqa: E402
import bnal_source as src  # noqa: E402
import ui_theme  # noqa: E402  (bnal_source がリポジトリ直下を sys.path に追加している)

OUTPUT_DIR = src.PLOTTING_DIR / 'heat_data'

ACCENT = ui_theme.PLOTTING
CANDIDATE_COLUMNS = ('group', 'name', 'tdms', 'cache', 'others')
CANDIDATE_TITLES = ('共有（読込元）', '実験 / サンプル / target', 'TDMS数（DB）', 'キャッシュ', 'ほかの共有')
ALL_SHARES = 'すべて'
LOADED_COLUMNS = ('group', 'experiment', 'sample', 'target', 'files', 'rows', 'missing', 'source', 'note')
LOADED_TITLES = ('共有', '実験名', 'サンプル名', 'target', 'ファイル数', '行数', '値なし行', '読込元', '備考')


def fmt_count(value):
    return '—' if value is None else f'{value:,}'


class LoadPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.entries: dict[tuple, src.TargetEntry] = {}
        self.choice: dict[tuple, str] = {}      # (実験, サンプル, target) -> 読む共有
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()

        self.filter_text = tk.StringVar()
        self.share_filter = tk.StringVar(value=ALL_SHARES)
        self.offline = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='')
        self.build()
        self.reload_db()

    # ---------- 画面 ----------
    def build(self):
        top = ttk.Frame(self)
        top.pack(fill='x')
        ttk.Label(top, text='絞り込み').pack(side='left')
        entry = ttk.Entry(top, textvariable=self.filter_text, width=40)
        entry.pack(side='left', padx=(6, 4))
        entry.bind('<Return>', lambda e: self.refresh_candidates())
        self.filter_text.trace_add('write', lambda *a: self.schedule_refresh())
        ttk.Label(top, text='（実験・サンプル・target名。空白区切りで AND）', style='Muted.TLabel').pack(side='left')
        ttk.Label(top, text='共有').pack(side='left', padx=(16, 0))
        self.share_box = ttk.Combobox(top, textvariable=self.share_filter, state='readonly', width=22,
                                      values=(ALL_SHARES,))
        self.share_box.pack(side='left', padx=(6, 0))
        self.share_box.bind('<<ComboboxSelected>>', lambda e: self.apply_share_filter())
        ttk.Button(top, text='DBを再読込', command=self.reload_db).pack(side='right')
        self.count_label = ttk.Label(top, style='Muted.TLabel')
        self.count_label.pack(side='right', padx=10)

        panes = ttk.Panedwindow(self, orient='vertical')
        panes.pack(fill='both', expand=True, pady=(8, 0))

        # 上段: DBの BNAL@ 一覧
        upper = ttk.Labelframe(panes, text='① 読み込む target を選択（実験・サンプル行を選ぶと配下の target 全部。Ctrl / Shift で複数可）',
                               padding=6)
        panes.add(upper, weight=3)
        # 共有を左端に置くため階層列（#0, 常に左端固定）は使わず、開閉は ▶/▼ で示す
        self.candidates = self.make_tree(upper, CANDIDATE_COLUMNS, CANDIDATE_TITLES, show='headings',
                                         widths=dict(group=170, name=420, tdms=110, cache=110, others=320))
        self.candidates.column('name', stretch=True)
        self.candidates.bind('<<TreeviewOpen>>', self.on_open_close)
        self.candidates.bind('<<TreeviewClose>>', self.on_open_close)
        self.candidates.tag_configure('sample', foreground=ACCENT)
        self.candidates.tag_configure('cached', background=ui_theme.row('success', 0.16))
        self.candidates.tag_configure('old_cache', background=ui_theme.row('warning', 0.12))
        self.candidates.bind('<Button-3>', self.share_menu)
        self.candidates.bind('<Double-1>', self.share_menu)
        ttk.Label(upper, style='Muted.TLabel',
                  text='同じ target が複数の共有にある場合は TDMS 数の多い共有を既定にしています。'
                       '右クリック／ダブルクリックで読込元の共有を変更できます（共有を絞り込むとその共有から読みます）。'
                       'サンプル行はダブルクリックで開閉。').pack(anchor='w', pady=(4, 0))

        actions = ttk.Frame(upper)
        actions.pack(fill='x', pady=(6, 0))
        self.load_button = ttk.Button(actions, text='選択を読み込む', style='Execute.TButton', command=self.start_load)
        self.load_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='中止', command=self.cancel_flag.set, state='disabled')
        self.cancel_button.pack(side='left', padx=6)
        ttk.Button(actions, text='同じ配列グループを選択', command=self.select_sequence_group).pack(side='left', padx=(10, 0))
        ttk.Button(actions, text='同じ実験の全サンプルから選択',
                   command=self.select_across_samples).pack(side='left', padx=(6, 0))
        ttk.Checkbutton(actions, text='キャッシュだけで読む（サーバーにアクセスしない）',
                        variable=self.offline).pack(side='left', padx=10)
        self.progress = ttk.Progressbar(actions, mode='determinate', length=260)
        self.progress.pack(side='right')
        ttk.Label(actions, textvariable=self.status, style='Muted.TLabel').pack(side='right', padx=10)

        # 下段: 読み込み済み
        lower = ttk.Labelframe(panes, text='② 読み込み済みデータ', padding=6)
        panes.add(lower, weight=2)
        self.loaded = self.make_tree(lower, LOADED_COLUMNS, LOADED_TITLES, show='headings',
                                     widths=dict(group=150, experiment=170, sample=110, target=200, files=90,
                                                 rows=90, missing=80, source=150, note=300))
        self.loaded.tag_configure('warn', background=ui_theme.row('warning', 0.2))
        buttons = ttk.Frame(lower)
        buttons.pack(fill='x', pady=(6, 0))
        ttk.Button(buttons, text='選択を外す', command=self.remove_selected).pack(side='left')
        ttk.Button(buttons, text='すべて外す', command=self.remove_all).pack(side='left', padx=6)
        ttk.Label(buttons, style='Muted.TLabel', text=f'キャッシュ: {src.CACHE_DIR}').pack(side='right')

    def make_tree(self, parent, columns, titles, show, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show=show, selectmode='extended')
        for column, title in zip(columns, titles):
            numeric = column in ('tdms', 'cache', 'files', 'rows', 'missing')
            tree.heading(column, text=title, anchor='e' if numeric else 'w')
            tree.column(column, width=widths.get(column, 120), anchor='e' if numeric else 'w', stretch=False)
        ys = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        xs = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tree.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    # ---------- 一覧 ----------
    def reload_db(self):
        try:
            entries = src.load_entries()
        except Exception as exc:
            messagebox.showerror('DBを読めません', f'{src.DEFAULT_DB}\n{exc}', parent=self)
            return
        self.entries = {e.key: e for e in entries}
        # 変更済みの読込元は、その共有がまだDBにあれば引き継ぐ
        old = self.choice
        self.choice = {key: old[key] if e.occurrence(old.get(key)) else e.default_occurrence().group
                       for key, e in self.entries.items()}
        shares = sorted({o.group for e in entries for o in e.occurrences}, key=lambda g: (src._group_order(g), g))
        self.share_box.configure(values=(ALL_SHARES, *shares))
        if self.share_filter.get() not in shares:
            self.share_filter.set(ALL_SHARES)
        self.apply_share_filter()

    def apply_share_filter(self):
        """共有で絞り込んだら、その共有にある target は読込元もその共有に切り替える"""
        share = self.share_filter.get()
        if share != ALL_SHARES:
            for key, entry in self.entries.items():
                if entry.occurrence(share):
                    self.choice[key] = share
        self.refresh_candidates()

    def schedule_refresh(self):
        if getattr(self, '_refresh_job', None):
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(300, self.refresh_candidates)

    def refresh_candidates(self):
        self._refresh_job = None
        terms = self.filter_text.get().casefold().split()
        share = self.share_filter.get()
        tree = self.candidates
        open_parents = {iid for iid in tree.get_children() if tree.item(iid, 'open')}
        tree.delete(*tree.get_children())
        samples, targets = set(), 0
        for key, entry in self.entries.items():
            text = ' '.join(key).casefold()
            if not all(t in text for t in terms):
                continue
            if share != ALL_SHARES and not entry.occurrence(share):
                continue
            parent = f'{entry.experiment}\x1f{entry.sample}'
            if parent not in samples:
                samples.add(parent)
                tree.insert('', 'end', iid=parent, open=parent in open_parents or bool(terms) and len(samples) <= 3,
                            tags=('sample',))
            targets += 1
            values, tags = self.candidate_row(entry)
            tree.insert(parent, 'end', iid=self.iid(key), values=values, tags=tags)
        for parent in samples:
            self.update_sample_row(parent)
        self.count_label.configure(text=f'{len(samples)} サンプル / {targets:,} target（DB全体 {len(self.entries):,}）')

    @staticmethod
    def iid(key):
        return '\x1e'.join(key)

    @staticmethod
    def key_of(iid):
        return tuple(iid.split('\x1e'))

    def candidate_row(self, entry):
        """(values, tags)。キャッシュ済みの target は色を付ける"""
        occ = entry.occurrence(self.choice[entry.key])
        info = src.cache_info(entry, occ)
        others = ', '.join(f'{o.group}({fmt_count(o.tdms)})' for o in entry.occurrences if o is not occ)
        if info is None:
            cache_text, tags = '未', ()
        elif info[1]:
            cache_text, tags = f'{info[0]:,} 件', ('cached',)
        else:   # q値の無い古い形式。次にサーバーから読むと全ファイル読み直す
            cache_text, tags = f'{info[0]:,} 件（Qなし）', ('old_cache',)
        return (occ.group, f'      {entry.target}', fmt_count(occ.tdms), cache_text, others), tags

    def update_sample_row(self, parent):
        """サンプル行: 開閉の印と、配下 target の共有をまとめて表示"""
        tree = self.candidates
        children = tree.get_children(parent)
        groups = list(dict.fromkeys(tree.set(c, 'group') for c in children))
        experiment, sample = parent.split('\x1f')
        mark = '▼' if tree.item(parent, 'open') else '▶'
        tree.item(parent, values=(', '.join(groups), f'{mark} {experiment} / {sample}',
                                  f'{len(children)} target', '', ''))

    def on_open_close(self, _event):
        # <<TreeviewOpen/Close>> は開閉状態が変わる前に来るので、反映後に印を更新する
        iid = self.candidates.focus()
        if iid and '\x1f' in iid and '\x1e' not in iid:
            self.after_idle(lambda: self.candidates.exists(iid) and self.update_sample_row(iid))

    def update_candidate(self, key):
        iid = self.iid(key)
        if self.candidates.exists(iid):
            values, tags = self.candidate_row(self.entries[key])
            self.candidates.item(iid, values=values, tags=tags)
            self.update_sample_row(self.candidates.parent(iid))

    def share_menu(self, event):
        iid = self.candidates.identify_row(event.y)
        if not iid:
            return
        if '\x1e' not in iid:   # サンプル行: ダブルクリックで開閉
            if event.num != 3:
                self.candidates.item(iid, open=not self.candidates.item(iid, 'open'))
                self.update_sample_row(iid)
                return 'break'
            return
        key = self.key_of(iid)
        entry = self.entries[key]
        if len(entry.occurrences) < 2 and event.num != 3:
            return
        menu = tk.Menu(self, tearoff=False)
        for occ in entry.occurrences:
            mark = '● ' if occ.group == self.choice[key] else '    '
            menu.add_command(label=f'{mark}{occ.group}  （TDMS {fmt_count(occ.tdms)}）',
                             command=lambda g=occ.group: self.set_share(key, g))
        menu.tk_popup(event.x_root, event.y_root)
        return 'break'

    def set_share(self, key, group):
        self.choice[key] = group
        self.update_candidate(key)

    def selected_keys(self):
        keys = []
        for iid in self.candidates.selection():
            children = self.candidates.get_children(iid)
            keys.extend(self.key_of(c) for c in children) if children else keys.append(self.key_of(iid))
        return list(dict.fromkeys(keys))

    def select_sequence_group(self):
        """選択中の target と同じサンプル・同じ基本配列の target（基本配列＋_M_/_N_/_F_ の修飾配列）をまとめて選択"""
        tree = self.candidates
        rows = [iid for iid in tree.selection() if '' in iid]
        if not rows:
            messagebox.showinfo('配列グループ', 'グループのどれか1つの target を選んでから押してください。', parent=self)
            return
        picked = []
        for iid in rows:
            parent = tree.parent(iid)
            base = src.sequence_base(self.key_of(iid)[2])
            picked += [c for c in tree.get_children(parent) if src.sequence_base(self.key_of(c)[2]) == base]
        tree.selection_set(list(dict.fromkeys(picked)))
        tree.see(picked[-1])
        tree.see(picked[0])
        self.status.set(f'{len(set(picked))} target を選択しました')

    def select_across_samples(self):
        """選択中の target と同じ名前の target を、同じ実験のほかのサンプル（一覧に出ているもの）からも選択。
        閉じているサンプルは開く。共有ごとの表記ゆれ（hsa-miR-… / miR…）は同一視する"""
        tree = self.candidates
        rows = [iid for iid in tree.selection() if '\x1e' in iid]
        if not rows:
            messagebox.showinfo('全サンプルから選択', 'まず1つのサンプルで target を選んでください'
                                '（「同じ配列グループを選択」と組み合わせると修飾配列もまとめて選べます）。', parent=self)
            return
        wanted = {}   # 実験 -> target 名の集合
        for iid in rows:
            experiment, _, target = self.key_of(iid)
            wanted.setdefault(experiment, set()).add(src.target_alias(target))
        picked, samples = [], 0
        for parent in tree.get_children():
            experiment = parent.split('\x1f')[0]
            if experiment not in wanted:
                continue
            hits = [c for c in tree.get_children(parent) if src.target_alias(self.key_of(c)[2]) in wanted[experiment]]
            if hits:
                samples += 1
                picked += hits
                if not tree.item(parent, 'open'):
                    tree.item(parent, open=True)
                    self.update_sample_row(parent)
        tree.selection_set(picked)
        tree.see(picked[-1])
        tree.see(picked[0])
        self.status.set(f'{samples} サンプルから {len(picked)} target を選択しました')

    # ---------- 読み込み ----------
    def start_load(self):
        if self.worker is not None:
            return
        keys = self.selected_keys()
        if not keys:
            messagebox.showinfo('読み込み', '上の一覧から target（または実験・サンプル行）を選んでください。', parent=self)
            return
        jobs = [(self.entries[k], self.entries[k].occurrence(self.choice[k])) for k in keys]
        offline = self.offline.get()
        if offline:
            missing = [e.target for e, o in jobs if src.cache_state(e, o) is None]
            if missing:
                messagebox.showwarning('キャッシュなし', 'キャッシュが無いため読み込めない target があります（スキップします）:\n'
                                       + '\n'.join(missing[:20]) + ('\n…' if len(missing) > 20 else ''), parent=self)
                jobs = [(e, o) for e, o in jobs if src.cache_state(e, o) is not None]
                if not jobs:
                    return
        total_tdms = sum(o.tdms or 0 for _, o in jobs)
        if not offline and len(jobs) > 20:
            if not messagebox.askyesno('読み込み', f'{len(jobs)} target（DB上のTDMS 約{total_tdms:,}件）を読み込みます。'
                                       '\nキャッシュ済みのファイルはサーバーから読み直しません。続けますか？', parent=self):
                return
        self.cancel_flag.clear()
        self.load_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.configure(maximum=len(jobs), value=0)
        self.worker = threading.Thread(target=self.run_jobs, args=(jobs, offline), daemon=True)
        self.worker.start()
        self.after(100, self.poll)

    def run_jobs(self, jobs, offline):
        """ワーカースレッド: target を1つずつ順番に読む（Tk には queue 経由でだけ触れる）"""
        for index, (entry, occ) in enumerate(jobs):
            if self.cancel_flag.is_set():
                break

            def progress(done, total, name, index=index, entry=entry):
                self.queue.put(('progress', index + (done / total if total else 1),
                                f'[{index + 1}/{len(jobs)}] {entry.sample} / {entry.target}  {done}/{total} ファイル'))
            try:
                result = src.load_target(entry, occ, offline=offline, progress=progress,
                                         cancel=self.cancel_flag.is_set)
                self.queue.put(('result', result, None))
            except Exception as exc:
                self.queue.put(('error', f'{occ.group} / {entry.sample} / {entry.target}', str(exc)))
            self.queue.put(('progress', index + 1, ''))
        self.queue.put(('done', None, None))

    def poll(self):
        errors = getattr(self, '_errors', [])
        try:
            while True:
                kind, a, b = self.queue.get_nowait()
                if kind == 'progress':
                    self.progress.configure(value=a)
                    if b:
                        self.status.set(b)
                elif kind == 'result':
                    self.app.add_dataset(a)
                    self.update_candidate(a.entry.key)
                elif kind == 'error':
                    errors.append(f'{a}: {b}')
                elif kind == 'done':
                    self.finish(errors)
                    return
        except queue.Empty:
            pass
        self._errors = errors
        self.after(100, self.poll)

    def finish(self, errors):
        self._errors = []
        self.worker = None
        self.load_button.configure(state='normal')
        self.cancel_button.configure(state='disabled')
        self.status.set('中止しました' if self.cancel_flag.is_set() else f'読み込み完了（{len(self.app.datasets)} target 読込済み）')
        if errors:
            messagebox.showerror('読み込みエラー', '\n'.join(errors[:20]), parent=self)

    # ---------- 読み込み済み ----------
    def show_dataset(self, ident, result: src.LoadResult):
        data = result.data
        missing = int(data['Relative Averaged Data'].isna().sum()) if not data.empty else 0
        source = ('キャッシュのみ' if result.offline
                  else f'サーバー {result.files_read} / キャッシュ {result.files_cached}')
        notes = []
        if result.skipped:
            notes.append(f'LAP Tableなし {len(result.skipped)}')
        if result.errors:
            notes.append(f'読込エラー {len(result.errors)}')
        if result.old_cache:
            notes.append('古いキャッシュ（q値なし）')
        if result.offline and result.occurrence.tdms is not None and result.files_total < result.occurrence.tdms:
            notes.append(f'DB上は {result.occurrence.tdms} 件（キャッシュ未更新）')
        if not result.offline and result.files_read + result.files_cached < result.files_total:
            notes.append('途中で中止')
        values = (result.occurrence.group, result.entry.experiment, result.entry.sample, result.entry.target,
                  f'{result.files_total:,}', f'{len(data):,}', f'{missing:,}', source, '、'.join(notes))
        tags = ('warn',) if notes else ()
        if self.loaded.exists(ident):
            self.loaded.item(ident, values=values, tags=tags)
        else:
            self.loaded.insert('', 'end', iid=ident, values=values, tags=tags)

    def remove_selected(self):
        for iid in self.loaded.selection():
            self.app.remove_dataset(iid)

    def remove_all(self):
        for iid in self.loaded.get_children():
            self.app.remove_dataset(iid)


class PlotPanel(ttk.Frame):
    """読み込み済みデータを選んで描画する。複数選択すると同じ軸・同じビンで比較する。"""

    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.idents: list[str] = []          # Listbox の並び順に対応
        self.kind = tk.StringVar(value='violin')
        self.exclude = tk.BooleanVar(value=True)
        self.iqr_k = tk.DoubleVar(value=plots.OUTLIER_IQR_K)
        self.per_position = tk.BooleanVar(value=False)
        self.bin_width = tk.DoubleVar(value=plots.BIN_WIDTH)
        self.scheme = tk.StringVar(value=next(iter(plots.POSITION_COLOR_SCHEMES)))
        self.pinned: str | None = None       # 基準として固定したデータ（常に比較の1番目＝島の一番左）
        self._job = None
        self.build()

    def build(self):
        left = ttk.Frame(self, width=340)
        left.pack(side='left', fill='y')
        left.pack_propagate(False)

        box = ttk.Labelframe(left, text='データ（Ctrl / Shift で複数選択、★基準と比較）', padding=6)
        box.pack(fill='both', expand=True)
        self.listbox = tk.Listbox(box, selectmode='extended', exportselection=False, height=10,
                                  activestyle='none', relief='flat', borderwidth=0, highlightthickness=1,
                                  highlightbackground=ui_theme.BORDER)
        self.listbox.pack(fill='both', expand=True)
        self.listbox.bind('<<ListboxSelect>>', lambda e: (self.color_selection(), self.schedule_draw()))
        self.listbox.bind('<Double-Button-1>', self.toggle_pin_at)
        row = ttk.Frame(box)
        row.pack(fill='x', pady=(6, 0))
        ttk.Button(row, text='すべて選択', command=self.select_all).pack(side='left')
        self.pin_button = ttk.Button(row, text='★ 選択行を基準に固定', command=self.toggle_pin)
        self.pin_button.pack(side='left', padx=6)
        ttk.Label(box, text='基準は常に比較に入り、1番目（島の一番左）になります。ダブルクリックでも固定／解除',
                  style='Muted.TLabel', wraplength=320).pack(anchor='w', pady=(4, 0))

        kinds = ttk.Labelframe(left, text='グラフの種類', padding=6)
        kinds.pack(fill='x', pady=(8, 0))
        for title, kind in plots.PLOTS.items():
            ttk.Radiobutton(kinds, text=title, value=kind, variable=self.kind,
                            command=self.schedule_draw).pack(anchor='w')

        colors = ttk.Labelframe(left, text='位置の色（箱ひげ・バイオリン・ヒストグラム）', padding=6)
        colors.pack(fill='x', pady=(8, 0))
        combo = ttk.Combobox(colors, textvariable=self.scheme, state='readonly',
                             values=list(plots.POSITION_COLOR_SCHEMES))
        combo.pack(fill='x')
        combo.bind('<<ComboboxSelected>>', lambda e: self.schedule_draw())
        ttk.Label(colors, text='複数データの比較ではデータごとの色になります', style='Muted.TLabel').pack(anchor='w')

        options = ttk.Labelframe(left, text='外れ値・ビン', padding=6)
        options.pack(fill='x', pady=(8, 0))
        ttk.Checkbutton(options, text='外れ値を除外する（IQR基準）', variable=self.exclude,
                        command=self.schedule_draw).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(options, text='IQR の倍率 k').grid(row=1, column=0, sticky='w', pady=(4, 0))
        ttk.Spinbox(options, from_=1, to=20, increment=0.5, width=7, textvariable=self.iqr_k,
                    command=self.schedule_draw).grid(row=1, column=1, sticky='w', pady=(4, 0))
        ttk.Checkbutton(options, text='位置（MS_IDX）ごとに判定する', variable=self.per_position,
                        command=self.schedule_draw).grid(row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Label(options, text='ビン幅').grid(row=3, column=0, sticky='w', pady=(4, 0))
        ttk.Spinbox(options, from_=0.005, to=0.2, increment=0.005, width=7, textvariable=self.bin_width,
                    command=self.schedule_draw).grid(row=3, column=1, sticky='w', pady=(4, 0))

        buttons = ttk.Frame(left)
        buttons.pack(fill='x', pady=(8, 0))
        ttk.Button(buttons, text='描画', style='Execute.TButton', command=self.draw).pack(side='left')
        ttk.Button(buttons, text='選択データの図・CSVを一式保存', command=self.save_outputs).pack(side='left', padx=6)
        ttk.Label(left, text=f'保存先: {OUTPUT_DIR}', style='Muted.TLabel', wraplength=330).pack(anchor='w', pady=(4, 0))

        right = ttk.Frame(self)
        right.pack(side='left', fill='both', expand=True, padx=(10, 0))
        self.figure = Figure(figsize=(10, 6), facecolor='white')
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        toolbar = NavigationToolbar2Tk(self.canvas, right, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side='top', fill='x')
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        self.info = tk.Text(right, height=4, wrap='word')
        ui_theme.style_text(self.info)
        self.info.pack(fill='x', pady=(6, 0))
        self.message('「読み込み」タブでデータを読み込み、左の一覧から選んでください。')

    def message(self, text):
        self.info.configure(state='normal')
        self.info.delete('1.0', 'end')
        self.info.insert('1.0', text)
        self.info.configure(state='disabled')

    # ---------- データ一覧 ----------
    def labels(self):
        """凡例用の名前。同じサンプル / target が複数の共有から読まれていれば共有名も付ける"""
        results = [self.app.datasets[i] for i in self.idents]
        names = [f'{r.entry.sample} / {r.entry.target}' for r in results]
        return [f'{n} [{r.occurrence.group}]' if names.count(n) > 1 else n for n, r in zip(names, results)]

    def refresh_list(self):
        selected = {self.idents[i] for i in self.listbox.curselection()}
        self.idents = [i for i in self.app.datasets if not self.app.datasets[i].data.empty]
        if self.pinned not in self.idents:
            self.pinned = None
        self.listbox.delete(0, 'end')
        for index, (ident, label) in enumerate(zip(self.idents, self.labels())):
            self.listbox.insert('end', f'★ {label}' if ident == self.pinned else label)
            if ident in selected:
                self.listbox.selection_set(index)
        if not self.listbox.curselection() and self.idents:
            self.listbox.selection_set(0)
        self.color_selection()

    def select_all(self):
        self.listbox.selection_set(0, 'end')
        self.color_selection()
        self.schedule_draw()

    def toggle_pin(self):
        """選択中の行（複数なら一番上）を基準に固定。すでに基準の行なら解除"""
        selected = self.listbox.curselection()
        if not selected:
            if self.pinned:
                self.pinned = None
                self.refresh_list()
                self.schedule_draw()
            return
        self._set_pin(self.idents[selected[0]])

    def toggle_pin_at(self, event):
        index = self.listbox.nearest(event.y)
        if 0 <= index < len(self.idents):
            self._set_pin(self.idents[index])

    def _set_pin(self, ident):
        self.pinned = None if ident == self.pinned else ident
        self.refresh_list()
        self.schedule_draw()

    def chosen_indices(self):
        """作図に使う行の並び。基準があれば先頭に置き、残りは一覧の上から順"""
        selected = list(self.listbox.curselection())
        if self.pinned is None:
            return selected
        pin = self.idents.index(self.pinned)
        return [pin] + [i for i in selected if i != pin]

    def color_selection(self):
        """複数比較時は、使う行を図のデータ色（凡例・島の短冊と同じ順・同じ色）で塗る。
        基準の行は選択していなくても比較に入るので、選択外でも色を付ける"""
        chosen = self.chosen_indices()
        multi = len(chosen) > 1
        rank = {index: k for k, index in enumerate(chosen)}
        pin = self.idents.index(self.pinned) if self.pinned else None
        for index in range(self.listbox.size()):
            color = plots.DATASET_COLORS[rank[index] % len(plots.DATASET_COLORS)] if multi and index in rank else ''
            self.listbox.itemconfigure(index, selectbackground=color, selectforeground='white' if color else '')
            if index == pin:
                self.listbox.itemconfigure(index, background=color or plots.DATASET_COLORS[0], foreground='white')
            else:
                self.listbox.itemconfigure(index, background='', foreground='')
        self.pin_button.configure(text='★ 基準の固定を解除' if self.pinned and (
            not self.listbox.curselection() or self.listbox.curselection()[0] == pin) else '★ 選択行を基準に固定')

    # ---------- 描画 ----------
    def schedule_draw(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(250, self.draw)

    def scheme_key(self):
        return plots.POSITION_COLOR_SCHEMES[self.scheme.get()]

    def selected_preps(self):
        labels = self.labels()
        k = float(self.iqr_k.get())
        preps = []
        for index in self.chosen_indices():
            result = self.app.datasets[self.idents[index]]
            preps.append(plots.prepare(result.data, labels[index], exclude_outliers=self.exclude.get(), iqr_k=k,
                                       per_position=self.per_position.get(), experiment=result.entry.experiment,
                                       sample=result.entry.sample, target=result.entry.target,
                                       group=result.occurrence.group))
        return preps

    def draw(self):
        self._job = None
        if not self.chosen_indices():
            return
        try:
            preps = self.selected_preps()
            edges = plots.shared_bin_edges(preps, bin_width=float(self.bin_width.get()))
            plots.draw(self.kind.get(), preps, edges, figure=self.figure, scheme=self.scheme_key())
        except (ValueError, tk.TclError) as exc:
            self.figure.clear()
            self.message(f'描画できません: {exc}')
        else:
            self.message('\n'.join(p.summary() for p in preps))
        self.canvas.draw_idle()

    def save_outputs(self):
        """選択データごとに heat_data/{日時}_{実験}_{サンプル}_{target}/ へ CSV と図を保存"""
        if not self.chosen_indices():
            messagebox.showinfo('保存', '保存するデータを選んでください。', parent=self)
            return
        stamp = datetime.now().strftime('%Y%m%d%H%M')
        bin_width = float(self.bin_width.get())
        self.configure(cursor='watch')
        self.update_idletasks()
        folders = []
        try:
            for prep in self.selected_preps():
                if prep.clean.empty:
                    continue
                edges = plots.shared_bin_edges([prep], bin_width=bin_width)
                folders.append(plots.save_outputs(prep, edges, OUTPUT_DIR, timestamp=stamp,
                                                  scheme=self.scheme_key()))
        finally:
            self.configure(cursor='')
        if folders and messagebox.askyesno('保存しました', f'{len(folders)} 件を保存しました。\n\n'
                                           + '\n'.join(os.path.basename(f) for f in folders)
                                           + '\n\n保存先フォルダを開きますか？', parent=self):
            os.startfile(OUTPUT_DIR)


class AssemblyPanel(ttk.Frame):
    """アセンブリ結果（Assigned Base の文字列）を塩基ごとに色分けして表示する。
    1データ: 参照配列・カバレッジ・読みのパイルアップ。複数データ: 参照配列の比較"""

    DIRECTIONS = {'両方': 'both', '順方向 → のみ': asm.FORWARD, '逆方向 ← のみ': asm.REVERSE}
    Q_TEXT_COLORS = {'high': '#7fd88f', 'mid': '#f2c94c', 'low': '#ff7b72', 'none': ui_theme.DIM}

    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.idents: list[str] = []
        self.sort = tk.StringVar(value=next(iter(asm.SORTS)))
        self.merge = tk.BooleanVar(value=True)
        self.direction = tk.StringVar(value='両方')
        self.min_length = tk.IntVar(value=1)
        self.max_rows = tk.IntVar(value=200)
        self.color = tk.StringVar(value=next(k for k, v in asm.COLOR_MODES.items() if v == 'base_q'))
        self.min_read_q = tk.DoubleVar(value=0.0)
        self.show_quality = tk.BooleanVar(value=True)
        self._job = None
        self._lines = []
        self.build()

    def build(self):
        left = ttk.Frame(self, width=320)
        left.pack(side='left', fill='y')
        left.pack_propagate(False)

        box = ttk.Labelframe(left, text='データ（1つ: パイルアップ／複数: 配列の比較）', padding=6)
        box.pack(fill='both', expand=True)
        self.listbox = tk.Listbox(box, selectmode='extended', exportselection=False, height=10,
                                  activestyle='none', relief='flat', borderwidth=0, highlightthickness=1,
                                  highlightbackground=ui_theme.BORDER)
        self.listbox.pack(fill='both', expand=True)
        self.listbox.bind('<<ListboxSelect>>', lambda e: self.schedule_draw())

        options = ttk.Labelframe(left, text='表示', padding=6)
        options.pack(fill='x', pady=(8, 0))
        ttk.Checkbutton(options, text='同じ区間・向きの読みを1行にまとめる', variable=self.merge,
                        command=self.schedule_draw).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(options, text='並び順').grid(row=1, column=0, sticky='w', pady=(4, 0))
        combo = ttk.Combobox(options, textvariable=self.sort, state='readonly', values=list(asm.SORTS), width=22)
        combo.grid(row=1, column=1, sticky='w', pady=(4, 0))
        combo.bind('<<ComboboxSelected>>', lambda e: self.schedule_draw())
        ttk.Label(options, text='向き').grid(row=2, column=0, sticky='w', pady=(4, 0))
        combo = ttk.Combobox(options, textvariable=self.direction, state='readonly', values=list(self.DIRECTIONS),
                             width=22)
        combo.grid(row=2, column=1, sticky='w', pady=(4, 0))
        combo.bind('<<ComboboxSelected>>', lambda e: self.schedule_draw())
        ttk.Label(options, text='最小の長さ').grid(row=3, column=0, sticky='w', pady=(4, 0))
        ttk.Spinbox(options, from_=1, to=50, width=7, textvariable=self.min_length,
                    command=self.schedule_draw).grid(row=3, column=1, sticky='w', pady=(4, 0))
        ttk.Label(options, text='図の最大行数').grid(row=4, column=0, sticky='w', pady=(4, 0))
        ttk.Spinbox(options, from_=10, to=5000, increment=50, width=7, textvariable=self.max_rows,
                    command=self.schedule_draw).grid(row=4, column=1, sticky='w', pady=(4, 0))
        ttk.Label(options, text='マスの色').grid(row=5, column=0, sticky='w', pady=(4, 0))
        combo = ttk.Combobox(options, textvariable=self.color, state='readonly', values=list(asm.COLOR_MODES),
                             width=22)
        combo.grid(row=5, column=1, sticky='w', pady=(4, 0))
        combo.bind('<<ComboboxSelected>>', lambda e: self.schedule_draw())

        quality = ttk.Labelframe(left, text='q値（LCP Table の塩基ごとの Q）', padding=6)
        quality.pack(fill='x', pady=(8, 0))
        ttk.Label(quality, text='読みの平均Q の下限').grid(row=0, column=0, sticky='w')
        ttk.Spinbox(quality, from_=0, to=40, increment=1, width=7, textvariable=self.min_read_q,
                    command=self.schedule_draw).grid(row=0, column=1, sticky='w', padx=(6, 0))
        ttk.Checkbutton(quality, text='文字列に q値（Phred+33）の行を付ける', variable=self.show_quality,
                        command=self.schedule_draw).grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))

        legend = ttk.Labelframe(left, text='色の見方', padding=6)
        legend.pack(fill='x', pady=(8, 0))
        chips = tk.Text(legend, height=4, wrap='word', font=(ui_theme.MONO, 11))
        ui_theme.style_text(chips)
        for base in ('A', 'U', 'G', 'C', 'M', 'N', 'S', 'X', 'Z'):
            chips.tag_configure(base, foreground=asm.base_color(base, text=True), font=(ui_theme.MONO, 11, 'bold'))
            chips.insert('end', f' {base} ', base)
        chips.insert('end', '\n A/U/G/C 以外は修飾塩基\n', 'muted')
        lo, hi = asm.Q_BANDS
        for band, text in (('high', f' Q≥{hi:.0f} '), ('mid', f' {lo:.0f}≤Q<{hi:.0f} '), ('low', f' Q<{lo:.0f} ')):
            chips.tag_configure(f'q_{band}', foreground=self.Q_TEXT_COLORS[band], font=(ui_theme.MONO, 10, 'bold'))
            chips.insert('end', text, f'q_{band}')
        chips.insert('end', '  ← q値の文字の色', 'muted')
        chips.tag_configure('muted', foreground=ui_theme.MUTED, font=(ui_theme.UI, 9))
        chips.configure(state='disabled')
        chips.pack(fill='x')

        buttons = ttk.Frame(left)
        buttons.pack(fill='x', pady=(8, 0))
        ttk.Button(buttons, text='描画', style='Execute.TButton', command=self.draw).pack(side='left')
        ttk.Button(buttons, text='文字列をコピー', command=self.copy_text).pack(side='left', padx=6)
        ttk.Button(buttons, text='読みをCSV保存', command=self.save_csv).pack(side='left')

        right = ttk.Panedwindow(self, orient='vertical')
        right.pack(side='left', fill='both', expand=True, padx=(10, 0))
        upper = ttk.Frame(right)
        right.add(upper, weight=3)
        self.figure = Figure(figsize=(10, 6), facecolor='white')
        self.canvas = FigureCanvasTkAgg(self.figure, master=upper)
        toolbar = NavigationToolbar2Tk(self.canvas, upper, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side='top', fill='x')
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

        lower = ttk.Labelframe(right, text='アセンブリ（文字列）', padding=4)
        right.add(lower, weight=2)
        frame = ttk.Frame(lower)
        frame.pack(fill='both', expand=True)
        self.text = tk.Text(frame, wrap='none', font=(ui_theme.MONO, 11), height=12)
        ui_theme.style_text(self.text)
        ys = ttk.Scrollbar(frame, orient='vertical', command=self.text.yview)
        xs = ttk.Scrollbar(frame, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for base, color in asm.TEXT_COLORS.items():
            self.text.tag_configure(base, foreground=color, font=(ui_theme.MONO, 11, 'bold'))
        self.text.tag_configure('other', foreground='#cfcfcf', font=(ui_theme.MONO, 11, 'bold'))
        self.text.tag_configure('gap', foreground=ui_theme.DIM)
        self.text.tag_configure('head', foreground=ui_theme.MUTED)
        self.text.tag_configure('ref', background=ui_theme.mix(ui_theme.FIELD, '#ffffff', 0.08))
        self.text.tag_configure('ruler', foreground=ui_theme.DIM)
        self.text.tag_configure('fwd', foreground=ui_theme.TEXT)
        self.text.tag_configure('rev', foreground=ui_theme.MUTED)
        self.text.tag_configure('title', foreground=ACCENT, font=(ui_theme.UI_BOLD, 10))
        for band, color in self.Q_TEXT_COLORS.items():
            self.text.tag_configure(f'q_{band}', foreground=color)
        self.write_message('「読み込み」タブでデータを読み込み、左の一覧から選んでください。')

    # ---------- データ一覧 ----------
    def refresh_list(self):
        selected = {self.idents[i] for i in self.listbox.curselection()}
        self.idents = [i for i in self.app.datasets if not self.app.datasets[i].data.empty]
        self.listbox.delete(0, 'end')
        for index, ident in enumerate(self.idents):
            r = self.app.datasets[ident]
            self.listbox.insert('end', f'{r.entry.sample} / {r.entry.target}')
            if ident in selected:
                self.listbox.selection_set(index)
        if not self.listbox.curselection() and self.idents:
            self.listbox.selection_set(0)

    def selected_assemblies(self):
        direction = self.DIRECTIONS[self.direction.get()]
        min_length = max(int(self.min_length.get() or 1), 1)
        min_read_q = max(float(self.min_read_q.get() or 0), 0.0)
        out = []
        for index in self.listbox.curselection():
            result = self.app.datasets[self.idents[index]]
            label = f'{result.entry.sample} / {result.entry.target}'
            out.append(asm.build(result.data, label, result.entry.target, direction, min_length, min_read_q))
        return out

    # ---------- 描画 ----------
    def schedule_draw(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(250, self.draw)

    def draw(self):
        self._job = None
        if not self.listbox.curselection():
            return
        self.configure(cursor='watch')
        self.update_idletasks()
        try:
            assemblies = self.selected_assemblies()
            sort = asm.SORTS[self.sort.get()]
            if len(assemblies) == 1:
                asm.plot_pileup(assemblies[0], self.figure, sort=sort, merge=self.merge.get(),
                                max_rows=max(int(self.max_rows.get() or 200), 10),
                                color=asm.COLOR_MODES[self.color.get()])
            else:
                asm.plot_compare(assemblies, self.figure)
            self.write_alignment(assemblies, sort)
        except (ValueError, tk.TclError) as exc:
            self.figure.clear()
            self.write_message(f'描画できません: {exc}')
        finally:
            self.configure(cursor='')
        self.canvas.draw_idle()

    def write_message(self, text):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.configure(state='disabled')

    def write_alignment(self, assemblies, sort):
        """文字列のアセンブリを、塩基ごとの色付きで書き出す（複数データは参照配列を並べたあとに順に）"""
        merge = self.merge.get()
        t = self.text
        t.configure(state='normal')
        t.delete('1.0', 'end')
        self._lines = []
        if len(assemblies) > 1:
            t.insert('end', '参照配列の比較\n', 'title')
            width = max(len(a.label) for a in assemblies)
            for a in assemblies:
                t.insert('end', f'{a.label:<{width}}  ', 'head')
                self.insert_bases(a.sequence)
                t.insert('end', f'  {len(a.reads):,} 本\n', 'head')
                self._lines.append(f'{a.label:<{width}}  {a.sequence}')
            t.insert('end', '\n')
        for a in assemblies:
            t.insert('end', f'{a.summary()}\n', 'title')
            self._lines.append(a.summary())
            for line in asm.alignment_lines(a, sort=sort, merge=merge, show_quality=self.show_quality.get()):
                t.insert('end', f'{line.head:>8}  ', 'head')
                if line.kind == 'ruler':
                    t.insert('end', line.seq, 'ruler')
                elif line.kind == 'ref':
                    start = t.index('end-1c')
                    self.insert_bases(line.seq)
                    t.tag_add('ref', start, 'end-1c')
                elif line.kind == 'qual':
                    self.insert_quality(line.seq, line.quals)
                else:
                    self.insert_bases(line.seq)
                tail = line.tail
                tag = 'fwd' if tail.startswith(asm.FORWARD) else 'rev' if tail.startswith(asm.REVERSE) else 'head'
                t.insert('end', f'  {tail}\n' if tail else '\n', tag)
                self._lines.append(f'{line.head:>8}  {line.seq}  {tail}'.rstrip())
            t.insert('end', '\n')
            self._lines.append('')
        t.configure(state='disabled')

    def insert_bases(self, seq):
        """同じ色の連続はまとめて1回で挿入する（数千行でも速く書けるように）"""
        t = self.text
        run, run_tag = '', None
        for ch in seq:
            tag = 'gap' if ch == asm.GAP_CHAR else ch if ch in asm.TEXT_COLORS else 'other'
            if tag != run_tag and run:
                t.insert('end', run, run_tag)
                run = ''
            run, run_tag = run + ch, tag
        if run:
            t.insert('end', run, run_tag)

    def insert_quality(self, chars, quals):
        """q値の文字列を、Q の高・中・低で色分けして挿入する（同じ色の連続はまとめる）"""
        t = self.text
        run, run_tag = '', None
        for ch, q in zip(chars, quals):
            tag = f'q_{asm.q_band(q)}'
            if tag != run_tag and run:
                t.insert('end', run, run_tag)
                run = ''
            run, run_tag = run + ch, tag
        if run:
            t.insert('end', run, run_tag)

    # ---------- 出力 ----------
    def copy_text(self):
        if not self._lines:
            return
        self.clipboard_clear()
        self.clipboard_append('\n'.join(self._lines))

    def save_csv(self):
        assemblies = self.selected_assemblies() if self.listbox.curselection() else []
        if not assemblies:
            messagebox.showinfo('保存', '保存するデータを選んでください。', parent=self)
            return
        stamp = datetime.now().strftime('%Y%m%d%H%M')
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        saved = []
        for a in assemblies:
            # 配列ごとの値・Q は位置の昇順（sequence と同じ並び）で空白区切り、q値文字列は Phred+33
            reads = a.reads.assign(
                values=[' '.join(f'{v:.4f}' for v in vals) for vals in a.reads['values']],
                quals=[' '.join(f'{q:.2f}' for q in qs) for qs in a.reads['quals']],
                ascii_q=[''.join(asm.q_char(q) for q in qs) for qs in a.reads['quals']])
            name = f'{stamp}_assembly_{plots._sanitize(a.label.replace(" / ", "_"))}.csv'
            reads.to_csv(OUTPUT_DIR / name, index=False, encoding='utf-8-sig')
            saved.append(name)
        if messagebox.askyesno('保存しました', '\n'.join(saved) + '\n\n保存先フォルダを開きますか？', parent=self):
            os.startfile(OUTPUT_DIR)


class ModificationPanel(ttk.Frame):
    """修飾配列（基本配列_M_22 など）と未修飾配列の、修飾位置での行数から修飾率を出す。
    0% / 100% の決め方（補正）と信頼区間の出し方を切り替えて比べられる"""

    TABLE_COLUMNS = ('sample', 'sequence', 'site', 'n_mod', 'n_unmod', 'chunks', 'raw', 'rate', 'ci', 'note')
    TABLE_TITLES = ('サンプル', '配列', '修飾', '修飾 行', '未修飾 行', 'ファイル', '補正なし', '修飾率', '95%区間', '備考')

    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.sample_keys: list[tuple] = []
        self.visible_sample_keys: list[tuple] = []
        self.selected_sample_keys: set[tuple] = set()
        self.sample_search = tk.StringVar()
        self.target_sequences: list[str] = []
        self.sites: list[mod.SiteCounts] = []
        self.table = None
        self.method = tk.StringVar(value=next(iter(mod.METHODS)))
        self.ci = tk.StringVar(value=next(iter(mod.CI_METHODS)))
        self.zero = tk.StringVar()
        self.full = tk.StringVar()
        self.min_q = tk.DoubleVar(value=0.0)
        self._job = None
        self._dirty = True      # 読み込み済みデータか Q の下限が変わったら数え直す
        self.build()

    def build(self):
        left = ttk.Frame(self, width=330)
        left.pack(side='left', fill='y')
        left.pack_propagate(False)

        box = ttk.Labelframe(left, text='サンプル（Ctrl / Shift で複数選択）', padding=6)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='実験名・サンプル名で検索（空白区切りで AND）').pack(anchor='w')
        search = ttk.Frame(box)
        search.pack(fill='x', pady=(2, 4))
        ttk.Entry(search, textvariable=self.sample_search).pack(side='left', fill='x', expand=True)
        ttk.Button(search, text='クリア', command=lambda: self.sample_search.set('')).pack(side='left', padx=(4, 0))
        self.sample_search.trace_add('write', lambda *args: self.filter_samples())
        buttons = ttk.Frame(box)
        buttons.pack(fill='x', pady=(0, 4))
        ttk.Button(buttons, text='検索結果を選択', command=self.select_search_results).pack(side='left')
        ttk.Button(buttons, text='選択を解除', command=self.clear_sample_selection).pack(side='left', padx=(4, 0))
        self.listbox = tk.Listbox(box, selectmode='extended', exportselection=False, height=8,
                                  activestyle='none', relief='flat', borderwidth=0, highlightthickness=1,
                                  highlightbackground=ui_theme.BORDER)
        self.listbox.pack(fill='both', expand=True)
        self.listbox.bind('<<ListboxSelect>>', self.sample_selection_changed)
        ttk.Label(box, style='Muted.TLabel', wraplength=310,
                  text='基本配列と、修飾配列（基本配列_M_22 など）を同じサンプルで読み込むと表示されます。'
                       '読み込みタブの「同じ配列グループを選択」が便利です。').pack(anchor='w', pady=(4, 0))

        targets = ttk.Labelframe(left, text='ターゲット（Ctrl / Shift で複数選択）', padding=6)
        targets.pack(fill='x', pady=(8, 0))
        target_buttons = ttk.Frame(targets)
        target_buttons.pack(fill='x', pady=(0, 4))
        ttk.Button(target_buttons, text='すべて選択',
                   command=lambda: self.select_targets(True)).pack(side='left')
        ttk.Button(target_buttons, text='選択を解除',
                   command=lambda: self.select_targets(False)).pack(side='left', padx=(4, 0))
        target_frame = ttk.Frame(targets)
        target_frame.pack(fill='x')
        self.target_listbox = tk.Listbox(target_frame, selectmode='extended', exportselection=False,
                                        height=4, activestyle='none', relief='flat', borderwidth=0,
                                        highlightthickness=1, highlightbackground=ui_theme.BORDER)
        target_scroll = ttk.Scrollbar(target_frame, orient='vertical', command=self.target_listbox.yview)
        self.target_listbox.configure(yscrollcommand=target_scroll.set)
        self.target_listbox.pack(side='left', fill='x', expand=True)
        target_scroll.pack(side='right', fill='y')
        self.target_listbox.bind('<<ListboxSelect>>', lambda e: self.schedule_draw())

        scale = ttk.Labelframe(left, text='0% と 100% の決め方', padding=6)
        scale.pack(fill='x', pady=(8, 0))
        for title in mod.METHODS:
            ttk.Radiobutton(scale, text=title, value=title, variable=self.method,
                            command=self.update_controls).pack(anchor='w')
        grid = ttk.Frame(scale)
        grid.pack(fill='x', pady=(4, 0))
        ttk.Label(grid, text='0% 対照').grid(row=0, column=0, sticky='w')
        self.zero_combo = ttk.Combobox(grid, textvariable=self.zero, state='readonly', width=28)
        self.zero_combo.grid(row=0, column=1, sticky='w', padx=(6, 0))
        ttk.Label(grid, text='100% 対照').grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.full_combo = ttk.Combobox(grid, textvariable=self.full, state='readonly', width=28)
        self.full_combo.grid(row=1, column=1, sticky='w', padx=(6, 0), pady=(4, 0))
        for combo in (self.zero_combo, self.full_combo):
            combo.bind('<<ComboboxSelected>>', lambda e: self.schedule_draw())
        ttk.Label(scale, style='Muted.TLabel', wraplength=310,
                  text='対照は同じ配列・同じ修飾のある読み込み済みサンプルから選びます'
                       '（未修飾の合成配列＝0%、全修飾の合成配列＝100% など）。').pack(anchor='w', pady=(4, 0))

        options = ttk.Labelframe(left, text='集計', padding=6)
        options.pack(fill='x', pady=(8, 0))
        ttk.Label(options, text='信頼区間').grid(row=0, column=0, sticky='w')
        combo = ttk.Combobox(options, textvariable=self.ci, state='readonly', values=list(mod.CI_METHODS), width=30)
        combo.grid(row=0, column=1, sticky='w', padx=(6, 0))
        combo.bind('<<ComboboxSelected>>', lambda e: self.schedule_draw())
        ttk.Label(options, text='修飾位置の Q 下限').grid(row=1, column=0, sticky='w', pady=(4, 0))
        ttk.Spinbox(options, from_=0, to=40, increment=1, width=7, textvariable=self.min_q,
                    command=self.recount).grid(row=1, column=1, sticky='w', padx=(6, 0), pady=(4, 0))
        ttk.Label(options, style='Muted.TLabel', wraplength=310,
                  text='0 で絞らない。q値の無い古いキャッシュには効きません。').grid(
            row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))

        buttons = ttk.Frame(left)
        buttons.pack(fill='x', pady=(8, 0))
        ttk.Button(buttons, text='計算', style='Execute.TButton', command=self.draw).pack(side='left')
        ttk.Button(buttons, text='表をCSV保存', command=self.save_csv).pack(side='left', padx=6)

        right = ttk.Panedwindow(self, orient='vertical')
        right.pack(side='left', fill='both', expand=True, padx=(10, 0))
        upper = ttk.Frame(right)
        right.add(upper, weight=3)
        self.figure = Figure(figsize=(10, 6), facecolor='white')
        self.canvas = FigureCanvasTkAgg(self.figure, master=upper)
        toolbar = NavigationToolbar2Tk(self.canvas, upper, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side='top', fill='x')
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

        lower = ttk.Labelframe(right, text='修飾率（行を選ぶと、その修飾位置の値の分布を下段に表示）', padding=4)
        right.add(lower, weight=1)
        frame = ttk.Frame(lower)
        frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(frame, columns=self.TABLE_COLUMNS, show='headings', selectmode='browse', height=8)
        widths = dict(sample=180, sequence=160, site=80, n_mod=80, n_unmod=80, chunks=70, raw=80, rate=80,
                      ci=130, note=320)
        for column, title in zip(self.TABLE_COLUMNS, self.TABLE_TITLES):
            numeric = column in ('n_mod', 'n_unmod', 'chunks', 'raw', 'rate')
            self.tree.heading(column, text=title, anchor='e' if numeric else 'w')
            self.tree.column(column, width=widths[column], anchor='e' if numeric else 'w', stretch=column == 'note')
        self.tree.tag_configure('warn', background=ui_theme.row('warning', 0.2))
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.render())
        ys = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side='left', fill='both', expand=True)
        ys.pack(side='left', fill='y')
        self.update_controls()

    # ---------- データ ----------
    def datasets(self):
        return [mod.Dataset(r.entry.experiment, r.entry.sample, r.entry.target, r.data, r.occurrence.group)
                for r in self.app.datasets.values() if not r.data.empty]

    def refresh_list(self):
        """読み込み済みデータが変わったら呼ばれる（数え直しは描画時にまとめて行う）"""
        self._dirty = True
        old = set(self.sample_keys)
        old_targets = set(self.target_sequences)
        selected_targets = {self.target_sequences[i] for i in self.target_listbox.curselection()}
        self.target_sequences = sorted({site[0] for d in self.datasets()
                                        if (site := mod.parse_site(d.target))}, key=str.casefold)
        self.target_listbox.delete(0, 'end')
        for index, sequence in enumerate(self.target_sequences):
            self.target_listbox.insert('end', sequence)
            if sequence in selected_targets or sequence not in old_targets:
                self.target_listbox.selection_set(index)
        self.remember_sample_selection()
        keys = {(d.experiment, d.sample) for d in self.datasets() if mod.parse_site(d.target)}
        self.sample_keys = sorted(keys, key=lambda k: (k[0].casefold(), k[1].casefold()))
        self.selected_sample_keys.intersection_update(keys)
        self.selected_sample_keys.update(keys - old)
        self.visible_sample_keys = []
        self.listbox.delete(0, 'end')
        self.filter_samples()
        labels = [mod.sample_label(k) for k in self.sample_keys]
        for combo, var in ((self.zero_combo, self.zero), (self.full_combo, self.full)):
            combo.configure(values=labels)
            if var.get() not in labels:
                var.set('')
        self.schedule_draw()

    def remember_sample_selection(self):
        self.selected_sample_keys.difference_update(self.visible_sample_keys)
        self.selected_sample_keys.update(self.visible_sample_keys[i] for i in self.listbox.curselection())

    def sample_selection_changed(self, event=None):
        self.remember_sample_selection()
        self.schedule_draw()

    def filter_samples(self):
        self.remember_sample_selection()
        terms = self.sample_search.get().casefold().split()
        self.visible_sample_keys = [key for key in self.sample_keys
                                    if all(term in mod.sample_label(key).casefold() for term in terms)]
        self.listbox.delete(0, 'end')
        for index, key in enumerate(self.visible_sample_keys):
            self.listbox.insert('end', mod.sample_label(key))
            if key in self.selected_sample_keys:
                self.listbox.selection_set(index)

    def select_search_results(self):
        self.selected_sample_keys = set(self.visible_sample_keys)
        self.listbox.selection_set(0, 'end')
        self.schedule_draw()

    def clear_sample_selection(self):
        self.selected_sample_keys.clear()
        self.listbox.selection_clear(0, 'end')
        self.schedule_draw()

    def update_controls(self):
        method = mod.METHODS[self.method.get()]
        self.zero_combo.configure(state='readonly' if method in ('zero', 'both') else 'disabled')
        self.full_combo.configure(state='readonly' if method == 'both' else 'disabled')
        self.schedule_draw()

    def select_targets(self, select):
        if select:
            self.target_listbox.selection_set(0, 'end')
        else:
            self.target_listbox.selection_clear(0, 'end')
        self.schedule_draw()

    def recount(self):
        self._dirty = True
        self.schedule_draw()

    def control_key(self, var):
        label = var.get()
        return next((k for k in self.sample_keys if mod.sample_label(k) == label), None)

    # ---------- 計算・描画 ----------
    def schedule_draw(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(250, self.draw)

    def draw(self):
        self._job = None
        if self._dirty:
            try:
                min_q = max(float(self.min_q.get() or 0), 0.0)
            except (ValueError, tk.TclError):
                min_q = 0.0
            self.configure(cursor='watch')
            self.update_idletasks()
            try:
                self.sites = mod.collect_sites(self.datasets(), min_q=min_q)
            finally:
                self.configure(cursor='')
            self._dirty = False
        method = mod.METHODS[self.method.get()]
        self.remember_sample_selection()
        chosen = self.selected_sample_keys
        table = mod.rate_table(self.sites, method, zero=self.control_key(self.zero),
                               full=self.control_key(self.full), ci=mod.CI_METHODS[self.ci.get()])
        targets = {self.target_sequences[i] for i in self.target_listbox.curselection()}
        keep = [(e, s) in chosen and sequence in targets
                for e, s, sequence in zip(table['experiment'], table['sample'], table['sequence'])]
        self.table = table[keep].reset_index(drop=True)
        self.fill_tree()
        self.render()

    def fill_tree(self):
        previous = self.tree.selection()
        self.tree.delete(*self.tree.get_children())

        def pct(x):
            return '—' if math.isnan(x) else f'{x * 100:.1f}%'
        for index, row in self.table.iterrows():
            ci = '—' if math.isnan(row['low']) else f'{row["low"] * 100:.1f} 〜 {row["high"] * 100:.1f}%'
            values = (f'{row["experiment"]} / {row["sample"]}', row['sequence'], row['site'], f'{row["n_mod"]:,}',
                      f'{row["n_unmod"]:,}', row['chunks'], pct(row['raw_rate']), pct(row['rate']), ci, row['note'])
            self.tree.insert('', 'end', iid=str(index), values=values, tags=('warn',) if row['note'] else ())
        if previous and self.tree.exists(previous[0]):
            self.tree.selection_set(previous[0])

    def selected_site(self):
        selection = self.tree.selection()
        if not selection or self.table is None:
            return None
        row = self.table.iloc[int(selection[0])]
        return next((s for s in self.sites if (s.experiment, s.sample, s.target) ==
                     (row['experiment'], row['sample'], row['target'])), None)

    def render(self):
        if self.table is None or self.table.empty:
            self.figure.clear()
            self.figure.text(0.5, 0.5, '選択条件に一致する結果がありません。\nサンプル・ターゲットの選択と読み込み済み配列を確認してください。',
                             ha='center', va='center', fontsize=11, color='#555555')
        else:
            mod.plot_rates(self.table, self.figure, mod.METHODS[self.method.get()], detail=self.selected_site())
        self.canvas.draw_idle()

    def save_csv(self):
        if self.table is None or self.table.empty:
            messagebox.showinfo('保存', '保存する結果がありません。', parent=self)
            return
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        method = mod.METHODS[self.method.get()]
        name = f'{datetime.now():%Y%m%d%H%M}_modification_{method}.csv'
        out = self.table.assign(method=method, ci=mod.CI_METHODS[self.ci.get()],
                                zero_control=self.zero.get() if method != 'raw' else '',
                                full_control=self.full.get() if method == 'both' else '',
                                min_q=self.min_q.get())
        out.to_csv(OUTPUT_DIR / name, index=False, encoding='utf-8-sig')
        if messagebox.askyesno('保存しました', f'{name}\n\n保存先フォルダを開きますか？', parent=self):
            os.startfile(OUTPUT_DIR)


class App:
    def __init__(self, root):
        self.root = root
        # 読み込み済みデータ: ident -> LoadResult（グラフ用タブはここから読む）
        self.datasets: dict[str, src.LoadResult] = {}
        ui_theme.header(root, 'BNALプロット', 'BNAL@ の TDMS を読み込んで MS_IDX ごとの分布を可視化',
                        '', ACCENT, on_close=self.close).pack(fill='x')
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True)
        self.load_panel = LoadPanel(self.notebook, self)
        self.plot_panel = PlotPanel(self.notebook, self)
        self.assembly_panel = AssemblyPanel(self.notebook, self)
        self.modification_panel = ModificationPanel(self.notebook, self)
        self.notebook.add(self.load_panel, text='読み込み')
        self.notebook.add(self.plot_panel, text='グラフ')
        self.notebook.add(self.assembly_panel, text='アセンブリ')
        self.notebook.add(self.modification_panel, text='修飾率')
        root.protocol('WM_DELETE_WINDOW', self.close)

    @staticmethod
    def ident(result: src.LoadResult):
        return '\x1e'.join((result.occurrence.group, *result.entry.key))

    def add_dataset(self, result: src.LoadResult):
        ident = self.ident(result)
        self.datasets[ident] = result
        self.load_panel.show_dataset(ident, result)
        self.plot_panel.refresh_list()
        self.assembly_panel.refresh_list()
        self.modification_panel.refresh_list()

    def remove_dataset(self, ident):
        self.datasets.pop(ident, None)
        if self.load_panel.loaded.exists(ident):
            self.load_panel.loaded.delete(ident)
        self.plot_panel.refresh_list()
        self.assembly_panel.refresh_list()
        self.modification_panel.refresh_list()

    def close(self):
        if self.load_panel.worker is not None:
            if not messagebox.askyesno('終了', '読み込み中です。中止して終了しますか？', parent=self.root):
                return
            self.load_panel.cancel_flag.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    root.title('BNALプロット')
    root.geometry('1400x900')
    root.minsize(900, 600)
    ui_theme.apply(root, ACCENT)
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
