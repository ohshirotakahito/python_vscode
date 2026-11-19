"""「実験データ」タブ: 上部のチップの FC・MA・TA・CAL・AC（と測定条件 GPSET）が SQserver のどこにあるか（フォルダ / zip）を出し、開く・取り出す。"""
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from chipdb import experiment_data as ed
from chipdb.log_chart import CURRENT, TIME, LogChart
from shared import ui_theme as theme

COLUMNS = ('実験ID', '日付', '装置', '操作', '場所', '形式', 'パス')
WIDTHS = {'実験ID': 330, '日付': 120, '装置': 60, '操作': 260, '場所': 100, '形式': 170, 'パス': 520}


def open_in_explorer(path, select=False):
    path = str(path)
    if select:
        subprocess.Popen(['explorer', '/select,', path])
    else:
        os.startfile(path)


class ExperimentView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.experiments, self.found = [], {}
        self.messages, self.generation = queue.Queue(), 0
        self.pending = None            # 表示されたら探すチップ
        bar = ttk.Frame(self)
        bar.pack(fill='x', pady=(0, 6))
        ttk.Button(bar, text='実験フォルダ / zip を開く', command=self.open_selected, style='Accent.TButton').pack(side='left')
        ttk.Button(bar, text='パスをコピー', command=self.copy_path).pack(side='left', padx=6)
        ttk.Button(bar, text='選んだファイルを保存', command=self.save_selected, style='Accent.TButton').pack(side='left', padx=(12, 0))
        ttk.Button(bar, text='保存先を開く', command=self.open_download_root).pack(side='left', padx=6)
        ttk.Button(bar, text='保存先を変える…', command=self.choose_download_root).pack(side='left')
        self.destination = ttk.Label(bar, foreground=theme.MUTED)
        self.destination.pack(side='left', padx=8)
        self.show_destination()
        ttk.Button(bar, text='探し直す', command=lambda: self.load(self.app.selected_path)).pack(side='right')
        self.state = ttk.Label(self, foreground=theme.MUTED, wraplength=1300)
        self.state.pack(fill='x', pady=(0, 6))
        split = ttk.Panedwindow(self, orient='vertical')
        split.pack(fill='both', expand=True)
        top, bottom = ttk.Frame(split), ttk.Frame(split)
        split.add(top, weight=2)
        split.add(bottom, weight=3)
        self.table = self._table(top, COLUMNS, 'extended', height=6)
        for column in COLUMNS:
            self.table.column(column, width=WIDTHS[column], stretch=column == 'パス')
        self.table.tag_configure('missing', foreground=theme.MUTED)
        self.table.bind('<<TreeviewSelect>>', self.show_files)
        self.table.bind('<Double-1>', lambda e: self.open_selected())
        ttk.Label(bottom, text='選んだ実験の測定条件（GPSET）と、このチップの操作のファイル。行を選ぶと PM_ 50 msログをグラフに、R_ の中身を右に表示。'
                  'Ctrl / Shift で複数選択、Ctrl+A で全部 →「選んだファイルを保存」。実験を複数選んで保存するとそれぞれ全部。'
                  'ダブルクリックでファイルを開く', foreground=theme.MUTED).pack(anchor='w', pady=(6, 2))
        lower = ttk.Panedwindow(bottom, orient='horizontal')
        lower.pack(fill='both', expand=True)
        files_pane, chart_pane, result_pane = (ttk.Frame(lower, width=340, height=380), ttk.Frame(lower, width=700),
                                               ttk.Frame(lower, width=300))
        for pane in (files_pane, chart_pane, result_pane):    # 表の列幅でペインが広がってグラフが潰れないように
            pane.pack_propagate(False)
        lower.add(files_pane, weight=0)
        lower.add(chart_pane, weight=1)
        lower.add(result_pane, weight=0)
        self.files = self._table(files_pane, ('操作', 'ファイル', '場所'), 'extended')
        self.files.column('操作', width=70, stretch=False)
        self.files.column('ファイル', width=280, stretch=False)
        self.files.column('場所', width=700)
        self.files.bind('<Double-1>', self.open_file)
        self.files.bind('<<TreeviewSelect>>', self.plot_selected)
        self.files.bind('<Control-a>', lambda e: (self.files.selection_set(self.files.get_children()), 'break')[1])
        controls = ttk.Frame(chart_pane)
        controls.pack(fill='x')
        ttk.Label(controls, text='下段の列').pack(side='left', padx=(6, 4))
        self.lower_column = tk.StringVar(value='Piezo (V)')
        self.lower_box = ttk.Combobox(controls, textvariable=self.lower_column, state='readonly', width=22)
        self.lower_box.pack(side='left')
        self.lower_box.bind('<<ComboboxSelected>>', lambda e: self.redraw())
        self.chart_state = ttk.Label(controls, foreground=theme.MUTED)
        self.chart_state.pack(side='left', padx=8)
        self.chart = LogChart(chart_pane, width=700, height=360)
        self.chart.pack(fill='both', expand=True)
        self.chart.message('下のファイル一覧から操作を選ぶと、PM_ ログ（電流と下段の列）を表示します。')
        self.result = self._table(result_pane, ('項目', '値'), 'browse')
        self.result.column('項目', width=190, stretch=False)
        self.result.column('値', width=110)
        self.logs, self.plot_generation = {}, 0           # (実験, ファイル名) → 読んだ列（同じ操作を選び直しても読み直さない）
        self.bind('<Map>', lambda e: self.load_pending())
        self.after(100, self.poll)

    @staticmethod
    def _table(parent, columns, mode, height=10):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        table = ttk.Treeview(frame, columns=columns, show='headings', selectmode=mode, height=height)
        for column in columns:
            table.heading(column, text=column)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=table.xview)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return table

    # --- 探す（SQserver への問い合わせは作業スレッドで。タブが表示されているときだけ） ---
    def chip_changed(self, path):
        self.pending = path
        if self.winfo_ismapped():
            self.load_pending()

    def load_pending(self):
        if self.pending is not None or not self.experiments:
            path, self.pending = self.pending or self.app.selected_path, None
            self.load(path)

    def load(self, path):
        self.generation += 1
        generation = self.generation
        self.table.delete(*self.table.get_children())
        self.files.delete(*self.files.get_children())
        document = self.app.document
        if path is None or document is None or Path(document.path) != Path(path):
            self.experiments, self.found = [], {}
            self.state.configure(text='履歴のあるチップを選ぶと、その実験データの場所を表示します。')
            return
        self.experiments, self.found = ed.chip_experiments(document.rows), {}
        self.chip = Path(path).stem                     # 保存先は <保存先>/<ChipID>/<実験ID>/
        for index, item in enumerate(self.experiments):
            self.table.insert('', 'end', iid=str(index), values=(item.name, item.date, item.machine or '?',
                              ed.operation_text(item.operations), '探しています…', '', ''))
        if not self.experiments:
            self.state.configure(text=f'{Path(path).stem}: FC・MA・TA・CAL の操作記録がありません。')
            return
        self.state.configure(text=f'{Path(path).stem}: {len(self.experiments)} 実験の場所を SQserver で探しています…')
        experiments = list(self.experiments)

        def task():
            from scoring import machine_data
            try:
                machines = machine_data.machines()
                inventories = {m: machine_data.inventory(m) for m in {e.machine for e in experiments if e.machine}}
            except Exception as error:                 # machinedb が読めない
                self.messages.put((generation, 'error', str(error)))
                return
            for index, item in enumerate(experiments):
                if generation != self.generation:
                    return
                try:
                    found = ed.locate(item, machines, inventories)
                except OSError:
                    found = None
                hint = [] if found else ed.renamed_candidates(item, inventories)
                self.messages.put((generation, 'found', (index, found, hint)))
            self.messages.put((generation, 'done', None))
        threading.Thread(target=task, daemon=True).start()

    def poll(self):
        try:
            while True:
                generation, kind, payload = self.messages.get_nowait()
                if kind == 'plot':
                    if generation == self.plot_generation:
                        self.show_plot(*payload)
                    continue
                if generation != self.generation:
                    continue
                if kind == 'error':
                    self.state.configure(text=f'装置一覧（machinedb）を読めません: {payload}')
                elif kind == 'found':
                    self.show_found(*payload)
                else:
                    self.show_summary()
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_found(self, index, found, hint):
        self.found[index] = found
        key = str(index)
        if found is None:
            place = '名前違いの候補あり' if hint else '見つからない'
            self.table.item(key, tags=('missing',))
            self.table.set(key, '場所', place)
            self.table.set(key, 'パス', ' / '.join(hint))
        else:
            self.table.set(key, '場所', found.place)
            self.table.set(key, '形式', found.kind)
            self.table.set(key, 'パス', str(found.path))
        if self.table.selection() == (key,):
            self.show_files()

    def show_summary(self):
        found = [f for f in self.found.values() if f]
        offline = sum(1 for f in found if not f.confirmed)
        text = (f'{len(self.experiments)} 実験中 {len(found)} 件の場所が分かりました'
                f'（zip {sum(1 for f in found if f.zipped)} · フォルダ {sum(1 for f in found if f.zipped is False)}）。')
        if offline:
            text += f' {offline} 件は SQserver に届かないため、OUNmain03 の一覧（database/Upload）での場所です。'
        if len(found) < len(self.experiments):
            text += f' 見つからない {len(self.experiments) - len(found)} 件は SQserver にアップロードされていない可能性があります。'
        self.state.configure(text=text)

    # --- 選択した実験 ---
    def selected(self):
        return [(self.experiments[int(k)], self.found.get(int(k))) for k in self.table.selection()]

    def show_files(self, _event=None):
        self.files.delete(*self.files.get_children())
        self.result.delete(*self.result.get_children())
        self.plot_generation += 1
        self.chart_state.configure(text='')
        self.chart.message('下のファイル一覧から操作を選ぶと、PM_ ログ（電流と下段の列）を表示します。')
        chosen = self.selected()
        if len(chosen) != 1:
            return
        item, found = chosen[0]
        if found is None or not found.confirmed:
            return
        try:
            for index, row in enumerate(ed.stage_files(found, item.name, item.operations)):
                self.files.insert('', 'end', iid=str(index), values=row)
        except (OSError, ValueError) as error:
            self.files.insert('', 'end', values=('', f'読めません: {error}', ''))
            return
        if self.files.get_children():
            self.files.selection_set(self.files.get_children()[0])      # 最初の操作をすぐグラフに

    # --- PM_ ログのグラフと R_ の結果 ---
    def plot_selected(self, _event=None):
        rows = self.files.selection()
        chosen = self.selected()
        if not rows or len(chosen) != 1 or chosen[0][1] is None:
            return
        focus = self.files.focus()
        row = focus if focus in rows else rows[0]
        item, found = chosen[0]
        operation, picked = self.files.set(row, '操作'), self.files.set(row, 'ファイル')
        # 同じ操作をやり直したファイル（AC#1 が日時違いで複数など）があるので、選んだ行と同じ日時の PM_ と R_ を組にする
        run = ed.run_of(picked)
        names = [self.files.set(k, 'ファイル') for k in self.files.get_children() if self.files.set(k, '操作') == operation]
        names = [n for n in names if ed.run_of(n) == run] or names
        stage = operation.split('#')[0]
        log_name = next((n for n in names if n.startswith('PM_')), None)
        result_name = picked if picked.startswith('R_') else next((n for n in names if n.startswith('R_')), None)
        self.plot_generation += 1
        generation = self.plot_generation
        self.chart_state.configure(text=f'{operation} を読み込み中…')
        cache = self.logs

        def task():
            columns, fields, error = {}, {}, ''
            try:
                if log_name:
                    key = (item.name, log_name)
                    columns = cache.get(key) or ed.read_log(found, item.name, stage, log_name)
                    if len(cache) > 40:                     # 13 000 行級のログもあるので覚えるのは最近の分だけ
                        cache.clear()
                    cache[key] = columns
                if result_name:
                    fields = ed.read_result(found, item.name, stage, result_name)
            except (OSError, ValueError, KeyError, StopIteration) as exc:
                error = str(exc)
            self.messages.put((generation, 'plot', (item.name, operation, log_name, columns, fields, error)))
        threading.Thread(target=task, daemon=True).start()

    def show_plot(self, experiment, operation, log_name, columns, fields, error):
        self.result.delete(*self.result.get_children())
        for name, value in fields.items():
            self.result.insert('', 'end', values=(name, value))
        self.current_plot = (f'{operation}  ·  {experiment}', columns)
        choices = [c for c in columns if c not in (TIME, CURRENT)]
        self.lower_box.configure(values=choices)
        if choices and self.lower_column.get() not in choices:
            self.lower_column.set('Piezo (V)' if 'Piezo (V)' in choices else choices[0])
        if error:
            self.chart_state.configure(text=f'読めません: {error}')
        elif not log_name:
            self.chart_state.configure(text=f'{operation}: PM_ ログがありません' + ('（測定条件は右の表）' if fields else ''))
        else:
            times = columns.get(TIME, [])
            span = f'{times[-1] - times[0]:.2f} s' if len(times) > 1 else '—'
            self.chart_state.configure(text=f'{log_name} · {len(times)} 点（{span}）· マウスを重ねると値を表示')
        self.redraw()

    def redraw(self):
        title, columns = getattr(self, 'current_plot', ('', {}))
        if columns:
            self.chart.show(title, columns, self.lower_column.get())
        else:
            self.chart.message('この操作の PM_ ログはありません。')

    def open_selected(self):
        for item, found in self.selected()[:3]:
            if found is None:
                continue
            try:
                if found.zipped:
                    open_in_explorer(found.path, select=True)
                elif found.zipped is None:
                    open_in_explorer(found.path.parent)
                else:
                    open_in_explorer(found.path)
            except OSError as error:
                messagebox.showerror('開く', str(error), parent=self)

    def copy_path(self):
        paths = [str(f.path) for _, f in self.selected() if f is not None]
        if paths:
            self.clipboard_clear()
            self.clipboard_append('\n'.join(paths))
            self.state.configure(text=f'パスを {len(paths)} 件コピーしました。')

    # --- 保存（この PC のフォルダへ。<保存先>/<実験ID>/ にファイルを並べる） ---
    def show_destination(self):
        self.destination.configure(text=f'保存先: {ed.download_root()}')

    def open_download_root(self):
        root = ed.download_root()
        root.mkdir(parents=True, exist_ok=True)
        open_in_explorer(root)

    def choose_download_root(self):
        folder = filedialog.askdirectory(parent=self, title='保存先（この PC のフォルダ）', initialdir=str(ed.download_root()),
                                         mustexist=False)
        if not folder:
            return
        try:
            ed.set_download_root(folder)
        except (OSError, ValueError) as error:
            messagebox.showerror('保存先', str(error), parent=self)
            return
        self.show_destination()

    def files_to_save(self, chosen):
        """[(実験, Found, [(stage, ファイル名)] か None=一覧の全部)]: 実験 1 件ならファイル一覧で選んだ行、複数なら全部。"""
        if len(chosen) == 1:
            rows = [self.files.item(k, 'values') for k in self.files.selection()]
            picked = [(op.split('#')[0], name) for op, name, where in rows if where]
            return [(chosen[0][0], chosen[0][1], picked or None)]
        return [(item, found, None) for item, found in chosen]

    def save_selected(self):
        chosen = [(i, f) for i, f in self.selected() if f is not None and f.confirmed]
        if not chosen:
            messagebox.showinfo('保存', 'SQserver で場所が確認できた実験を選んでください。', parent=self)
            return
        jobs, root = self.files_to_save(chosen), ed.download_root() / self.chip
        self.state.configure(text=f'{len(jobs)} 実験のファイルを {root} に保存しています…')

        def task():
            done, count, errors = [], 0, []
            for item, found, files in jobs:
                try:
                    if files is None:
                        files = [(op.split('#')[0], name) for op, name, where in ed.stage_files(found, item.name, item.operations)
                                 if where]
                    target, copied = ed.save_files(found, item.name, files, root)
                    done.append(target)
                    count += len(files)
                except (OSError, ValueError, KeyError, StopIteration) as error:
                    errors.append(f'{item.name}: {error}')
            self.after(0, lambda: self.saved(root, done, count, errors))
        threading.Thread(target=task, daemon=True).start()

    def saved(self, root, done, count, errors):
        text = f'{len(done)} 実験・{count} ファイルを保存しました: {root}'
        if errors:
            text += ' | 失敗: ' + ' / '.join(errors)[:300]
        self.state.configure(text=text)
        target = done[0] if len(done) == 1 else root if done else None
        if target is not None and Path(target).exists():
            open_in_explorer(target)

    def extract_selected(self, then_open, stages):
        """zip の中のファイルを開くために、その段のフォルダを作業場所（LOCALAPPDATA）に取り出してから開く。"""
        chosen = [(i, f) for i, f in self.selected() if f is not None and f.confirmed][:1]
        if not chosen:
            return
        self.state.configure(text=f'{Path(then_open).name} を取り出して開いています…')

        def task():
            done, errors = [], []
            for item, found in chosen:
                try:
                    done.append(ed.extract(found, item.name, stages=stages))
                except (OSError, ValueError, KeyError) as error:
                    errors.append(f'{item.name}: {error}')
            self.after(0, lambda: self.extracted(done, errors, then_open))
        threading.Thread(target=task, daemon=True).start()

    def extracted(self, done, errors, then_open):
        self.state.configure(text=('開きました: ' + str(then_open)) if not errors else '開けません: ' + ' / '.join(errors)[:300])
        if Path(then_open).exists():
            os.startfile(str(then_open))

    def open_file(self, event):
        row = self.files.identify_row(event.y)
        chosen = self.selected()
        if not row or len(chosen) != 1 or chosen[0][1] is None:
            return
        item, found = chosen[0]
        operation, name, _ = self.files.item(row, 'values')
        stage = operation.split('#')[0]
        if not found.zipped:
            target = found.path / stage / name
            if target.is_file():
                os.startfile(str(target))
            return
        self.extract_selected(then_open=ed.extract_root() / item.name / stage / name, stages=(stage,))
