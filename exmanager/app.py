"""Run with: python exmanager/app.py"""
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import core
import pc_config
from csv_editor import CsvEditor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ui_theme  # noqa: E402


def experiment_data_counts(folder):
    """Count only TDMS files directly inside each acquisition folder."""
    counts = []
    for relative in ('EXSV/T', 'EXZSV'):
        try:
            count = sum(p.is_file() and p.suffix.lower() == '.tdms'
                        for p in (folder / relative).iterdir())
        except FileNotFoundError:
            count = 0
        except OSError:
            count = '取得不可'
        counts.append(count)
    return tuple(counts)


def history_summary(records):
    # Legacy history has no server path: count recorded experiment/sample/type.
    destinations = {(r[7], r[8], r[9], r[13]) for r in records}
    latest = max((r[0] for r in records), default='—')
    return (f'{len(records)}件' if records else 'なし', f'{len(destinations)}か所', latest)


def history_detail(row):
    kind = 'Sample' if row[9].endswith('_Sample') else 'Blank' if row[9].endswith('_Blank') else row[9]
    return (row[0], row[4], row[7], row[8], kind, row[1], row[5])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.configure_colors()
        self.title('実験データ整理 — Python版')
        self.geometry(f'{min(1180, self.winfo_screenwidth() - 80)}x{min(960, self.winfo_screenheight() - 100)}')
        self.events = queue.Queue()
        self.busy = False
        self.current = None
        self.sets = []
        self.vars = {}
        self.boxes = {}
        self.pc = pc_config.load()
        self.restore_initial = True
        ui_theme.header(self, '実験データ整理', '元実験の転送セット作成・照合・コピーと履歴管理',
                        '\uE8AB', ui_theme.EXMANAGER, on_close=self.close).pack(fill='x')
        self.main_split = tk.PanedWindow(self, orient='vertical', sashwidth=6, sashrelief='flat',
                                         background=ui_theme.BORDER, borderwidth=0, opaqueresize=True)
        self.main_split.pack(fill='both', expand=True)
        upper = ttk.Frame(self.main_split)
        lower = ttk.Frame(self.main_split)
        self.main_split.add(upper, minsize=280, stretch='always')
        self.main_split.add(lower, minsize=200, stretch='never')
        pc_panel = ttk.Frame(upper, padding=(10, 5))
        pc_panel.pack(fill='x')
        self.pc_label = ttk.Label(pc_panel)
        self.pc_label.pack(side='left')
        ttk.Button(pc_panel, text='このPCの設定', command=self.configure_pc).pack(side='right')
        select = ttk.LabelFrame(upper, text='1. コピー元の設定・確認', padding=8)
        select.pack(fill='x', padx=10)
        for n, (key, label) in enumerate([('machine','装置'), ('selection','コピー元'), ('uploader','アップロード担当者')]):
            ttk.Label(select, text=label).grid(row=(n//4)*2, column=n%4, sticky='w')
            self.vars[key] = tk.StringVar()
            box = ttk.Combobox(select, textvariable=self.vars[key], state='readonly', width=27)
            box.grid(row=(n//4)*2+1, column=n%4, padx=4, pady=4)
            self.boxes[key] = box
        self.boxes['selection']['values'] = ('Recent_Data', 'Archived_Data')
        self.vars['selection'].set('Recent_Data')
        ttk.Button(select, text='① コピー元一覧を更新', style='Primary.TButton', command=self.sources).grid(row=1, column=3, sticky='w', padx=(12, 4))
        ttk.Button(select, text='装置・担当者CSVを再読み込み', style='Conditional.TButton', command=self.load).grid(row=1, column=4, sticky='w', padx=4)
        ttk.Button(select, text='保存履歴を開く', command=self.open_history_window).grid(row=1, column=5, sticky='w', padx=4)
        ttk.Button(select, text='装置CSVを閲覧・編集', command=lambda: self.edit_csv('machine')).grid(row=1, column=6, sticky='w', padx=4)
        ttk.Button(select, text='担当者CSVを閲覧・編集', command=lambda: self.edit_csv('operator')).grid(row=1, column=7, sticky='w', padx=4)
        self.source_label = ttk.Label(upper, text='黄色の行：履歴あり ／ 無地の行：履歴なし（履歴だけではスキップしません）')
        self.source_label.pack(anchor='w', padx=10)
        tree_frame = ttk.Frame(upper)
        tree_frame.pack(fill='x', padx=10)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=('name','10khz','100khz','history','destinations','latest'), show='headings', height=10, selectmode='extended')
        self.tree.heading('10khz', text='10kHz件数')
        self.tree.heading('100khz', text='100kHz件数')
        self.tree.column('10khz', width=90, stretch=False, anchor='e')
        self.tree.column('100khz', width=90, stretch=False, anchor='e')
        self.tree.heading('name', text='実験フォルダ（Ctrl / Shiftで複数選択）')
        self.tree.heading('history', text='履歴件数')
        self.tree.heading('destinations', text='保存先数')
        self.tree.heading('latest', text='最終保存日時')
        self.tree.column('name', width=390)
        self.tree.column('history', width=90, stretch=False)
        self.tree.column('destinations', width=90, stretch=False)
        self.tree.column('latest', width=180, stretch=False)
        self.tree.tag_configure('yes', background=ui_theme.row('warning', 0.2), foreground=ui_theme.TEXT)
        self.tree.tag_configure('no', background=ui_theme.FIELD, foreground=ui_theme.TEXT)
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(tree_frame, self.tree)
        self.source_histories = {}
        self.tree.bind('<<TreeviewSelect>>', self.show_history)
        self.tree.bind('<ButtonRelease-1>', self.show_history)
        self.tree.bind('<KeyRelease>', self.show_history)
        self.history_window = tk.Toplevel(self)
        self.history_window.withdraw()
        self.history_window.title('保存履歴')
        self.history_window.geometry('1100x400')
        self.history_window.protocol('WM_DELETE_WINDOW', self.history_window.withdraw)
        self.history_label = ttk.Label(self.history_window, text='実験を選択すると保存履歴を表示します。')
        self.history_label.pack(anchor='w', padx=10, pady=8)
        history_frame = ttk.Frame(self.history_window)
        history_frame.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        history_frame.rowconfigure(0, weight=1)
        history_frame.columnconfigure(0, weight=1)
        self.history_tree = ttk.Treeview(history_frame, columns=('date','experiment_at','experiment','sample','kind','operator','uploader'), show='headings', height=12)
        for key, title, width in [('date','アップロード日時',160), ('experiment_at','実験日時',160), ('experiment','保存先実験',180), ('sample','サンプル',140), ('kind','区分',80), ('operator','実験担当者',130), ('uploader','アップロード担当者',150)]:
            self.history_tree.heading(key, text=title)
            self.history_tree.column(key, width=width)
        self.history_tree.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(history_frame, self.history_tree)
        panel = ttk.LabelFrame(upper, text='2. 保存先の選択', padding=8)
        panel.pack(fill='x')
        fields = [('db', '装置・担当者CSVフォルダ', str(core.DB)), ('legacy', 'LabVIEW版履歴CSV', str(core.DB / 'Uploader.csv')), ('local', 'Python版履歴CSV', str(core.BASE / 'logs' / 'Uploader_python.csv')), ('root', '保存先解析ルート', r'\\Rackstation\analysis')]
        self.vars['db'] = tk.StringVar(value=str(core.DB))
        fields = [field for field in fields if field[0] != 'db']
        for n, (key, label, default) in enumerate(fields):
            self.vars[key] = tk.StringVar(value=default)
            if key in ('legacy', 'local'):
                continue
            ttk.Label(panel, text=label).grid(row=0, column=0, sticky='w')
            if key == 'root':
                root_panel = ttk.Frame(panel)
                root_panel.grid(row=0, column=1, sticky='w')
                self.boxes['root'] = ttk.Combobox(root_panel, textvariable=self.vars[key], state='readonly', width=45)
                self.boxes['root'].grid(row=0, column=0, sticky='ew')
                self.boxes['root'].bind('<<ComboboxSelected>>', lambda event: self.load_destinations())
                ttk.Button(root_panel, text='解析ルートCSVを閲覧・編集', command=self.edit_roots).grid(row=0, column=1, padx=(8, 0))
            else:
                ttk.Entry(panel, textvariable=self.vars[key], width=95).grid(row=n, column=1, sticky='ew')
        panel.columnconfigure(1, weight=1)
        loaders = ttk.Frame(root_panel)
        loaders.grid(row=0, column=2, sticky='w')
        ttk.Button(loaders, text='保存先一覧を読み込み', style='Conditional.TButton', command=self.load_destinations).pack(side='left', padx=8)
        for label, key in [('LabVIEW版CSV', 'legacy'), ('Python版CSV', 'local')]:
            ttk.Button(loaders, text=label + 'を閲覧・編集', command=lambda k=key: self.edit_csv(k)).pack(side='left', padx=3)
        select = ttk.Frame(upper, padding=(10, 4))
        select.pack(fill='x')
        for n, (key, label) in enumerate([('experiment','保存先実験'), ('sample','保存先サンプル'), ('kind','区分')]):
            ttk.Label(select, text=label).grid(row=(n//4)*2, column=n%4, sticky='w')
            self.vars[key] = tk.StringVar()
            box = ttk.Combobox(select, textvariable=self.vars[key], state='readonly', width=27)
            box.grid(row=(n//4)*2+1, column=n%4, padx=4, pady=4)
            self.boxes[key] = box
        self.boxes['kind']['values'] = ('Sample', 'Blank')
        self.vars['kind'].set('Sample')
        self.boxes['experiment'].bind('<<ComboboxSelected>>', self.samples)
        ttk.Button(select, text='② 選択内容を転送セットに追加', style='Primary.TButton', command=self.add_set).grid(row=1, column=3, sticky='w', padx=(12, 4))
        ttk.Button(select, text='保存先実験・サンプルを追加', style='Conditional.TButton', command=self.add_destination).grid(row=1, column=4, sticky='w', padx=4)
        ttk.Label(upper, text='3. 登録内容の確認').pack(anchor='w', padx=10)
        batch_frame = ttk.Frame(upper)
        batch_frame.pack(fill='both', expand=True, padx=10)
        batch_frame.rowconfigure(0, weight=1)
        batch_frame.columnconfigure(0, weight=1)
        self.batch_tree = ttk.Treeview(batch_frame, columns=('set','source','target','source10','target10','source100','target100','duplicates','nonduplicates','person'), show='headings', height=6)
        for key, title, width in [('set','セット',55),('source','コピー元実験',250),('target','保存先・区分',300),('source10','元10kHz',90),('target10','先10kHz',90),('source100','元100kHz',90),('target100','先100kHz',90),('duplicates','内容重複確認',160),('nonduplicates','重複なし（TDMS）',180),('person','アップロード担当者',160)]:
            self.batch_tree.heading(key, text=title)
            self.batch_tree.column(key, width=width)
        self.batch_tree.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(batch_frame, self.batch_tree)
        actions = ttk.Frame(lower, padding=(10, 4))
        actions.pack(fill='x')
        ttk.Button(actions, text='選択セット削除', style='Conditional.TButton', command=self.remove_set).pack(side='left')
        ttk.Button(actions, text='簡易チェック', style='Conditional.TButton', command=lambda: self.check_set_duplicates(quick=True)).pack(side='left', padx=8)
        ttk.Button(actions, text='③ コピー予定作成', style='Primary.TButton', command=self.preview).pack(side='left', padx=4)
        ttk.Button(actions, text='④ コピー予定表を確認', style='Primary.TButton', command=self.open_plan_window).pack(side='left', padx=4)
        self.copy_button = ttk.Button(actions, text='⑤ コピー開始', style='Execute.TButton', command=self.copy)
        self.copy_button.pack(side='left')
        ttk.Label(lower, text='通常：① コピー元一覧を更新 → ② セットに追加 → ③ 予定作成 → ④ 予定表を確認 → ⑤ コピー開始', wraplength=1000).pack(anchor='w', padx=10)
        legend = ttk.Frame(lower)
        legend.pack(fill='x', padx=10)
        for text, color in [('青：通常の手順', ui_theme.row('info', 0.22)), ('緑：実行', ui_theme.row('success', 0.22)),
                            ('橙：必要に応じて操作', ui_theme.row('warning', 0.22)), ('灰：閲覧・編集・設定', '#1d2730')]:
            tk.Label(legend, text=text, background=color, foreground=ui_theme.TEXT, padx=6).pack(side='left', padx=(0, 6))
        self.update_pc_display()
        progress_frame = ttk.Frame(lower, padding=(10, 0))
        progress_frame.pack(fill='x')
        self.progress_text = tk.StringVar(value='待機中')
        ttk.Label(progress_frame, textvariable=self.progress_text).pack(anchor='w')
        self.progress_bar = ttk.Progressbar(progress_frame, maximum=100)
        self.progress_bar.pack(fill='x')
        self.file_text = tk.StringVar()
        ttk.Entry(progress_frame, textvariable=self.file_text, state='readonly').pack(fill='x')
        self.plan_label = ttk.Label(lower, text='コピー予定：未作成（詳細は「コピー予定表を開く」で確認）')
        self.plan_label.pack(anchor='w', padx=10)
        # Keep the row model unmounted; details are shown only in the popup.
        plan_frame = ttk.Frame(lower)
        plan_frame.rowconfigure(0, weight=1)
        plan_frame.columnconfigure(0, weight=1)
        self.plan_tree = ttk.Treeview(plan_frame, columns=('set','action','rate','source','destination'), show='headings', height=6)
        for key, title, width in [('set','セット',55), ('action','コピー予定',180), ('rate','区分',90), ('source','保存元ファイル',450), ('destination','保存先ファイル',450)]:
            self.plan_tree.heading(key, text=title)
            self.plan_tree.column(key, width=width)
        for tag, kind in [('new', 'success'), ('skip', 'info'), ('conflict', 'danger'), ('pending', 'warning')]:
            self.plan_tree.tag_configure(tag, background=ui_theme.row(kind), foreground=ui_theme.TEXT)
        self.plan_tree.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(plan_frame, self.plan_tree)
        output_frame = ttk.Frame(lower)
        output_frame.pack(fill='both', expand=True, padx=10, pady=(4, 5))
        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)
        self.output = tk.Text(output_frame, height=4, wrap='none')
        ui_theme.style_text(self.output)
        self.output.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(output_frame, self.output)
        for key, value in self.pc.get('last_selection', {}).items():
            if key in self.vars and isinstance(value, str):
                self.vars[key].set(value)
        for key, default in [('selection', 'Recent_Data'), ('kind', 'Sample')]:
            if self.vars[key].get() not in self.boxes[key]['values']:
                self.vars[key].set(default)
        self.root_csv = pc_config.DEFAULT_DIR / 'AnalysisRoots.csv'
        self.load_roots()
        self.after(100, self.poll)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(200, self.load)

    def configure_colors(self):
        """Apply the shared dark theme used by the project launcher."""
        ui_theme.apply(self, ui_theme.EXMANAGER)

    def load_roots(self):
        try:
            if not self.root_csv.exists():
                core.write_csv(self.root_csv, ['解析ルート'],
                               [[path] for path in dict.fromkeys([r'\\Rackstation\analysis', self.vars['root'].get()]) if path])
            rows = core.rows(self.root_csv)
            choices = list(dict.fromkeys(row[0].strip() for row in rows[1:] if row and row[0].strip()))
            self.set_choices('root', choices)
            if not self.vars['root'].get() and choices:
                self.vars['root'].set(choices[0])
        except Exception as exc:
            self.boxes['root']['values'] = ()
            self.vars['root'].set('')
            self.log(f'解析ルートCSV読込エラー: {exc}')

    def edit_roots(self):
        if self.busy:
            messagebox.showinfo('処理中', '処理終了後に解析ルートCSVを開いてください。', parent=self)
            return
        def saved():
            self.current = None
            self.load_roots()
            self.load_destinations()
        try:
            CsvEditor(self, self.root_csv, saved, column_titles=('解析ルート',), first_row_header=True)
        except Exception as exc:
            messagebox.showerror('CSVを開けません', str(exc), parent=self)

    def edit_csv(self, key):
        if self.busy:
            messagebox.showinfo('処理中', '処理終了後にCSVを開いてください。', parent=self)
            return
        path = (Path(self.vars['db'].get()) / ('Machine_N.csv' if key == 'machine' else 'Operator.csv')
                if key in ('machine', 'operator') else Path(self.vars[key].get()))
        def saved():
            self.current = None
            label = {'legacy': 'LabVIEW版CSV', 'local': 'Python版CSV', 'machine': '装置CSV', 'operator': '担当者CSV'}[key]
            self.log(f'{label}を保存しました（バックアップを作成しました）')
            if key in ('machine', 'operator'):
                self.load()
            else:
                self.source_histories = {}
                self.history_tree.delete(*self.history_tree.get_children())
                self.sources()
        try:
            CsvEditor(self, path, saved,
                      column_titles=(core.HEADERS if key in ('legacy', 'local') else
                                     ('Name', 'initial_name', 'affiliation') if key == 'operator' else ()),
                      first_row_header=key == 'machine',
                      history_source=self.vars['local'].get() if key == 'legacy' else None)
        except Exception as exc:
            messagebox.showerror('CSVを開けません', str(exc), parent=self)

    def close(self):
        if self.busy:
            messagebox.showinfo('処理中', '処理が終わってから閉じてください。')
        else:
            config = dict(self.pc, last_selection=self.values())
            try:
                pc_config.save(config)
            except Exception as exc:
                messagebox.showerror('終了設定の保存失敗', f'前回の選択を保存できませんでした。\n{exc}')
                return
            self.pc = config
            self.destroy()

    def update_pc_display(self):
        self.pc_label.configure(text=f"このPC：{self.pc['display_name']} ［{pc_config.ROLES[self.pc['role']]}］ ID: {self.pc['pc_id'][:8]} ／ 遠隔受付・常駐は未実装")
        self.update_copy_button()

    def update_copy_button(self):
        ready = self.pc['role'] == 'transfer' and bool(self.current) and not self.busy
        self.copy_button.configure(state='normal' if ready else 'disabled')

    def configure_pc(self):
        if self.busy:
            messagebox.showinfo('処理中', 'PC設定は処理終了後に変更してください。')
            return
        dialog = tk.Toplevel(self)
        dialog.title('このPCの設定（ローカル保存）')
        dialog.transient(self)
        dialog.grab_set()
        name = tk.StringVar(value=self.pc['display_name'])
        role = tk.StringVar(value=pc_config.ROLES[self.pc['role']])
        ttk.Label(dialog, text=f"固有ID：{self.pc['pc_id']}").pack(padx=12, pady=8)
        ttk.Label(dialog, text='表示するPC名').pack(anchor='w', padx=12)
        ttk.Entry(dialog, textvariable=name, width=55).pack(padx=12)
        ttk.Label(dialog, text='役割').pack(anchor='w', padx=12, pady=5)
        ttk.Combobox(dialog, values=list(pc_config.ROLES.values()), textvariable=role, state='readonly').pack(padx=12)
        ttk.Label(dialog, text='転送担当：このPCからコピー可能\n操作・閲覧：コピー実行不可（遠隔依頼は未実装）').pack(padx=12, pady=8)
        def commit():
            config = dict(self.pc, display_name=name.get().strip(), role=next(k for k,v in pc_config.ROLES.items() if v==role.get()))
            try:
                pc_config.save(config)
            except Exception as exc:
                messagebox.showerror('設定保存失敗', str(exc), parent=dialog)
                return
            self.pc = config
            self.current = None
            self.update_pc_display()
            dialog.destroy()
        ttk.Button(dialog, text='保存', command=commit).pack(pady=10)

    @staticmethod
    def add_scrollbars(frame, widget):
        vertical = ttk.Scrollbar(frame, orient='vertical', command=widget.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=widget.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

    def progress(self, phase, count, total, detail):
        self.events.put(('progress', phase, count, total, detail))

    def values(self):
        return {key: value.get() for key, value in self.vars.items()}

    def log(self, text):
        follow = self.output.yview()[1] >= 0.999
        self.output.insert('end', str(text) + '\n')
        if follow:
            self.output.yview_moveto(1.0)

    def run(self, work, done):
        if self.busy:
            return
        self.busy = True
        self.update_copy_button()
        self.progress_text.set('処理中（対象を読み込み中）')
        self.file_text.set('')
        self.progress_bar.configure(mode='indeterminate', value=0)
        self.progress_bar.start(50)
        def worker():
            try:
                self.events.put(('done', done, work()))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            for _ in range(100):
                event = self.events.get_nowait()
                if event[0] == 'log':
                    self.log(event[1])
                elif event[0] == 'progress':
                    _, phase, count, total, detail = event
                    self.progress_bar.stop()
                    self.progress_bar.configure(mode='determinate', value=100 * count / max(total, 1))
                    self.progress_text.set(f'{phase}: {count}/{total} 件完了 ({100 * count / max(total, 1):.1f}%)')
                    self.file_text.set(detail)
                elif event[0] == 'error':
                    self.busy = False
                    self.progress_bar.stop()
                    self.progress_text.set('エラーで停止')
                    self.current = None
                    self.log('エラー: ' + event[1])
                    messagebox.showerror('処理失敗', event[1])
                else:
                    self.busy = False
                    self.progress_bar.stop()
                    self.progress_bar.configure(mode='determinate', value=100)
                    self.progress_text.set('処理完了')
                    event[1](event[2])
        except queue.Empty:
            pass
        self.update_copy_button()
        self.after(100, self.poll)

    def load(self):
        if self.busy:
            self.log('処理中です。終了後にCSVを再読み込みしてください。')
            return
        v = self.values()
        def work():
            results = {}
            try:
                results['history_sync'] = core.sync_history(v['legacy'], v['local'])
            except Exception as exc:
                results['history_sync_error'] = str(exc)
            for name, reader in [('machines', lambda: core.machines(Path(v['db'])/'Machine_N.csv')), ('operators', lambda: core.rows(Path(v['db'])/'Operator.csv'))]:
                try:
                    results[name] = reader()
                except Exception as exc:
                    results[name + '_error'] = str(exc)
            return results
        def done(data):
            self.machine_data = data.get('machines', {})
            self.set_choices('machine', list(self.machine_data))
            operators = data.get('operators', [])
            self.people = {f'{r[0]} ({r[1]})':r[1] for r in operators if len(r)>=2 and r[0] and r[1]}
            self.set_choices('uploader', list(self.people))
            self.log(f'CSV読込: 装置 {len(self.machine_data)} 件 / 担当者 {len(self.people)} 件。保存先は「保存先一覧を読み込み」で取得してください。')
            if 'history_sync' in data:
                added, removed = data['history_sync']
                self.log(f'履歴CSV同期: LabVIEW版CSVから {added} 件追加 / Python版CSVの重複 {removed} 件整理。')
            for key in ('machines_error', 'operators_error', 'history_sync_error'):
                if key in data:
                    self.log(f'CSV読込エラー ({key}): {data[key]}')
            if self.restore_initial:
                self.restore_initial = False
                if self.vars['experiment'].get():
                    self.load_destinations(restore=True)
        self.run(work, done)

    def set_choices(self, key, choices):
        self.boxes[key]['values'] = choices
        if self.vars[key].get() not in choices:
            self.vars[key].set('')

    def load_destinations(self, restore=False):
        if self.busy:
            self.log('処理中です。終了後に保存先一覧を読み込んでください。')
            return
        self.current = None
        v = self.values()
        previous_experiment = v['experiment']
        previous_sample = v['sample']
        self.vars['experiment'].set('')
        self.vars['sample'].set('')
        self.boxes['experiment']['values'] = ()
        self.boxes['sample']['values'] = ()
        if not v['root']:
            self.log('解析ルートCSVに保存先を追加し、解析ルートを選択してください。')
            return
        def done(names):
            self.set_choices('experiment', names)
            if restore and previous_experiment in names:
                self.vars['experiment'].set(previous_experiment)
                self.samples(restore_sample=previous_sample)
        self.run(lambda: sorted(p.name for p in Path(v['root']).iterdir() if p.is_dir()), done)

    def add_destination(self):
        if self.busy:
            return
        dialog = tk.Toplevel(self)
        dialog.title('保存先実験・サンプルの追加')
        dialog.transient(self)
        dialog.grab_set()
        root = self.vars['root'].get()
        experiment = tk.StringVar(value=self.vars['experiment'].get())
        samples = tk.StringVar()
        ttk.Label(dialog, text=f'保存先: {root}').grid(row=0, column=0, columnspan=2, padx=12, pady=8, sticky='w')
        ttk.Label(dialog, text='保存先実験（新規入力／既存から選択）').grid(row=1, column=0, padx=12, sticky='w')
        ttk.Combobox(dialog, textvariable=experiment, values=self.boxes['experiment']['values'], width=40).grid(row=1, column=1, padx=12, pady=6)
        ttk.Label(dialog, text='サンプル名（カンマ区切り）').grid(row=2, column=0, padx=12, sticky='w')
        ttk.Entry(dialog, textvariable=samples, width=43).grid(row=2, column=1, padx=12, pady=6)
        ttk.Label(dialog, text='10k/100kのSample・Blank、raw data、T、stocked等を作成します。\n既存フォルダはそのまま利用します。サンプル名は必須です。').grid(row=3, column=0, columnspan=2, padx=12, pady=8, sticky='w')

        def create():
            if self.busy:
                return
            ex = experiment.get().strip()
            names = list(dict.fromkeys(n.strip() for n in samples.get().split(',') if n.strip()))
            def done(result):
                experiments, sample_names = result
                self.vars['root'].set(root)
                self.set_choices('experiment', experiments)
                self.vars['experiment'].set(ex)
                self.set_choices('sample', sample_names)
                self.vars['sample'].set(names[0])
                self.current = None
                self.log(f'保存先フォルダ作成完了: {Path(root) / ex} / {", ".join(names)}')
                if dialog.winfo_exists():
                    dialog.destroy()
            self.run(lambda: core.create_destinations(root, ex, names), done)

        ttk.Button(dialog, text='フォルダ作成', command=create).grid(row=4, column=0, padx=12, pady=12, sticky='w')

    def samples(self, event=None, restore_sample=''):
        if self.busy:
            return
        v = self.values()
        self.vars['sample'].set('')
        self.boxes['sample']['values'] = ()
        def done(names):
            self.set_choices('sample', names)
            if restore_sample in names:
                self.vars['sample'].set(restore_sample)
        self.run(lambda: sorted(p.name for p in (Path(v['root'])/v['experiment']).iterdir() if p.is_dir()), done)

    def sources(self):
        v = self.values()
        if not v['machine'] or not hasattr(self, 'machine_data'):
            return
        root = Path(self.machine_data[v['machine']]['Tmp_backup' if v['selection']=='Recent_Data' else 'SQ_backup'])
        def work():
            folders = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
            counts = {p: experiment_data_counts(p) for p in folders}
            return folders, core.history(v['legacy'], v['local']), counts
        def done(data):
            self.source_histories = {}
            self.tree.delete(*self.tree.get_children())
            self.history_tree.delete(*self.history_tree.get_children())
            self.history_label.configure(text='実験を選択すると保存履歴を表示します。')
            self.source_paths = {}
            self.source_context = (v['machine'], v['selection'])
            for n,p in enumerate(data[0]):
                records = [r for r in data[1] if r[2]==v['machine'] and r[6]==p.name]
                self.source_histories[str(n)] = records
                self.tree.insert('', 'end', iid=str(n), values=(p.name, *data[2][p], *history_summary(records)), tags=('yes' if records else 'no',))
                self.source_paths[str(n)] = p
            self.source_label.configure(text=f'{root} — 黄色の行：履歴あり ／ 無地の行：履歴なし')
        self.run(work, done)

    def open_history_window(self):
        self.show_history()
        self.history_window.deiconify()
        self.history_window.lift()

    def show_history(self, event=None):
        selected = self.tree.selection()
        active = self.tree.focus()
        # Mouse release identifies the actual clicked row even during range selection.
        if event is not None and getattr(event, 'num', None) == 1:
            clicked = self.tree.identify_row(event.y)
            if clicked in selected:
                active = clicked
        if active not in selected:
            active = selected[-1] if selected else ''
        self.history_tree.delete(*self.history_tree.get_children())
        if not active:
            self.history_label.configure(text='実験を選択すると保存履歴を表示します。')
            return
        name = self.tree.item(active, 'values')[0]
        records = self.source_histories.get(active, [])
        self.history_label.configure(text=f'{len(selected)}件選択中 ／ 履歴表示：{name}（{len(records)}件）')
        for row in sorted(records, key=lambda r: r[0], reverse=True):
            self.history_tree.insert('', 'end', values=history_detail(row))

    def add_set(self):
        if self.busy:
            return
        self.current = None
        v = self.values()
        selected = self.tree.selection()
        if not selected or not all(v[k] for k in ('experiment','sample','uploader')):
            messagebox.showinfo('選択不足', '実験・保存先・担当者を選択してください。')
            return
        if self.source_context != (v['machine'], v['selection']):
            messagebox.showinfo('一覧更新', 'コピー元一覧を更新してください。')
            return
        if Path(v['legacy']).resolve() == Path(v['local']).resolve():
            messagebox.showerror('設定', 'LabVIEW版CSVとPython版CSVは別のパスにしてください。')
            return
        dst = Path(v['root']) / v['experiment'] / v['sample']
        context = dict(uploader=self.people[v['uploader']], machine=v['machine'], selection=v['selection'], kind=v['kind'])
        if self.sets and (v['legacy'], v['local']) != (self.sets[0]['legacy'], self.sets[0]['local']):
            messagebox.showerror('履歴設定', '同じ一括処理では履歴CSVの設定を統一してください。')
            return
        self.sets.append(dict(sources=[self.source_paths[s] for s in selected], target=dst, context=context, legacy=v['legacy'], local=v['local']))
        self.refresh_sets()

    def close_plan_window(self):
        window = getattr(self, 'plan_window', None)
        if window is not None and window.winfo_exists():
            window.destroy()
        self.plan_window = None

    def open_plan_window(self):
        rows = [self.plan_tree.item(iid) for iid in self.plan_tree.get_children()]
        if not rows:
            messagebox.showinfo('コピー予定', '「コピー予定作成」で予定を作成してください。', parent=self)
            return
        window = getattr(self, 'plan_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_set()
            return
        window = self.plan_window = tk.Toplevel(self)
        window.title('コピー予定・重複スキップの確認')
        window.geometry(f'{min(1500, self.winfo_screenwidth() - 80)}x{min(850, self.winfo_screenheight() - 100)}')
        window.rowconfigure(2, weight=1)
        window.columnconfigure(0, weight=1)
        ttk.Label(window, text=self.plan_label.cget('text')).grid(row=0, column=0, sticky='w', padx=10, pady=8)
        toolbar = ttk.Frame(window)
        toolbar.grid(row=1, column=0, sticky='ew', padx=10, pady=(0, 8))
        ttk.Label(toolbar, text='表示：').pack(side='left')
        selected = tk.StringVar(value='すべて')
        choices = ttk.Combobox(toolbar, textvariable=selected, state='readonly',
                               values=('すべて', 'コピー予定', '重複スキップ予定', '競合（コピー不可）', 'コピー完了', '既存一致スキップ', '停止箇所', '未処理'), width=24)
        choices.pack(side='left')
        count_label = ttk.Label(toolbar)
        count_label.pack(side='left', padx=12)
        ttk.Button(toolbar, text='閉じる', command=self.close_plan_window).pack(side='right')
        frame = ttk.Frame(window)
        frame.grid(row=2, column=0, sticky='nsew', padx=10, pady=(0, 10))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        columns = tuple(self.plan_tree['columns'])
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=24)
        widths = {'set': 60, 'action': 84, 'rate': 64, 'source': 644, 'destination': 612}
        for column in columns:
            tree.heading(column, text=self.plan_tree.heading(column, 'text'))
            tree.column(column, width=widths[column], minwidth=widths[column], stretch=False)
        # Keep the reference layout when dragging column separators as well.
        tree.bind('<Button-1>', lambda event: 'break' if tree.identify_region(event.x, event.y) == 'separator' else None)
        for tag in ('new', 'skip', 'conflict', 'pending'):
            tree.tag_configure(tag, background=self.plan_tree.tag_configure(tag, 'background'), foreground=ui_theme.TEXT)
        tree.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(frame, tree)
        def render(event=None):
            tree.delete(*tree.get_children())
            visible = [row for row in rows if selected.get() == 'すべて' or row['values'][1] == selected.get()]
            for row in visible:
                tree.insert('', 'end', values=row['values'], tags=row['tags'])
            count_label.configure(text=f'{len(visible)}ファイル表示（TXTを含む）')
        choices.bind('<<ComboboxSelected>>', render)
        window.protocol('WM_DELETE_WINDOW', self.close_plan_window)
        render()

    def refresh_sets(self):
        self.close_plan_window()
        self.current = None
        self.plan_tree.delete(*self.plan_tree.get_children())
        self.plan_label.configure(text='コピー予定：登録内容が変更されました。再作成してください。')
        self.batch_tree.delete(*self.batch_tree.get_children())
        for n, spec in enumerate(self.sets):
            c = spec['context']
            self.batch_tree.insert('', 'end', iid=str(n), values=(n+1, ' / '.join(p.name for p in spec['sources']), f"{spec['target']} [{c['kind']}]", *('集計中',)*4, '未確認', '未確認', c['uploader']))
        if self.sets:
            specs = list(self.sets)
            def done(results):
                for n, (counts, _) in enumerate(results):
                    for key, count in zip(('source10','target10','source100','target100'), counts):
                        self.batch_tree.set(str(n), key, count)
            self.run(lambda: [core.inspect_set_data(spec) for spec in specs], done)

    def check_set_duplicates(self, quick=False):
        if self.busy or not self.sets:
            return
        specs = list(self.sets)
        for iid in self.batch_tree.get_children():
            self.batch_tree.set(iid, 'duplicates', '確認中')
            self.batch_tree.set(iid, 'nonduplicates', '確認中')
        self.log('簡易チェック：同じサイズのTDMSの先頭・中央・末尾を各64KiB比較します。部分一致のため、結果は重複候補です。' if quick else '保存元と保存先のTDMSをサイズ・SHA-256で照合します。大容量データでは時間がかかります。')
        def done(results):
            for n, (counts, duplicates) in enumerate(results):
                for key, count in zip(('source10','target10','source100','target100'), counts):
                    self.batch_tree.set(str(n), key, count)
                unique = {(rate, source) for rate, source, _ in duplicates}
                label = '重複候補' if quick else '重複'
                self.batch_tree.set(str(n), 'duplicates', f'{label} {len(unique)}件')
                remaining = counts[0] + counts[2] - len(unique)
                self.batch_tree.set(str(n), 'nonduplicates', f'{remaining}件（簡易）' if quick else f'{remaining}件')
                self.log(f'セット {n+1}: {label}の保存元TDMS {len(unique)}件')
                self.log(f'  重複なしTDMS: {remaining}件' + ('（簡易チェック結果）' if quick else ''))
                for rate, source, dest in duplicates:
                    self.log(f'  {rate}: {source} → {dest}')
        self.run(lambda: [core.inspect_set_data(spec, True, self.progress, quick=quick) for spec in specs], done)

    def remove_set(self):
        if self.busy:
            return
        for n in sorted((int(s) for s in self.batch_tree.selection()), reverse=True):
            del self.sets[n]
        self.refresh_sets()

    def preview(self):
        if self.busy or not self.sets:
            return
        self.close_plan_window()
        self.current = None
        specs = list(self.sets)
        self.output.delete('1.0','end')
        self.plan_tree.delete(*self.plan_tree.get_children())
        self.plan_label.configure(text='コピー予定を作成中…')
        self.log('重複候補を全内容照合してコピー予定を作成します。')
        def done(jobs):
            self.current = jobs
            new_count = skip_count = conflict_count = 0
            set_counts = {n: [0, 0, 0] for n in range(1, len(specs) + 1)}
            for number, plan, _ in jobs:
                for item in plan.items:
                    if item.raw:
                        continue
                    if item.status in ('内容重複スキップ', 'コピー済み'):
                        action, tag = '重複スキップ予定', 'skip'
                    elif item.status == '競合':
                        action, tag = '競合（コピー不可）', 'conflict'
                    else:
                        action, tag = 'コピー予定', 'new'
                    self.plan_tree.insert('', 'end', values=(number, action, '10kHz' if item.source.parent.name in ('EXSV', 'T') else '100kHz', str(item.source), str(item.destination)), tags=(tag,))
                    if item.source.suffix.lower() == '.tdms':
                        new_count += tag == 'new'
                        skip_count += tag == 'skip'
                        conflict_count += tag == 'conflict'
                        set_counts[number][0] += tag == 'new'
                        set_counts[number][1] += tag == 'skip'
                        set_counts[number][2] += tag == 'conflict'
            for number, (new, skipped, conflicts) in set_counts.items():
                self.batch_tree.set(str(number - 1), 'duplicates', f'解析対象：重複 {skipped}件')
                self.batch_tree.set(str(number - 1), 'nonduplicates', f'解析対象：{new}件' + (f'／競合 {conflicts}件' if conflicts else ''))
            self.plan_label.configure(text=f'解析用TDMS：コピー予定 {new_count}件 ／ 重複スキップ予定 {skip_count}件 ／ 競合 {conflict_count}件（raw dataは別途保存）')
            lines = []
            for number, p, spec in jobs:
                lines.append(f'セット {number}: {p.source} → {p.target} [{spec["context"]["kind"]}]')
                lines.extend('警告: ' + w for w in p.warnings)
                for rate, nums in p.numbers.items():
                    lines.append(f'{rate}: {min(nums) if nums else "-"} ～ {max(nums) if nums else "-"}')
                lines.extend(f'{i.status}: {i.source} → {i.destination}' for i in p.items)
                lines.append(f'合計 {len(p.items)} / 新規 {sum(i.status=="新規" for i in p.items)} / 既存・未検証 {sum(i.status=="既存（内容未検証）" for i in p.items)} / 競合 {sum(i.status=="競合" for i in p.items)}')
            self.log('\n'.join(lines))
            self.open_plan_window()
        def work():
            jobs = core.plan_batch(specs, progress=self.progress)
            return core.review_batch_duplicates(jobs, self.progress)
        self.run(work, done)

    def copy(self):
        if self.pc['role'] != 'transfer':
            messagebox.showinfo('操作・閲覧モード', 'このPCではコピーを実行できません。')
            return
        if not self.current or self.busy:
            return
        jobs = self.current
        for _, _, spec in jobs:
            spec['context'].update(pc_id=self.pc['pc_id'], pc_name=self.pc['display_name'])
        if not messagebox.askyesno('一括コピー開始', f'{len(self.sets)} セット / {len(jobs)} 実験を登録済みの保存先へコピーします。\n解析用ファイルは全内容が一致する保存先ファイルがあればスキップします。\nraw dataは実験フォルダ単位で保存します。開始しますか？'):
            return
        def work():
            return core.execute_batch(jobs, lambda s: self.events.put(('log',s)), progress=self.progress)
        def done(results):
            self.current = None
            failed = any(r[3] == '失敗（以降停止）' for r in results)
            self.progress_text.set('一括処理停止（結果ログを確認）' if failed else '一括処理終了')
            self.close_plan_window()
            self.plan_tree.delete(*self.plan_tree.get_children())
            counts = {}
            for number, plan, _ in jobs:
                for item in plan.items:
                    status = getattr(item, 'execution_status', '未処理')
                    counts[status] = counts.get(status, 0) + 1
                    tag = 'new' if status == 'コピー完了' else 'skip' if status == '既存一致スキップ' else 'conflict' if status == '停止箇所' else 'pending'
                    kind = 'raw data' if item.raw else '10kHz' if item.source.parent.name in ('EXSV', 'T') else '100kHz'
                    self.plan_tree.insert('', 'end', values=(number, status, kind, str(item.source), str(item.destination)), tags=(tag,))
            summary = '実行結果（全ファイル）：' + ' ／ '.join(f'{label} {counts.get(label, 0)}件' for label in ('コピー完了', '既存一致スキップ', '停止箇所', '未処理'))
            self.plan_label.configure(text=summary)
            self.log(summary)
            self.log('実験ごとの結果を確認してください。再実行する場合はコピー予定を再作成してください。')
            self.open_plan_window()
        self.run(work, done)


if __name__ == '__main__':
    App().mainloop()
