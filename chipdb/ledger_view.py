"""チップ台帳: 管理情報（AXN共有台帳 machinedb/PLAN/chip_info.json）と、測定の集計（各チップの履歴CSV）を
1 チップ 1 行で並べる。チェックイン・管理情報の編集、分布・装置・担当者のグラフ、読み込みエラーもここ。

以前は「チップ台帳」と「全チップ比較」の 2 つの表に分かれていた。台帳とCSVは 1 対 1 なので 1 つにまとめ、
列のまとまり（管理情報 / 測定の集計 / すべて）を切り替えて見る（2026-11-18）。
"""
import csv
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from shared import chip_aliases as aliases
from chipdb.registry import FIELDS, STATES, Store, find, normalize_code
from chipdb.search import matches
from chipdb.summary import COLUMNS, HISTOGRAMS, histogram, shares
from chipdb.summary_view import RULES, comparison_record, table
from shared import ui_theme as theme

ACCENT = '#dfb86b'
LEDGER_COLUMNS = ('チップID', 'ID修正', '状態', '保管場所', '使用者', '製作者', '主体', '作製日', '使用開始日', '履歴')
USAGE_DURATION = '使用経過時間'
LAST_DATE_INDEX = COLUMNS.index('EX Last Date')
MEASUREMENT_COLUMNS = (*COLUMNS[1:LAST_DATE_INDEX + 1], USAGE_DURATION, *COLUMNS[LAST_DATE_INDEX + 1:])
STAT_COLUMNS = ('CSV内ChipID', 'ID確認', *MEASUREMENT_COLUMNS)
ALL_COLUMNS = LEDGER_COLUMNS + STAT_COLUMNS
COLUMN_SETS = {
    # 工程の最新値（FC・MA・TA・CAL の Info1、AXN と同じ有効範囲）も管理情報と一緒に見る（2026-11-18、ユーザー）
    '管理情報': LEDGER_COLUMNS + ('GAP#', 'Ex#', 'Run#', 'FC Last', 'MA Last', 'TA Last', 'CAL Last',
                               'EX Last Date', USAGE_DURATION, 'Machine', 'ID確認'),
    '測定の集計': ('チップID', '履歴', 'ID確認', *MEASUREMENT_COLUMNS),
    'すべて': ALL_COLUMNS,
}
# 見出し（列の名前は summary の列名のまま。表示だけ短く・分かりやすく）
HEADINGS = {'チップID': 'チップID（ファイル名）', 'FC Last': 'FC 最新', 'MA Last': 'MA 最新', 'TA Last': 'TA 最新',
            'CAL Last': 'CAL 最新', 'EX Last Date': '最終使用', 'Machine': '装置', 'Operator': '担当者'}
CELL_PAD, MIN_WIDTH, MAX_WIDTH = 22, 44, 320
NUMERIC = {c for c in COLUMNS[1:] if c not in ('EX FIRST Date', 'EX Last Date', 'Machine', 'Operator', 'FC Chip R Time')}
NO_HISTORY = '測定前・履歴なし'
READ_ERROR = '読み込みエラー'
SEARCH_KEYS = ('code', 'state', 'location', 'user', 'owner', 'origin', 'organization', 'description')


