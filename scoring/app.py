# -*- coding: utf-8 -*-
"""Scoring App: EXSV / AC / Gap Generation（Auto GG）スコアの一覧・推移・再計算と、
Experimental ID で計画 → 計測 → 転送 → ANAL をつなぐ台帳（Tkinter）。

    python scoring/app.py        （ランチャー launch_apps.bat からも起動できる）

- ① 実験: 1 実験 × 転送先サンプルの台帳（計画・転送・ANAL・スコアのまとめ）。行を選ぶと下にその実験の全スコア
- ② スコア: EXSV / AC / Auto GG の推移（各 run の点、20 run の移動平均、全体の平均）と一覧。計測時と再構成を色で分ける
- ③ 再計算: SQserver の一覧から実験を選び、AXN と同じ関数でスコアを再構成して D:\\Qstatus\\database\\Scores に保存。
  計測時の Score.csv の取り込みもここ。SQserver 上のファイルは読むだけ
"""
import queue
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # リポジトリ直下（shared/ を読むため）

from shared import ui_theme  # noqa: E402
from scoring import axn_bridge, experiment_ledger, machine_data, report_history, rescore, score_store  # noqa: E402
from scoring.score_store import MEASURED, RECONSTRUCTED  # noqa: E402

try:
    import logging
    import matplotlib
    matplotlib.use('TkAgg')
    logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)   # 無いフォントの警告は出さない
    from matplotlib import font_manager
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    _fonts = {f.name for f in font_manager.fontManager.ttflist}
    _jp = next((n for n in ('Yu Gothic', 'Meiryo', 'MS Gothic', 'MS UI Gothic', 'Yu Gothic UI') if n in _fonts), None)
    if _jp:                              # 日本語の軸ラベル・凡例のため（描画時に効くので rcParams に入れる）
        matplotlib.rcParams['font.family'] = [_jp, 'sans-serif']
except ImportError:                      # グラフなしでも一覧と再計算は使える
    Figure = None

ACCENT = ui_theme.SCORING
MOVING_RUNS = 20
ERA_COLOURS = {score_store.ERA_PYTHON: '#5b9dff', score_store.ERA_LABVIEW: '#f0a860',
               score_store.ERA_MIXED: '#b68cf5', score_store.ERA_UNKNOWN: '#8b9a92'}
ALL = 'すべて'
TABLE_LIMIT = 3000          # 表に出す行の上限（グラフは全部）
LEDGER_SHOW = ('machine', 'name', 'experiment_at', 'era', 'plan', 'transfer', 'destination', 'anal_state',
               'exsv_count', 'exsv_mean', 'exsv_max', 'ac_last', 'gg_score', 'gg_missing', 'score_source', 'location')
# 実験一覧の列幅（px、利用者の指定で固定。ドラッグでは変えられない）
LEDGER_WIDTH = {'machine': 58, 'name': 300, 'experiment_at': 120, 'era': 97, 'plan': 156, 'transfer': 72,
                'destination': 162, 'anal_state': 85, 'exsv_count': 90, 'exsv_mean': 90, 'exsv_max': 90,
                'ac_last': 100, 'gg_score': 97, 'gg_missing': 108, 'score_source': 74, 'location': 163}
KIND_TITLES = {'EXSV': 'EXSV', 'AC': 'AC', 'AutoGG': 'Gap Generation (Auto GG)'}
KIND_SHOW = {
    'EXSV': ('Source', 'Machine', 'Experimental ID', 'EXSV#', 'EXSV Time', 'EXSV Score', 'EXSV Score Detail',
             'EXSV Set Distance [nm]', 'EXSV Total Signal # [n]', 'Era', 'Criteria'),
    'AC': ('Source', 'Machine', 'Experimental ID', 'AC#', 'AC Time', 'AC QC', 'AC Score', 'AC Score Detail',
           'AC Score End I [pA]', 'Era', 'Criteria'),
    'AutoGG': ('Source', 'Machine', 'Experimental ID', 'Round', 'Round start', 'Round end', 'AutoGG Score',
               'Stages scored', 'Stages missing', 'FC score', 'MA score', 'TA score', 'AC score', 'CAL score',
               'Decision', 'Era', 'Criteria'),
}


