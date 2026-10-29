"""Data 退避・退避整理: move analysed originals T -> T/stocked so ANAL resumes on the rest, and move them back.

A T file counts as analysed when ANAL or ANAL/stocked holds a non-empty TDMS with the same measurement number
(rackstation_view.measurement_key, the same rule as the database's ANAL progress). Unanalysed and gap numbers always stay in T.
Files sharing the TDMS name with another extension (e.g. .tdms_index) move together with it.
Moves are journaled by anal_manager.execute_moves and can be undone from the history.
"""
from datetime import datetime
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from database import catalog
from database.rackstation_view import measurement_key
from filemanager.anal_manager import (execute_moves, matches_inventory_query, undo_plan, refresh_db, scan_warning,
                                      add_share_filter, update_shares, share_matches)

SUFFIXES = ('.tdms', '.tdms_index')


def t_folder(row):
    return Path(row['root']) / row['experiment'] / row['sample'] / (row['sample'] + '_10k_Sample') / 'T'


def progress_of(row):
    """(analysed, total, ratio) from the saved scan, same formula as the ANAL進捗 map."""
    if row.get('t_tdms') is None or row.get('anal_tdms') is None:
        return None, None, None
    total = row['t_tdms'] + (row.get('t_stocked_tdms') or 0)
    analysed = row['anal_tdms'] + (row.get('anal_stocked_tdms') or 0)
    return analysed, total, (analysed / total if total else None)


def is_target(row):
    """Partially analysed (0% < progress < 100%, T present) or still holding T/stocked files to restore."""
    _, total, ratio = progress_of(row)
    return bool(row.get('t_stocked_tdms')) or (bool(total) and ratio is not None and 0 < ratio < 1)


