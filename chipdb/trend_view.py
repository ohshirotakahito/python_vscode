"""解析・トレンド: 左のチップ一覧で 1 つ、または複数のチップを選び、操作・Info の数値を重ねて比べる。

- 左の一覧は履歴のあるチップ（検索欄でIDを絞り込み）。Ctrl / Shift で複数選択すると、同じグラフに色分けして重ねる。
  選んだ行は、そのチップの線と同じ色で塗る（どの線がどのチップか一覧でも分かるように）。
- 1 つだけ選ぶと、そのチップがアプリ全体の選択になる（上部のチップ名・概要・履歴も切り替わる）。台帳を開き直さずに次々と見られる。
- 横軸は「回数（記録順）」「経過時間」「日時」。経過時間は各チップの最初の Experiment（使用開始）を 0 とし、
  Info 3 の測定時刻（なければ実験の日付）までの時間。チップどうしを使用開始から揃えて比べられる。縦軸は切りのよい目盛り。
"""
from datetime import timedelta
from pathlib import Path
import math
import re
import tkinter as tk
from tkinter import ttk

from chipdb.axis import nice_ticks, parse_time, tick_label, time_ticks
from chipdb.store import Document
from shared import ui_theme as theme

ACCENT = '#dfb86b'
X_AXES = ('回数（記録順）', '経過時間', '日時')
METRICS = ('Info 1', 'Info 2', 'Info 3', 'Info 4', 'Info 5', 'Info 6')
LIST_COLUMNS = ('id', 'ex', 'count', 'last')
COLOURS = (ACCENT, '#5b9dff', '#5fbf77', '#f27fb2', '#b68cf5', '#4fc1b5', '#ff7b72', '#e6efe9')
MAX_SERIES = len(COLOURS)
LIST_STYLE = 'Trend.Treeview'
ROW_TEXT = '#10161b'           # 線の色で塗った行の文字（明るい色の上でも読める濃い色）


def natural_key(path):
    """数字を数として並べる（SC25-2115-2 → -3 → … → -10 の順）。"""
    return [(0, int(part), '') if part.isdigit() else (1, 0, part.casefold())
            for part in re.split(r'(\d+)', Path(path).stem) if part]


def experiment_rows(rows):
    return [r for r in rows if len(r) > 6 and r[0] == 'Experiment']


def kinds_of(rows):
    """操作の種類（FC・MA・TA・CAL など）を記録に出てくる順に。"""
    return list(dict.fromkeys(r[5].split('#')[0] for r in experiment_rows(rows)))


def start_time(rows):
    """チップの使用開始: 最初の Experiment 行の日時（読めなければ None）。"""
    times = [t for t in (parse_time(r[2]) for r in experiment_rows(rows)) if t is not None]
    return min(times) if times else None


def series_points(rows, kind, column, measured=False):
    """(回数, 日時 | None, 値) の一覧。回数はその操作の記録順で 1 から（数値でない行も数える）。

    measured=True なら日時は Info 3 の測定時刻（'20190419_12:38'）を優先し、読めなければ実験の日付。"""
    points, count = [], 0
    for row in experiment_rows(rows):
        if row[5].split('#')[0] != kind:
            continue
        count += 1
        if len(row) <= column:
            continue
        try:
            value = float(row[column])
        except ValueError:
            continue
        if math.isfinite(value):
            moment = parse_time(row[8]) if measured and len(row) > 8 else None
            points.append((count, moment or parse_time(row[2]), value))
    return points


