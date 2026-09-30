"""Run with: python exmanager/app.py"""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import core
import pc_config


def history_summary(records):
    # Legacy history has no server path: count recorded experiment/sample/type.
    destinations = {(r[7], r[8], r[9], r[13]) for r in records}
    latest = max((r[0] for r in records), default='—')
    return (f'{len(records)}件' if records else 'なし', f'{len(destinations)}か所', latest)


def history_detail(row):
    kind = 'Sample' if row[9].endswith('_Sample') else 'Blank' if row[9].endswith('_Blank') else row[9]
    return (row[0], row[7], row[8], kind, row[1], row[5])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('実験データ整理 — 試用版')
        self.geometry('1180x820')
        self.events = queue.Queue()
        self.busy = False
        self.current = None
        self.sets = []
        self.vars = {}
        self.boxes = {}
        self.pc = pc_config.load()
        self.restore_initial = True
        pc_panel = ttk.Frame(self, padding=(10, 5))
        pc_panel.pack(fill='x')
        self.pc_label = ttk.Label(pc_panel)
        self.pc_label.pack(side='left')
        ttk.Button(pc_panel, text='このPCの設定', command=self.configure_pc).pack(side='right')
        ttk.Button(pc_panel, text='終了', command=self.close).pack(side='right', padx=8)
        panel = ttk.Frame(self, padding=10)
        panel.pack(fill='x')
        fields = [('db', '装置・担当者CSVフォルダ', str(core.DB)), ('legacy', '既存Uploader.csv（読取専用）', str(core.DB / 'Uploader.csv')), ('local', '試用版履歴CSV', str(core.BASE / 'logs' / 'Uploader_python.csv')), ('root', '保存先解析ルート（検証先へ変更可）', r'\\Rackstation\analysis')]
        for n, (key, label, default) in enumerate(fields):
            ttk.Label(panel, text=label).grid(row=n, column=0, sticky='w')
            self.vars[key] = tk.StringVar(value=default)
            ttk.Entry(panel, textvariable=self.vars[key], width=95).grid(row=n, column=1, sticky='ew')
        panel.columnconfigure(1, weight=1)
        loaders = ttk.Frame(panel)
        loaders.grid(row=4, column=1, sticky='w')
        ttk.Button(loaders, text='装置・担当者CSVを再読み込み', command=self.load).pack(side='left')
        ttk.Button(loaders, text='保存先一覧を読み込み', command=self.load_destinations).pack(side='left', padx=8)
        select = ttk.Frame(self, padding=10)
        select.pack(fill='x')
        for n, (key, label) in enumerate([('machine','装置'), ('selection','コピー元'), ('operator','作業者'), ('uploader','アップロード担当者'), ('experiment','保存先実験'), ('sample','保存先サンプル'), ('kind','区分')]):
            ttk.Label(select, text=label).grid(row=(n//4)*2, column=n%4, sticky='w')
            self.vars[key] = tk.StringVar()
            box = ttk.Combobox(select, textvariable=self.vars[key], state='readonly', width=27)
            box.grid(row=(n//4)*2+1, column=n%4, padx=4, pady=4)
            self.boxes[key] = box
        self.boxes['selection']['values'] = ('Recent_Data', 'Archived_Data')
        self.vars['selection'].set('Recent_Data')
        self.boxes['kind']['values'] = ('Sample', 'Blank')
        self.vars['kind'].set('Sample')
        self.boxes['experiment'].bind('<<ComboboxSelected>>', self.samples)
        ttk.Button(select, text='コピー元一覧を更新', command=self.sources).grid(row=3, column=3)
        self.source_label = ttk.Label(self, text='明るい緑：履歴あり ／ 暗い緑：履歴なし（履歴だけではスキップしません）')
        self.source_label.pack(anchor='w', padx=10)
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill='both', expand=True, padx=10)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=('name','history','destinations','latest'), show='headings', height=6, selectmode='extended')
        self.tree.heading('name', text='実験フォルダ（Ctrl / Shiftで複数選択）')
        self.tree.heading('history', text='履歴件数')
        self.tree.heading('destinations', text='保存先数')
        self.tree.heading('latest', text='最終保存日時')
        self.tree.column('name', width=570)
        self.tree.column('history', width=90, stretch=False)
        self.tree.column('destinations', width=90, stretch=False)
        self.tree.column('latest', width=180, stretch=False)
        self.tree.tag_configure('yes', background='#a4ef72', foreground='black')
        self.tree.tag_configure('no', background='#24572a', foreground='white')
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(tree_frame, self.tree)
        self.source_histories = {}
        self.tree.bind('<<TreeviewSelect>>', self.show_history)
        self.tree.bind('<ButtonRelease-1>', self.show_history)
        self.tree.bind('<KeyRelease>', self.show_history)
        self.history_label = ttk.Label(self, text='実験を選択すると保存履歴を表示します。')
        self.history_label.pack(anchor='w', padx=10)
        history_frame = ttk.Frame(self)
        history_frame.pack(fill='x', padx=10)
        history_frame.columnconfigure(0, weight=1)
        self.history_tree = ttk.Treeview(history_frame, columns=('date','experiment','sample','kind','operator','uploader'), show='headings', height=3)
        for key, title, width in [('date','保存日時',160), ('experiment','保存先実験',200), ('sample','サンプル',150), ('kind','区分',90), ('operator','作業者',100), ('uploader','アップロード担当者',150)]:
            self.history_tree.heading(key, text=title)
            self.history_tree.column(key, width=width)
        self.history_tree.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(history_frame, self.history_tree)
        actions = ttk.Frame(self, padding=10)
        actions.pack(fill='x')
        ttk.Button(actions, text='選択を転送セットに追加', command=self.add_set).pack(side='left')
        ttk.Button(actions, text='選択セット削除', command=self.remove_set).pack(side='left')
        ttk.Button(actions, text='全セットを確認（高速）', command=self.preview).pack(side='left')
        ttk.Button(actions, text='プレビューCSVを保存', command=self.export).pack(side='left', padx=8)
        self.copy_button = ttk.Button(actions, text='一括コピー開始', command=self.copy)
        self.copy_button.pack(side='left')
        self.update_pc_display()
        batch_frame = ttk.Frame(self)
        batch_frame.pack(fill='x', padx=10)
        batch_frame.columnconfigure(0, weight=1)
        self.batch_tree = ttk.Treeview(batch_frame, columns=('set','source','target','person'), show='headings', height=4)
        for key, title, width in [('set','セット',55),('source','コピー元実験',350),('target','保存先・区分',400),('person','作業者 / 担当者',160)]:
            self.batch_tree.heading(key, text=title)
            self.batch_tree.column(key, width=width)
        self.batch_tree.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(batch_frame, self.batch_tree)
        progress_frame = ttk.Frame(self, padding=(10, 0))
        progress_frame.pack(fill='x')
        self.progress_text = tk.StringVar(value='待機中')
        ttk.Label(progress_frame, textvariable=self.progress_text).pack(anchor='w')
        self.progress_bar = ttk.Progressbar(progress_frame, maximum=100)
        self.progress_bar.pack(fill='x')
        self.file_text = tk.StringVar()
        ttk.Entry(progress_frame, textvariable=self.file_text, state='readonly').pack(fill='x')
        output_frame = ttk.Frame(self)
        output_frame.pack(fill='both', expand=True, padx=10, pady=10)
        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)
        self.output = tk.Text(output_frame, height=15, wrap='none')
        self.output.grid(row=0, column=0, sticky='nsew')
        self.add_scrollbars(output_frame, self.output)
        for key, value in self.pc.get('last_selection', {}).items():
            if key in self.vars and isinstance(value, str):
                self.vars[key].set(value)
        for key, default in [('selection', 'Recent_Data'), ('kind', 'Sample')]:
            if self.vars[key].get() not in self.boxes[key]['values']:
                self.vars[key].set(default)
        self.after(100, self.poll)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(200, self.load)

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
        self.copy_button.configure(state='normal' if self.pc['role']=='transfer' else 'disabled')

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
        self.after(100, self.poll)

    def load(self):
        if self.busy:
            self.log('処理中です。終了後にCSVを再読み込みしてください。')
            return
        v = self.values()
        def work():
            results = {}
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
            for key in ('operator','uploader'):
                self.set_choices(key, list(self.people))
            self.log(f'CSV読込: 装置 {len(self.machine_data)} 件 / 担当者 {len(self.people)} 件。保存先は「保存先一覧を読み込み」で取得してください。')
            for key in ('machines_error', 'operators_error'):
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
        v = self.values()
        previous_experiment = v['experiment']
        previous_sample = v['sample']
        self.vars['experiment'].set('')
        self.vars['sample'].set('')
        self.boxes['experiment']['values'] = ()
        self.boxes['sample']['values'] = ()
        def done(names):
            self.set_choices('experiment', names)
            if restore and previous_experiment in names:
                self.vars['experiment'].set(previous_experiment)
                self.samples(restore_sample=previous_sample)
        self.run(lambda: sorted(p.name for p in Path(v['root']).iterdir() if p.is_dir()), done)

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
            return sorted([p for p in root.iterdir() if p.is_dir()], reverse=True), core.history(v['legacy'], v['local'])
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
                self.tree.insert('', 'end', iid=str(n), values=(p.name, *history_summary(records)), tags=('yes' if records else 'no',))
                self.source_paths[str(n)] = p
            self.source_label.configure(text=f'{root} — 明るい緑：履歴あり ／ 暗い緑：履歴なし')
        self.run(work, done)

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
        if not selected or not all(v[k] for k in ('experiment','sample','operator','uploader')):
            messagebox.showinfo('選択不足', '実験・保存先・担当者を選択してください。')
            return
        if self.source_context != (v['machine'], v['selection']):
            messagebox.showinfo('一覧更新', 'コピー元一覧を更新してください。')
            return
        if Path(v['legacy']).resolve() == Path(v['local']).resolve():
            messagebox.showerror('設定', '既存CSVと試用版CSVは別のパスにしてください。')
            return
        dst = Path(v['root']) / v['experiment'] / v['sample']
        context = dict(operator=self.people[v['operator']], uploader=self.people[v['uploader']], machine=v['machine'], selection=v['selection'], kind=v['kind'])
        if self.sets and (v['legacy'], v['local']) != (self.sets[0]['legacy'], self.sets[0]['local']):
            messagebox.showerror('履歴設定', '同じ一括処理では履歴CSVの設定を統一してください。')
            return
        self.sets.append(dict(sources=[self.source_paths[s] for s in selected], target=dst, context=context, legacy=v['legacy'], local=v['local']))
        self.refresh_sets()

    def refresh_sets(self):
        self.current = None
        self.batch_tree.delete(*self.batch_tree.get_children())
        for n, spec in enumerate(self.sets):
            c = spec['context']
            self.batch_tree.insert('', 'end', iid=str(n), values=(n+1, ' / '.join(p.name for p in spec['sources']), f"{spec['target']} [{c['kind']}]", f"{c['operator']} / {c['uploader']}"))

    def remove_set(self):
        if self.busy:
            return
        for n in sorted((int(s) for s in self.batch_tree.selection()), reverse=True):
            del self.sets[n]
        self.refresh_sets()

    def preview(self):
        if self.busy or not self.sets:
            return
        self.current = None
        specs = list(self.sets)
        self.output.delete('1.0','end')
        self.log('ファイル名・サイズ・保存先・採番を確認します。内容は読み取りません。')
        def done(jobs):
            self.current = jobs
            lines = []
            for number, p, spec in jobs:
                lines.append(f'セット {number}: {p.source} → {p.target} [{spec["context"]["kind"]}]')
                lines.extend('警告: ' + w for w in p.warnings)
                for rate, nums in p.numbers.items():
                    lines.append(f'{rate}: {min(nums) if nums else "-"} ～ {max(nums) if nums else "-"}')
                lines.extend(f'{i.status}: {i.source} → {i.destination}' for i in p.items)
                lines.append(f'合計 {len(p.items)} / 新規 {sum(i.status=="新規" for i in p.items)} / 既存・未検証 {sum(i.status=="既存（内容未検証）" for i in p.items)} / 競合 {sum(i.status=="競合" for i in p.items)}')
            self.log('\n'.join(lines))
        self.run(lambda: core.plan_batch(specs, progress=self.progress), done)

    def export(self):
        if not self.current or self.busy:
            return
        path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile='copy_preview.csv')
        if path:
            core.write_csv(path, ['set','source','destination','sha256','status'], [(n,i.source,i.destination,i.sha256,i.status) for n,p,_ in self.current for i in p.items])

    def copy(self):
        if self.pc['role'] != 'transfer':
            messagebox.showinfo('操作・閲覧モード', 'このPCではコピーを実行できません。')
            return
        if not self.current or self.busy:
            return
        jobs = self.current
        for _, _, spec in jobs:
            spec['context'].update(pc_id=self.pc['pc_id'], pc_name=self.pc['display_name'])
        if not messagebox.askyesno('一括コピー開始', f'{len(self.sets)} セット / {len(jobs)} 実験を登録済みの保存先へコピーします。\n内容照合は実行中に行います。開始しますか？'):
            return
        def work():
            return core.execute_batch(jobs, lambda s: self.events.put(('log',s)), progress=self.progress)
        def done(results):
            self.current = None
            failed = any(r[3] == '失敗（以降停止）' for r in results)
            self.progress_text.set('一括処理停止（結果ログを確認）' if failed else '一括処理終了')
            self.log('実験ごとの結果を確認してください。再実行する場合は全セットを再プレビューしてください。')
        self.run(work, done)


if __name__ == '__main__':
    App().mainloop()
