"""Read-only Recent_Data folder/history comparison (exmanager が書く SEQ 履歴CSVを shared.seq_records で読む)."""
from collections import defaultdict
from pathlib import Path, PureWindowsPath
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # リポジトリ直下（shared/ を読むため）
from shared import seq_records, ui_theme  # noqa: E402


def inspect_recent(machine_csv, legacy, local):
    machines = seq_records.machines(machine_csv)
    history_error = ''
    try:
        records = seq_records.history(legacy, local)
    except (OSError, ValueError, UnicodeError) as exc:
        records = []
        history_error = str(exc)
    index = defaultdict(list)
    for row in records:
        index[(row[2], row[6])].append(row)
    roots = defaultdict(list)
    for machine, info in machines.items():
        root = info.get('Tmp_backup', '').strip()
        name = root.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1].lower()
        if name in {f'backup_{n:02}' for n in range(1, 9)}:
            roots[(name, root)].append(machine)
    summaries, details = [], []
    for number in range(1, 9):
        backup = f'backup_{number:02}'
        matched = [(root, codes) for (name, root), codes in roots.items() if name == backup]
        if not matched:
            summaries.append((backup, '未設定', '', '', '', '', 'Machine_N.csvにTmp_backupの設定がありません'))
        for root, codes in matched:
            try:
                folders = sorted(p.name for p in Path(root).iterdir() if p.is_dir())
            except OSError as exc:
                summaries.append((backup, '確認不可', '', '', '', '', str(exc)))
                continue
            started = 0
            for name in folders:
                hits = sorted({r for code in codes for r in index[(code, name)]}, reverse=True)
                state = '確認不可' if history_error else '着手済み' if hits else '未着手'
                started += bool(hits)
                details.append((backup, name, state, len(hits) if not history_error else '',
                                hits[0][0] if hits else '',
                                '; '.join(sorted({r[7] + '/' + r[8] for r in hits})), root))
            total = len(folders)
            summaries.append((backup, '履歴確認不可' if history_error else '確認済み', total,
                              '' if history_error else started,
                              '' if history_error else total - started,
                              '' if history_error or not total else f'{started / total:.1%}', history_error))
    return summaries, details


def inventory_counts(path):
    """Direct experiment counts from the saved DB; history reads never write CSVs."""
    from shared.catalog import connect
    counts = {}
    try:
        machines = seq_records.machines(seq_records.MACHINE_CSV)
        records = seq_records.history(seq_records.LEGACY_HISTORY, seq_records.LOCAL_HISTORY)
        codes = defaultdict(set)
        for code, info in machines.items():
            codes[str(PureWindowsPath(info.get('Tmp_backup', ''))).casefold()].add(code)
        names = defaultdict(set)
        for row in records:
            names[row[2]].add(row[6])
        history_error = ''
    except (OSError, ValueError, UnicodeError) as exc:
        codes, names = {}, {}
        history_error = str(exc)
    with connect(path, readonly=True) as db:
        for source in db.execute('SELECT * FROM sources'):
            root = str(PureWindowsPath(source['root'])).casefold()
            backup = root.rstrip('\\').rsplit('\\', 1)[-1]
            if source['server'].casefold() != 'sqserver' or backup not in {f'backup_{n:02}' for n in range(1, 9)}:
                continue
            folders = {r[0] for r in db.execute(
                "SELECT relative_path FROM folders WHERE source_id=? AND relative_path!='.' AND instr(relative_path,'/')=0",
                (source['id'],))}
            known = source['last_success'] is not None
            machine_codes = codes.get(root, set())
            started_names = set().union(*(names.get(code, set()) for code in machine_codes))
            counts[root] = dict(direct_folders=len(folders) if known else '未取得',
                                analysis_started=len(folders & started_names) if known and machine_codes and not history_error else '確認不可',
                                count_note=history_error or ('' if machine_codes else '装置CSVの対応設定なし'))
    return counts


def open_window(parent):
    window = tk.Toplevel(parent)
    window.title('Recent_Data 解析着手状況（backup_01〜08）')
    window.geometry('1200x760')
    fields = {}
    for label, key, default in (
        ('装置CSV', 'machines', seq_records.MACHINE_CSV),
        ('LabVIEW版履歴CSV', 'legacy', seq_records.LEGACY_HISTORY),
        ('Python版履歴CSV', 'local', seq_records.LOCAL_HISTORY),
    ):
        bar = ttk.Frame(window, padding=4)
        bar.pack(fill='x')
        ttk.Label(bar, text=label, width=20).pack(side='left')
        value = tk.StringVar(value=str(default))
        fields[key] = value
        ttk.Entry(bar, textvariable=value).pack(side='left', fill='x', expand=True)

        def browse(value=value):
            path = filedialog.askopenfilename(parent=window, filetypes=[('CSV', '*.csv')])
            if path:
                value.set(path)

        ttk.Button(bar, text='選択', command=browse).pack(side='left')
    ttk.Label(window, text='着手済み＝装置コードと元フォルダ名がコピー履歴に一致。コピー全件完了や解析完了を示すものではありません。').pack(anchor='w', padx=8)
    controls = ttk.Frame(window, padding=8)
    controls.pack(fill='x')
    status = tk.StringVar(value='「現状を照合」でSQserver直下のフォルダ名と履歴を読み取ります。')
    only_pending = tk.BooleanVar(value=False)
    summaries = parent.table(('backup', 'state', 'total', 'started', 'pending', 'rate', 'error'),
                             ('backup', '確認状態', '現存フォルダ数', '着手済み', '未着手', '着手率', '確認エラー'),
                             8, parent=window)
    details = parent.table(('backup', 'name', 'state', 'count', 'latest', 'destination', 'root'),
                           ('backup', '実験フォルダ名', '着手状況', '履歴件数', '最終コピー履歴', '保存先実験／サンプル', '確認元'),
                           16, True, parent=window)
    details.tag_configure('pending', background=ui_theme.row('warning'))
    results = queue.Queue()
    data = []

    def render():
        details.delete(*details.get_children())
        for row in data:
            if only_pending.get() and row[2] != '未着手':
                continue
            details.insert('', 'end', values=row, tags=('pending',) if row[2] == '未着手' else ())

    def refresh():
        paths = [fields[key].get() for key in ('machines', 'legacy', 'local')]
        button.config(state='disabled')
        status.set('SQserverのフォルダ名とコピー履歴を照合中…')

        def worker():
            try:
                results.put((inspect_recent(*paths), None))
            except Exception as exc:
                results.put((None, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def poll():
        nonlocal data
        try:
            result, error = results.get_nowait()
            button.config(state='normal')
            summaries.delete(*summaries.get_children())
            data = []
            if error:
                status.set('確認できません: ' + error)
            else:
                summary_rows, data = result
                for row in summary_rows:
                    summaries.insert('', 'end', values=row)
                status.set('照合終了。確認不可・未設定は未着手数に含めません。再照合で現状を更新できます。')
            render()
        except queue.Empty:
            pass
        window.after(150, poll)

    button = ttk.Button(controls, text='現状を照合', command=refresh)
    button.pack(side='left')
    ttk.Checkbutton(controls, text='未着手のみ表示', variable=only_pending, command=render).pack(side='left', padx=12)
    ttk.Label(window, textvariable=status, wraplength=1150, padding=8).pack(fill='x')
    details.bind('<Double-1>', lambda _: parent.show_row_details(details))
    window.after(150, poll)