def usage_duration_minutes(start_date, last_used):
    """使用開始日の午前0時から最終使用までの分数。計算できない場合は None。"""
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(last_used, '%Y/%m/%d_%H:%M')
    except (TypeError, ValueError):
        return None
    if end >= start:
        return int((end - start).total_seconds() // 60)
    return None


def id_fix_text(code, table):
    """「ID修正」の列: 旧名なら「→ 新名（状態）」、新名なら「旧名 …」。対応表になければ空。"""
    if not table:
        return ''
    for entry in table.get('entries', []):
        if entry['old'].casefold() == code.casefold():
            return f"→ {entry['new']}（{entry.get('status', '予定')}{'・統合' if entry.get('merge') else ''}）"
    olds = [e['old'] for e in table.get('entries', []) if e['new'].casefold() == code.casefold()]
    return '旧名 ' + '・'.join(olds) if olds else ''


def ledger_rows(paths, chips, entries, history_paths, alias_table=None):
    """表の行（列名 → 値。'_path' に CSV のパス、'_text' に検索用の管理情報、'_dates' に使用日）。

    paths: 台帳とCSVを合わせた全チップの CSV パス（測定前のチップは存在しないパス）。
    chips: AXN共有台帳のチップ（chip_info.json の chips）。entries: library.Index の {パス: Entry}。
    alias_table: チップIDの旧名 → 新名の対応表（shared.chip_aliases）。修正の途中で旧名・新名が並ぶ間の目印に使う。"""
    by_code = {c['code'].casefold(): c for c in chips}
    rows = []
    for path in paths:
        chip = by_code.get(path.stem.casefold(), {})
        entry = entries.get(path)
        row = dict.fromkeys(ALL_COLUMNS, '')
        if entry is not None and entry.record is not None:
            row.update(comparison_record(dict(entry.record, _path=str(path))))
        history = NO_HISTORY if path not in history_paths else READ_ERROR if entry is not None and entry.error else 'あり'
        row.update({'チップID': path.stem, 'ID修正': id_fix_text(path.stem, alias_table),
                    '状態': chip.get('state', '未登録'), '保管場所': chip.get('location', ''),
                    '使用者': chip.get('user', chip.get('owner', '')), '製作者': chip.get('origin', ''),
                    '主体': chip.get('organization', ''), '作製日': chip.get('fabricated_on', ''),
                    '使用開始日': chip.get('use_started_on', ''), '履歴': history, '_path': str(path),
                    '_dates': entry.usage_dates if entry is not None else set(),
                    '_text': ' '.join(str(chip.get(key, '未登録' if key == 'state' else '')) for key in SEARCH_KEYS)
                    + ' ' + ' '.join(chip.get('aliases', []))})          # 直す前の表記（別名）でも探せる
        minutes = usage_duration_minutes(row['使用開始日'], row['EX Last Date'])
        if minutes is not None:
            days, remainder = divmod(minutes, 24 * 60)
            hours, minute = divmod(remainder, 60)
            row[USAGE_DURATION] = f'{days}日 {hours:02d}:{minute:02d}'
            row['_usage_minutes'] = minutes
        rows.append(row)
    return rows


def row_matches(row, query, span):
    """検索: 期間（2026-11 など）なら履歴の実験日、それ以外は ID・管理情報・集計のどれかに含まれるか。"""
    if span is not None:
        return matches(row['チップID'], row['_dates'], query, span)
    text = query.strip().casefold()
    return not text or any(text in str(row[c]).casefold() for c in ALL_COLUMNS) or text in row['_text'].casefold()


def sort_rows(rows, column, reverse):
    """column で並べる（数値は数値として）。空欄は昇順・降順のどちらでも最後（測定前のチップが上に来ないように）。"""
    filled = [r for r in rows if str(r.get(column, '')).strip()]
    empty = [r for r in rows if not str(r.get(column, '')).strip()]
    return sorted(filled, key=sort_key(column), reverse=reverse) + empty


def sort_key(column):
    def key(row):
        if column == USAGE_DURATION:
            return (0, row['_usage_minutes'], '')
        value = row.get(column, '')
        try:
            return (0, float(value), '')
        except (TypeError, ValueError):
            return (1, 0., str(value).casefold())
    return key


class LedgerView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.data = dict(chips=[], revision=0)
        self.alias_table = None          # チップIDの旧名 → 新名（shared.chip_aliases）
        self.store = None
        self.enabled = False
        self.rows, self.shown = [], []
        self.errors = []
        self.sort_column, self.reverse = None, False
        bar = ttk.Frame(self)
        bar.pack(fill='x', pady=(0, 10))
        ttk.Button(bar, text='＋ チップをチェックイン', style='Accent.TButton', command=lambda: self.edit(new=True)).pack(side='left')
        ttk.Button(bar, text='選択チップの管理情報を編集', command=self.edit).pack(side='left', padx=8)
        ttk.Button(bar, text='選択チップの概要を開く', command=self.open_selected).pack(side='left')
        ttk.Button(bar, text='表示一覧をCSV出力', command=self.export).pack(side='right')
        ttk.Button(bar, text='全件読み直し', command=lambda: app.scan(force=True)).pack(side='right', padx=8)
        self.column_set = tk.StringVar(value='管理情報')
        columns_box = ttk.Combobox(bar, textvariable=self.column_set, values=list(COLUMN_SETS), state='readonly', width=10)
        columns_box.pack(side='right')
        columns_box.bind('<<ComboboxSelected>>', lambda e: self.apply_columns())
        ttk.Label(bar, text='表示する列').pack(side='right', padx=(0, 6))
        search = ttk.Frame(self)
        search.pack(fill='x', pady=(0, 6))
        ttk.Label(search, text='検索（ID・管理情報・集計値・使用日）').pack(side='left')
        ttk.Entry(search, textvariable=app.query, width=40).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(search, text='検索をクリア', command=lambda: app.query.set('')).pack(side='left')
        ttk.Label(self, text='使用日：2026-11 / 2026-11-13 / 2026-10〜2026-11（履歴の実験日で検索）', style='Muted.TLabel').pack(anchor='w')
        self.search_state = ttk.Label(self, style='Muted.TLabel')
        self.search_state.pack(anchor='w')
        self.state = ttk.Label(self, style='Muted.TLabel')
        self.state.pack(anchor='w')
        self.index_state = ttk.Label(self, style='Muted.TLabel')
        self.index_state.pack(anchor='w', pady=(0, 4))
        self.progress = ttk.Progressbar(self)
        self.progress.pack(fill='x', pady=(0, 6))
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True)
        listing, charts, errors = ttk.Frame(tabs), ttk.Frame(tabs, padding=10), ttk.Frame(tabs)
        tabs.add(listing, text='一覧')
        tabs.add(charts, text='分布・装置・担当者')
        tabs.add(errors, text='読み込みエラー')
        self.error_tab, self.sub_tabs = errors, tabs
        self.view = table(listing, ALL_COLUMNS)
        for col in ALL_COLUMNS:
            self.view.heading(col, text=HEADINGS.get(col, col))
            if col in NUMERIC:
                self.view.column(col, anchor='e')      # 数値は右寄せ（桁がそろう）
        self.view.tag_configure('identity_mismatch', background=theme.row('warning', 0.22), foreground=theme.TEXT)
        self.view.tag_configure('no_history', foreground=theme.MUTED)
        self.view.tag_configure('read_error', background=theme.row('danger', 0.22), foreground=theme.TEXT)
        for col in ALL_COLUMNS:
            self.view.heading(col, command=lambda key=col: self.sort(key))
        self.view.bind('<<TreeviewSelect>>', self.selection_changed)
        self.view.bind('<Double-1>', self.open_selected)
        self.error_view = table(errors, ('ファイル', '理由'))
        self.error_view.column('理由', width=600, stretch=True)
        self.chart_kind = ttk.Combobox(charts, state='readonly', values=[h[0] for h in HISTOGRAMS] + ['装置別', '担当者別'], width=25)
        self.chart_kind.current(0)
        self.chart_kind.pack(anchor='w')
        self.chart_kind.bind('<<ComboboxSelected>>', self.draw)
        chart_frame = ttk.Frame(charts)
        chart_frame.pack(fill='both', expand=True, pady=8)
        self.chart = tk.Canvas(chart_frame, bg=theme.FIELD, highlightthickness=0)
        scroll = ttk.Scrollbar(chart_frame, orient='vertical', command=self.chart.yview)
        self.chart.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.chart.pack(fill='both', expand=True)
        self.chart.bind('<Configure>', self.draw)
        ttk.Label(self, text=RULES + '\n測定前を含む全チップの入口です。クリックで対象を選択、ダブルクリックで概要を開きます。'
                  '管理情報は上の編集ボタン、測定履歴は「操作履歴・編集」で編集します。分布・内訳は検索で絞り込んだチップが対象です。',
                  style='Muted.TLabel', wraplength=1100).pack(anchor='w', pady=8)
        self.apply_columns()

    # --- 台帳（AXN共有） ---
    def reload(self, db):
        self.store = Store(db)
        try:
            self.alias_table = aliases.load(aliases.table_path(db))
        except (OSError, ValueError) as exc:
            self.alias_table = None
            self.app.status.configure(text=f'チップID対応表を読めません: {exc}')
        try:
            self.data = self.store.load()
            self.state.configure(text=f'AXN共有台帳 · revision {self.data["revision"]}')
            self.enabled = True
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            self.data = dict(chips=[], revision=0)
            self.enabled = False
            self.state.configure(text=f'台帳を読み込めません（編集停止）: {exc}')

    # --- 表 ---
    def apply_columns(self):
        self.view.configure(displaycolumns=COLUMN_SETS[self.column_set.get()])
        self.fit_columns()

    def fit_columns(self):
        """表示している列の幅を、見出しと中身の一番長い値に合わせる（余白を詰めて、なるべく全部の列が見えるように）。"""
        from tkinter import font as tkfont
        try:
            body = tkfont.Font(font=ttk.Style(self).lookup('Treeview', 'font') or (theme.UI, 10))
        except tk.TclError:
            body = tkfont.Font(font=(theme.UI, 10))
        head = tkfont.Font(font=(theme.UI_BOLD, 10))
        rows = self.rows or self.shown            # 全チップで測る（検索のたびに幅が変わらないように）
        for col in COLUMN_SETS[self.column_set.get()]:
            longest = max((str(r.get(col, '')) for r in rows), key=len, default='')
            width = max(head.measure(HEADINGS.get(col, col)), body.measure(longest)) + CELL_PAD
            self.view.column(col, width=max(MIN_WIDTH, min(MAX_WIDTH, width)), stretch=False)

    def show(self, rows):
        """rows（検索で絞り込み済み）を表示し、選択中のチップを選び直す。"""
        if self.sort_column:
            rows = sort_rows(rows, self.sort_column, self.reverse)
        self.shown = rows
        self.view.delete(*self.view.get_children())
        for row in rows:
            tags = ('read_error',) if row['履歴'] == READ_ERROR else ('no_history',) if row['履歴'] == NO_HISTORY else \
                ('identity_mismatch',) if row['ID確認'] not in ('', '一致') else ()
            self.view.insert('', 'end', iid=row['_path'], values=[row[c] for c in ALL_COLUMNS], tags=tags)
        self.fit_columns()
        self.view.yview_moveto(0)                 # 描き直したら先頭から（選択中のチップがあれば、そこへ移る）
        self.sync_selection(self.app.selected_path)
        self.draw()

    def show_errors(self, errors):
        self.errors = list(errors)
        self.error_view.delete(*self.error_view.get_children())
        for error in self.errors:
            self.error_view.insert('', 'end', values=error)
        self.sub_tabs.tab(self.error_tab, text=f'読み込みエラー ({len(self.errors)})' if self.errors else '読み込みエラー')

    def sort(self, column):
        self.reverse = not self.reverse if self.sort_column == column else False
        self.sort_column = column
        self.show(self.shown)

    def selection_changed(self, event=None):
        selected = self.view.selection()
        if selected and Path(selected[0]) != self.app.selected_path:
            self.app.select_path(Path(selected[0]))

    def open_selected(self, event=None):
        selected = self.view.selection()
        if selected:
            self.app.open_chip(Path(selected[0]))

    def sync_selection(self, path):
        key = str(path) if path is not None else ''
        selected = self.view.selection()
        if key and self.view.exists(key):
            if selected != (key,):
                self.view.selection_set(key)
            self.view.see(key)
        elif selected:
            self.view.selection_remove(*selected)

    def measured(self):
        """集計がある（履歴CSVを読めた）表示中の行。グラフとCSV出力の集計列はこれが対象。"""
        return [r for r in self.shown if r['ID確認']]

    def draw(self, event=None):
        c = self.chart
        c.delete('all')
        width, height = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        records = self.measured()
        if not records:
            c.create_text(24, 28, anchor='nw', text='集計できたチップがありません（読み込み中、または検索の結果が測定前のチップだけ）', fill=theme.MUTED)
            return
        index = self.chart_kind.current()
        if index >= len(HISTOGRAMS):
            counts = shares(records, 'Machine' if index == len(HISTOGRAMS) else 'Operator')
            total_height = max(height, len(counts) * 30 + 40)
            c.configure(scrollregion=(0, 0, width, total_height))
            maximum = max(n for _, n in counts)
            for i, (name, count) in enumerate(counts):
                y = 25 + i * 30
                c.create_text(12, y + 8, anchor='w', text=name, fill=theme.TEXT)
                end = 160 + (width - 240) * count / maximum
                c.create_rectangle(160, y, end, y + 18, fill=ACCENT, outline='')
                c.create_text(end + 8, y + 8, anchor='w', text=str(count), fill=theme.MUTED)
            return
        c.configure(scrollregion=(0, 0, width, height))
        _, column, maximum = HISTOGRAMS[index]
        edges, counts = histogram(records, column, maximum)
        if not counts:
            c.create_text(24, 28, anchor='nw', text='有効な数値がありません', fill=theme.MUTED)
            return
        peak = max(counts)
        c.create_text(20, 18, anchor='nw', text=f'{column} · 有効数 {sum(counts)} · 50区間 · ' + (f'0 < 値 ≤ {maximum}' if maximum else '全数値'), fill=ACCENT)
        for i in range(5):
            y = 55 + (height - 100) * i / 4
            c.create_line(60, y, width - 25, y, fill=theme.BORDER)
            c.create_text(50, y, anchor='e', text=f'{peak * (1 - i / 4):.0f}', fill=theme.MUTED)
        step = (width - 85) / len(counts)
        for i, count in enumerate(counts):
            x = 60 + i * step
            c.create_rectangle(x, height - 45 - (height - 100) * count / peak, x + max(step - 1, 1), height - 45, fill=ACCENT, outline='')
        c.create_text(60, height - 22, anchor='w', text=f'{edges[0]:.5g}', fill=theme.MUTED)
        c.create_text(width - 25, height - 22, anchor='e', text=f'{edges[-1]:.5g}', fill=theme.MUTED)

    def export(self):
        if not self.shown:
            return
        name = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', initialfile='chip_ledger.csv')
        if not name:
            return
        columns = COLUMN_SETS[self.column_set.get()]
        try:
            # 書き出しが共有元のCSVを置き換えないように
            if Path(name).resolve() in {Path(r['_path']).resolve() for r in self.rows}:
                raise ValueError('共有元CSVと別の保存先を指定してください。')
            with open(name, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows([r[c] for c in columns] for r in self.shown)
        except (OSError, ValueError) as exc:
            messagebox.showerror('書き出し', str(exc), parent=self)

    # --- チェックイン・管理情報の編集 ---
    def edit_current(self):
        code = self.app.selected_path.stem if self.app.selected_path else None
        self.edit(code=code)

    def edit(self, new=False, code=None):
        if not self.store or not self.enabled:
            messagebox.showerror('チップ台帳', '共有フォルダと台帳を再読み込みしてください。', parent=self)
            return
        if not new and code is None:
            selected = self.view.selection()
            if not selected:
                messagebox.showinfo('チップ台帳', '台帳から編集するチップを選択してください。', parent=self)
                return
            code = Path(selected[0]).stem
        try:
            chip = find(self.data, code) if code else None
        except ValueError as exc:
            messagebox.showerror('チップ台帳', str(exc), parent=self)
            return
        chip = chip or dict(code=code or '', state='保管中')
        store, revision = self.store, self.data['revision']
        dialog = tk.Toplevel(self)
        dialog.title('チップをチェックイン' if new else 'チップの管理情報を編集')
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        fields = {}
        for index, (key, label) in enumerate({'code': 'チップID', **FIELDS}.items()):
            ttk.Label(dialog, text=label).grid(row=index, column=0, sticky='w', padx=18, pady=6)
            value = chip.get(key, '')
            widget = ttk.Combobox(dialog, state='readonly', values=STATES, width=48) if key == 'state' else ttk.Entry(dialog, width=50)
            if key == 'state':
                widget.set(value)
            else:
                widget.insert(0, value)
                if key == 'code' and not new:
                    widget.configure(state='readonly')
            widget.grid(row=index, column=1, padx=18, pady=6)
            fields[key] = widget
        ttk.Label(dialog, text='日付は YYYY-MM-DD（未定は空欄）。測定履歴やGPSETは作成しません。\n新規IDの区切り文字（_・=など）はAXNと同じく「-」に統一します。', style='Muted.TLabel').grid(row=len(FIELDS) + 1, columnspan=2, padx=18, pady=10)

        def save():
            try:
                store.save_chip(fields['code'].get(), {k: fields[k].get().strip() for k in FIELDS}, revision, new=new)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
                messagebox.showerror('保存できません', str(exc), parent=dialog)
                return
            saved_code = normalize_code(fields['code'].get()) if new else fields['code'].get().strip()
            dialog.destroy()
            self.app.scan()
            self.app.query.set(saved_code)
            self.app.tabs.select(self)
            self.app.status.configure(text=f'{saved_code} · チップ台帳を保存しました')
        buttons = ttk.Frame(dialog)
        buttons.grid(row=len(FIELDS) + 2, columnspan=2, sticky='e', padx=18, pady=12)
        ttk.Button(buttons, text='キャンセル', command=dialog.destroy).pack(side='left', padx=8)
        ttk.Button(buttons, text='チェックイン' if new else '変更を保存', style='Accent.TButton', command=save).pack(side='left')
        dialog.grab_set()