def score_tag(value):
    score = score_store.number(value)
    if score is None:
        return ''
    return 'great' if score >= 90 else 'good' if score >= 80 else 'fair' if score >= 70 else ''


class Table(ttk.Frame):
    """スクロールバー付きの Treeview。"""

    def __init__(self, master, columns, titles, widths=None, height=12, fixed=False):
        super().__init__(master)
        self.tree = ttk.Treeview(self, columns=columns, show='headings', height=height, selectmode='extended')
        for col, title in zip(columns, titles):
            self.tree.heading(col, text=title, command=lambda c=col: self.sort_by(c))
            self.tree.column(col, width=(widths or {}).get(col, 110), stretch=False, anchor='w')
        if fixed:                                    # 見出しの境目のドラッグで列幅を変えさせない
            self.tree.bind('<Button-1>', self._block_resize, add=True)
            self.tree.bind('<Motion>', self._block_resize, add=True)
        self.tree.tag_configure('even', background=ui_theme.STRIPE)
        self.tree.tag_configure('great', background=ui_theme.row('success', 0.45))
        self.tree.tag_configure('good', background=ui_theme.row('success', 0.28))
        self.tree.tag_configure('fair', background=ui_theme.row('success', 0.14))
        self.tree.tag_configure('warn', background=ui_theme.row('warning', 0.2))
        vertical = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.columns = columns
        self._reverse = {}

    def _block_resize(self, event):
        if self.tree.identify_region(event.x, event.y) == 'separator':
            return 'break'

    def fill(self, rows, tag_of=None, limit=None):
        """rows を表示する。limit を渡すと先頭（新しい順）からその数だけ（多すぎると Tk が重くなるため）。"""
        self.tree.delete(*self.tree.get_children())
        if limit is not None:
            rows = rows[:limit]
        for i, row in enumerate(rows):
            tags = [tag_of(row)] if tag_of else []
            if i % 2:
                tags.append('even')
            self.tree.insert('', 'end', iid=str(i), values=[row.get(c, '') for c in self.columns],
                             tags=[t for t in tags if t])
        self.rows = rows

    def sort_by(self, column):
        reverse = self._reverse.get(column, False)

        def key(item):
            value = self.tree.set(item, column)
            number = score_store.number(value)
            return (0, number, '') if number is not None else (1, 0., value)
        items = sorted(self.tree.get_children(''), key=key, reverse=reverse)
        for position, item in enumerate(items):
            self.tree.move(item, '', position)
        self._reverse[column] = not reverse

    def selected_rows(self):
        return [self.rows[int(i)] for i in self.tree.selection()]


def one_per_run(rows, kind):
    """同じ回に計測時と再構成の両方があれば再構成だけを残す（グラフで同じ回を 2 点にしないため）。"""
    spec = score_store.KINDS[kind]
    if spec.number_column is None or kind == 'AutoGG':
        return rows
    key = lambda r: (r.get('Experimental ID', ''), r.get(spec.number_column, ''), r.get(spec.time_column, ''))  # noqa: E731
    rebuilt = {key(r) for r in rows if r.get('Source') == RECONSTRUCTED}
    return [r for r in rows if r.get('Source') == RECONSTRUCTED or key(r) not in rebuilt]


