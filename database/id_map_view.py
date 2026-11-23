"""実験ID 対応 panel: 実験名 → サンプル名 → 実験ID（測定フォルダ名）を 1 行ずつ並べる（読むだけ）。

    実験名   : 解析サーバーの実験フォルダ（= Ex Info の実験。① Plan & Issues・④ Progress と同じ名前）
    サンプル名: その下のサンプルフォルダ
    実験ID   : 測定フォルダ名（AXN の Experimental ID = SQserver の元の名前）。1 サンプルに複数あることがある

材料（どれもサーバーには接続しない）:
    転送履歴   : Uploader.csv（LabVIEW 版）＋ Uploader_python.csv（exmanager）。実験ID → 転送先の実験名・サンプル名
    転送の記録 : machinedb\\SEQ\\Transfers.jsonl。転送先の共有と計測区分（サンプル / ブランク）
    計画       : machinedb\\PLAN\\MeasurementLinks.jsonl（AXN の G PSET）。実験ID → Ex Info の実験・サンプル
"""
import tkinter as tk
from tkinter import ttk

from shared import ex_info_links, seq_records, ui_theme

try:
    from . import transfer_ledger
except ImportError:
    import transfer_ledger

COLUMNS = ('experiment', 'sample', 'experimental_id', 'machine', 'kind', 'experiment_at', 'sq', 'state',
           'transferred', 'shares', 'plan')
TITLES = ('実験名', 'サンプル名', '実験ID（測定フォルダ名）', '装置', '計測区分', '実験日時', 'SQserver の測定フォルダ',
          '状態', '転送日時', '転送先の共有', '計画（Ex Info）')
WIDTHS = dict(experiment=170, sample=140, experimental_id=330, machine=60, kind=90, experiment_at=130, sq=170,
              state=230, transferred=200, shares=170, plan=300)

AS_PLANNED = '計画どおり転送'
TRANSFERRED = '転送済み（計画なし）'
OFF_PLAN = '計画と別の送り先'
PLANNED_ONLY = '計画のみ（未転送）'
UNSORTED = 'SQserver のみ（解析サーバーへ未整理）'
STATE_TAGS = {OFF_PLAN: ('danger', 0.3), UNSORTED: ('warning', 0.14), PLANNED_ONLY: ('info', 0.2)}
STATES = (AS_PLANNED, TRANSFERRED, OFF_PLAN, PLANNED_ONLY, UNSORTED)
ALL_MACHINES, ALL_STATES = 'すべての装置', 'すべての状態'
NOT_ON_SQ = 'なし'
ARCHIVE_SUFFIXES = ('.zip', '.7z', '.rar', '.tar', '.gz')


def _date(text):
    """'2026/10/31_14:14' -> '2026-10-31 14:14'."""
    return str(text or '').replace('/', '-').replace('_', ' ')


def _id_date(eid):
    """'20261110_1055 UNAN#2…' -> '2026-11-10 10:55' (the measurement folder starts with its date), else ''."""
    head = str(eid)[:13]
    if len(head) == 13 and head[8] == '_' and (head[:8] + head[9:]).isdigit():
        return f'{head[:4]}-{head[4:6]}-{head[6:8]} {head[9:11]}:{head[11:13]}'
    return ''


def _share(root):
    parts = [p for p in str(root or '').replace('/', '\\').split('\\') if p]
    return '/'.join(parts[:2])


def _folder_name(entry):
    """The measurement folder name of a SQserver entry (an archive counts under its name without the suffix)."""
    name = str(entry.get('name', ''))
    if entry.get('entry_type') == '圧縮ファイル' and name.casefold().endswith(ARCHIVE_SUFFIXES):
        name = name.rsplit('.', 1)[0]
    return name


