"""サンプル台帳 panel: the sample ledger (machinedb) next to the saved scan, with each sample's issues.

Reading never touches the servers. Edits write only the chosen fields of the selected samples to the ledger
(shared.sample_plan.update), so changes made meanwhile on another PC are kept.
The right-click menu is the starting point for a sample: open it in ④ Progress (ANAL / BNAL), or re-read it from the
analysis servers (only the selected samples; the one action here that touches the servers).
"""
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk, messagebox

try:
    from . import bnal_progress, rackstation_view
except ImportError:
    import bnal_progress, rackstation_view
from shared import bnal_lists, catalog, machinedb, plan_check, rack_store, sample_plan, ui_theme  # noqa: E402

COLUMNS = ('experiment', 'sample', 'shares', 'anal', 'bnal_started', 'kind', 'bnal', 'seqlists', 'bc', 'issues', 'updated')
TITLES = ('実験名', 'サンプル名', '共有', 'ANAL（最も進んだ共有）', 'BNAL開始target数', '試料の種類', 'BNAL', 'SEQlist', 'BC',
          '確認事項', '最終更新')
WIDTHS = dict(experiment=170, sample=130, shares=210, anal=150, bnal_started=110, kind=80, bnal=70, seqlists=180,
              bc=110, issues=330, updated=230)
VIEWS = {'すべて': lambda r: True, '確認事項あり': lambda r: r['issues'],
         '矛盾': lambda r: r['level'] == plan_check.CONFLICT, '設定不足': lambda r: plan_check.MISSING in
         {plan_check.ISSUES[i] for i in r['issues']}, '作業待ち（BNAL未着手）': lambda r: 'BNAL未着手' in r['issues'],
         '未分類': lambda r: not r['bnal'], 'BNAL対象': lambda r: r['bnal'] == 'target',
         'BNAL対象外': lambda r: r['bnal'] == 'excluded'}
KEEP, NONE = '変更しない', 'なし'
LEVEL_TAGS = {plan_check.CONFLICT: ('danger', 0.3), plan_check.MISSING: ('warning', 0.14),
              plan_check.WAITING: ('info', 0.2)}


def row_values(r):
    return dict(r, shares='、'.join(r['shares']) or '—', bnal=sample_plan.BNAL.get(r['bnal'], '未分類'),
                seqlists='、'.join(r['seqlists']), issues='、'.join(r['issues']),
                updated=f"{r['updated']}（{r['updated_by']}）" if r['updated'] else '')


def changes_for(keys, kind, bnal, seqlist, bc):
    """Ledger changes for the selected samples; KEEP leaves a field alone, 未設定 / 未分類 / なし clear it."""
    fields = {}
    if kind != KEEP:
        fields['kind'] = kind if kind in sample_plan.KINDS else None
    if bnal != KEEP:
        fields['bnal'] = {label: code for code, label in sample_plan.BNAL.items()}.get(bnal)
    if seqlist != KEEP:
        fields['seqlists'] = [] if seqlist == NONE else [seqlist.split('（')[0]]
    if bc != KEEP:
        fields['bc'] = None if bc == NONE else bc.split('（')[0]
    return {k: dict(fields) for k in keys} if fields else {}


def refresh_targets(keys, samples, roots, is_dir=lambda path: Path(path).is_dir()):
    """(root, experiment, sample) to re-read for the chosen (experiment, sample) keys.

    Every share where the DB already has the sample, plus every analysis share whose T folder exists now (so a
    sample copied since the last scan, or a ledger-only sample, is found). Roots are compared by identity."""
    found = {}
    for r in samples:
        if (r['experiment'], r['sample']) in keys:
            found.setdefault((catalog.root_identity(r['root']), r['experiment'], r['sample']), r['root'])
    for experiment, sample in keys:
        for root in roots:
            ident = (catalog.root_identity(root), experiment, sample)
            if ident not in found and is_dir(Path(root) / experiment / sample / f'{sample}_10k_Sample' / 'T'):
                found[ident] = root
    return [(root, experiment, sample) for (_, experiment, sample), root in found.items()]