class TrendView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.documents = {}            # {パス: (サイズ, 更新日時, 行)}: 比較で読んだチップの控え
        self.chosen = []               # 比べているチップ（選んだ順）
        self.notes = []
        self.colours = {}              # {パス: 線の色}: 一覧の選んだ行を同じ色で塗る
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 10, 0))
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)

        ttk.Label(left, text='チップ（Ctrl / Shift で複数選んで比較。選んだ行をもう一度クリックで外す）', wraplength=300).pack(anchor='w')
        self.search = tk.StringVar()
        search = ttk.Frame(left)
        search.pack(fill='x', pady=(4, 4))
        ttk.Entry(search, textvariable=self.search, width=18).pack(side='left', fill='x', expand=True)
        ttk.Button(search, text='クリア', command=lambda: self.search.set('')).pack(side='left', padx=(4, 0))
        self.search.trace_add('write', lambda *_: self.refresh_list())
        frame = ttk.Frame(left)
        frame.pack(fill='both', expand=True)
        # 「回数」は右で選んだ操作（FC・MA など）の回数。見出しを押すとその列で並べ替え（もう一度で逆順）
        # 選んだ行の色は行ごとのタグで付ける。共通の Treeview の「選択中は 1 色」が勝たないよう、専用スタイルで空にする
        style = ttk.Style(self)
        style.map(LIST_STYLE, background=[], foreground=[])
        self.chips = ttk.Treeview(frame, columns=LIST_COLUMNS, show='headings', selectmode='extended', height=20,
                                  style=LIST_STYLE)
        self.chips.tag_configure('chosen', background=style.lookup('Treeview', 'background', ['selected']),
                                 foreground='#ffffff')
        for col, title, width in (('id', 'チップID', 130), ('ex', '実験', 46), ('count', '回数', 64), ('last', '最終使用', 92)):
            self.chips.heading(col, text=title, command=lambda c=col: self.sort_list(c))
            self.chips.column(col, width=width, stretch=col == 'id', anchor='e' if col in ('ex', 'count') else 'w')
        self.list_sort = (None, False)
        self.all_kinds = []
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.chips.yview)
        self.chips.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.chips.pack(fill='both', expand=True)
        self.chips.bind('<<TreeviewSelect>>', self.chips_selected)
        self.chips.bind('<Button-1>', self.clicked)            # 選んでいる行のクリックで外す（比較から 1 つ抜く）
        self.chips.bind('<Up>', lambda e: self.step(-1))
        self.chips.bind('<Down>', lambda e: self.step(1))
        self.chips_state = ttk.Label(left, style='Muted.TLabel')
        self.chips_state.pack(anchor='w', pady=(4, 0))

        options = ttk.Frame(right)
        options.pack(fill='x')
        ttk.Label(options, text='操作').pack(side='left')
        self.kind = ttk.Combobox(options, state='readonly', width=15)
        self.kind.pack(side='left', padx=10)
        self.metric = ttk.Combobox(options, state='readonly', values=METRICS, width=12)
        self.metric.current(0)
        self.metric.pack(side='left')
        ttk.Label(options, text='横軸').pack(side='left', padx=(24, 0))
        self.x_axis = ttk.Combobox(options, state='readonly', values=X_AXES, width=14)
        self.x_axis.current(1)
        self.x_axis.pack(side='left', padx=10)
        self.kind.bind('<<ComboboxSelected>>', self.kind_changed)
        for combo in (self.metric, self.x_axis):
            combo.bind('<<ComboboxSelected>>', self.plot)
        self.chart = tk.Canvas(right, bg=theme.FIELD, highlightthickness=0)
        self.chart.pack(fill='both', expand=True, pady=12)
        self.chart.bind('<Configure>', self.plot)
        ttk.Label(right, text='左の一覧で選んだチップを最大 8 つまで色分けして表示。上下キーで次のチップへ。'
                  '比較は横軸「経過時間」（使用開始=0）が向きます。Infoの意味・単位はAXNの操作ごとに異なります。', style='Muted.TLabel').pack(anchor='w')

    # --- 左の一覧 ---
    def list_rows(self):
        """一覧の行 (パス, チップID, 実験, 回数, 最終使用)。検索で絞り込み、選んだ列で並べる。"""
        text = self.search.get().strip().casefold()
        kind = self.kind.get()
        rows = []
        for path in self.app.history_paths:
            if text and text not in path.stem.casefold():
                continue
            entry = self.app.entries.get(path)
            record = getattr(entry, 'record', None) or {}
            count = getattr(entry, 'kinds', {}).get(kind, 0) if entry is not None and kind else ''
            rows.append((path, path.stem, record.get('Ex#', ''), count, str(record.get('EX Last Date', ''))[:10]))
        column, reverse = self.list_sort
        if column is None:
            rows.sort(key=lambda r: natural_key(r[0]))
        else:
            index = LIST_COLUMNS.index(column) + 1
            number = lambda v: float(v) if str(v).strip() else None  # noqa: E731
            if column == 'id':
                rows.sort(key=lambda r: natural_key(r[0]), reverse=reverse)
            elif column in ('ex', 'count'):
                filled = [r for r in rows if number(r[index]) is not None]
                rows = sorted(filled, key=lambda r: number(r[index]), reverse=reverse) + \
                    [r for r in rows if number(r[index]) is None]
            else:
                filled = [r for r in rows if r[index]]
                rows = sorted(filled, key=lambda r: r[index], reverse=reverse) + [r for r in rows if not r[index]]
        return rows

    def refresh_list(self):
        """履歴のあるチップの一覧（検索でIDを絞り込み）。選んでいるチップは選んだまま。"""
        self.chosen = [p for p in self.chosen if p in self.app.history_paths]   # 消えたチップは比較から外す
        totals = {}
        for entry in self.app.entries.values():
            for kind, count in getattr(entry, 'kinds', {}).items():
                totals[kind] = totals.get(kind, 0) + count
        self.all_kinds = sorted(totals, key=lambda k: -totals[k])
        if not self.kind.get() and self.all_kinds:
            self.update_kinds()
        else:                          # 新しく出てきた操作も選択肢に（今の選択は変えない）
            self.kind.configure(values=list(dict.fromkeys(list(self.kind.cget('values')) + self.all_kinds)))
        self.chips.heading('count', text=f'{self.kind.get()} 回数' if self.kind.get() else '回数')
        rows = self.list_rows()
        self.chips.delete(*self.chips.get_children())
        for path, *values in rows:
            self.chips.insert('', 'end', iid=str(path), values=values)
        self.chips_state.configure(text=f'{len(rows)} / {len(self.app.history_paths)} チップ')
        self._select_in_list(self.chosen)
        self.paint_list()

    def paint_list(self):
        """選んでいる行をそのチップの線の色で塗る。線のないチップ（数値なし・9 つ目以降）は共通の選択色。"""
        selected = set(self.chips.selection())
        for iid in self.chips.get_children():
            colour = self.colours.get(Path(iid)) if iid in selected else None
            if colour:
                tag = 'line' + colour
                self.chips.tag_configure(tag, background=colour, foreground=ROW_TEXT)
                self.chips.item(iid, tags=(tag,))
            else:
                self.chips.item(iid, tags=('chosen',) if iid in selected else ())

    def sort_list(self, column):
        current, reverse = self.list_sort
        # 数値の列は最初の 1 回で多い順（比べるため）、もう一度押すと逆順
        reverse = not reverse if current == column else column in ('ex', 'count', 'last')
        self.list_sort = (column, reverse)
        self.refresh_list()

    def kind_changed(self, event=None):
        self.refresh_list()            # 「回数」の列を選んだ操作の回数に
        self.plot()

    def _select_in_list(self, paths):
        keys = [str(p) for p in paths if self.chips.exists(str(p))]
        if set(keys) != set(self.chips.selection()):
            self.chips.selection_set(keys)
        if keys:
            self.chips.see(keys[-1])
        # 一覧の作り直し・選び直しでも <<TreeviewSelect>> が後から届く。その時点の選択がこれと同じなら操作ではない
        self._programmatic = set(self.chips.selection())

    def chips_selected(self, event=None):
        if set(self.chips.selection()) == getattr(self, '_programmatic', None):
            return
        self._programmatic = None
        selected = [Path(i) for i in self.chips.selection()]
        hidden = [p for p in self.chosen if not self.chips.exists(str(p))]     # 検索で隠れているだけのものは残す
        chosen = [p for p in self.chosen if p in selected] + [p for p in selected if p not in self.chosen]
        if not selected:
            return
        if len(selected) == 1:
            chosen, hidden = selected, []
        self.chosen = hidden + chosen
        if len(selected) == 1 and selected[0] != self.app.selected_path:
            self.app.select_path(selected[0])          # 1 つだけ: アプリ全体の選択にする（描き直しは render から）
        else:
            self.update_kinds()
            self.plot()

    def clicked(self, event):
        """選んでいる行を（Ctrl・Shift なしで）クリックしたら、その行だけを外す。ほかのクリックはいつも通り。"""
        if event.state & (0x0001 | 0x0004):                     # Shift / Ctrl: Tk の標準の選び方
            return None
        if self.chips.identify_region(event.x, event.y) != 'cell':
            return None
        row = self.chips.identify_row(event.y)
        if row and self.unselect(row):
            return 'break'                                       # 標準の「その行だけを選ぶ」をさせない
        return None

    def unselect(self, row):
        """比べている中から row を外す。最後の 1 つは外さない（グラフが空にならないように）。外したら True。"""
        selected = self.chips.selection()
        if row not in selected or len(selected) < 2:
            return False
        self.chips.selection_remove(row)
        path = Path(row)
        self.chosen = [p for p in self.chosen if p != path]
        if path == self.app.selected_path and self.chosen:
            self.app.select_path(self.chosen[0])                 # 上部のチップ名も比較に残っているチップへ
        return True

    def step(self, delta):
        """上下キー: 一覧の前後のチップに移る（1 チップの表示で次々と見るため）。"""
        items = self.chips.get_children()
        if not items:
            return 'break'
        current = self.chips.focus() or (self.chips.selection() or (items[0],))[0]
        index = max(0, min(len(items) - 1, items.index(current) + delta)) if current in items else 0
        self.chips.selection_set(items[index])
        self.chips.focus(items[index])
        self.chips.see(items[index])
        return 'break'

    def chip_changed(self, path):
        """アプリ全体の選択が変わった（台帳などから）。比較中のチップに含まれていればそのまま、なければそれだけにする。"""
        if path is None:
            self.chosen = []
        elif path not in self.chosen:
            self.chosen = [path] if path in self.app.history_paths else []
        self._select_in_list(self.chosen)
        self.update_kinds()
        self.plot()

    # --- データ ---
    def rows_of(self, path):
        if self.app.document is not None and self.app.document.path == path:
            return self.app.document.rows
        try:
            stat = path.stat()
            cached = self.documents.get(path)
            if cached is None or cached[:2] != (stat.st_size, stat.st_mtime_ns):
                cached = (stat.st_size, stat.st_mtime_ns, Document(path).rows)
                self.documents[path] = cached
            return cached[2]
        except (OSError, UnicodeError):
            return None

    def update_kinds(self):
        """操作の選択肢: 比べているチップにある操作を先に、ほかのチップにある操作も後ろに（チップを選ぶ前に操作を選べる）。"""
        kinds = []
        for path in self.chosen[:MAX_SERIES]:
            rows = self.rows_of(path)
            if rows:
                kinds += [k for k in kinds_of(rows) if k not in kinds]
        first = kinds[0] if kinds else (self.all_kinds[0] if self.all_kinds else '')
        kinds += [k for k in self.all_kinds if k not in kinds]
        current = self.kind.get()
        self.kind.configure(values=kinds)
        self.kind.set(current if current in kinds else first)
        if self.kind.get() != current and self.chips.get_children():
            if self.list_sort[0] == 'count':
                self.refresh_list()        # 回数で並べているときは並びも新しい操作の回数で
            else:
                self.refresh_counts()

    def refresh_counts(self):
        """「回数」の列だけを今の操作の回数に書き換える（一覧の並びと選択はそのまま）。"""
        kind = self.kind.get()
        self.chips.heading('count', text=f'{kind} 回数' if kind else '回数')
        for iid in self.chips.get_children():
            entry = self.app.entries.get(Path(iid))
            self.chips.set(iid, 'count', getattr(entry, 'kinds', {}).get(kind, 0) if entry is not None and kind else '')

    def series(self):
        """[(チップID, 色, 点の一覧)]。点のないチップは notes に回す。"""
        column = 7 + max(self.metric.current(), 0)
        out, self.notes, self.colours = [], [], {}
        if len(self.chosen) > MAX_SERIES:
            self.notes.append(f'先に選んだ {MAX_SERIES} チップだけを表示（{len(self.chosen)} 選択）')
        for path in self.chosen[:MAX_SERIES]:
            rows = self.rows_of(path)
            if rows is None:
                self.notes.append(f'{path.stem}: 読めない')
                continue
            elapsed = self.x_axis.get() == X_AXES[1]
            points = series_points(rows, self.kind.get(), column, measured=elapsed)
            if elapsed:                                # 日時 → 使用開始からの経過時間（時間）
                origin = start_time(rows) or min((t for _, t, _ in points if t is not None), default=None)
                points = [(n, (t - origin).total_seconds() / 3600 if t and origin else None, v) for n, t, v in points]
            if points:
                self.colours[path] = COLOURS[len(out) % len(COLOURS)]
                out.append((path.stem, self.colours[path], points))
            else:
                self.notes.append(f'{path.stem}: この操作の数値なし')
        return out

    # --- グラフ ---
    def plot(self, event=None):
        c = self.chart
        c.delete('all')
        by_time = self.x_axis.get() != X_AXES[0]
        elapsed = self.x_axis.get() == X_AXES[1]
        series = self.series()
        if by_time:
            undated = sum(1 for _, _, pts in series for _, t, _ in pts if t is None)
            series = [(name, colour, sorted((p for p in pts if p[1] is not None), key=lambda p: p[1]))
                      for name, colour, pts in series]
            series = [s for s in series if s[2]]
            self.colours = {p: c for p, c in self.colours.items() if c in {colour for _, colour, _ in series}}
            if undated:
                self.notes.append(f'日時のない {undated} 点は除外')
        self.paint_list()
        if not series:
            text = '左の一覧でチップを選んでください' if not self.chosen else '数値データがある操作を選択してください'
            c.create_text(35, 40, anchor='nw', text=text, fill=theme.MUTED, font=(theme.UI, 12))
            return
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        values = [v for _, _, pts in series for _, _, v in pts]
        low, high = min(values), max(values)
        title = f'{self.kind.get()} / {self.metric.get()}'
        if len(series) == 1:
            title += f'     n={len(values)}     min {low:.5g}     max {high:.5g}     mean {sum(values)/len(values):.5g}'
        else:
            title += f'     {len(series)} チップを比較'
        c.create_text(24, 18, anchor='nw', text=title, fill=ACCENT)
        top = 60
        if len(series) > 1:                            # 凡例: 色・チップID・回数・平均。幅が足りなければ次の行へ
            x, y = 24, 42
            for name, colour, pts in series:
                vals = [v for _, _, v in pts]
                item = c.create_text(0, 0, anchor='w', text=f'{name}  n={len(vals)}  mean {sum(vals)/len(vals):.4g}',
                                     fill=theme.TEXT)
                width = c.bbox(item)[2] - c.bbox(item)[0]
                if x > 24 and x + 16 + width > w - 30:
                    x, y = 24, y + 22
                c.coords(item, x + 16, y + 8)
                c.create_oval(x, y + 3, x + 10, y + 13, fill=colour, outline='')
                x += 16 + width + 24
            top = y + 40
        if self.notes:
            c.create_text(w - 30, 18, anchor='ne', text=' · '.join(self.notes), fill=theme.MUTED)
        left, right, bottom = 80, w - 30, h - 55
        y_low, y_high, y_step, y_ticks = nice_ticks(low, high)
        y_of = lambda v: bottom - (v - y_low) / (y_high - y_low) * (bottom - top)  # noqa: E731
        for value in y_ticks:
            y = y_of(value)
            c.create_line(left, y, right, y, fill=theme.BORDER)
            c.create_text(left - 10, y, anchor='e', text=tick_label(value, y_step), fill=theme.MUTED)
        if elapsed:
            hours = max(p[1] for _, _, pts in series for p in pts)
            low_h = min(p[1] for _, _, pts in series for p in pts)
            unit, scale = ('日', 24.) if hours >= 72 else ('時間', 1.)
            e_low, e_high, e_step, e_ticks = nice_ticks(min(low_h, 0) / scale, max(hours, 1) / scale)
            e_low = max(e_low, min(low_h, 0) / scale)
            x_of = lambda p: left + (p[1] / scale - e_low) / (e_high - e_low) * (right - left)  # noqa: E731
            for value in e_ticks:
                if value < e_low:
                    continue
                x = left + (value - e_low) / (e_high - e_low) * (right - left)
                c.create_line(x, top, x, bottom, fill=theme.BORDER, dash=(2, 4))
                c.create_text(x, bottom + 14, anchor='n', text=tick_label(value, e_step), fill=theme.MUTED)
            axis_name = f'経過時間（{unit}、使用開始 = 0）→'
        elif by_time:
            first = min(pts[0][1] for _, _, pts in series)
            last = max(pts[-1][1] for _, _, pts in series)
            if first == last:
                first, last = first - timedelta(hours=12), last + timedelta(hours=12)
            seconds = (last - first).total_seconds()
            x_of = lambda p: left + (p[1] - first).total_seconds() / seconds * (right - left)  # noqa: E731
            ticks, fmt = time_ticks(first, last)
            for moment in ticks:
                x = left + (moment - first).total_seconds() / seconds * (right - left)
                c.create_line(x, top, x, bottom, fill=theme.BORDER, dash=(2, 4))
                c.create_text(x, bottom + 14, anchor='n', text=moment.strftime(fmt), fill=theme.MUTED)
            axis_name = '日時 →'
        else:
            n_low, n_high, _, n_ticks = nice_ticks(1, max(2, max(p[0] for _, _, pts in series for p in pts)))
            n_low = max(n_low, 0)
            x_of = lambda p: left + (p[0] - n_low) / (n_high - n_low) * (right - left)  # noqa: E731
            for number in n_ticks:
                if number < n_low or not float(number).is_integer():
                    continue
                x = left + (number - n_low) / (n_high - n_low) * (right - left)
                c.create_line(x, top, x, bottom, fill=theme.BORDER, dash=(2, 4))
                c.create_text(x, bottom + 14, anchor='n', text=f'{number:.0f}', fill=theme.MUTED)
            axis_name = '回数（記録順）→'
        for name, colour, pts in series:
            coords = [(x_of(p), y_of(p[2])) for p in pts]
            if len(coords) > 1:
                c.create_line(*[n for xy in coords for n in xy], fill=colour, width=2)
            radius = 3 if len(series) == 1 else 2.5
            for x, y in coords:
                c.create_oval(x - radius, y - radius, x + radius, y + radius, fill=colour, outline=theme.FIELD)
        c.create_text(right, h - 12, anchor='e', text=axis_name, fill=theme.MUTED)