def build_rows(history, transfers=(), plans=None, sq_entries=None):
    """One row per (実験ID, 実験名, サンプル名): what was transferred where, and what Ex Info planned.

    history: Uploader CSV rows (17 columns); transfers: ex_info_links.Transfer; plans: {実験ID: Planned};
    sq_entries: the saved SQserver list (② と同じ: name・group・machine・experiment_at). Measurement folders there
    with no transfer become rows without 実験名・サンプル名 (UNSORTED). None = the list is not read yet."""
    plans = plans or {}
    rows = {}

    def entry(eid, experiment, sample):
        key = (eid.casefold(), experiment.casefold(), sample.casefold())
        return rows.setdefault(key, dict(experimental_id=eid, experiment=experiment, sample=sample, machine='',
                                         kind='', experiment_at='', dates=[], shares=[], plan='', planned=False))

    for record in history:
        uploaded_at, machine, experiment_at = record[0], record[2], record[4]
        eid, experiment, sample = record[6], record[7], record[8]
        if not (eid and experiment):
            continue
        row = entry(eid, experiment, sample)
        row['machine'] = row['machine'] or machine
        row['experiment_at'] = row['experiment_at'] or _date(experiment_at)
        row['dates'].append(uploaded_at)
    for t in transfers:
        if not (t.source and t.experiment):
            continue
        row = entry(t.source, t.experiment, t.sample)
        row['machine'] = row['machine'] or t.machine
        row['kind'] = row['kind'] or t.kind
        if not row['dates'] and t.transferred_at:      # recorded but missing from the CSV history
            row['dates'].append(t.transferred_at[:16].replace('T', ' ').replace('-', '/'))
        share = _share(t.root)
        if share and share not in row['shares']:
            row['shares'].append(share)
    for eid, plan in plans.items():
        experiment = plan.experiment_folder if plan.decided else plan.project
        sample = plan.sample_folder if plan.decided else plan.sample
        transferred = [r for k, r in rows.items() if k[0] == eid.casefold() and r['dates']]
        match = [r for r in transferred if r['experiment'].casefold() == experiment.casefold()
                 and r['sample'].casefold() == sample.casefold()]
        text = plan.destination if plan.decided else f'{plan.project} / {plan.sample}（保存先未定）'
        for r in transferred:
            r['plan'] = text
            r['planned'] = any(r is m for m in match)
            r['kind'] = r['kind'] or plan.kind
        if not match:
            row = entry(eid, experiment, sample)
            row.update(plan=text, kind=row['kind'] or plan.kind)
            row['planned'] = True
    on_sq = {}
    for e in sq_entries or ():
        name = _folder_name(e)
        if name and (_id_date(name) or e.get('parse_status') == '解析済み'):   # 人名などの整理用フォルダは除く
            on_sq.setdefault(name.casefold(), []).append(e)
    known = {k[0] for k in rows}
    for key, found in on_sq.items():
        if key not in known:
            first = found[0]
            row = entry(_folder_name(first), '', '')
            row.update(machine=first.get('machine') or '', experiment_at=str(first.get('experiment_at') or ''))
    checked = '・'.join(sorted({e.get('group', '') for e in sq_entries or () if e.get('group')}))
    missing = f'{NOT_ON_SQ}（{checked} を確認）' if checked else NOT_ON_SQ
    out = []
    for row in rows.values():
        groups = dict.fromkeys(e.get('group', '') for e in on_sq.get(row['experimental_id'].casefold(), ()))
        row['sq'] = '・'.join(g for g in groups if g) or (missing if sq_entries is not None else '')
        row['experiment_at'] = row['experiment_at'] or _id_date(row['experimental_id'])
        dates = sorted(row.pop('dates'))
        if not row['experiment']:
            state = UNSORTED
        elif not dates:
            state = PLANNED_ONLY
        elif row['plan']:
            state = AS_PLANNED if row['planned'] else OFF_PLAN
        else:
            state = TRANSFERRED
        transferred = ''
        if dates:
            transferred = _date(dates[0]) + (f'（ほか {len(dates) - 1} 回）' if len(dates) > 1 else '')
        out.append(dict(row, state=state, transferred=transferred, shares='・'.join(row.pop('shares'))))
    out.sort(key=lambda r: (not r['experiment'], r['experiment'].casefold(), r['sample'].casefold(), r['experiment_at'],
                            r['experimental_id'].casefold()))
    return out


def matches(row, query, machine=ALL_MACHINES, focus=None, state=ALL_STATES):
    if state != ALL_STATES and row['state'] != state:
        return False
    if focus is not None and (row['experiment'].casefold(), row['sample'].casefold()) not in focus:
        return False
    if machine != ALL_MACHINES and row['machine'] != machine:
        return False
    text = query.strip().casefold()
    return not text or any(text in row[k].casefold() for k in ('experiment', 'sample', 'experimental_id'))


def load(sq_entries=None):
    """(rows, warnings) from the files on machinedb; a missing file is a warning, not an error."""
    warnings = []
    try:
        history = seq_records.history(seq_records.LEGACY_HISTORY, seq_records.LOCAL_HISTORY)
    except (OSError, ValueError) as exc:
        history = []
        warnings.append(f'転送履歴を読めません: {exc}')
    try:
        transfers = ex_info_links.load_transfers()
    except OSError as exc:
        transfers = []
        warnings.append(f'転送の記録を読めません: {exc}')
    try:
        plans = ex_info_links.load_plans()
    except OSError as exc:
        plans = {}
        warnings.append(f'計画（MeasurementLinks）を読めません: {exc}')
    if sq_entries is not None:
        try:   # 装置は backup フォルダで決める（backup_06 → AXN2。転送台帳と同じ）。読めなければ名前から読んだ装置のまま
            backups = transfer_ledger.machine_by_backup(seq_records.machines(seq_records.MACHINE_CSV))
        except (OSError, ValueError):
            backups = {}
        sq_entries = [dict(e, machine=backups.get(str(e.get('container', '')).casefold()) or e.get('machine') or '')
                      for e in sq_entries]
    else:
        warnings.append('SQserver の実験一覧（② と同じ保存済みデータ）を読み込み中。読み込むと未整理の測定フォルダも表示します')
    return build_rows(history, transfers, plans, sq_entries), warnings


