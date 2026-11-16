"""Measurement-folder entry point for the shared waveform/clip workspace."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from measurement_files import (channel_metadata, load_preferences, measurement_t_folder,
                               save_preferences, scan_measurement, signature)


class MeasurementPanel(ttk.Frame):
    def __init__(self, master, viewer):
        super().__init__(master, padding=16)
        self.viewer = viewer
        self.results = queue.Queue()
        self.rows = {}
        self.busy = False
        self.catalog_root = None
        self.last_open = None
        self.progress_message = ''
        preferences = load_preferences()
        saved = preferences.get('settings', {})
        self.folder = tk.StringVar(value=next(iter(preferences.get('recent', [])), ''))
        self.hz = tk.StringVar(value=str(saved.get('hz', '10000')))
        self.group = tk.StringVar(value=str(saved.get('group', 'Data')))
        self.channel = tk.StringVar(value=str(saved.get('channel', 'Ch1')))
        self.window = tk.StringVar(value=str(saved.get('window', '1.0')))
        self.auto_hz = tk.BooleanVar(value=saved.get('auto_hz', True))
        self.auto_channel = tk.BooleanVar(value=saved.get('auto_channel', True))
        self.detect = tk.BooleanVar(value=saved.get('detect', False))
        self.status = tk.StringVar(value='計測フォルダを指定するとEXSV/Tの一覧を表示します。生波形の表示ではANAL処理を行いません。')
        row = ttk.Frame(self)
        row.pack(fill='x')
        ttk.Label(row, text='計測フォルダ / EXSV / T').pack(side='left', padx=(0, 8))
        self.recent = ttk.Combobox(row, textvariable=self.folder, values=preferences.get('recent', []))
        self.recent.pack(side='left', fill='x', expand=True)
        self.recent.bind('<Return>', lambda _: self.scan())
        self.recent.bind('<<ComboboxSelected>>', lambda _: self.scan())
        ttk.Button(row, text='参照…', command=self.browse).pack(side='left', padx=6)
        self.refresh_button = ttk.Button(row, text='一覧を読み込む / 更新', command=self.scan)
        self.refresh_button.pack(side='left')
        settings = ttk.LabelFrame(self, text='計測フォルダの表示設定（ANAL読み込みタブとは独立）', padding=10)
        settings.pack(fill='x', pady=12)
        for index, (label, variable) in enumerate([('周波数 (Hz)', self.hz), ('表示区間 (秒)', self.window),
                                                  ('TDMSグループ', self.group), ('チャンネル', self.channel)]):
            ttk.Label(settings, text=label).grid(row=0, column=index * 2, padx=(0, 6))
            ttk.Entry(settings, textvariable=variable, width=15).grid(row=0, column=index * 2 + 1, padx=(0, 16))
        ttk.Checkbutton(settings, text='TDMSに周波数情報があれば自動使用（なければ入力値を使用）',
                        variable=self.auto_hz).grid(row=1, column=0, columnspan=5, sticky='w', pady=(8, 0))
        ttk.Checkbutton(settings, text='ビューア内の信号検出プレビュー（ANAL結果は作成しません）',
                        variable=self.detect).grid(row=2, column=0, columnspan=8, sticky='w')
        ttk.Checkbutton(settings, text='指定チャンネルがなければ波形チャンネルを自動判別（候補が1つの場合のみ）',
                        variable=self.auto_channel).grid(row=3, column=0, columnspan=8, sticky='w')
        ttk.Label(self, textvariable=self.status, wraplength=1300).pack(fill='x', pady=6)
        self.bar = ttk.Progressbar(self, mode='indeterminate')
        self.bar.pack(fill='x', pady=(0, 8))
        columns = [('name', '計測ファイル', 680), ('location', '保存場所', 100),
                   ('versions', 'ANAL版数', 90), ('status', '状態', 330)]
        body = ttk.Frame(self)
        body.pack(fill='both', expand=True)
        self.table = ttk.Treeview(body, columns=[c[0] for c in columns], show='headings', selectmode='browse')
        for key, label, width in columns:
            self.table.heading(key, text=label)
            self.table.column(key, width=width)
        self.table.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.table.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(body, orient='horizontal', command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.table.bind('<<TreeviewSelect>>', lambda _: self.selected())
        self.table.bind('<Double-1>', lambda _: self.open('raw'))
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=10)
        self.raw_button = ttk.Button(actions, text='生TDMSの波形を見る', command=lambda: self.open('raw'), state='disabled')
        self.raw_button.pack(side='left')
        self.anal_button = ttk.Button(actions, text='ANALの波形を見る', command=lambda: self.open('anal'), state='disabled')
        self.anal_button.pack(side='left', padx=8)
        ttk.Label(actions, text='ANAL版').pack(side='left')
        self.version = ttk.Combobox(actions, state='readonly', width=65)
        self.version.pack(side='left', fill='x', expand=True, padx=8)
        ttk.Label(self, text='未解析のシグナル数・頻度は計算しません。T/stocked・ANAL/stockedも確認し、元ファイル名が一致する解析結果だけを対応づけます。',
                  wraplength=1300).pack(fill='x')
        self.after(100, self.poll)

    def browse(self):
        value = filedialog.askdirectory(parent=self, title='計測フォルダを選択')
        if value:
            self.folder.set(value)
            self.scan()

    def set_busy(self, value):
        self.busy = value
        self.refresh_button.configure(state='disabled' if value else 'normal')
        if value:
            self.raw_button.configure(state='disabled')
            self.anal_button.configure(state='disabled')
            self.bar.start()
        else:
            self.bar.stop()
            self.selected()

    def scan(self):
        if self.busy:
            return
        value = self.folder.get()
        self.set_busy(True)
        self.status.set('EXSV/TとANALの一覧を確認中…（表示中の波形は維持します）')
        def task():
            try:
                result = scan_measurement(value, lambda message: self.results.put(('progress', message)))
                self.results.put(('scan', result))
            except Exception as error:
                self.results.put(('error', error))
        threading.Thread(target=task, daemon=True).start()

    def selected(self):
        selection = self.table.selection()
        row = self.rows.get(selection[0]) if selection else None
        old = self.version.get()
        versions = [v['label'] + (f" | 条件: {v['condition']}" if v['condition'] else '') for v in row['versions']] if row else []
        self.version.configure(values=versions)
        self.version.set(old if old in versions else (versions[0] if versions else ''))
        self.raw_button.configure(state='normal' if row and row['ready'] and not self.busy else 'disabled')
        self.anal_button.configure(state='normal' if versions and not self.busy else 'disabled')

    def open(self, mode):
        if self.busy:
            return
        selection = self.table.selection()
        row = self.rows.get(selection[0]) if selection else None
        if not row:
            return
        try:
            if self.catalog_root != measurement_t_folder(self.folder.get()):
                raise ValueError('計測フォルダが変更されています。一覧を更新してください。')
            if mode == 'raw':
                if not row['ready']:
                    raise ValueError('生TDMSは計測中・未確認・または見つかりません。一覧を更新してください。')
                path, expected = row['path'], row['signature']
            else:
                index = self.version.current()
                if index < 0 or index >= len(row['versions']):
                    raise ValueError('表示するANAL版を選択してください。')
                version = row['versions'][index]
                path, expected = version['path'], version['signature']
            # Copy Tk values before starting the worker.
            options = dict(hz=self.hz.get(), window=self.window.get(), group=self.group.get(),
                           channel=self.channel.get(), auto_hz=self.auto_hz.get(), detect=self.detect.get(),
                           auto_channel=self.auto_channel.get())
            root, revision = self.catalog_root, self.viewer.display_revision
            if signature(path) != expected:
                raise ValueError('一覧確認後にファイルが更新されています。一覧を更新してください。')
        except (ValueError, OSError) as error:
            messagebox.showerror('波形を開けません', str(error), parent=self)
            return
        item = selection[0]
        self.set_busy(True)
        self.status.set(f'選択したTDMSを読み込み中… {Path(path).name}')
        def task():
            try:
                import math
                from tdms_signal_base import tdms_read_channel
                metadata = channel_metadata(path, options['group'], options['channel'], options['auto_channel'])
                automatic_hz = metadata['hz']
                frequency_source = 'TDMS取得値'
                if options['auto_hz'] and not automatic_hz and mode == 'anal' and row['ready']:
                    if signature(row['path']) == row['signature']:
                        try:
                            original_metadata = channel_metadata(row['path'], options['group'], options['channel'],
                                                                 options['auto_channel'])
                            automatic_hz = original_metadata['hz']
                            frequency_source = '元TDMS取得値'
                        except (OSError, ValueError, KeyError):
                            pass
                hz = automatic_hz if options['auto_hz'] and automatic_hz else float(options['hz'])
                if not options['auto_hz'] or not automatic_hz:
                    frequency_source = '入力設定値'
                window = float(options['window'])
                if not math.isfinite(hz) or hz <= 0 or not math.isfinite(window) or window <= 0 or hz * window < 1:
                    raise ValueError('周波数・表示区間に有効な正数を入力してください。')
                data = tdms_read_channel(path, metadata['group'], metadata['channel'])
                if not len(data):
                    raise ValueError('チャンネルに波形データがありません。')
                if signature(path) != expected:
                    raise ValueError('読み込み中にファイルが更新されました。一覧を更新してください。')
                result = dict(item=item, row=row, mode=mode, path=path, hz=hz, data=data,
                              options=options, metadata=metadata, root=root, revision=revision)
                result['frequency_source'] = frequency_source
                self.results.put(('open', result))
            except Exception as error:
                self.results.put(('error', error))
        threading.Thread(target=task, daemon=True).start()

    def navigate(self, direction):
        if self.last_open is None or self.busy:
            return
        current, mode = self.last_open
        candidates = [key for key, row in self.rows.items() if row['ready']] if mode == 'raw' else [
            key for key, row in self.rows.items() if row['versions']]
        if not candidates:
            return
        index = (candidates.index(current) + direction) % len(candidates) if current in candidates else 0
        self.table.selection_set(candidates[index])
        self.table.see(candidates[index])
        self.selected()
        self.open(mode)

    def switch(self, mode):
        if self.last_open:
            item, _ = self.last_open
            if item in self.rows:
                self.table.selection_set(item)
                self.selected()
                self.open(mode)

    def show(self, result):
        from view_tdms_signal_tk import FIELDS, parse_settings
        if self.viewer.display_revision != result['revision']:
            self.status.set('読み込み中に別の波形が開かれたため表示を維持しました。表示するにはもう一度開いてください。')
            return
        options, path, mode = result['options'], result['path'], result['mode']
        values = {key: default for key, _, default in FIELDS}
        values.update(sampling_hz=str(result['hz']), chunk_sec=options['window'],
                      target_group=result['metadata']['group'], target_channel=result['metadata']['channel'])
        settings = parse_settings(values)
        frequency_source = result['frequency_source']
        label = (f"{'生TDMS' if mode == 'raw' else '保存済みANAL'} | {result['root'].parent.parent.name} | "
                 f"{result['metadata']['group']}/{result['metadata']['channel']} | {result['hz']:g} Hz（{frequency_source}） | "
                 f"{len(result['data']) / result['hz']:.3f} 秒\n{path}")
        settings.update(initial_path=path, file_paths=[path], preloaded_data={path: result['data']},
                        out_root=self.viewer.output.get(), detection_enabled=options['detect'],
                        output_label=f'{result["root"].parent.parent.name}_{mode}')
        restore = None
        if (self.last_open and self.last_open[0] == result['item'] and self.viewer.browser is not None
                and getattr(self.viewer.browser, 'measurement_owner', None) is self):
            browser = self.viewer.browser
            restore = self.viewer.capture_view(browser)
        self.viewer.embed_viewer(settings, [str(Path(path).parent)], source_label=label,
                                 source_navigation=self.navigate, source_switch=self.switch,
                                 return_panel=self, restore_view=restore, detection_changed=self.detect.set)
        self.viewer.browser.measurement_owner = self
        self.last_open = result['item'], mode
        self.viewer.status.set(label)
        self.status.set(label)
        try:
            save_preferences(result['root'], options)
            self.recent.configure(values=load_preferences().get('recent', []))
        except OSError as error:
            self.status.set(label + f'\n履歴設定を保存できません: {error}')

    def poll(self):
        try:
            while True:
                kind, payload = self.results.get_nowait()
                if kind == 'progress':
                    self.status.set(payload)
                    continue
                self.set_busy(False)
                if kind == 'error':
                    self.status.set(str(payload))
                    messagebox.showerror('計測フォルダ', str(payload), parent=self)
                elif kind == 'scan':
                    old = self.rows.get(self.table.selection()[0], {}) if self.table.selection() else {}
                    selected_path = old.get('path') or old.get('name')
                    opened = self.rows.get(self.last_open[0], {}) if self.last_open else {}
                    opened_path = opened.get('path') or opened.get('name')
                    opened_mode = self.last_open[1] if self.last_open else None
                    previous_root = self.catalog_root
                    root, rows = payload
                    self.catalog_root = root
                    self.rows = {str(i): row for i, row in enumerate(rows)}
                    self.table.delete(*self.table.get_children())
                    self.last_open = None
                    for key, row in self.rows.items():
                        self.table.insert('', 'end', iid=key, values=(row['name'], row['location'], len(row['versions']), row['status']))
                        if selected_path and selected_path == (row.get('path') or row.get('name')):
                            self.table.selection_set(key)
                        if previous_root == root and opened_path and opened_path == (row.get('path') or row.get('name')):
                            self.last_open = key, opened_mode
                    self.selected()
                    self.status.set(f'{root} | {len(rows)}計測 | 生TDMS表示可能 {sum(r["ready"] for r in rows)}件 | ANALあり {sum(bool(r["versions"]) for r in rows)}件')
                elif kind == 'open':
                    self.show(payload)
        except queue.Empty:
            pass
        except Exception as error:
            self.status.set(str(error))
            messagebox.showerror('計測フォルダ', str(error), parent=self)
        self.after(100, self.poll)
