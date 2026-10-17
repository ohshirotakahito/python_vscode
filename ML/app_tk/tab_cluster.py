"""Unsupervised learning using the same version/file selection as inspection."""
import tkinter as tk
from tkinter import ttk, messagebox

from tab_review import ReviewTab
from widgets import CheckList, ProgressPanel, DataTable, ImageGallery, ScrollableFrame, open_path


class ClusterTab(ReviewTab):
    def __init__(self, master, runner):
        super().__init__(master, runner, workspace_tabs=True)
        self.hist_layout.set('同じ図に重ねる')
        self.hist_range = (0.0, 50.0)
        self.range_min.set('0')
        self.range_max.set('50')
        self.train_button.configure(text='この条件で教師なし学習・可視化', command=self._open_training)
        self.cluster_window = None
        ttk.Label(self.learning_page, text='「データ精査・絞り込み」でデータを読み込んでから、'
                  '上部の「③ クラスタリング設定・実行」を押してください。', padding=20).pack(anchor='w')
        toolbar = ttk.LabelFrame(self, text='教師なしクラスタリング', padding=8)
        toolbar.pack(fill='x', before=self.workspace_tabs, pady=(0, 8))
        ttk.Label(toolbar, text='① 下でサンプル・抽出版を選択 → ② データを読み込む → '
                  '③ クラスタリング設定・実行\nK-means / 階層法 / Gaussian Mixture / DBSCAN：'
                  'クラスタ数の自動推奨・手動変更、PCA散布図、評価値を確認できます。',
                  wraplength=1100).pack(side='left', fill='x', expand=True)
        self.cluster_load_button = ttk.Button(toolbar, text='② データを読み込む', command=self._load)
        self.cluster_load_button.pack(side='left', padx=6)
        self.cluster_run_button = ttk.Button(toolbar, text='③ クラスタリング設定・実行', command=self._open_training)
        self.cluster_run_button.pack(side='left')
        self.load_button.configure(text='データ・ファイル統計を読み込む')
        # Keep the learning entry point visible even when the filter panel exceeds the screen height.
        self.train_button.pack_forget()
        def busy_changed(busy):
            for button in (self.cluster_load_button, self.cluster_run_button):
                button.configure(state='disabled' if busy else 'normal')
        runner.add_busy_listener(busy_changed)

    def _open_training(self):
        from common.file_review import selected_events
        from common.clustering import feature_columns, ALGORITHMS
        if self.events.empty or self.filtered.empty:
            messagebox.showinfo('データ選択', '統計を読み込み、対象ファイルを選んでください。')
            return
        if self.runner.busy:
            messagebox.showinfo('実行中', '処理の完了後に対象データを更新してください。')
            return
        # Freeze the loaded data and rules: later changes in the parent cannot alter a run.
        events = selected_events(self.events, self.filtered).copy()
        selection = self.loaded_selection
        rules = list(self.rules)
        for child in self.learning_page.winfo_children():
            child.destroy()
        window = self.cluster_window = self.learning_page
        self.workspace_tabs.select(self.learning_page)
        settings = ttk.Frame(window, padding=8)
        settings.pack(side='left', fill='y')
        actions = ttk.Frame(settings)
        actions.pack(fill='x', pady=(0, 8))
        # Reserve action space before allocating the remaining height to settings.
        ttk.Button(actions, text='実行 / 設定を変えて再実行',
                   command=lambda: run()).pack(fill='x', pady=(0, 4))
        ttk.Button(actions, text='保存フォルダを開く',
                   command=lambda: open_path(output[0]) if output[0] else None).pack(fill='x')
        scroll = ScrollableFrame(settings)
        scroll.canvas.configure(width=340)
        scroll.pack(fill='both', expand=True)
        options = scroll.inner
        right = ttk.Frame(window, padding=8)
        right.pack(side='right', fill='both', expand=True)
        ttk.Label(options, text=f'対象: {len(events):,} イベント\n読み込み済みの版・絞り込みを固定', wraplength=320).pack(anchor='w')
        ttk.Label(options, text='使用する特徴量（複数選択）').pack(anchor='w')
        features = CheckList(options, height=8)
        features.pack(fill='x')
        columns = feature_columns(events)
        features.set_items(columns)
        # Start with physical event properties, avoiding duplicated signal representations.
        for i, name in enumerate(columns):
            if name in ('signal', 'duration') or name.startswith(('wave_', 'embedding_')):
                features.listbox.selection_set(i)
        variables = {}

        def field(label, key, value, choices=None):
            ttk.Label(options, text=label).pack(anchor='w', pady=(3, 0))
            variable = variables[key] = tk.StringVar(value=str(value))
            widget = (ttk.Combobox(options, textvariable=variable, values=choices, state='readonly')
                      if choices else ttk.Entry(options, textvariable=variable))
            widget.pack(fill='x')
            return widget

        field('アルゴリズム', 'algorithm', ALGORITHMS[0], ALGORITHMS)
        field('クラスタ数：auto または整数（DBSCANでは無効）', 'k', 'auto')
        field('自動探索の最大数（GMMは1、他は2から）', 'max_k', 10)
        field('標準化：standard / 外れ値に頑健：robust / なし', 'scaling', 'standard', ['standard', 'robust', 'none'])
        field('欠損値：中央値補完 median / 行除外 drop', 'missing', 'median', ['median', 'drop'])
        field('DBSCAN eps（前処理後の距離）', 'eps', .5)
        field('DBSCAN min_samples', 'min_samples', 5)
        field('解析イベント上限（超過時は固定乱数で抽出）', 'max_events', 5000)
        ttk.Label(options, text='階層法・DBSCANは大量データでメモリを使います。\n推奨数は探索範囲・特徴量・前処理に依存します。\nPCAは表示専用。学習には選択した特徴量を使用。', wraplength=320).pack(anchor='w', pady=5)
        panel = ProgressPanel(right, log_height=3)
        panel.pack(fill='x')
        note = ttk.Label(right, wraplength=750)
        note.pack(fill='x', pady=5)
        table = DataTable(right, height=7)
        table.pack(fill='x')
        gallery = ImageGallery(right, columns=1)
        gallery.pack(fill='both', expand=True)
        output = [None]

        def show(payload):
            directory, scores, manifest = payload
            if not window.winfo_exists():
                return
            output[0] = directory
            note.configure(text=manifest['note'] + f"\n解析 {manifest['analyzed_events']:,} / 対象 {manifest['input_events']:,} 件。シルエット評価は最大2000件。\n保存先: {directory}")
            table.show(scores)
            gallery.show([directory / 'clustering.png', directory / 'feature_distributions.png'])

        def run():
            try:
                args = {key: var.get().strip() for key, var in variables.items()}
                args['k'] = None if args['k'] == 'auto' else int(args['k'])
                for key in ('max_k', 'min_samples', 'max_events'):
                    args[key] = int(args[key])
                args['eps'] = float(args['eps'])
                chosen = features.get_selected()
                if not chosen:
                    raise ValueError('特徴量を1つ以上選択してください。')
            except ValueError as exc:
                messagebox.showerror('設定エラー', str(exc), parent=window)
                return

            def task(progress):
                from common.clustering import analyze, save_result
                from common.paths import PROJECT_ROOT
                result = analyze(events, chosen, progress=progress, **args)
                result[2]['source_selection'] = selection
                result[2]['file_rules'] = rules
                directory = save_result(result, PROJECT_ROOT / 'cluster_results')
                return directory, result[1], result[2]
            self.runner.run(task, panel, on_done=show)