def create_panel(parent, window):
    """Build the panel; returns (focus(keys) showing only these (実験名, サンプル名), set_sq_entries(entries))."""
    ttk.Label(window, padding=(8, 6, 8, 0), wraplength=1300, style='Muted.TLabel',
              text='実験名（解析サーバーの実験フォルダ・Ex Info の実験）› サンプル名（その下のフォルダ）› '
                   '実験ID（測定フォルダ名。AXN の Experimental ID・SQserver の元の名前）。1 サンプルに複数の実験IDがあります。'
                   '実験IDは SQserver の測定フォルダ側から見ています（② と同じ保存済み一覧）。解析サーバー側はファイル数だけを'
                   '保存しているため、転送履歴・転送の記録で結び付けます。どこにも転送していない測定フォルダは、実験名・サンプル名が'
                   '空の「SQserver のみ」の行（黄）になります。サーバーには接続しません。').pack(fill='x')
    bar = ttk.Frame(window, padding=8)
    bar.pack(fill='x')
    query, machine, state = tk.StringVar(), tk.StringVar(value=ALL_MACHINES), tk.StringVar(value=ALL_STATES)
    ttk.Label(bar, text='絞り込み（実験名 / サンプル名 / 実験ID）').pack(side='left')
    ttk.Entry(bar, textvariable=query, width=30).pack(side='left', padx=6)
    ttk.Label(bar, text='装置').pack(side='left', padx=(10, 4))
    machines = ttk.Combobox(bar, textvariable=machine, state='readonly', width=14, values=(ALL_MACHINES,))
    machines.pack(side='left')
    ttk.Label(bar, text='状態').pack(side='left', padx=(10, 4))
    ttk.Combobox(bar, textvariable=state, state='readonly', width=34, values=(ALL_STATES, *STATES)).pack(side='left')
    ttk.Button(bar, text='再読み込み', command=lambda: reload()).pack(side='left', padx=8)
    focus_text = tk.StringVar()
    focus_label = ttk.Label(bar, textvariable=focus_text)
    focus_label.pack(side='left', padx=(10, 4))
    clear_button = ttk.Button(bar, text='絞り込みを解除', command=lambda: focus(None))
    status = tk.StringVar()
    ttk.Label(window, textvariable=status, padding=(8, 4), wraplength=1300).pack(side='bottom', fill='x')
    ttk.Label(window, padding=(8, 0), style='Muted.TLabel',
              text=f'赤＝{OFF_PLAN}（Ex Info の計画と違う実験名・サンプル名に転送）　黄＝{UNSORTED}　青＝{PLANNED_ONLY}'
                   f'　SQserver の測定フォルダ「{NOT_ON_SQ}」＝② で取得済みのグループにない（未取得のグループにある・移動・削除）').pack(
        side='bottom', fill='x')
    tree = parent.table(COLUMNS, TITLES, 18, True, parent=window)
    for col in COLUMNS:
        tree.column(col, width=WIDTHS[col], stretch=col == 'plan')
    for tag, (hue, strength) in STATE_TAGS.items():
        tree.tag_configure(tag, background=ui_theme.row(hue, strength))
    saved = dict(rows=[], warnings=[], focus=None, loaded=False, sq=None)

    def show(*_):
        tree.delete(*tree.get_children())
        shown = [r for r in saved['rows'] if matches(r, query.get(), machine.get(), saved['focus'], state.get())]
        for r in shown:
            tree.insert('', 'end', values=[r[c] for c in COLUMNS], tags=(r['state'],) if r['state'] in STATE_TAGS else ())
        samples = {(r['experiment'].casefold(), r['sample'].casefold()) for r in shown if r['experiment']}
        unsorted = sum(r['state'] == UNSORTED for r in saved['rows'])
        status.set(f"表示 {len(shown):,} 実験ID / {len(samples):,} サンプル（全 {len(saved['rows']):,} 行・"
                   f"{UNSORTED} {unsorted:,}）"
                   + ''.join(f'　⚠ {w}' for w in saved['warnings']))

    def reload():
        saved['rows'], saved['warnings'] = load(saved['sq'])
        saved['loaded'] = True
        machines['values'] = (ALL_MACHINES, *sorted({r['machine'] for r in saved['rows'] if r['machine']}))
        if machine.get() not in machines['values']:
            machine.set(ALL_MACHINES)
        show()

    def focus(keys):
        if keys:
            saved['focus'] = {(e.casefold(), s.casefold()) for e, s in keys}
            names = [f'{e} / {s}' for e, s in keys]
            focus_text.set('① から: ' + '、'.join(names[:3]) + (f' ほか {len(names) - 3}' if len(names) > 3 else ''))
            query.set('')
            state.set(ALL_STATES)
            clear_button.pack(side='left')
        else:
            saved['focus'] = None
            focus_text.set('')
            clear_button.pack_forget()
        if saved['loaded']:
            show()
        else:
            reload()

    def set_sq_entries(entries):
        saved['sq'] = list(entries)
        reload()

    query.trace_add('write', show)
    machine.trace_add('write', show)
    state.trace_add('write', show)
    window.after(0, reload)
    return focus, set_sq_entries