class Chart(ttk.Frame):
    """スコアの推移: 各 run の点（時代で色分け）、移動平均、全体の平均。"""

    def __init__(self, master, title):
        super().__init__(master)
        self.title = title
        if Figure is None:
            ttk.Label(self, text='matplotlib がないためグラフは表示できません').pack()
            return
        self.figure = Figure(figsize=(8, 2.6), dpi=96, facecolor=ui_theme.PANEL)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

    def draw(self, rows, score_column, time_column):
        if Figure is None:
            return
        ax = self.ax
        ax.clear()
        ax.set_facecolor(ui_theme.FIELD)
        for spine in ax.spines.values():
            spine.set_color(ui_theme.BORDER)
        ax.tick_params(colors=ui_theme.MUTED, labelsize=8)
        ordered = sorted(rows, key=lambda r: (r.get(time_column, ''), r.get('Experimental ID', '')))
        points = [(i, score_store.number(r.get(score_column)), r.get('Era') or score_store.ERA_UNKNOWN)
                  for i, r in enumerate(ordered)]
        points = [(i, s, era) for i, s, era in points if s is not None]
        for era, colour in ERA_COLOURS.items():
            xs = [i for i, _, e in points if e == era]
            ys = [s for _, s, e in points if e == era]
            if xs:
                ax.plot(xs, ys, 'o', ms=3, color=colour, alpha=0.75, label=f'{era} ({len(xs)})')
        if len(points) >= 2:
            ys = [s for _, s, _ in points]
            moving = [sum(ys[max(0, k - MOVING_RUNS + 1):k + 1]) / len(ys[max(0, k - MOVING_RUNS + 1):k + 1])
                      for k in range(len(ys))]
            ax.plot([i for i, _, _ in points], moving, color='#dfe8ff', lw=1.6, label=f'{MOVING_RUNS} run の移動平均')
            mean = sum(ys) / len(ys)
            ax.axhline(mean, color='#f08c6c', ls='--', lw=1.2, label=f'平均 {mean:.1f}')
        ax.set_ylim(0, 100)
        ax.set_xlabel('run (古い順)', color=ui_theme.MUTED, fontsize=8)
        ax.set_title(f'{self.title} Score', color=ui_theme.TEXT, fontsize=10, loc='left')
        if points:
            legend = ax.legend(loc='lower right', fontsize=8, facecolor=ui_theme.PANEL, edgecolor=ui_theme.BORDER)
            for text in legend.get_texts():
                text.set_color(ui_theme.TEXT)
        self.figure.tight_layout()
        self.canvas.draw_idle()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Scoring · スコア')
        from shared.app_release import configure_tk
        configure_tk(self, 'scoring')
        self.geometry('1480x900')
        self.minsize(1100, 700)
        ui_theme.apply(self, ACCENT)
        ui_theme.header(self, 'Scoring · スコア', 'EXSV / AC / Gap Generation スコアの一覧・再計算と、計画 → 計測 → 解析のつながり',
                        '\uE9D9', ACCENT, on_close=self.destroy).pack(fill='x')
        self.folder = score_store.default_dir()
        self.machines = machine_data.measurement_machines() or [f'AXN{n}' for n in range(1, 9)]
        self.scores = {kind: [] for kind in ('AC', 'EXSV', 'AutoGG')}
        self.ledger_rows = []
        self.inventory = {}
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._busy = False
        self._build()
        self.after(150, self._poll)
        self.after(50, self.reload)

    # --- 画面 ---
    def _build(self):
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(fill='x')
        ttk.Label(bar, text='装置').pack(side='left')
        self.machine_var = tk.StringVar(value='すべて')
        box = ttk.Combobox(bar, textvariable=self.machine_var, values=['すべて'] + self.machines, state='readonly', width=9)
        box.pack(side='left', padx=(4, 12))
        box.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        ttk.Label(bar, text='時代').pack(side='left')
        self.era_var = tk.StringVar(value=ALL)
        era_box = ttk.Combobox(bar, textvariable=self.era_var, values=[ALL] + list(score_store.ERAS), state='readonly', width=8)
        era_box.pack(side='left', padx=(4, 12))
        era_box.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        ttk.Label(bar, text='検索').pack(side='left')
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.filter_var, width=32)
        entry.pack(side='left', padx=(4, 12))
        entry.bind('<Return>', lambda e: self.refresh())
        ttk.Button(bar, text='絞り込む', command=self.refresh).pack(side='left')
        ttk.Button(bar, text='再読込', command=self.reload).pack(side='left', padx=(8, 0))
        ttk.Button(bar, text='台帳 CSV を書き出す', style='Accent.TButton', command=self.export_ledger).pack(side='left', padx=(8, 0))
        ttk.Label(bar, text=f'保存先: {self.folder}', foreground=ui_theme.MUTED).pack(side='right')

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=(0, 6))
        self._build_ledger_tab()
        self._build_score_tab()
        self._build_rescore_tab()
        self.status = tk.StringVar(value='読み込み中…')
        ttk.Label(self, textvariable=self.status, style='Status.TLabel', padding=(10, 4)).pack(fill='x')

    def _build_ledger_tab(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text='① 実験ID（計画 → 計測 → 転送 → ANAL → スコア）')
        ttk.Label(tab, text='1 行 = 1 実験ID × 転送先の 1 サンプル（sq_transfers.csv と同じ単位）。スコアは再構成があればそれを、なければ計測時の Score.csv。'
                  '行を選ぶと下にその実験IDの全スコアを出します。').pack(anchor='w')
        titles = [experiment_ledger.TITLE_OF[c] for c in LEDGER_SHOW]
        self.ledger_table = Table(tab, LEDGER_SHOW, titles, LEDGER_WIDTH, height=18, fixed=True)
        self.ledger_table.pack(fill='both', expand=True, pady=(6, 4))
        self.ledger_table.tree.bind('<<TreeviewSelect>>', lambda e: self.show_experiment())
        self.reports_button = ttk.Button(tab, text='実行結果', command=self.show_reports, state='disabled')
        self.reports_button.pack(anchor='w', pady=(0, 4))
        self.detail = tk.Text(tab, height=10, wrap='none', font=(ui_theme.MONO, 9))
        ui_theme.style_text(self.detail)
        self.detail.pack(fill='x')

    def _build_score_tab(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text='② スコア（EXSV / AC / Auto GG）')
        inner = ttk.Notebook(tab)
        inner.pack(fill='both', expand=True)
        self.charts, self.score_tables = {}, {}
        for kind in ('EXSV', 'AC', 'AutoGG'):
            page = ttk.Frame(inner, padding=6)
            inner.add(page, text=KIND_TITLES[kind])
            self.charts[kind] = Chart(page, KIND_TITLES[kind])
            self.charts[kind].pack(fill='x')
            columns = KIND_SHOW[kind]
            widths = {'Experimental ID': 280, 'EXSV Score Detail': 420, 'AC Score Detail': 420, 'Criteria': 260,
                      'Stages missing': 110, 'Source': 70, 'Machine': 60}
            self.score_tables[kind] = Table(page, columns, columns, widths, height=14)
            self.score_tables[kind].pack(fill='both', expand=True, pady=(6, 0))

    def _build_rescore_tab(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text='③ 再計算（SQserver のログから）')
        ttk.Label(tab, text='SQserver の一覧（Upload）にある、左上で選んだ装置の実験ID。AXN と同じ関数で AC・EXSV・Gap Generation（1 回分 = FC・MA・TA・AC・CAL）'
                  'のスコアを再構成し、保存先に書きます（SQserver は読むだけ）。装置を「すべて」以外にしてください。',
                  wraplength=1400).pack(anchor='w')
        columns = ('name', 'experiment_at', 'location', 'measured', 'reconstructed')
        titles = ('Experimental ID', '実験日時', 'SQserver', '計測時のスコア', '再構成')
        self.rescore_table = Table(tab, columns, titles, {'name': 320, 'experiment_at': 120, 'location': 170,
                                                           'measured': 150, 'reconstructed': 150}, height=14)
        self.rescore_table.pack(fill='both', expand=True, pady=(6, 4))
        buttons = ttk.Frame(tab)
        buttons.pack(fill='x')
        self.run_button = ttk.Button(buttons, text='選択した実験IDを再計算', style='Execute.TButton', command=self.rescore_selected)
        self.run_button.pack(side='left')
        self.run_all_button = ttk.Button(buttons, text='未計算の実験IDをすべて再計算', style='Conditional.TButton',
                                         command=self.rescore_all)
        self.run_all_button.pack(side='left', padx=(8, 0))
        self.import_button = ttk.Button(buttons, text='計測時の Score.csv を取り込む', command=self.import_measured)
        self.import_button.pack(side='left', padx=(8, 0))
        self.stop_button = ttk.Button(buttons, text='中止', command=self._stop.set, state='disabled')
        self.stop_button.pack(side='left', padx=(8, 0))
        self.progress = ttk.Progressbar(buttons, length=260, mode='determinate')
        self.progress.pack(side='right')
        self.log = tk.Text(tab, height=9, wrap='none', font=(ui_theme.MONO, 9))
        ui_theme.style_text(self.log)
        self.log.pack(fill='x', pady=(6, 0))

    # --- 読み込みと表示 ---
    def reload(self):
        try:
            self.scores = experiment_ledger.load_scores(self.folder)
            self.inventory = {m: machine_data.inventory(m) for m in self.machines}
            self.ledger_rows = experiment_ledger.build(self.scores, inventories=self.inventory, machines=self.machines)
        except Exception as exc:  # noqa: BLE001 - 共有フォルダが無いなどはそのまま見せる
            messagebox.showerror('読み込みエラー', f'{type(exc).__name__}: {exc}', parent=self)
            return
        self.refresh()

    def _match(self, text, *values):
        text = text.strip().casefold()
        return not text or any(text in str(v).casefold() for v in values)

    def refresh(self):
        machine, era, text = self.machine_var.get(), self.era_var.get(), self.filter_var.get()
        rows = [r for r in self.ledger_rows if (machine == ALL or r['machine'] == machine)
                and (era == ALL or r.get('era') == era) and self._match(text, *r.values())]
        # GG の総合点で緑の濃さ。転送まで済んでいるのにスコアが 1 つも無い実験は橙（再計算の候補）
        self.ledger_table.fill(rows, lambda r: score_tag(r.get('gg_score'))
                               or ('warn' if not r.get('score_source') and r.get('transfer', '').startswith('転送済み') else ''),
                               limit=TABLE_LIMIT)
        clipped = []
        for kind in ('EXSV', 'AC', 'AutoGG'):
            score_rows = [r for r in self.scores[kind] if (machine == ALL or r.get('Machine') == machine)
                          and (era == ALL or r.get('Era') == era) and self._match(text, *r.values())]
            column = score_store.SCORE_COLUMN[kind]
            self.score_tables[kind].fill(score_rows, lambda r, c=column: score_tag(r.get(c)), limit=TABLE_LIMIT)
            self.charts[kind].draw(one_per_run(score_rows, kind), column, score_store.KINDS[kind].time_column)
            if len(score_rows) > TABLE_LIMIT:
                clipped.append(f'{kind} {len(score_rows)}')
        if len(rows) > TABLE_LIMIT:
            clipped.append(f'台帳 {len(rows)}')
        self._fill_rescore_table()
        counts = ', '.join(f'{k} {len(v)}' for k, v in self.scores.items())
        note = (f'表は新しい {TABLE_LIMIT} 行まで（{"・".join(clipped)} 行あり。装置・時代・検索で絞ってください）。'
                if clipped else '')
        self.status.set(f'{note}台帳 {len(rows)} 行を表示（全 {len(self.ledger_rows)} 行）。保存済みスコア: {counts}。'
                        f'AXN: {axn_bridge.axn_root() or "見つからない"}')

    def _fill_rescore_table(self):
        machine = self.machine_var.get()
        if machine == 'すべて':
            self.rescore_table.fill([])
            return
        have = {}
        for kind, rows in self.scores.items():
            for r in rows:
                if r.get('Machine') == machine:
                    have.setdefault((r['Experimental ID'], r.get('Source', '')), set()).add(kind)
        rows = []
        for name, place in sorted(self.inventory.get(machine, {}).items(), reverse=True):
            when = machine_data.experiment_time(name)
            rows.append({'name': name, 'experiment_at': when.strftime('%Y-%m-%d %H:%M') if when else '', 'location': place,
                         'measured': '・'.join(sorted(have.get((name, MEASURED), ()))),
                         'reconstructed': '・'.join(sorted(have.get((name, RECONSTRUCTED), ())))})
        text = self.filter_var.get()
        self.rescore_table.fill([r for r in rows if self._match(text, r['name'])],
                                lambda r: 'good' if r['reconstructed'] else '')

    def show_experiment(self):
        rows = self.ledger_table.selected_rows()
        self.reports_button.configure(state='normal' if rows else 'disabled')
        self.detail.delete('1.0', 'end')
        if not rows:
            return
        row = rows[0]
        lines = [f'{experiment_ledger.TITLE_OF[c]}: {row[c]}' for c in experiment_ledger.COLUMNS if row.get(c)]
        for kind in ('AutoGG', 'AC', 'EXSV'):
            kind_rows = [r for r in self.scores[kind] if r.get('Experimental ID') == row['name']]
            if not kind_rows:
                continue
            lines.append('')
            lines.append(f'[{KIND_TITLES[kind]}]')
            for r in sorted(kind_rows, key=lambda r: (r.get(score_store.KINDS[kind].time_column, ''), r.get('Source', ''))):
                if kind == 'AutoGG':
                    stages = ' '.join(f"{s} {r.get(f'{s} score') or '-'}" for s in machine_data.STAGES)
                    lines.append(f"  {r.get('Source')}  {r.get('Round start')} → {r.get('Round end') or '(EXSV なし)'}  "
                                 f"総合 {r.get('AutoGG Score') or '-'} ({r.get('Stages scored')})  {stages}  "
                                 f"未実施: {r.get('Stages missing') or 'なし'}  [{r.get('Era')}] {r.get('Criteria')}")
                else:
                    n = score_store.KINDS[kind].number_column
                    lines.append(f"  {r.get('Source')}  {kind}#{r.get(n)} {r.get(score_store.KINDS[kind].time_column)}  "
                                 f"{r.get(score_store.SCORE_COLUMN[kind])}  {r.get(f'{kind} Score Detail', '')}  "
                                 f"[{r.get('Era')}] {r.get('Criteria')}")
        self.detail.insert('1.0', '\n'.join(lines))

    def show_reports(self):
        rows = self.ledger_table.selected_rows()
        if not rows:
            return
        machine, name = rows[0]['machine'], rows[0]['name']
        window = tk.Toplevel(self)
        window.title('実行結果')
        window.transient(self)
        window.geometry('960x620')
        body = ttk.Frame(window, padding=10)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f'{machine}  {name}').pack(anchor='w')
        status = ttk.Label(body, text='実行結果を読み込み中…')
        status.pack(anchor='w', pady=(4, 6))
        columns = ('when', 'operation', 'summary')
        table = Table(body, columns, ('時刻', '操作', '結果'),
                      dict(zip(columns, (155, 120, 620))), height=9)
        table.tree.configure(selectmode='browse')
        table.pack(fill='both', expand=True)
        ttk.Label(body, text='詳細情報').pack(anchor='w', pady=(6, 2))
        detail_frame = ttk.Frame(body)
        detail_frame.pack(fill='both', expand=True)
        details = tk.Text(detail_frame, height=12, wrap='char', state='disabled', font=(ui_theme.MONO, 9))
        ui_theme.style_text(details)
        scrollbar = ttk.Scrollbar(detail_frame, orient='vertical', command=details.yview)
        details.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        details.pack(side='left', fill='both', expand=True)
        ttk.Button(body, text='閉じる', command=window.destroy).pack(anchor='e', pady=(6, 0))

        def show_details(_event=None):
            selected = table.selected_rows()
            details.configure(state='normal')
            details.delete('1.0', 'end')
            if selected:
                details.insert('1.0', selected[0]['details'])
            details.configure(state='disabled')

        table.tree.bind('<<TreeviewSelect>>', show_details)
        results = queue.Queue()

        def worker():
            try:
                results.put(report_history.load(name, machine))
            except Exception as exc:
                results.put(exc)

        def poll():
            if not window.winfo_exists():
                return
            try:
                result = results.get_nowait()
            except queue.Empty:
                self.after(75, poll)
                return
            if isinstance(result, Exception):
                status.configure(text=f'実行結果を読み込めませんでした。 {result}')
                return
            path, reports = result
            if not reports:
                status.configure(text='保存された実行結果がありません。' if path else '実験IDの保存先が見つかりません。')
                return
            status.configure(text=path)
            table.fill([vars(report) for report in reports])
            table.tree.selection_set(table.tree.get_children()[0])

        threading.Thread(target=worker, daemon=True).start()
        self.after(75, poll)

    def export_ledger(self):
        initial = experiment_ledger.ledger_path(self.folder)
        target = filedialog.asksaveasfilename(parent=self, initialdir=str(initial.parent), initialfile=initial.name,
                                              defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not target:
            return
        try:
            score_store.write_csv(Path(target), experiment_ledger.TITLES,
                                  [{experiment_ledger.TITLE_OF[k]: v for k, v in r.items()} for r in self.ledger_rows])
        except OSError as exc:
            messagebox.showerror('書き出せません', str(exc), parent=self)
            return
        self.status.set(f'台帳を書き出しました: {target}')

    # --- 再計算（作業スレッド） ---
    def _set_busy(self, busy):
        self._busy = busy
        state = 'disabled' if busy else 'normal'
        for button in (self.run_button, self.run_all_button, self.import_button):
            button.configure(state=state)
        self.stop_button.configure(state='normal' if busy else 'disabled')

    def _log(self, text):
        self.log.insert('end', text + '\n')
        self.log.see('end')

    def _start(self, func):
        if self._busy:
            messagebox.showwarning('実行中', '別の処理が実行中です。終わるまでお待ちください。', parent=self)
            return
        self._stop.clear()
        self._set_busy(True)
        self.progress.configure(value=0, maximum=1)

        def worker():
            try:
                func(lambda i, n, msg: self._queue.put(('progress', (i, n, msg))), lambda t: self._queue.put(('log', t)))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(('error', (exc, traceback.format_exc())))
            else:
                self._queue.put(('done', None))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == 'log':
                    self._log(payload)
                elif kind == 'progress':
                    i, n, message = payload
                    self.progress.configure(value=i, maximum=max(n, 1))
                    self.status.set(f'{i}/{n} {message}')
                elif kind == 'error':
                    exc, tb = payload
                    self._log(tb)
                    self._set_busy(False)
                    messagebox.showerror('エラー', f'{type(exc).__name__}: {exc}', parent=self)
                elif kind == 'done':
                    self._set_busy(False)
                    self.reload()
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _machine_or_warn(self):
        machine = self.machine_var.get()
        if machine == 'すべて':
            messagebox.showinfo('装置を選んでください', '左上の「装置」で 1 台を選んでください。', parent=self)
            return None
        return machine

    def _run_rescore(self, machine, names):
        def task(progress, log):
            mods = axn_bridge.load()
            log(f'AXN: {mods.root} ({mods.criteria})')
            table = machine_data.machines()
            outcomes = []
            for i, name in enumerate(names):
                if self._stop.is_set():
                    log('中止しました（ここまでの分を保存します）')
                    break
                progress(i, len(names), name)
                outcome = rescore.rescore_experiment(name, machine, table, mods)
                outcomes.append(outcome)
                log(outcome.summary())
                for line in outcome.skipped:
                    log(f'    - {line}')
            for kind, (added, replaced) in rescore.save(outcomes, self.folder).items():
                log(f'{kind}: {added} 件を足し、{replaced} 件を置き換えました')
            progress(len(names), len(names), '保存しました')
            experiment_ledger.write(experiment_ledger.build(experiment_ledger.load_scores(self.folder),
                                                            inventories=self.inventory, machines=self.machines), self.folder)
            log(f'台帳を書き出しました: {experiment_ledger.ledger_path(self.folder)}')
        self._start(task)

    def rescore_selected(self):
        machine = self._machine_or_warn()
        if machine is None:
            return
        names = [r['name'] for r in self.rescore_table.selected_rows()]
        if not names:
            messagebox.showinfo('実験IDを選んでください', '一覧で実験IDを選んでください（複数可）。', parent=self)
            return
        self._run_rescore(machine, names)

    def rescore_all(self):
        machine = self._machine_or_warn()
        if machine is None:
            return
        names = [r['name'] for r in self.rescore_table.rows if not r['reconstructed']]
        if not names:
            messagebox.showinfo('再計算', 'この装置の実験IDはすべて再構成済みです。', parent=self)
            return
        if not messagebox.askyesno('再計算', f'{len(names)} 件の実験IDを SQserver から読んで再計算します。時間がかかります。続けますか？', parent=self):
            return
        self._run_rescore(machine, names)

    def import_measured(self):
        machine = self._machine_or_warn()
        if machine is None:
            return

        def task(progress, log):
            progress(0, 1, f'{machine} の Score.csv を読んでいます')
            for kind, (added, replaced) in rescore.import_measured(machine, self.folder).items():
                log(f'{kind}: 計測時の行 {added} 件を足し、{replaced} 件を置き換えました')
            progress(1, 1, '取り込みました')
        self._start(task)


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    App().mainloop()


if __name__ == '__main__':
    main()
