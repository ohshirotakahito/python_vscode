"""Browse the AXN Ex Info ledger and edit project Owners and descriptions."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox

from shared import ex_info_links, ex_info_store

ALL, UNSET = 'すべての責任者', '責任者未設定'


def rows(data):
    locations = {}
    for loc in data['locations']:
        locations.setdefault(loc.get('sample_key') or loc.get('project_key'), []).append(
            '\\'.join(str(loc.get(k, '')).rstrip('\\/') for k in
                      ('root_path', 'experiment_folder', 'sample_folder') if loc.get(k)))
    result = []
    for project in data['projects']:
        samples = [s for s in data['samples'] if s['project_key'] == project['key']]
        for sample in samples or [None]:
            result.append(dict(iid=('sample:' + sample['key']) if sample else 'project:' + project['key'],
                project_key=project['key'], sample_key=sample['key'] if sample else '',
                project=project['name'], sample=sample['name'] if sample else '',
                owner_names=project.get('owner_names', []), owner='、'.join(project.get('owner_names', [])),
                project_description=project.get('description', ''),
                sample_description=sample.get('description', '') if sample else '',
                medium=sample.get('medium', '') if sample else '',
                locations='・'.join(locations.get(sample['key'] if sample else project['key'], [])),
                active='有効' if project.get('active', True) and (sample is None or sample.get('active', True)) else '非表示設定'))
    return result


def matches(row, query, owner):
    return ((owner == ALL or owner in row['owner_names'] or owner == UNSET and not row['owner_names'])
            and (not query.strip() or query.strip().casefold() in ' '.join(str(row[k]) for k in
                 ('project', 'sample', 'owner', 'project_description', 'sample_description', 'medium', 'locations')).casefold()))


def create_panel(parent, window, on_saved=lambda: None):
    bar = ttk.Frame(window, padding=8)
    bar.pack(fill='x')
    query, owner = tk.StringVar(), tk.StringVar(value=ALL)
    ttk.Label(bar, text='プロジェクト名・サンプル名・説明').pack(side='left')
    ttk.Entry(bar, textvariable=query, width=30).pack(side='left', padx=6)
    ttk.Label(bar, text='責任者').pack(side='left', padx=(10, 4))
    owners = ttk.Combobox(bar, textvariable=owner, state='readonly', width=22, values=(ALL, UNSET))
    owners.pack(side='left')
    ttk.Button(bar, text='再読み込み', command=lambda: reload()).pack(side='left', padx=8)
    read_only = getattr(parent, 'read_only', False)   # ユーザー端末: Ex Info の編集は AXN で行う
    edit_button = ttk.Button(bar, text='責任者・説明を編集', command=lambda: edit())
    if not read_only:
        edit_button.pack(side='left')
    ttk.Label(window, text=f'AXNと共通の台帳: {ex_info_links.LEDGER}', padding=(8, 2)).pack(anchor='w')
    columns = ('project', 'sample', 'owner', 'project_description', 'sample_description', 'medium', 'locations', 'active')
    titles = ('プロジェクト名', 'サンプル名', '責任者（プロジェクト共通）', 'プロジェクトの説明', 'サンプルの説明', '溶媒', '登録した保存場所', '表示設定')
    tree = parent.table(columns, titles, 18, True, parent=window)
    tree.configure(selectmode='browse')
    for col in columns:
        tree.column(col, width=300 if col == 'locations' else 180, stretch=col in ('project_description', 'sample_description'))
    status = tk.StringVar()
    ttk.Label(window, textvariable=status, padding=8).pack(fill='x')
    saved = dict(data=None, rows=[])

    def show(*_):
        selected = tree.selection()
        tree.delete(*tree.get_children())
        visible = [r for r in saved['rows'] if matches(r, query.get(), owner.get())]
        for row in visible:
            tree.insert('', 'end', iid=row['iid'], values=[row[k] for k in columns])
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])
        if saved['data'] is not None:
            status.set(f"表示 {len(visible)} / {len(saved['rows'])} 行　責任者はプロジェクト内の全サンプルに共通です。"
                       '名前・保存場所は閲覧のみ。編集ではサーバーフォルダを変更しません。')

    def reload():
        try:
            data = ex_info_store.load()
            saved.update(data=data, rows=rows(data))
        except (OSError, ValueError) as exc:
            saved.update(data=None, rows=[])
            show()
            edit_button.configure(state='disabled')
            status.set(f'Ex Infoを読めません: {exc}')
            return
        edit_button.configure(state='normal')
        names = sorted({n for p in data['projects'] for n in p.get('owner_names', [])}, key=str.casefold)
        owners['values'] = (ALL, *names, UNSET)
        if owner.get() not in owners['values']:
            owner.set(ALL)
        show()

    def edit():
        selected = tree.selection()
        if read_only or not selected or saved['data'] is None:
            return
        row = next(r for r in saved['rows'] if r['iid'] == selected[0])
        baseline = copy.deepcopy(saved['data'])
        project = next(p for p in baseline['projects'] if p['key'] == row['project_key'])
        sample = next((s for s in baseline['samples'] if s['key'] == row['sample_key']), None)
        dialog = tk.Toplevel(window)
        dialog.title('Ex Info — 責任者・説明')
        dialog.transient(parent)
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=row['project'] + (' / ' + row['sample'] if sample else '')).pack(anchor='w')
        inputs = {}
        fields = [('owner_names', 'プロジェクトの責任者（全サンプル共通・1行に1人）', '\n'.join(project.get('owner_names', []))),
                  ('project_description', 'プロジェクトの説明（全サンプル共通）', project.get('description', ''))]
        if sample:
            fields.append(('sample_description', '選択したサンプルの説明', sample.get('description', '')))
        for key, title, value in fields:
            ttk.Label(body, text=title).pack(anchor='w', pady=(8, 2))
            widget = tk.Text(body, height=4, width=65, wrap='word')
            widget.insert('1.0', value)
            widget.pack(fill='both', expand=True)
            inputs[key] = widget

        def save():
            names = list(dict.fromkeys(n.strip() for n in inputs['owner_names'].get('1.0', 'end-1c').splitlines() if n.strip()))
            changes = {('projects', project['key']): dict(owner_names=names,
                       description=inputs['project_description'].get('1.0', 'end-1c'))}
            if sample:
                changes[('samples', sample['key'])] = dict(description=inputs['sample_description'].get('1.0', 'end-1c'))
            try:
                ex_info_store.save_changes(baseline, changes)
            except (OSError, ValueError) as exc:
                messagebox.showerror('Ex Infoを保存できません', str(exc), parent=dialog)
                return
            dialog.destroy()
            reload()
            on_saved()

        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(12, 0))
        ttk.Button(buttons, text='キャンセル', command=dialog.destroy).pack(side='right')
        ttk.Button(buttons, text='保存', command=save).pack(side='right', padx=8)
        dialog.grab_set()

    query.trace_add('write', show)
    owner.trace_add('write', show)
    tree.bind('<Double-1>', lambda _: edit())
    window.after(0, reload)
    return reload