def create_panel(parent, window):
    bar = ttk.Frame(window, padding=8)
    bar.pack(fill='x')
    query, view = tk.StringVar(), tk.StringVar(value='確認事項あり')
    ttk.Label(bar, text='絞り込み（実験 / サンプル）').pack(side='left')
    ttk.Entry(bar, textvariable=query, width=28).pack(side='left', padx=6)
    ttk.Label(bar, text='表示').pack(side='left', padx=(10, 4))
    ttk.Combobox(bar, textvariable=view, values=list(VIEWS), state='readonly', width=22).pack(side='left')
    ttk.Button(bar, text='台帳とDBを再読み込み', command=lambda: reload()).pack(side='left', padx=10)
    ttk.Label(bar, text=f'台帳: {machinedb.SAMPLE_PLAN}', style='Muted.TLabel').pack(side='right')

    edit = ttk.LabelFrame(window, text='選択したサンプルの台帳を変更（Ctrl / Shift で複数選択。「変更しない」の項目はそのまま）',
                          padding=8)
    edit.pack(fill='x', padx=8)
    choice = {}
    for n, (key, label, width) in enumerate((('kind', '試料の種類', 10), ('bnal', 'BNAL', 10),
                                             ('seqlist', 'SEQlist', 34), ('bc', 'BC', 24))):
        ttk.Label(edit, text=label).grid(row=0, column=n * 2, padx=(0 if n == 0 else 14, 4), sticky='w')
        choice[key] = tk.StringVar(value=KEEP)
        box = ttk.Combobox(edit, textvariable=choice[key], state='readonly', width=width)
        box.grid(row=0, column=n * 2 + 1)
        choice[key + '_box'] = box
    choice['kind_box']['values'] = (KEEP, '未設定', *sample_plan.KINDS)
    choice['bnal_box']['values'] = (KEEP, '未分類', *sample_plan.BNAL.values())
    ttk.Button(edit, text='選択したサンプルに適用', style='Execute.TButton', command=lambda: apply()).grid(
        row=0, column=8, padx=(18, 0))

    status = tk.StringVar()
    ttk.Label(window, textvariable=status, padding=(8, 4), wraplength=1300).pack(side='bottom', fill='x')
    ttk.Label(window, padding=(8, 0), wraplength=1300, style='Muted.TLabel',
              text='確認事項　赤＝矛盾（台帳と実際が食い違う・参照先がない）　黄＝設定不足（未分類・種類・SEQlist・BC）　'
                   '青＝作業待ち（BNAL 対象で ANAL 完了なのに BNAL 未開始）。BNAL@ フォルダが空のものは開始に数えません。'
                   '行を右クリック（ダブルクリック）で ④ Progress を開く・解析サーバーから再取得。'
                   'SEQlist・BC は machinedb の表で、核酸・アミノ酸は中身で判定します。').pack(side='bottom', fill='x')
    tree = parent.table(COLUMNS, TITLES, 18, True, parent=window)
    tree.configure(selectmode='extended')
    for col in COLUMNS:
        tree.column(col, width=WIDTHS[col], stretch=col in ('issues', 'shares'))
    for lv, (hue, strength) in LEVEL_TAGS.items():
        tree.tag_configure(lv, background=ui_theme.row(hue, strength))
    saved = dict(rows=[])

    def show(*_):
        text = query.get().strip().casefold()
        wanted = VIEWS[view.get()]
        tree.delete(*tree.get_children())
        shown = [r for r in saved['rows'] if wanted(r)
                 and (not text or text in r['experiment'].casefold() or text in r['sample'].casefold())]
        # worst first (矛盾 → 設定不足 → 作業待ち → none), then by experiment / sample
        shown.sort(key=lambda r: (plan_check.LEVELS.index(r['level']) if r['level'] else len(plan_check.LEVELS),
                                  r['key'].casefold()))
        for r in shown:
            values = row_values(r)
            tree.insert('', 'end', iid=r['key'], values=[parent.cell(values, c) for c in COLUMNS],
                        tags=(r['level'],) if r['level'] else ())
        # a sample counts under every level it has (e.g. 設定不足 and BNAL未着手 together)
        counts = {lv: sum(any(plan_check.ISSUES[i] == lv for i in r['issues']) for r in saved['rows'])
                  for lv in plan_check.LEVELS}
        status.set(f"表示 {len(shown):,} / {len(saved['rows']):,} サンプル　"
                   + '　'.join(f'{lv} {n:,}' for lv, n in counts.items())
                   + (f"　⚠ 台帳に同期の競合コピーがあります: {'、'.join(p.name for p in saved['conflicts'])}"
                      if saved.get('conflicts') else ''))

    def reload():
        try:
            samples, bnals = bnal_progress.load_saved(parent.db_path)
            plan = sample_plan.load()
            seq_kinds, bc_kinds = bnal_lists.seq_files(), bnal_lists.bc_files()
        except Exception as exc:
            status.set(f'読み込めません: {exc}')
            return
        labels = bnal_progress.group_labels(parent.configured)
        saved['rows'] = plan_check.sample_rows(samples, bnals, plan, labels, seq_kinds, bc_kinds)
        saved['conflicts'] = sample_plan.conflicted_copies()
        label = dict(Nucleotide='核酸', Peptide='アミノ酸', Base='核酸', **{'Amino acid': 'アミノ酸'})
        choice['seqlist_box']['values'] = (KEEP, NONE, *(f'{n}（{label.get(k, "不明")}）' for n, k in seq_kinds.items()))
        choice['bc_box']['values'] = (KEEP, NONE, *(f'{n}（{label.get(k, "不明")}）' for n, k in bc_kinds.items()))
        show()

    def apply():
        keys = list(tree.selection())
        changes = changes_for(keys, *(choice[k].get() for k in ('kind', 'bnal', 'seqlist', 'bc')))
        if not keys or not changes:
            messagebox.showinfo('サンプル台帳', 'サンプルを選び、変更する項目を「変更しない」以外にしてください。', parent=window)
            return
        fields = next(iter(changes.values()))
        text = '、'.join(f'{name}={value if value not in (None, []) else "（消去）"}' for name, value in fields.items())
        if not messagebox.askyesno('サンプル台帳', f'{len(keys)} サンプルの台帳を変更します。\n{text}\n\n'
                                   f'例: {", ".join(keys[:5])}{" …" if len(keys) > 5 else ""}', parent=window):
            return
        try:
            sample_plan.update(changes)
        except (OSError, ValueError) as exc:
            messagebox.showerror('サンプル台帳', f'保存できませんでした。\n{exc}', parent=window)
            return
        for k in ('kind', 'bnal', 'seqlist', 'bc'):
            choice[k].set(KEEP)
        reload()
        for k in keys:
            if tree.exists(k):
                tree.selection_add(k)

    def chosen():
        """(experiment, sample) of the selected rows, in table order."""
        rows = {r['key']: r for r in saved['rows']}
        return [(rows[k]['experiment'], rows[k]['sample']) for k in tree.selection() if k in rows]

    def open_progress(stage):
        keys = chosen()
        if keys:
            parent.open_progress(stage, keys)

    def refresh():
        keys = chosen()
        title = '解析サーバーから再取得'
        if not keys:
            return
        if (Path(parent.db_path) != Path(catalog.DEFAULT_DB) or parent.busy or getattr(parent, 'rack_busy', False)
                or getattr(parent, 'sample_refresh_busy', False) or getattr(parent.fileops, 'busy', False)):
            messagebox.showinfo(title, 'ローカルDBを表示中で、更新処理・ファイル操作が動いていないときに実行してください。',
                                parent=window)
            return
        roots = [rackstation_view.default_root(parent.configured, server, share)
                 for server, share in rackstation_view.ANALYSIS_GROUPS]
        try:
            samples, _ = bnal_progress.load_saved(parent.db_path)
        except Exception as exc:
            status.set(f'DBを読めません: {exc}')
            return
        parent.sample_refresh_busy = True
        status.set(f'{len(keys)} サンプルを解析サーバーから再取得しています…（サーバーを探してから T を読みます）')

        def work():
            results, failed = [], []
            try:
                targets = refresh_targets(set(keys), samples, roots)
                for root, experiment, sample in targets:
                    try:
                        rack_store.refresh_sample(parent.db_path, root, experiment, sample)
                        results.append((root, experiment, sample))
                    except (OSError, ValueError) as exc:
                        failed.append(f'{experiment}/{sample}（{root}）: {exc}')
            except OSError as exc:
                failed.append(str(exc))
            window.after(0, lambda: finish(results, failed))

        def finish(results, failed):
            parent.sample_refresh_busy = False
            found = {(e, s) for _, e, s in results}
            missing = [f'{e}/{s}' for e, s in keys if (e, s) not in found]
            reload()
            for name in ('anal', 'bnal'):
                (parent.progress_reloads.get(name) or (lambda: None))()
            status.set(f'再取得 {len(found)}/{len(keys)} サンプル（{len(results)} 共有）'
                       + (f'　どの解析サーバーにもない: {"、".join(missing[:5])}{" …" if len(missing) > 5 else ""}'
                          if missing else '')
                       + (f'　失敗 {len(failed)}件: {failed[0]}' if failed else ''))

        threading.Thread(target=work, daemon=True).start()

    menu = tk.Menu(window, tearoff=False)
    menu.add_command(label='④ Progress › Stage 1 · ANAL で開く', command=lambda: open_progress('anal'))
    menu.add_command(label='④ Progress › Stage 2 · BNAL で開く（台帳の SEQlist で集計）', command=lambda: open_progress('bnal'))
    menu.add_separator()
    menu.add_command(label='選択サンプルを解析サーバーから再取得', command=refresh)

    def popup(event):
        item = tree.identify_row(event.y)
        if item and item not in tree.selection():
            tree.selection_set(item)
        if tree.selection():
            menu.tk_popup(event.x_root, event.y_root)

    tree.bind('<Button-3>', popup)
    tree.bind('<Double-1>', popup)
    query.trace_add('write', show)
    view.trace_add('write', show)
    window.after(0, reload)
    return reload