def load_inventory(path):
    with catalog.connect(path, readonly=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rack_samples'").fetchone():
            raise ValueError('保存済みのAnalysis_server調査がありません。実験データベースでAnalysis_serverを更新してください。')
        rows = [dict(r) for r in db.execute('SELECT * FROM rack_samples ORDER BY experiment, sample, root')]
    for row in rows:
        row['analysed'], row['total'], row['ratio'] = progress_of(row)
        row['target'] = is_target(row)
        row['t_path'] = str(t_folder(row))
    return rows


def _groups(folder, orphans=False):
    """{stem: [files]} for TDMS-family files directly in folder.

    orphans=False keeps groups with a .tdms; True returns only companions whose .tdms is not here
    (left behind when a run stopped between a .tdms and its .tdms_index)."""
    groups = {}
    if not folder.is_dir():
        return groups
    for path in folder.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        groups.setdefault(path.stem, []).append(path)
    return {stem: files for stem, files in groups.items()
            if any(p.suffix.lower() == '.tdms' for p in files) != orphans}


def _anal_numbers(t):
    numbers = set()
    for folder in (t / 'ANAL', t / 'ANAL' / 'stocked'):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            key = measurement_key(path.name)
            # A zero-byte ANAL file is an interrupted write, not a finished analysis.
            if (isinstance(key, int) and path.suffix.lower() == '.tdms' and path.is_file()
                    and not path.is_symlink() and path.stat().st_size > 0):
                numbers.add(key)
    return numbers


def scan_t(t):
    """Current files in T and T/stocked with their analysed state (reads the share, never writes)."""
    t = Path(t)
    if not t.is_dir():
        raise ValueError(f'Tフォルダが見つかりません: {t}')
    analysed = _anal_numbers(t)
    files = []
    for folder, location in ((t, 'T'), (t / 'stocked', 'T/stocked')):
        for stem, paths in sorted(_groups(folder).items()):
            main = next(p for p in paths if p.suffix.lower() == '.tdms')
            key = measurement_key(main.name)
            number = key if isinstance(key, int) else None
            files.append(dict(location=location, stem=stem, number=number,
                              analysed=number is not None and number in analysed,
                              name=main.name, extra=sorted(p.name for p in paths if p != main),
                              size=sum(p.stat().st_size for p in paths),
                              modified_ns=main.stat().st_mtime_ns,
                              paths=[dict(path=str(p.absolute()), size=p.stat().st_size,
                                          modified_ns=p.stat().st_mtime_ns) for p in sorted(paths)]))
    orphans = [dict(location=location, stem=stem, name=paths[0].name,
                    paths=[dict(path=str(p.absolute()), size=p.stat().st_size, modified_ns=p.stat().st_mtime_ns)
                           for p in sorted(paths)])
               for folder, location in ((t, 'T'), (t / 'stocked', 'T/stocked'))
               for stem, paths in sorted(_groups(folder, orphans=True).items())]
    return dict(folder=str(t.absolute()), files=files, anal_numbers=analysed, orphans=orphans)


def summary(scan):
    files = scan['files']
    in_t = [f for f in files if f['location'] == 'T']
    numbers = {f['number'] for f in files if f['number'] is not None}
    tail = max(scan['anal_numbers'], default=None)
    gaps = sorted(n for n in numbers - scan['anal_numbers'] if tail is not None and n < tail)
    return dict(t=len(in_t), t_analysed=sum(f['analysed'] for f in in_t),
                t_unanalysed=sum(f['number'] is not None and not f['analysed'] for f in in_t),
                t_unknown=sum(f['number'] is None for f in in_t),
                stocked=sum(f['location'] == 'T/stocked' for f in files),
                anal=len(scan['anal_numbers'] & numbers), total=len(numbers), gaps=gaps, last=tail)


def plan_moves(scan, restore, keep_last=False):
    """Archive: analysed T files -> T/stocked. Restore: every T/stocked file -> T.

    Returns (plan, skipped) where skipped lists (name, reason) left in place."""
    t = Path(scan['folder'])
    source_location = 'T/stocked' if restore else 'T'
    target_parent = t if restore else t / 'stocked'
    last = max(scan['anal_numbers'], default=None)
    operations, skipped = [], []
    for f in scan['files']:
        if f['location'] != source_location:
            continue
        if not restore:
            if f['number'] is None:
                skipped.append((f['name'], '#番号なし'))
                continue
            if not f['analysed']:
                continue  # unanalysed / gap: stays in T for the analysis run
            if keep_last and f['number'] == last:
                skipped.append((f['name'], '最後の解析番号（書きかけ対策でTに残す）'))
                continue
        conflict = [p for p in f['paths'] if os.path.lexists(target_parent / Path(p['path']).name)]
        if conflict:
            skipped.append((f['name'], '移動先に同名あり'))
            continue
        for p in f['paths']:
            operations.append(dict(source=p['path'], target=str(target_parent / Path(p['path']).name),
                                   size=p['size'], modified_ns=p['modified_ns'], status='予定'))
    pair_up(scan.get('orphans', []), scan['files'], operations, skipped, {'T': t, 'T/stocked': t / 'stocked'})
    plan = dict(folder=str(t), action='restore' if restore else 'archive', suffixes=SUFFIXES,
                verify_content=False, operations=operations)
    return plan, skipped


def pair_up(orphans, files, operations, skipped, folders):
    """Send each companion left without its .tdms to the folder where that .tdms ends up after this plan."""
    moved = {Path(o['source']).stem: Path(o['target']).parent for o in operations}
    final = {f['stem']: moved.get(f['stem'], folders[f['location']]) for f in files}
    for orphan in orphans:
        target = final.get(orphan['stem'])
        if target is None:
            skipped.append((orphan['name'], '対応する.tdmsがない付属ファイル'))
            continue
        if target == folders[orphan['location']]:
            continue
        if any(os.path.lexists(target / Path(p['path']).name) for p in orphan['paths']):
            skipped.append((orphan['name'], '移動先に同名あり'))
            continue
        for p in orphan['paths']:
            operations.append(dict(source=p['path'], target=str(target / Path(p['path']).name),
                                   size=p['size'], modified_ns=p['modified_ns'], status='予定'))


def describe(plan):
    """Confirmation text: each folder pair once, then one line per TDMS with its companions."""
    pairs = {}
    for o in plan['operations']:
        source, target = Path(o['source']), Path(o['target'])
        pairs.setdefault((str(source.parent), str(target.parent)), {}).setdefault(source.stem, []).append(source.suffix)
    lines = []
    for (source, target), stems in pairs.items():
        lines += [f'移動元: {source}', f'移動先: {target}',
                  f'{len(stems)}件（付属を含め{sum(map(len, stems.values()))}ファイル）', '']
        lines += [f'  [{" + ".join(sorted(suffixes))}]  {stem}' for stem, suffixes in sorted(stems.items())]
        lines.append('')
    return '\n'.join(lines)


class TStockPanel(ttk.Frame):
    """Tab: T直下 ⇄ T/stocked for one sample on one share."""

    def __init__(self, owner):
        super().__init__(owner.tabs, padding=10)
        self.owner, self.busy = owner, False
        self.log_dir = Path(__file__).parent / 't_stock_logs'
        self.events = queue.Queue()
        self.inventory, self.scan_result, self.plan = [], None, None
        self.db = tk.StringVar(value=str(catalog.DEFAULT_DB))
        self.query = tk.StringVar()
        self.exact_experiment = tk.BooleanVar(value=False)
        self.only_targets = tk.BooleanVar(value=True)
        self.keep_last = tk.BooleanVar(value=False)
        top = ttk.Frame(self)
        top.pack(fill='x')
        ttk.Label(top, text='データベース').pack(side='left')
        ttk.Entry(top, textvariable=self.db).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(top, text='DBを選択', command=self.choose_db).pack(side='left')
        ttk.Button(top, text='保存済み調査を読み込む', command=self.load).pack(side='left', padx=6)
        filters = ttk.Frame(self)
        filters.pack(fill='x', pady=6)
        ttk.Label(filters, text='実験・サンプル・共有を検索').pack(side='left')
        ttk.Entry(filters, textvariable=self.query).pack(side='left', fill='x', expand=True, padx=6)
        add_share_filter(self, filters)
        ttk.Checkbutton(filters, text='実験名を完全一致', variable=self.exact_experiment,
                        command=self.render).pack(side='left', padx=(0, 6))
        ttk.Checkbutton(filters, text='解析途中（0%＜進捗＜100%）・T/stockedありのみ', variable=self.only_targets,
                        command=self.render).pack(side='left')
        self.query.trace_add('write', lambda *_: self.render())
        self.samples = self.tree(('root', 'experiment', 'sample', 't', 't_stocked', 'anal', 'progress', 'date'),
                                 ('共有', '実験', 'サンプル', 'T直下', 'T/stocked', 'ANAL（＋stocked）', '進捗', 'DB調査日時（UTC）'), 6)
        self.samples.tag_configure('stocked', background='#593d2d', foreground='#ffffff')
        self.samples.tag_configure('partial', background='#58432a', foreground='#ffffff')
        self.samples.bind('<<TreeviewSelect>>', lambda _: self.invalidate())
        controls = ttk.Frame(self)
        controls.pack(fill='x', pady=4)
        self.scan_button = ttk.Button(controls, text='選択した保存先を再調査', command=self.scan)
        self.scan_button.pack(side='left')
        ttk.Button(controls, text='選択サンプルのDBを更新', command=self.refresh_selected).pack(side='left', padx=(6, 0))
        self.activity = tk.StringVar(value='待機中')
        self.activity_bar = ttk.Progressbar(controls, mode='indeterminate', length=140)
        self.activity_bar.pack(side='left', padx=(16, 6))
        ttk.Label(controls, textvariable=self.activity).pack(side='left')
        self.counts = tk.StringVar(value='保存先を選択して再調査してください。')
        ttk.Label(self, textvariable=self.counts).pack(anchor='w')
        self.detail = self.tree(('plan', 'location', 'number', 'analysed', 'name', 'extra', 'size', 'date'),
                                ('予定', '場所', '番号', 'ANAL', 'ファイル名', '同名の付属', 'バイト数', '更新日時'), 8)
        for tag, color in [('archive', '#25464b'), ('restore', '#593d2d'), ('stay', ''), ('skip', '#703b42')]:
            if color:
                self.detail.tag_configure(tag, background=color, foreground='#ffffff')
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=4)
        ttk.Button(actions, text='退避予定を作成（解析済みT → T/stocked）',
                   command=lambda: self.preview(False)).pack(side='left', padx=(0, 6))
        ttk.Checkbutton(actions, text='最後の解析番号はTに残す', variable=self.keep_last,
                        command=lambda: self.plan and self.preview(self.plan['action'] == 'restore')).pack(side='left', padx=(0, 12))
        ttk.Button(actions, text='復帰予定を作成（T/stocked → T）',
                   command=lambda: self.preview(True)).pack(side='left', padx=(0, 6))
        ttk.Button(actions, text='予定を実行', command=self.execute).pack(side='left', padx=(0, 6))
        ttk.Button(actions, text='履歴から元に戻す', command=self.undo).pack(side='left', padx=(0, 6))
        ttk.Button(actions, text='実行履歴を開く', command=self.history).pack(side='left', padx=(0, 6))
        self.notice = tk.StringVar(value='DBの保存済み調査から対象を表示します。操作前に保存先を再調査します。')
        ttk.Label(self, textvariable=self.notice, wraplength=1400).pack(anchor='w')
        ttk.Label(self, text='退避：ANAL（＋ANAL/stocked）に同じ#番号がある（0バイトは除く）T直下のTDMSをT/stockedへ。未解析・欠番はTに残ります。'
                  '\n復帰：T/stockedのTDMSをT直下へ戻します。同名がある場合は移動せず残します。.tdms_indexも一緒に移動します。'
                  '\n色: 青緑＝退避予定／茶＝復帰予定／赤＝移動しない（理由は予定欄）。進捗の分母はT＋T/stockedのため退避しても変わりません。'
                  '\n実行前に解析ソフトを停止してください。DB件数の更新は実験データベースのAnalysis_serverで行ってください。').pack(anchor='w')
        self.after(100, self.poll)

    def tree(self, columns, titles, height):
        frame = ttk.Frame(self)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=height)
        for col, title in zip(columns, titles):
            tree.heading(col, text=title)
            tree.column(col, width=500 if col == 'name' else 220 if col in ('root', 'plan', 'progress') else 120)
        y = ttk.Scrollbar(frame, command=tree.yview)
        x = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        tree.grid(row=0, column=0, sticky='nsew')
        y.grid(row=0, column=1, sticky='ns')
        x.grid(row=1, column=0, sticky='ew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return tree

    def run(self, work, done, activity='処理中…'):
        others = any(getattr(getattr(self.owner, key, None), 'busy', False) for key in ('anal_panel', 'bnal_panel'))
        if self.busy or self.owner.busy or others:
            return
        self.busy = True
        self.notice.set(activity)
        self.activity.set(activity)
        self.activity_bar.start(12)
        self.scan_button.configure(state='disabled')

        def worker():
            try:
                self.events.put((done, work(), None))
            except Exception as exc:
                self.events.put((done, None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            done, value, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.activity_bar.stop()
            self.scan_button.configure(state='normal')
            self.activity.set('エラー' if error else '完了')
            if error:
                self.plan = None
                self.notice.set(error)
                messagebox.showerror('Data 退避', error, parent=self)
            else:
                done(value)
        self.after(100, self.poll)

    def choose_db(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('SQLite', '*.sqlite3 *.db'), ('すべて', '*')])
        if path:
            self.db.set(path)

    def load(self):
        path = self.db.get()

        def done(rows):
            self.inventory = rows
            self.render()
            self.notice.set(f'DBから{len(rows)}件取得（対象 {sum(r["target"] for r in rows)}件）。件数は保存済み調査時点です。')
        self.run(lambda: load_inventory(path), done)

    def refresh_selected(self, rows=None, rescan=False):
        """Update the saved counts of the selected samples from the share, then reload and keep the selection."""
        if self.busy or self.owner.busy:
            return
        rows = rows or [self.inventory[int(i)] for i in self.samples.selection()]
        if not rows:
            messagebox.showinfo('Data 退避', 'DBを更新するサンプルの行を選択してください（複数可）。', parent=self)
            return
        keys = list(dict.fromkeys((r['root'], r['experiment'], r['sample']) for r in rows))
        path = self.db.get()

        def done(inventory):
            self.inventory = inventory
            self.render()
            again = [i for i in self.samples.get_children()
                     if (self.inventory[int(i)]['root'], self.inventory[int(i)]['experiment'], self.inventory[int(i)]['sample']) in keys]
            self.samples.selection_set(again)
            if again:
                self.samples.see(again[0])
            warning = scan_warning(path, [root for root, _, _ in keys])
            self.notice.set(f'{len(keys)}サンプルのDBを更新しました。' + ('' if again else '絞り込み条件から外れたため一覧に表示されていません。')
                            + (' ' + warning.split('\n')[0] + '。データベース側の調査が同じサンプルを古い件数で上書きした場合は、もう一度更新してください。'
                               if warning else ''))
            if rescan and len(again) == 1:
                self.after(100, self.scan)  # show the real files after the move
        self.run(lambda: (refresh_db(path, keys), load_inventory(path))[1], done,
                 activity=f'DB更新中… {len(keys)}サンプル（共有を読み直しています）')

    def invalidate(self):
        self.scan_result, self.plan = None, None
        self.detail.delete(*self.detail.get_children())
        self.counts.set('保存先を選択して再調査してください。')

    def render(self):
        if self.busy:
            return
        self.invalidate()
        self.samples.delete(*self.samples.get_children())
        update_shares(self)
        for i, row in enumerate(self.inventory):
            if not matches_inventory_query(row, self.query.get(), self.exact_experiment.get()) or not share_matches(self, row):
                continue
            if self.only_targets.get() and not row['target']:
                continue
            progress = '未取得' if row['ratio'] is None else f"{row['ratio'] * 100:.1f}%（{row['analysed']}/{row['total']}）"
            values = (row['root'], row['experiment'], row['sample'], row.get('t_tdms'), row.get('t_stocked_tdms'),
                      row['analysed'], progress, row.get('last_success'))
            tag = 'stocked' if row.get('t_stocked_tdms') else 'partial' if row['target'] else ''
            self.samples.insert('', 'end', iid=str(i), tags=(tag,), values=['未取得' if v is None else v for v in values])

    def selected_row(self):
        selected = self.samples.selection()
        if len(selected) != 1:
            messagebox.showinfo('Data 退避', '保存先（共有・サンプルの行）を1件選択してください。', parent=self)
            return None, None
        return selected, self.inventory[int(selected[0])]

    def scan(self, then=None):
        selected, row = self.selected_row()
        if not row:
            return
        self.invalidate()

        def done(result):
            if self.samples.selection() != selected:
                self.invalidate()
                self.notice.set('選択先が変わりました。保存先を再調査してください。')
                return
            self.scan_result = result
            s = summary(result)
            ratio = f"{s['anal'] / s['total'] * 100:.1f}%" if s['total'] else '—'
            self.counts.set(f"現在：T直下 {s['t']}件（解析済み {s['t_analysed']}／未解析 {s['t_unanalysed']}"
                            + (f"／#番号なし {s['t_unknown']}" if s['t_unknown'] else '') + f"）　T/stocked {s['stocked']}件　"
                            f"ANAL済み番号 {s['anal']}/{s['total']}（{ratio}）　最後の解析番号 "
                            + (f"#{s['last']:03d}" if s['last'] is not None else 'なし')
                            + (f"　⚠ 欠番 {len(s['gaps'])}件（Tに残して再解析）" if s['gaps'] else ''))
            self.notice.set(f"{result['folder']} を再調査しました（DBの保存済み件数は変更していません）。")
            self.show()
            if then:
                then()
        self.run(lambda: scan_t(row['t_path']), done, activity=f"再調査中… {row['experiment']} / {row['sample']}")

    def preview(self, restore):
        if self.busy:
            return
        if not self.scan_result:
            self.scan(then=lambda: self.preview(restore))
            return
        plan, skipped = plan_moves(self.scan_result, restore, self.keep_last.get())
        self.plan = dict(plan, skipped=skipped)
        self.show()
        label = '復帰' if restore else '退避'
        count = len({Path(o['source']).stem for o in plan['operations']})
        self.notice.set(f'{label}予定：{count}件（付属ファイル含め{len(plan["operations"])}ファイル）。移動しない：{len(skipped)}件。'
                        '内容を確認して「予定を実行」を押してください。')

    def show(self):
        self.detail.delete(*self.detail.get_children())
        if not self.scan_result:
            return
        moving = {Path(o['source']).stem + '|' + str(Path(o['source']).parent) for o in (self.plan or {}).get('operations', [])}
        skipped = dict((self.plan or {}).get('skipped', []))
        folder = Path(self.scan_result['folder'])
        action = (self.plan or {}).get('action')
        for i, f in enumerate(sorted(self.scan_result['files'], key=lambda f: (f['number'] is None, f['number'] or 0, f['location']))):
            parent = folder if f['location'] == 'T' else folder / 'stocked'
            if f['stem'] + '|' + str(parent.absolute()) in moving:
                plan, tag = ('復帰' if action == 'restore' else '退避'), action
            elif f['name'] in skipped and ((action == 'restore') == (f['location'] == 'T/stocked')):
                plan, tag = '移動しない：' + skipped[f['name']], 'skip'
            else:
                plan, tag = '', 'stay'
            self.detail.insert('', 'end', iid=str(i), tags=(tag,), values=(
                plan, f['location'], '不明' if f['number'] is None else f"#{f['number']:03d}",
                '済' if f['analysed'] else '未', f['name'], ', '.join(f['extra']), f'{f["size"]:,}',
                datetime.fromtimestamp(f['modified_ns'] / 1e9).astimezone().isoformat(timespec='seconds')))

    def execute(self):
        if self.busy or self.owner.busy:
            return
        if not self.plan or not self.plan['operations']:
            messagebox.showinfo('Data 退避', '移動する予定がありません。退避予定または復帰予定を作成してください。', parent=self)
            return
        plan = {k: v for k, v in self.plan.items() if k != 'skipped'}
        label = {'archive': '退避', 'restore': '復帰', 'undo': '元に戻す'}[plan['action']]
        text = describe(plan)
        if self.plan.get('skipped'):
            text = '移動しないファイル:\n' + '\n'.join(f'  {n}（{r}）' for n, r in self.plan['skipped']) + '\n\n' + text
        row = next((r for r in self.inventory if Path(r['t_path']) == Path(plan['folder'])), None)
        warning = scan_warning(self.db.get(), [row['root']] if row else [])
        if warning:
            text = warning + '\n\n' + text
        if not self.confirm(label, text):
            return
        self.plan = None

        def done(log):
            self.notice.set(f'{label}が完了しました。履歴: {log}')
            self.owner.log.configure(state='normal')
            self.owner.log.insert('end', f'\n=== Data {label} 完了（{len(plan["operations"])}ファイル）===\n履歴: {log}\n')
            self.owner.log.configure(state='disabled')
            self.owner.log.see('end')
            if row is not None:
                self.after(100, lambda: self.refresh_selected([row], rescan=True))  # DB, then the real files
        self.run(lambda: execute_moves(plan, self.log_dir), done, activity=f'{label}を実行中…')

    def confirm(self, action, text):
        window = tk.Toplevel(self)
        window.title(f'Data {action}の確認')
        window.geometry(f'{min(1100, self.winfo_screenwidth() - 80)}x{min(650, self.winfo_screenheight() - 100)}')
        window.transient(self.winfo_toplevel())
        ttk.Label(window, text=f'{action}の全対象・移動先を確認してください。', padding=10).pack(anchor='w')
        content = ScrolledText(window, wrap='none')
        import ui_theme
        ui_theme.style_text(content)
        content.insert('1.0', text)
        content.configure(state='disabled')
        content.pack(fill='both', expand=True, padx=10)
        stopped = tk.BooleanVar(value=False)
        result = [False]

        def approve():
            result[0] = True
            window.destroy()
        buttons = ttk.Frame(window, padding=10)
        buttons.pack(fill='x')
        ok = ttk.Button(buttons, text=f'{action}を実行', command=approve, state='disabled')
        ttk.Checkbutton(buttons, text='このサンプルの解析ソフトを停止しました', variable=stopped,
                        command=lambda: ok.configure(state='normal' if stopped.get() else 'disabled')).pack(side='left')
        ttk.Button(buttons, text='キャンセル', command=window.destroy).pack(side='right', padx=6)
        ok.pack(side='right')
        window.grab_set()
        self.wait_window(window)
        return result[0]

    def undo(self):
        if self.busy or self.owner.busy:
            return
        history = filedialog.askopenfilename(parent=self, initialdir=str(self.log_dir),
                                             filetypes=[('Data 退避の履歴', '*.json')])
        if not history:
            return

        def done(plan):
            self.plan = dict(plan, skipped=[])
            self.execute()  # shows the complete reverse mapping for confirmation
        self.run(lambda: undo_plan(history), done)

    def history(self):
        self.log_dir.mkdir(exist_ok=True)
        os.startfile(self.log_dir)
