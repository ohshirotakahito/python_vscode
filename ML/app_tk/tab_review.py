"""抽出済みファイルの統計による精査。"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd
import numpy as np
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from inspection_versions import InspectionVersions
from common.feature_store import resolve_version
from lib.catalog import list_available_samples
from lib.file_review import (comparison, event_histogram, histogram_markers, file_statistics, filter_files,
                             load_events, numeric_features, selected_events)
from widgets import CheckList, DataTable, ProgressPanel

SAMPLE_PALETTE = [('青', '#1f77b4'), ('橙', '#e66b00'), ('緑', '#228b22'),
                  ('赤', '#d62728'), ('紫', '#8045ad'), ('茶', '#8c564b'),
                  ('桃', '#c52f89'), ('黒', '#222222')]


class ReviewTab(InspectionVersions, ttk.Frame):
    def __init__(self, master, runner, on_train=None):
        super().__init__(master, padding=10)
        self.runner = runner
        self.on_train = on_train
        self.loaded_selection = None
        self.events = pd.DataFrame()
        self.files = pd.DataFrame()
        self.filtered = pd.DataFrame()
        self.result = pd.DataFrame()
        self.rules = []
        self.table_window = None
        self.subset = pd.DataFrame()
        self.hist_range = None
        self.range_selectors = []
        self.sample_color_overrides = {}
        pane = ttk.PanedWindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True)
        left = ttk.Frame(pane, width=310)
        right = ttk.Frame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=4)
        ttk.Label(left, text='特徴量セット / サンプル').pack(anchor='w')
        self.feature_set = tk.StringVar(value='rmc')
        combo = ttk.Combobox(left, textvariable=self.feature_set, values=['rmb', 'rmc', 'rmd'], state='readonly')
        combo.pack(fill='x')
        combo.bind('<<ComboboxSelected>>', lambda _: self.refresh_samples())
        self.samples = CheckList(left, height=7, on_change=self._samples_selected)
        self.samples.pack(fill='x', pady=4)
        self.sample_selection = ttk.Label(left, text='選択中：0 サンプル', wraplength=400,
                                          foreground='#174ea6')
        self.sample_selection.pack(fill='x', pady=(0, 4))
        ttk.Button(left, text='サンプルの選択をすべて解除',
                   command=self.samples.clear_selection).pack(fill='x', pady=(0, 4))
        self._build_version_picker(left)
        self.load_button = ttk.Button(left, text='統計を読み込む', command=self._load)
        self.load_button.pack(fill='x')
        filters = ttk.LabelFrame(left, text='ファイルの絞り込み（すべてAND）', padding=6)
        filters.pack(fill='x', pady=6)
        self.column = tk.StringVar()
        self.columns = ttk.Combobox(filters, textvariable=self.column, state='readonly')
        self.columns.pack(fill='x')
        self.columns.bind('<<ComboboxSelected>>', lambda _: self._column_changed())
        self.operator = tk.StringVar(value='含む')
        self.operators = ttk.Combobox(filters, textvariable=self.operator, state='readonly')
        self.operators.pack(fill='x', pady=3)
        ttk.Label(filters, text='条件値（>= 下限 / <= 上限 / = 一致）').pack(anchor='w')
        self.value = tk.StringVar()
        ttk.Entry(filters, textvariable=self.value).pack(fill='x')
        self.range_hint = ttk.Label(filters, text='指標を選んで条件値を入力してください')
        self.range_hint.pack(anchor='w')
        ttk.Button(filters, text='条件を追加', command=self._add_rule).pack(fill='x', pady=3)
        self.rule_list = tk.Listbox(filters, height=5)
        self.rule_list.pack(fill='x')
        ttk.Button(filters, text='選択した条件を削除', command=self._remove_rule).pack(fill='x')
        ttk.Button(filters, text='絞り込みを解除', command=self._reset).pack(fill='x', pady=3)
        self.train_button = ttk.Button(left, text='この条件で学習・評価へ', command=self._open_training)
        self.train_button.pack(fill='x', pady=(0, 6))
        self.progress = ProgressPanel(left, log_height=3)
        self.progress.pack(fill='both', expand=True)
        self.summary = ttk.Label(right, text='サンプルを選んで読み込んでください')
        self.summary.pack(anchor='w')
        ttk.Label(right, text='全ファイル = 読み込んだサンプルの全イベント。欠損値は特徴量ごとに除外。標準偏差は標本標準偏差。').pack(anchor='w')
        plot_bar = ttk.Frame(right)
        plot_bar.pack(fill='x', pady=6)
        ttk.Label(plot_bar, text='表示する指標').pack(side='left')
        self.metric = tk.StringVar()
        self.metrics = ttk.Combobox(plot_bar, textvariable=self.metric, state='readonly', width=30)
        self.metrics.pack(side='left', padx=6)
        self.metrics.bind('<<ComboboxSelected>>', lambda _: self._draw_plot())
        ttk.Button(plot_bar, text='この指標で絞り込む', command=self._use_metric).pack(side='left')
        ttk.Button(plot_bar, text='統計表を表示', command=self._show_tables).pack(side='right')
        hist_bar = ttk.Frame(right)
        hist_bar.pack(fill='x', pady=4)
        self.plot_mode = tk.StringVar(value='ファイル別指標')
        mode = ttk.Combobox(hist_bar, textvariable=self.plot_mode,
                           values=['ファイル別指標', '全イベントのヒストグラム'], state='readonly', width=25)
        mode.pack(side='left')
        mode.bind('<<ComboboxSelected>>', lambda _: self._mode_changed())
        ttk.Label(hist_bar, text='イベント指標').pack(side='left', padx=5)
        self.hist_feature = tk.StringVar()
        self.hist_features = ttk.Combobox(hist_bar, textvariable=self.hist_feature, state='readonly', width=18)
        self.hist_features.pack(side='left')
        self.hist_features.bind('<<ComboboxSelected>>', lambda _: self._reset_hist_range())
        ttk.Label(hist_bar, text='ビン数').pack(side='left', padx=5)
        self.hist_bins = tk.StringVar(value='200')
        bins = ttk.Combobox(hist_bar, textvariable=self.hist_bins, values=['20', '50', '100', '200'], state='readonly', width=5)
        bins.pack(side='left')
        bins.bind('<<ComboboxSelected>>', lambda _: self._draw_plot())
        self.hist_percent = tk.BooleanVar(value=False)
        ttk.Checkbutton(hist_bar, text='各集団内の割合（%）', variable=self.hist_percent,
                        command=lambda: self._normalization_changed('percent')).pack(side='left', padx=6)
        self.hist_normalized = tk.BooleanVar(value=False)
        layout_bar = ttk.Frame(right)
        layout_bar.pack(fill='x', pady=2)
        ttk.Checkbutton(layout_bar, text='総カウントを10,000に正規化', variable=self.hist_normalized,
                        command=lambda: self._normalization_changed('normalized')).pack(side='left', padx=5)
        ttk.Label(layout_bar, text='ヒストグラム配置').pack(side='left')
        self.hist_layout = tk.StringVar(value='サンプル別')
        layout = ttk.Combobox(layout_bar, textvariable=self.hist_layout,
                              values=['サンプル別', '同じ図に重ねる'], state='readonly', width=20)
        layout.pack(side='left', padx=5)
        layout.bind('<<ComboboxSelected>>', lambda _: self._draw_plot())
        self.show_markers = tk.BooleanVar(value=True)
        ttk.Checkbutton(layout_bar, text='統計の縦線・数値を表示', variable=self.show_markers,
                        command=self._draw_plot).pack(side='left')
        visibility_bar = ttk.Frame(right)
        visibility_bar.pack(fill='x', pady=2)
        ttk.Label(visibility_bar, text='ヒストグラムの表示対象').pack(side='left', padx=5)
        self.hist_population = tk.StringVar(value='All と Selected')
        population = ttk.Combobox(visibility_bar, textvariable=self.hist_population,
                                  values=['All と Selected', 'All のみ', 'Selected のみ'],
                                  state='readonly', width=20)
        population.pack(side='left', padx=5)
        population.bind('<<ComboboxSelected>>', lambda _: self._draw_plot())
        color_bar = ttk.Frame(right)
        color_bar.pack(fill='x', pady=3)
        ttk.Label(color_bar, text='色を変更するサンプル').pack(side='left')
        self.color_sample = tk.StringVar()
        self.color_samples = ttk.Combobox(color_bar, textvariable=self.color_sample,
                                          state='readonly', width=22)
        self.color_samples.pack(side='left', padx=5)
        self.color_samples.bind('<<ComboboxSelected>>', lambda _: self._update_color_buttons())
        self.color_buttons = {}
        for name, color in SAMPLE_PALETTE:
            button = tk.Button(color_bar, text=name, background=color, foreground='white',
                               activebackground=color, activeforeground='white', width=3,
                               command=lambda c=color: self._set_sample_color(c))
            button.pack(side='left', padx=2)
            self.color_buttons[color] = button
        ttk.Button(color_bar, text='標準色に戻す', command=self._reset_sample_color).pack(side='left', padx=5)
        range_bar = ttk.Frame(right)
        range_bar.pack(fill='x', pady=4)
        ttk.Label(range_bar, text='横軸範囲　下限').pack(side='left')
        self.range_min = tk.StringVar(value='0')
        self.range_max = tk.StringVar()
        ttk.Entry(range_bar, textvariable=self.range_min, width=12).pack(side='left', padx=4)
        ttk.Label(range_bar, text='上限').pack(side='left')
        ttk.Entry(range_bar, textvariable=self.range_max, width=12).pack(side='left', padx=4)
        ttk.Button(range_bar, text='範囲を適用', command=self._apply_hist_range).pack(side='left')
        ttk.Button(range_bar, text='自動範囲に戻す', command=self._reset_hist_range).pack(side='left', padx=4)
        self.drag_range = tk.BooleanVar(value=False)
        ttk.Checkbutton(range_bar, text='ドラッグで範囲指定', variable=self.drag_range,
                        command=self._draw_plot).pack(side='left')
        ttk.Label(right, text='色：サンプル　散布図の塗りつぶし：選択／白抜き：対象外　ヒストグラムの実線：選択／破線：全体').pack(anchor='w')
        self.figure = Figure(figsize=(8, 5), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, right)
        self.toolbar.update()
        self.canvas.mpl_connect('pick_event', self._pick_file)
        self.point_info = ttk.Label(right, text='', wraplength=850)
        self.point_info.pack(anchor='w')
        bar = ttk.Frame(right)
        bar.pack(fill='x')
        ttk.Button(bar, text='ファイル一覧をCSV保存', command=lambda: self._export(self.filtered)).pack(side='left')
        ttk.Button(bar, text='比較統計をCSV保存', command=lambda: self._export(self.result)).pack(side='left', padx=4)
        runner.add_busy_listener(lambda busy: self.load_button.configure(state='disabled' if busy else 'normal'))
        runner.add_busy_listener(lambda busy: self.train_button.configure(state='disabled' if busy else 'normal'))
        self.refresh_samples()

    def refresh_samples(self):
        self._refresh_version_set()
        self.samples.set_items(list_available_samples(self.feature_set.get()))

    def _samples_selected(self, samples):
        self.sample_selection.configure(text=f'選択中：{len(samples)} サンプル\n' + '、'.join(samples))

    def _load(self):
        samples = self.samples.get_selected()
        if not samples:
            messagebox.showerror('入力エラー', 'サンプルを1つ以上選んでください。')
            return
        feature_set = self.feature_set.get()
        versions = dict(self.feature_versions)

        def task(progress):
            selected = {sample: resolve_version(feature_set, sample, versions.get(sample))
                        for sample in samples}
            events = load_events(feature_set, samples, progress, feature_versions=selected)
            return events, file_statistics(events), (feature_set, list(samples), versions,
                    {sample: version['run_id'] for sample, version in selected.items()})

        self.runner.run(task, self.progress, on_done=self._loaded)

    def _loaded(self, data):
        self.events, self.files, self.loaded_selection = data
        samples = sorted(self.files['sample'].unique()) if not self.files.empty else []
        self.color_samples.configure(values=samples)
        if self.color_sample.get() not in samples:
            self.color_sample.set(samples[0] if samples else '')
        self._update_color_buttons()
        self.columns.configure(values=list(self.files.columns))
        self.column.set('events' if not self.files.empty else '')
        numeric = list(self.files.select_dtypes(include='number').columns)
        self.metrics.configure(values=numeric)
        self.metric.set('events' if 'events' in numeric else '')
        features = numeric_features(self.events)
        self.hist_features.configure(values=features)
        self.hist_feature.set('signal' if 'signal' in features else features[0] if features else '')
        self.value.set('')
        self._column_changed()
        self._reset()

    def _open_training(self):
        if self.runner.busy or self.on_train is None:
            return
        if self.loaded_selection is None or self.files.empty:
            messagebox.showerror('入力エラー', '先に統計を読み込んで精査してください。')
            return
        feature_set, samples, requested, resolved = self.loaded_selection
        current_versions = {s: self.feature_versions.get(s) for s in samples}
        loaded_versions = {s: requested.get(s) for s in samples}
        if (self.feature_set.get() != feature_set or
                set(self.samples.get_selected()) != set(samples) or
                current_versions != loaded_versions):
            messagebox.showerror('再読み込みが必要です', 'サンプル・特徴量セット・抽出版が変更されています。統計を読み込み直してください。')
            return
        if len(samples) < 2:
            messagebox.showerror('入力エラー', '学習・評価にはサンプルを2つ以上選んでください。')
            return
        missing = set(samples) - set(self.filtered['sample'])
        if missing:
            messagebox.showerror('入力エラー', '条件に一致するファイルがないサンプル：' + '、'.join(sorted(missing)))
            return
        self.on_train(feature_set, list(samples), dict(resolved), list(self.rules))

    def _mode_changed(self):
        if self.plot_mode.get() == '全イベントのヒストグラム':
            feature = self.metric.get().rsplit('.', 1)[0]
            if feature in numeric_features(self.events):
                self.hist_feature.set(feature)
        self._draw_plot()

    def _column_changed(self):
        if self.column.get() not in self.files:
            return
        values = self.files[self.column.get()]
        numeric = pd.api.types.is_numeric_dtype(values)
        options = ['>=', '<=', '='] if numeric else ['含む', '=']
        self.operators.configure(values=options)
        self.operator.set(options[0])
        self.range_hint.configure(text=(f'値の範囲：{values.min():g} ～ {values.max():g}'
                                       if numeric and values.notna().any() else '条件値を入力してください'))

    def _use_metric(self):
        if self.metric.get():
            self.column.set(self.metric.get())
            self._column_changed()

    def _add_rule(self):
        if self.column.get() not in self.files.columns:
            return
        rule = (self.column.get(), self.operator.get(), self.value.get())
        try:
            filter_files(self.files, [rule])
        except (ValueError, TypeError):
            messagebox.showerror('入力エラー', '条件値を入力してください。数値指標には有限の数値が必要です。')
            return
        self.rules.append(rule)
        self._apply()

    def _remove_rule(self):
        for index in reversed(self.rule_list.curselection()):
            del self.rules[index]
        self._apply()

    def _reset(self):
        self.rules.clear()
        self._apply()

    def _apply(self):
        self.rule_list.delete(0, 'end')
        for column, operator, value in self.rules:
            self.rule_list.insert('end', f'{column} {operator} {value}')
        if self.events.empty:
            self.filtered = self.files.copy()
            self.result = pd.DataFrame()
            self.subset = self.events.copy()
            count = 0
        else:
            self.filtered = filter_files(self.files, self.rules)
            subset = selected_events(self.events, self.filtered)
            self.subset = subset
            count = len(subset)
            self.result = comparison(self.events, subset)
        self._draw_plot()
        self._update_tables()
        self.summary.configure(text=f'ファイル: {len(self.filtered):,} / {len(self.files):,}　イベント: {count:,} / {len(self.events):,}')

    def _display_files(self):
        files = self.files.copy()
        if files.empty:
            return files
        files.insert(0, '対象', np.where(files.index.isin(self.filtered.index), '選択', '対象外'))
        files.insert(1, 'ファイル番号', files['sample_name'].str.extract(r'#(\d+)', expand=False).fillna('不明'))
        return files

    def _show_tables(self):
        if self.table_window is not None and self.table_window.winfo_exists():
            self.table_window.lift()
            return
        self.table_window = tk.Toplevel(self)
        self.table_window.title('抽出ファイル精査：統計表（青：選択 / 灰：対象外）')
        self.table_window.geometry('1200x650')
        views = ttk.Notebook(self.table_window)
        views.pack(fill='both', expand=True)
        self.file_table = DataTable(views)
        self.stat_table = DataTable(views)
        views.add(self.file_table, text='全ファイルの統計・選択状態')
        views.add(self.stat_table, text='全体との統計比較')
        self.file_table.tree.tag_configure('included', background='#dbeafe', foreground='#174ea6')
        self.file_table.tree.tag_configure('excluded', background='#f1f1f1', foreground='#777777')
        self._update_tables()

    def _update_tables(self):
        if self.table_window is None or not self.table_window.winfo_exists():
            return
        files = self._display_files()
        self.file_table.show(files.round(6))
        for item, included in zip(self.file_table.tree.get_children(), files.get('対象', [])):
            self.file_table.tree.item(item, tags=('included' if included == '選択' else 'excluded',))
        self.stat_table.show(self.result.round(6))

    def _draw_plot(self):
        for selector in self.range_selectors:
            selector.disconnect_events()
        self.range_selectors.clear()
        self.figure.clear()
        self.axes = self.figure.add_subplot(111)
        self.point_info.configure(text='')
        self.plot_files = self.files.copy()
        if self.plot_mode.get() == '全イベントのヒストグラム':
            self._draw_histogram()
            return
        metric = self.metric.get()
        if not self.files.empty and metric in self.files:
            numbers = pd.to_numeric(self.files['sample_name'].str.extract(r'#(\d+)', expand=False), errors='coerce')
            self.plot_files = self.files.assign(_number=numbers).sort_values(
                ['sample', '_number', 'ex_id', 'sample_name'], kind='stable').drop(columns='_number')
            positions = np.arange(len(self.plot_files))
            selected = self.plot_files.index.isin(self.filtered.index)
            values = self.plot_files[metric].to_numpy(dtype=float)
            for sample, color in self._sample_colors().items():
                sample_mask = self.plot_files['sample'].eq(sample).to_numpy()
                for mask, included in [(~selected, False), (selected, True)]:
                    valid = sample_mask & mask & np.isfinite(values)
                    if not valid.any():
                        continue
                    artist = self.axes.scatter(positions[valid], values[valid],
                        facecolors=color if included else 'none', edgecolors=color,
                        alpha=1 if included else .55,
                        label=f'{sample}: {"Selected" if included else "Excluded"}', picker=5)
                    artist.file_positions = positions[valid]
            labels = self.plot_files['sample_name'].str.extract(r'#(\d+)', expand=False).fillna(self.plot_files['sample_name'])
            if self.plot_files['sample'].nunique() > 1:
                labels = self.plot_files['sample'] + ':' + labels
            step = max(1, int(np.ceil(len(positions) / 35)))
            self.axes.set_xticks(positions[::step], labels.iloc[::step], rotation=60, ha='right')
            self.axes.set_ylabel(metric)
            self.axes.set_xlabel('File number (sample:number for multiple samples)')
            self.axes.grid(alpha=.25)
            self.axes.legend()
            missing = int((~np.isfinite(values)).sum())
            if missing:
                self.point_info.configure(text=f'この指標が欠損している {missing} ファイルは点を表示していません。')
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _sample_colors(self):
        samples = sorted(self.files['sample'].unique()) if not self.files.empty else []
        return {sample: self.sample_color_overrides.get(sample, SAMPLE_PALETTE[index % len(SAMPLE_PALETTE)][1])
                for index, sample in enumerate(samples)}

    def _update_color_buttons(self):
        current = self._sample_colors().get(self.color_sample.get())
        for color, button in self.color_buttons.items():
            button.configure(relief='sunken' if color == current else 'raised',
                             text=('✓' if color == current else '') + next(name for name, c in SAMPLE_PALETTE if c == color))

    def _set_sample_color(self, color):
        sample = self.color_sample.get()
        if sample not in self._sample_colors():
            return
        self.sample_color_overrides[sample] = color
        self._update_color_buttons()
        self._draw_plot()

    def _reset_sample_color(self):
        self.sample_color_overrides.pop(self.color_sample.get(), None)
        self._update_color_buttons()
        self._draw_plot()

    def _normalization_changed(self, source):
        if source == 'normalized' and self.hist_normalized.get():
            self.hist_percent.set(False)
        elif source == 'percent' and self.hist_percent.get():
            self.hist_normalized.set(False)
        self._draw_plot()

    def _draw_histogram(self):
        feature = self.hist_feature.get()
        if not self.events.empty and feature in self.events:
            value_range = self.hist_range
            if value_range is None:
                values = pd.to_numeric(self.events[feature], errors='coerce').to_numpy(dtype=float)
                finite = values[np.isfinite(values)]
                value_range = (0., max(float(finite.max()), 1.) if len(finite) else 1.)
            edges, _, _, total, chosen = event_histogram(
                self.events, self.subset, feature, bins=int(self.hist_bins.get()), value_range=value_range)
            self.figure.clear()
            samples = list(self.events['sample'].unique())
            colors = self._sample_colors()
            overlay = self.hist_layout.get() == '同じ図に重ねる'
            show_all = self.hist_population.get() != 'Selected のみ'
            show_selected = self.hist_population.get() != 'All のみ'
            group_count = int(show_all) + int(show_selected)
            columns = min(2, len(samples))
            rows = int(np.ceil(len(samples) / columns))
            grid = self.figure.add_gridspec(1 if overlay else rows, 2 if overlay else columns * 2,
                                           width_ratios=[3, 2] if overlay else [3, 2] * columns)
            shared_ax = self.figure.add_subplot(grid[0, 0]) if overlay else None
            shared_info = self.figure.add_subplot(grid[0, 1]) if overlay else None
            if shared_info is not None:
                shared_info.axis('off')
            for index, sample in enumerate(samples):
                all_events = self.events.loc[self.events['sample'].eq(sample)]
                subset = self.subset.loc[self.subset['sample'].eq(sample)]
                _, all_counts, chosen_counts, n_all, n_chosen = event_histogram(
                    all_events, subset, feature, bins=edges, percent=self.hist_percent.get(),
                    normalize_to=10000 if self.hist_normalized.get() else None)
                color = colors[sample]
                ax = shared_ax if overlay else self.figure.add_subplot(grid[index // columns, (index % columns) * 2])
                info_ax = shared_info if overlay else self.figure.add_subplot(grid[index // columns, (index % columns) * 2 + 1])
                info_ax.axis('off')
                ax.set_title('Samples' if overlay else f'Sample {sample}')
                if show_all:
                    ax.stairs(all_counts, edges, color=color, linestyle='--', linewidth=2,
                              label=f'{sample}: All (n={n_all:,})')
                if show_selected:
                    ax.stairs(chosen_counts, edges, color=color, linewidth=2.5,
                              label=f'{sample}: Selected (n={n_chosen:,})')
                ax.set_xlim(edges[0], edges[-1])
                ax.set_xlabel(feature)
                ax.set_ylabel('Events (normalized to 10,000)' if self.hist_normalized.get()
                              else 'Events (%)' if self.hist_percent.get() else 'Event count')
                ax.grid(alpha=.25)
                info_ax.legend(*ax.get_legend_handles_labels(), fontsize=10, loc='upper left', framealpha=1)
                groups = []
                if self.show_markers.get():
                    if show_all:
                        groups.append(('All', all_events, color, .97))
                    if show_selected:
                        groups.append(('Selected', subset, color, .83))
                for group_index, (group, frame, color, y) in enumerate(groups):
                    markers = histogram_markers(frame, feature, edges)
                    lines = []
                    styles = {'Mean': '-', 'Mode*': ':', 'Q1': '--', 'Median': '-.', 'Q3': '--'}
                    for name, value in markers.items():
                        visible = np.isfinite(value) and edges[0] <= value <= edges[-1]
                        if visible:
                            ax.axvline(value, color=color, linestyle=styles[name], linewidth=1.5,
                                       alpha=1)
                        lines.append(f'{name}={value:.5g}' + (' (outside)' if np.isfinite(value) and not visible else ''))
                    y = (.74 - (index * group_count + group_index) * min(.17, .70 / (len(samples) * group_count))) if overlay else .76 - group_index * .36
                    info_ax.text(.02, y, f'{sample} {group}\n' + ('\n'.join(lines) if lines else 'No valid events'),
                            transform=info_ax.transAxes, va='top', fontsize=11, color='#111111',
                            fontweight='bold',
                            bbox=dict(facecolor='white', edgecolor=color, linewidth=1.5, alpha=1, pad=4))
                if self.drag_range.get() and (not overlay or index == 0):
                    selector = SpanSelector(ax, self._drag_hist_range, 'horizontal',
                                            useblit=True, props=dict(alpha=.25, facecolor='#2563eb'))
                    self.range_selectors.append(selector)
            self.point_info.configure(text=(
                f'全ファイル：{total:,} 件 / 選択ファイル：{chosen:,} 件（有限のイベント値のみ）。'
                'サンプル別に共通のビン境界で比較。範囲外の値は表示しません。'
                ' 平均・四分位は全有効イベントから算出。Mode*は表示範囲の最多ビン中心（同数なら左側）。'
                + (' 割合の分母は各サンプル・各集団の全有効イベント数です。' if self.hist_percent.get() else '')))
            if self.hist_normalized.get():
                self.point_info.configure(text='各サンプル・全体／選択それぞれの全有効イベント数を10,000に正規化。範囲外も分母に含むため、表示範囲内の合計は10,000未満になる場合があります。')
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _apply_hist_range(self):
        try:
            low, high = float(self.range_min.get()), float(self.range_max.get())
            if not np.isfinite([low, high]).all() or low >= high:
                raise ValueError()
        except ValueError:
            messagebox.showerror('範囲エラー', '有限の数値で、下限 < 上限となる範囲を入力してください。')
            return
        self.hist_range = (low, high)
        self._draw_plot()

    def _reset_hist_range(self):
        self.hist_range = None
        self.range_min.set('0')
        self.range_max.set('')
        self._draw_plot()

    def _drag_hist_range(self, low, high):
        if low >= high or self.toolbar.mode:
            return
        self.range_min.set(f'{low:.10g}')
        self.range_max.set(f'{high:.10g}')
        self.hist_range = (low, high)
        # Rebuild selectors after Matplotlib finishes dispatching the mouse event.
        self.after_idle(self._draw_plot)

    def _pick_file(self, event):
        if not hasattr(event.artist, 'file_positions') or not len(event.ind):
            return
        row = self.plot_files.iloc[event.artist.file_positions[event.ind[0]]]
        state = '選択' if row.name in self.filtered.index else '対象外'
        self.point_info.configure(text=f'{state} | {row["sample_name"]} | sample={row["sample"]} | ex_id={row["ex_id"]} | {self.metric.get()}={row[self.metric.get()]:g}')

    def _export(self, frame):
        if frame.empty:
            return
        path = filedialog.asksaveasfilename(defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if path:
            try:
                frame.to_csv(path, index=False, encoding='utf-8-sig')
            except OSError as exc:
                messagebox.showerror('保存エラー', str(exc))
