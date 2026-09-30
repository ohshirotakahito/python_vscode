"""ファイル管理 GUI。起動: python filemanager/app.py"""
import json
import errno
import stat
import time
import shutil
import tempfile
from datetime import datetime, timedelta
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

BASE = Path(__file__).resolve().parent
ERROR_PREFIX = '__FILEMANAGER_ERROR__'
PROGRESS_PREFIX = '__FILEMANAGER_PROGRESS__'


def format_file_size(size):
    """コピー予定で読みやすいファイルサイズを返す。"""
    value = float(size)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{int(value)} {unit}' if unit == 'B' else f'{value:.2f} {unit}'
        value /= 1024


def copy_with_progress(plans):
    """一時ファイルへ分割転送し、転送バイト数をGUIへ通知する。"""
    files = []
    skipped = 0
    for source, destination, extension in plans:
        with os.scandir(source) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                if entry.is_file() and entry.name.lower().endswith(extension.lower()):
                    target = os.path.join(destination, entry.name)
                    if os.path.exists(target):
                        skipped += 1
                    else:
                        files.append((entry.path, target, entry.stat().st_size))
    total = sum(size for _, _, size in files)
    done = completed = copied = 0
    started = time.monotonic()
    last = started

    def report(force=False):
        nonlocal last
        now = time.monotonic()
        if force or now - last >= 0.25:
            print(PROGRESS_PREFIX + json.dumps(dict(done=done, total=total, completed=completed,
                  count=len(files), skipped=skipped, elapsed=now-started)), flush=True)
            last = now

    report(True)
    for source, target, size in files:
        if os.path.exists(target):
            total -= size
            completed += 1
            skipped += 1
            report(True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = None
        transferred = 0
        try:
            with open(source, 'rb') as reader, tempfile.NamedTemporaryFile(
                    dir=os.path.dirname(target), prefix='.filemanager-', suffix='.part', delete=False) as writer:
                temporary = writer.name
                while True:
                    chunk = reader.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    transferred += len(chunk)
                    done += len(chunk)
                    report()
            total += transferred - size
            shutil.copymode(source, temporary)
            # Windowsのrenameは既存の宛先を上書きしない。
            if os.path.exists(target):
                skipped += 1
            else:
                try:
                    os.rename(temporary, target)
                except FileExistsError:
                    skipped += 1
                else:
                    copied += 1
                    print(f'コピー完了: {target}')
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        completed += 1
        report(True)
    report(True)
    print(f'コピーしたファイル: {copied}件 / スキップ: {skipped}件')


def describe_error(exc):
    """OSが返した原因を、パスと確認事項を添えて伝える。"""
    winerror = getattr(exc, 'winerror', None)
    code = getattr(exc, 'errno', None)
    if winerror in (112, 39) or code == errno.ENOSPC:
        reason, advice = 'コピー先の空き容量が不足しています。', '同期用共有の空き容量や容量制限を確認してください。'
    elif winerror in (32, 33):
        reason, advice = 'ファイルが別のプログラムで使用されています。', '対象ファイルを開いているアプリを閉じて再実行してください。'
    elif winerror in (53, 64, 67, 1231):
        reason, advice = 'ネットワーク先に接続できません。', 'サーバー名・共有名、ネットワーク接続、サーバーの稼働状態を確認してください。'
    elif winerror in (1326, 86, 1219):
        reason, advice = 'サーバーへの認証に失敗しました。', 'エクスプローラーで対象共有を開き、ログイン情報や既存接続を確認してください。'
    elif isinstance(exc, PermissionError):
        reason, advice = 'アクセスが拒否されました。', 'コピー元の読み取り権限、同期用共有の書き込み権限、サーバーのログイン状態を確認してください。'
    elif isinstance(exc, FileNotFoundError):
        reason, advice = '指定したファイルまたはフォルダが見つかりません。', '表示されたパスのサーバー・共有・実験・サンプル名、周波数、Sample / Blankを確認してください。'
    elif isinstance(exc, NotADirectoryError):
        reason, advice = '指定先がフォルダではありません。', '同じ名前のファイルがないか、指定パスを確認してください。'
    elif isinstance(exc, ValueError):
        reason, advice = str(exc), '入力欄と選択したコピー対象を確認し、「コピー予定を確認」で再確認してください。'
    else:
        reason, advice = '処理を続行できませんでした。', '以下の詳細と実行ログを確認してください。'
    paths = [str(path) for path in (getattr(exc, 'filename', None), getattr(exc, 'filename2', None)) if path]
    result = f'原因: {reason}'
    if paths:
        result += '\n対象パス:\n' + '\n'.join(paths)
    result += f'\n確認事項: {advice}\n詳細: {type(exc).__name__}: {exc}'
    return result


def require_directory(path):
    # isdirは権限・接続エラーもFalseにするため、statで元のエラーを保持する。
    if not stat.S_ISDIR(os.stat(path).st_mode):
        raise NotADirectoryError(errno.ENOTDIR, 'フォルダを指定してください', path)
COPY_SERVERS = ('Rackstation', 'QTserver', 'QDserver', 'QKserver')
COPY_SHARES = {
    frozenset(('Rackstation', 'QTserver')): 'RT_server',
    frozenset(('Rackstation', 'QDserver')): 'RD_server',
    frozenset(('Rackstation', 'QKserver')): 'RK_server',
    frozenset(('QDserver', 'QTserver')): 'DT_server',
    frozenset(('QDserver', 'QKserver')): 'DK_server',
    frozenset(('QTserver', 'QKserver')): 'TK_server',
}
COPY_TARGETS = (
    ('text', 'サンプル接尾辞フォルダ直下の .txt', '', '.txt'),
    ('raw', 'T 直下の .tdms', 'T', '.tdms'),
    ('anal', 'T / ANAL 直下の .tdms', 'T/ANAL', '.tdms'),
    ('bnal', 'T / BNAL@＊ 直下の .tdms', 'T', '.tdms'),
)


def list_server_shares(server):
    """Windowsの共有列挙APIでサーバー直下のディスク共有を取得する。"""
    import ctypes
    from ctypes import wintypes

    class ShareInfo(ctypes.Structure):
        _fields_ = [('name', wintypes.LPWSTR), ('type', wintypes.DWORD),
                    ('remark', wintypes.LPWSTR)]

    api = ctypes.WinDLL('Netapi32.dll')
    enum = api.NetShareEnum
    enum.argtypes = [wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    enum.restype = wintypes.DWORD
    api.NetApiBufferFree.argtypes = [ctypes.c_void_p]
    api.NetApiBufferFree.restype = wintypes.DWORD
    resume = wintypes.DWORD()
    names = []
    while True:
        buffer = ctypes.c_void_p()
        count, total = wintypes.DWORD(), wintypes.DWORD()
        result = enum('\\\\' + server, 1, ctypes.byref(buffer), 0xFFFFFFFF,
                      ctypes.byref(count), ctypes.byref(total), ctypes.byref(resume))
        try:
            if result not in (0, 234):
                raise ctypes.WinError(result)
            entries = ctypes.cast(buffer, ctypes.POINTER(ShareInfo))
            names.extend(entries[i].name for i in range(count.value)
                         if entries[i].type & 0xFFFF == 0)
        finally:
            if buffer:
                api.NetApiBufferFree(buffer)
        if result == 0:
            return sorted(set(names), key=str.casefold)


def component(value, label):
    if not value or value in ('.', '..') or any(c in value for c in '<>:"/\\|?*'):
        raise ValueError(f'{label}にはフォルダ名を入力してください。')
    if value.endswith((' ', '.')):
        raise ValueError(f'{label}の末尾に空白やピリオドは使えません。')
    return value


def relative(value):
    value = value.replace('\\', '/')
    if value:
        for part in value.split('/'):
            component(part, '相対パス')
    return value


def validate(data):
    """GUIとワーカーの双方で入力を検証する。ネットワークアクセスはしない。"""
    d = dict(data)
    kind = d['kind']
    if kind in ('copy', 'folders'):
        for key in ('server', 'share', 'experiment'):
            component(d[key], key)
        d['samples'] = list(dict.fromkeys(re.split(r'[,、\s]+', d['samples'].strip())))
        if d['samples'] == ['']:
            d['samples'] = []
        for sample in d['samples']:
            component(sample, 'サンプル名')
        if kind == 'folders' and not d['samples']:
            raise ValueError('作成するサンプル名を入力してください。')
    if kind == 'copy':
        if d['server'] not in COPY_SERVERS or d['dest_server'] not in COPY_SERVERS:
            raise ValueError('コピー元・先サーバーを一覧から選択してください。')
        if d['server'] == d['dest_server']:
            raise ValueError('コピー元とは異なるコピー先サーバーを選択してください。')
        if d.get('destination_mode', '直接入力') == '自動入力':
            d['destination'] = COPY_SHARES[frozenset((d['server'], d['dest_server']))]
        component(d['destination'], 'コピー先共有名')
        if d['share'].casefold() == d['destination'].casefold():
            raise ValueError('コピー元共有と同期用共有には異なる名前を指定してください。')
        if 'frequency' in d:
            if d['frequency'] not in ('10kHz', '100kHz') or d['sample_kind'] not in ('Sample', 'Blank'):
                raise ValueError('周波数とSample / Blankを選択してください。')
            d['suffix'] = '_' + d['frequency'].replace('Hz', '') + '_' + d['sample_kind']
        component('sample' + d['suffix'], '接尾辞')
        if 'target_text' in d:
            if not any(d.get('target_' + key) for key, *_ in COPY_TARGETS):
                raise ValueError('コピー対象を1つ以上選択してください。')
            if d.get('target_bnal') and d.get('bnal_mode', 'すべて') == '一覧から選択':
                selected = json.loads(d.get('bnal_selection', '[]'))
                if not selected:
                    raise ValueError('BNALの対象を一覧から選択してください。')
                for sample, folder in selected:
                    component(sample, 'サンプル名')
                    component(folder, 'BNALフォルダ名')
                    if not folder.startswith('BNAL@'):
                        raise ValueError('BNAL@で始まるフォルダを選択してください。')
        else:
            d['subpath'] = relative(d['subpath'])
            d['tree'] = [relative(p.strip()) for p in d['tree'].split(',') if p.strip()]
            if d['keyword']:
                re.compile(d['keyword'])
    if (kind == 'copy' and 'target_text' not in d) or (kind == 'rename' and d['mode'] == '整形'):
        if not d['extension'].startswith('.') or len(d['extension']) < 2:
            raise ValueError('拡張子は .tdms のように入力してください。')
        component('file' + d['extension'], '拡張子')
    if kind == 'rename':
        if not d['root']:
            raise ValueError('対象フォルダを指定してください。')
        d['limit'] = int(d['limit']) if d['limit'] else None
        if d['limit'] is not None and d['limit'] < 1:
            raise ValueError('件数制限には1以上を指定してください。')
        if d['mode'] == '置換':
            if not d['old'] or d['old'] == d['new']:
                raise ValueError('置換前と置換後に異なる文字列を入力してください。')
            if any(c in d['old'] + d['new'] for c in '<>:"/\\|?*'):
                raise ValueError('置換文字列にパス区切りや使用できない記号が含まれています。')
        else:
            if re.compile(d['pattern']).groups != 3:
                raise ValueError('整形ルールには3つのキャプチャグループが必要です。')
    return d


def stocked_files(d, common):
    """選択サンプル・接尾辞配下のstocked直下を列挙する（移動はしない）。"""
    root = common.server_path(d['server'], d['share'], d['experiment'])
    require_directory(root)
    samples = d['samples'] or common.list_subfolder_names(root)
    found = []
    def fail(error):
        raise error
    for sample in samples:
        source = common.server_path(d['server'], d['share'], d['experiment'], sample, sample + d['suffix'])
        require_directory(source)
        for folder, dirs, files in os.walk(source, onerror=fail):
            if os.path.basename(folder).casefold() != 'stocked':
                continue
            for name in sorted(files):
                old = os.path.join(folder, name)
                if os.path.isfile(old) and not os.path.islink(old):
                    new = os.path.join(os.path.dirname(folder), name)
                    found.append((old, new))
    return sorted(found)


def restore_stocked(d, common):
    allowed = dict(stocked_files(d, common))
    selected = d.get('stocked_selected', [])
    if not selected:
        raise ValueError('取り出すファイルを選択してください。')
    if any(source not in allowed for source in selected):
        raise ValueError('stockedの内容が変わりました。再度一覧を確認してください。')
    for source in dict.fromkeys(selected):
        destination = allowed[source]
        if os.path.lexists(destination):
            print(f'移動スキップ（同名あり・stockedに残します）: {source} → {destination}')
            continue
        try:
            os.rename(source, destination)  # Windowsでは既存ファイルを上書きしない。
        except FileExistsError:
            print(f'移動スキップ（同名あり）: {source}')
        else:
            print(f'stockedから移動: {source} → {destination}')


def run_job(raw):
    # 既存スクリプトはimport時に実処理を開始しない。
    sys.path.insert(0, str(BASE))
    import create_folders
    import data_transfer_common as common
    import file_rename
    import rename_replace
    import transfer_copy

    d = validate(raw)
    kind = d['kind']
    if kind in ('copy', 'folders'):
        root = common.server_path(d['server'], d['share'], d['experiment'])
        required = root if kind == 'copy' else common.server_path(d['server'], d['share'])
        require_directory(required)
        samples = d['samples'] or common.list_subfolder_names(root)
        if not samples:
            raise ValueError('対象サンプルがありません。')
        if kind == 'folders':
            for sample in samples:
                create_folders.fo_xx(d['server'], d['share'], d['experiment'], sample)
        else:
            if d.get('stocked_action') == 'restore':
                restore_stocked(d, common)
                d['preview'] = True
                print('\n=== 移動後のコピー予定を再確認 ===')
            destination_root = common.server_path(d['server'], d['destination'])
            require_directory(destination_root)
            if 'target_text' in d:
                copy_selected_targets(d, samples, common, create_folders)
                if d.get('preview'):
                    print('\nstockedの移動とコピー予定の再確認が完了しました。コピーは実行していません。'
                          if d.get('stocked_action') == 'restore' else
                          '\nコピー予定の確認が完了しました。ファイル・フォルダは変更していません。')
                else:
                    print('\n同期用共有へのコピー終了。同期完了の確認とanalysisへの移動は手動で行ってください。')
                return
            if d.get('preview'):
                raise ValueError('コピー予定の確認は、コピー対象のチェック項目から指定してください。')
            for sample in samples:
                source = common.server_path(d['server'], d['share'], d['experiment'], sample,
                                            sample + d['suffix'], d['subpath'])
                if not os.path.isdir(source):
                    raise ValueError(f'コピー元が見つかりません: {source}')
            transfer_copy.transfer(d['server'], d['share'], d['destination'],
                                   d['experiment'], samples, d['extension'],
                                   sample_suffix=d['suffix'], search_subpath=d['subpath'],
                                   keyword=d['keyword'] or None, extra_tree=d['tree'],
                                   dest_server=d['server'])
    elif kind == 'rename':
        if not os.path.isdir(d['root']):
            raise ValueError(f'対象フォルダにアクセスできません: {d["root"]}')
        if d['mode'] == '置換':
            rename_replace.replace_in_tree(d['root'], d['old'], d['new'],
                                           include_folders=d['include_folders'],
                                           dry_run=d['preview'], max_items=d['limit'])
        else:
            file_rename.rename_files(sorted(common.list_files_in_folder(d['root'])),
                                     pattern=d['pattern'], ext=d['extension'],
                                     dry_run=d['preview'], max_files=d['limit'])
    elif kind == 'undo':
        import csv
        with open(d['log'], encoding='utf-8', newline='') as stream:
            headers = csv.DictReader(stream).fieldnames or []
        if not {'old_path', 'new_path', 'renamed_at'}.issubset(headers):
            raise ValueError('名前変更の履歴CSVを指定してください。')
        rename_replace.undo_from_log(d['log'])
    print('\n処理終了。スキップや対象件数はログを確認してください。')


def copy_selected_targets(d, samples, common, create_folders):
    """選択したファイル群を元サーバーの同期共有へコピーする。"""
    import fnmatch
    stocked = stocked_files({**d, 'samples': samples}, common)
    if stocked:
        print(f'\n【stockedに{len(stocked)}件あります・通常のコピー対象には含まれません】')
        for source, destination in stocked:
            conflict = '（同名あり・移動不可）' if os.path.lexists(destination) else ''
            print(f'  {source} → {destination} {conflict}')
        print('必要なファイルは「stockedを確認・取り出し」から移動し、コピー予定を再確認してください。')
    plans = []
    for sample in samples:
        source = common.server_path(d['server'], d['share'], d['experiment'], sample, sample + d['suffix'])
        require_directory(source)
        destination = common.server_path(d['server'], d['destination'], d['experiment'], sample, sample + d['suffix'])
        for key, _, subpath, extension in COPY_TARGETS:
            if not d.get('target_' + key):
                continue
            folder = os.path.join(source, subpath) if subpath else source
            try:
                require_directory(folder)
            except FileNotFoundError:
                print(f'対象なし（フォルダがありません）: {folder}')
                continue
            folders = [folder]
            if key == 'bnal':
                with os.scandir(folder) as entries:
                    folders = sorted(entry.path for entry in entries
                                     if entry.is_dir() and fnmatch.fnmatchcase(entry.name, 'BNAL@*'))
                if d.get('bnal_mode', 'すべて') == '一覧から選択':
                    selected_pairs = {tuple(pair) for pair in json.loads(d['bnal_selection'])}
                    folders = [path for path in folders if (sample, os.path.basename(path)) in selected_pairs]
                if not folders:
                    print(f'対象なし（BNAL@*）: {folder}')
            for selected in folders:
                target = os.path.join(destination, os.path.relpath(selected, source))
                plans.append((selected, target, extension))
    if d.get('preview'):
        copy_count = skip_count = total_bytes = 0
        groups = []
        for source, destination, extension in plans:
            rows = []
            with os.scandir(source) as entries:
                files = sorted((entry.name for entry in entries
                                if entry.is_file() and entry.name.lower().endswith(extension.lower())))
            for name in files:
                source_file = os.path.join(source, name)
                destination_file = os.path.normpath(os.path.join(destination, name))
                size = os.path.getsize(source_file)
                if os.path.exists(destination_file):
                    action = '既存・スキップ'
                    skip_count += 1
                else:
                    action = 'コピー'
                    copy_count += 1
                    total_bytes += size
                rows.append((action, name, size))
            if rows:
                groups.append((source, destination, rows))

        print('\n=== コピー予定の概要 ===')
        print(f'対象: {copy_count + skip_count}件')
        print(f'  コピー予定: {copy_count}件 / {format_file_size(total_bytes)}')
        print(f'  スキップ予定: {skip_count}件（コピー先に同名あり）')
        print(f'コピー元: //{d["server"]}/{d["share"]}/{d["experiment"]}')
        print(f'コピー先: //{d["server"]}/{d["destination"]}/{d["experiment"]}')
        print(f'サンプル: {", ".join(samples)} / 接尾辞: {d["suffix"]}')

        print('\n=== ファイル一覧 ===')
        for source, destination, rows in groups:
            print(f'\nコピー元フォルダ: {source}')
            print(f'コピー先フォルダ: {destination}')
            for index, (action, name, size) in enumerate(rows, start=1):
                print(f'  {index:>3}. [{action}] {name}  ({format_file_size(size)})')
        print('\n※「既存・スキップ」はコピー先に同名ファイルがあり、上書きしない項目です。')
        print('※確認だけではコピーしません。実行時には標準フォルダ構造も作成します。')
        print('※確認後にファイルが変わると、実行結果も変わる場合があります。')
        return
    matched = 0
    for source, _, extension in plans:
        with os.scandir(source) as entries:
            matched += sum(entry.is_file() and entry.name.lower().endswith(extension.lower()) for entry in entries)
    if not matched:
        raise ValueError('コピー対象のファイルが0件です。周波数・Sample / Blank・コピー対象・BNAL選択を確認してください。'
                         f'\nコピー元: //{d["server"]}/{d["share"]}/{d["experiment"]}'
                         f'\nサンプル: {", ".join(samples)} / 接尾辞: {d["suffix"]}')
    for sample in samples:
        create_folders.fo_xx(d['server'], d['destination'], d['experiment'], sample)
        destination = common.server_path(d['server'], d['destination'], d['experiment'], sample, sample + d['suffix'])
        for subpath in ('T', 'stocked', 'T/ANAL', 'T/stocked'):
            os.makedirs(os.path.join(destination, subpath), exist_ok=True)
    copy_with_progress(plans)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('File Manager — 実験ファイル管理')
        self.geometry('1000x850')
        self.minsize(820, 740)
        self.events = queue.Queue()
        self.busy = False
        self.preview_signature = None
        self.fields = {}
        self.buttons = []
        ttk.Label(self, text='実験ファイル管理', font=('', 17, 'bold')).pack(anchor='w', padx=16, pady=12)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='x', padx=12)
        self.copy_tab()
        self.rename_tab()
        self.folder_tab()
        status = ttk.Frame(self, padding=12)
        status.pack(fill='x')
        self.status = tk.StringVar(value='待機中')
        ttk.Label(status, textvariable=self.status).pack(side='left')
        self.progress = ttk.Progressbar(status, mode='indeterminate', length=200)
        self.progress.pack(side='right')
        self.timing = tk.StringVar(value='経過時間・残り時間・終了予定時刻はコピー開始後に表示します。')
        ttk.Label(self, textvariable=self.timing).pack(anchor='w', padx=12)
        ttk.Label(self, text='実行ログ / 変更予定（名前変更の前に確認してください）').pack(anchor='w', padx=12)
        self.log = ScrolledText(self, height=12, wrap='none', state='disabled')
        self.log.pack(fill='both', expand=True, padx=12, pady=(5, 12))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(80, self.poll)

    def tab(self, key, title):
        frame = ttk.Frame(self.tabs, padding=12)
        frame.columnconfigure(1, weight=1)
        self.tabs.add(frame, text=title)
        self.fields[key] = {}
        return frame

    def field(self, frame, group, key, label, default='', browse=False, values=None):
        row = len(self.fields[group])
        var = tk.StringVar(value=default)
        self.fields[group][key] = var
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=3)
        widget = (ttk.Combobox(frame, textvariable=var, values=values, state='readonly')
                  if values else ttk.Entry(frame, textvariable=var))
        widget.grid(row=row, column=1, sticky='ew', pady=3)
        if browse:
            def select():
                path = filedialog.askdirectory(parent=self)
                if path:
                    var.set(path)
            ttk.Button(frame, text='一覧から選択 / 更新' if callable(browse) else '選択…',
                       command=browse if callable(browse) else select).grid(
                row=row, column=2, padx=5)
        return widget

    def actions(self, frame, group, pairs, note):
        row = len(self.fields[group]) + 1
        ttk.Label(frame, text=note, wraplength=740).grid(row=row, column=0, columnspan=3, sticky='w', pady=8)
        bar = ttk.Frame(frame)
        bar.grid(row=row + 1, column=0, columnspan=3, sticky='w')
        for label, command in pairs:
            button = ttk.Button(bar, text=label, command=command)
            button.pack(side='left', padx=(0, 8))
            self.buttons.append(button)

    def shared(self, frame, group):
        for key, label, default in [('server', 'サーバー名', 'Rackstation'),
                                    ('share', '共有フォルダ名', 'analysis'),
                                    ('experiment', '実験フォルダ名', ''),
                                    ('samples', 'サンプル名（カンマ区切り）', '')]:
            self.field(frame, group, key, label, default)

    def copy_tab(self):
        f = self.tab('copy', 'ファイルコピー')
        server = self.field(f, 'copy', 'server', 'コピー元サーバー', 'Rackstation', values=COPY_SERVERS)
        self.field(f, 'copy', 'share', 'コピー元共有フォルダ', 'analysis',
                   browse=lambda: self.select_copy_folder('share'))
        self.field(f, 'copy', 'experiment', '実験フォルダ名',
                   browse=lambda: self.select_copy_folder('experiment'))
        self.field(f, 'copy', 'samples', 'サンプル名（カンマ区切り）',
                   browse=lambda: self.select_copy_folder('samples'))
        server.bind('<<ComboboxSelected>>', self.copy_server_changed)
        self.fields['copy']['share'].trace_add(
            'write', lambda *_: self.fields['copy']['experiment'].set(''))
        self.dest_server_box = self.field(f, 'copy', 'dest_server', '同期先サーバー', 'QTserver',
                                          values=COPY_SERVERS[1:])
        mode = self.field(f, 'copy', 'destination_mode', 'コピー先共有名の指定方法', '自動入力',
                          values=('自動入力', '直接入力'))
        self.destination_entry = self.field(f, 'copy', 'destination', 'コピー先共有名', 'RT_server')
        self.dest_server_box.bind('<<ComboboxSelected>>', self.update_copy_destination)
        mode.bind('<<ComboboxSelected>>', self.update_copy_destination)
        self.update_copy_destination()
        frequency = self.field(f, 'copy', 'frequency', 'サンプリング周波数', '10kHz', values=('10kHz', '100kHz'))
        sample_kind = self.field(f, 'copy', 'sample_kind', 'データ区分', 'Sample', values=('Sample', 'Blank'))
        suffix = self.field(f, 'copy', 'suffix', 'サンプル接尾辞（自動）', '_10k_Sample')
        suffix.configure(state='readonly')
        def update_suffix(_event=None):
            fields = self.fields['copy']
            fields['suffix'].set('_' + fields['frequency'].get().replace('Hz', '') + '_' + fields['sample_kind'].get())
        frequency.bind('<<ComboboxSelected>>', update_suffix)
        sample_kind.bind('<<ComboboxSelected>>', update_suffix)
        for key, label, _, _ in COPY_TARGETS:
            row = len(self.fields['copy'])
            var = tk.BooleanVar(value=key == 'anal')
            self.fields['copy']['target_' + key] = var
            if key == 'text':
                ttk.Label(f, text='コピー対象（複数選択可）').grid(row=row, column=0, sticky='w')
            ttk.Checkbutton(f, text=label, variable=var).grid(row=row, column=1, sticky='w')
        row = len(self.fields['copy'])
        self.fields['copy']['bnal_mode'] = tk.StringVar(value='すべて')
        self.fields['copy']['bnal_selection'] = tk.StringVar(value='[]')
        panel = ttk.LabelFrame(f, text='BNAL@ フォルダの対象確認・選択', padding=8)
        panel.grid(row=row, column=0, columnspan=3, sticky='ew', pady=6)
        self.bnal_summary = tk.StringVar()
        mode = self.fields['copy']['bnal_mode']
        ttk.Radiobutton(panel, text='すべてのBNAL@フォルダ', variable=mode, value='すべて').grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(panel, text='一部のフォルダを選択', variable=mode, value='一覧から選択').grid(row=0, column=1, padx=12)
        ttk.Button(panel, text='対象フォルダを確認・選択…', command=self.select_bnal).grid(row=0, column=2)
        ttk.Label(panel, textvariable=self.bnal_summary, wraplength=850).grid(row=1, column=0, columnspan=3, sticky='w', pady=(6, 0))
        def show_bnal(*_):
            fields = self.fields['copy']
            if not fields['target_bnal'].get():
                text = 'BNALはコピー対象外です。上のBNALチェックを入れてください。'
            elif mode.get() == 'すべて':
                text = 'コピー対象：各サンプルのT内にある、すべてのBNAL@フォルダ'
            else:
                pairs = json.loads(fields['bnal_selection'].get())
                text = ('コピー対象：' + '、'.join(f'{s}/{n}' for s, n in pairs)
                        if pairs else '対象未選択：「対象フォルダを確認・選択…」から選んでください。')
            self.bnal_summary.set(text)
        for name in ('target_bnal', 'bnal_mode', 'bnal_selection'):
            self.fields['copy'][name].trace_add('write', show_bnal)
        show_bnal()
        def reset_bnal(*_):
            self.fields['copy']['bnal_selection'].set('[]')
        for name in ('server', 'share', 'experiment', 'samples', 'frequency', 'sample_kind'):
            self.fields['copy'][name].trace_add('write', reset_bnal)
        self.actions(f, 'copy', [('コピー予定を確認', lambda: self.submit('copy', True)),
                                ('stockedを確認・取り出し', self.select_stocked),
                                ('コピー実行', lambda: self.submit('copy'))],
                     '標準フォルダ構造を同期用共有に作成します。既存ファイルはスキップします。\n'
                     'サンプル名が空欄なら全サンプルが対象です。同期後のanalysisへの移動は手動です。')

    def select_stocked(self):
        if self.busy:
            return
        raw = {key: var.get() for key, var in self.fields['copy'].items()}
        raw.update(kind='copy', preview=True)
        try:
            data = validate(raw)
        except ValueError as exc:
            messagebox.showerror('入力を確認してください', str(exc), parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title('stockedの確認・親フォルダへの移動')
        dialog.geometry('1050x500')
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text='選択したファイルを親フォルダへ移動します。同名は上書きしません。Ctrl / Shiftで複数選択できます。').pack(padx=12, pady=8)
        status = tk.StringVar(value='読み込み中…')
        ttk.Label(dialog, textvariable=status).pack(anchor='w', padx=12)
        frame = ttk.Frame(dialog)
        frame.pack(fill='both', expand=True, padx=12, pady=8)
        tree = ttk.Treeview(frame, columns=('source', 'destination', 'state'), show='headings', selectmode='extended')
        for key, title, width in [('source', 'stocked内のファイル', 440), ('destination', '移動先', 440), ('state', '状態', 130)]:
            tree.heading(key, text=title)
            tree.column(key, width=width)
        tree.grid(row=0, column=0, sticky='nsew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        vertical = ttk.Scrollbar(frame, command=tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        results = queue.Queue()
        pairs = []
        def load():
            refresh.state(['disabled'])
            move.state(['disabled'])
            status.set('読み込み中…')
            def worker():
                try:
                    sys.path.insert(0, str(BASE))
                    import data_transfer_common as common
                    found = stocked_files(data, common)
                    results.put(([(s, t, os.path.lexists(t)) for s, t in found], None))
                except Exception as exc:
                    results.put(([], describe_error(exc)))
            threading.Thread(target=worker, daemon=True).start()
            self.after(80, poll)
        def poll():
            if not dialog.winfo_exists():
                return
            try:
                found, error = results.get_nowait()
            except queue.Empty:
                self.after(80, poll)
                return
            pairs[:] = found
            tree.delete(*tree.get_children())
            for i, (source, destination, conflict) in enumerate(found):
                tree.insert('', 'end', iid=str(i), values=(source, destination, '同名あり・残す' if conflict else '移動可能'))
            refresh.state(['!disabled'])
            if found:
                move.state(['!disabled'])
            status.set(error or f'{len(found)}件（移動後も、現在のコピー対象・拡張子の設定に一致するものだけコピーされます）')
        def choose():
            selected = [pairs[int(i)][0] for i in tree.selection()]
            if not selected:
                messagebox.showinfo('ファイル選択', '取り出すファイルを選択してください。', parent=dialog)
                return
            if not messagebox.askyesno('移動の確認', f'{len(selected)}件をstockedから親フォルダへ移動します。\n同名はstockedに残します。\n移動後にコピー予定を再確認します。', parent=dialog):
                return
            dialog.destroy()
            self.start({**raw, 'stocked_action': 'restore', 'stocked_selected': selected})
        bar = ttk.Frame(dialog, padding=12)
        bar.pack(fill='x')
        refresh = ttk.Button(bar, text='更新', command=load)
        refresh.pack(side='left')
        ttk.Button(bar, text='全件選択', command=lambda: tree.selection_set(tree.get_children())).pack(side='left', padx=8)
        move = ttk.Button(bar, text='選択を移動してコピー予定を再確認', command=choose)
        move.pack(side='left')
        ttk.Button(bar, text='閉じる', command=dialog.destroy).pack(side='right')
        load()

    def select_bnal(self):
        fields = self.fields['copy']
        raw = {key: var.get() for key, var in fields.items()}
        try:
            data = validate({**raw, 'kind': 'copy', 'target_bnal': True, 'bnal_mode': 'すべて'})
        except ValueError as exc:
            messagebox.showerror('入力を確認してください', str(exc), parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title('BNALフォルダを選択')
        dialog.geometry('720x460')
        dialog.transient(self)
        dialog.grab_set()
        status = tk.StringVar(value='読み込み中…')
        ttk.Label(dialog, text='サンプルごとに選択できます（Ctrl / Shiftで複数選択）。').pack(anchor='w', padx=12, pady=8)
        ttk.Label(dialog, textvariable=status, wraplength=680).pack(anchor='w', padx=12)
        frame = ttk.Frame(dialog)
        frame.pack(fill='both', expand=True, padx=12, pady=8)
        tree = ttk.Treeview(frame, columns=('sample', 'folder'), show='headings', selectmode='extended')
        tree.heading('sample', text='サンプル')
        tree.heading('folder', text='BNALフォルダ')
        tree.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(frame, command=tree.yview)
        scrollbar.pack(side='right', fill='y')
        tree.configure(yscrollcommand=scrollbar.set)
        results = queue.Queue()
        pairs = []

        def load():
            refresh.state(['disabled'])
            accept.state(['disabled'])
            status.set('読み込み中…')
            def worker():
                try:
                    sys.path.insert(0, str(BASE))
                    import data_transfer_common as common
                    root = common.server_path(data['server'], data['share'], data['experiment'])
                    samples = data['samples']
                    if not samples:
                        with os.scandir(root) as entries:
                            samples = sorted(entry.name for entry in entries if entry.is_dir())
                    found, missing = [], []
                    for sample in samples:
                        path = common.server_path(data['server'], data['share'], data['experiment'],
                                                  sample, sample + data['suffix'], 'T')
                        try:
                            with os.scandir(path) as entries:
                                found.extend((sample, entry.name) for entry in entries
                                             if entry.is_dir() and entry.name.startswith('BNAL@'))
                        except FileNotFoundError:
                            missing.append(sample)
                    results.put((sorted(found), missing, None))
                except OSError as exc:
                    results.put(([], [], str(exc)))
            threading.Thread(target=worker, daemon=True).start()
            self.after(80, poll)

        def poll():
            if not dialog.winfo_exists():
                return
            try:
                found, missing, error = results.get_nowait()
            except queue.Empty:
                self.after(80, poll)
                return
            pairs[:] = found
            tree.delete(*tree.get_children())
            previous = {tuple(pair) for pair in json.loads(raw['bnal_selection'])}
            for i, pair in enumerate(pairs):
                tree.insert('', 'end', iid=str(i), values=pair)
                if raw['bnal_mode'] == 'すべて' or pair in previous:
                    tree.selection_add(str(i))
            refresh.state(['!disabled'])
            if not error:
                accept.state(['!disabled'])
            status.set(f'読み込めませんでした: {error}' if error else
                       f'{len(pairs)}件' + (f' / Tフォルダなし: {", ".join(missing)}' if missing else ''))

        def choose():
            selected = [pairs[int(item)] for item in tree.selection()]
            if not selected:
                messagebox.showinfo('対象の選択', 'BNALフォルダを1つ以上選択してください。', parent=dialog)
                return
            fields['bnal_selection'].set(json.dumps(selected, ensure_ascii=False))
            fields['bnal_mode'].set('一覧から選択')
            fields['target_bnal'].set(True)
            dialog.destroy()

        bar = ttk.Frame(dialog, padding=12)
        bar.pack(fill='x')
        refresh = ttk.Button(bar, text='更新', command=load)
        refresh.pack(side='left')
        ttk.Button(bar, text='全件選択', command=lambda: tree.selection_set(tree.get_children())).pack(side='left', padx=8)
        accept = ttk.Button(bar, text='選択を確定', command=choose)
        accept.pack(side='left')
        ttk.Button(bar, text='キャンセル', command=dialog.destroy).pack(side='right')
        load()

    def copy_server_changed(self, _event=None):
        self.fields['copy']['share'].set('analysis')
        self.update_copy_destination()

    def update_copy_destination(self, _event=None):
        fields = self.fields['copy']
        source = fields['server'].get()
        choices = tuple(server for server in COPY_SERVERS if server != source)
        self.dest_server_box.configure(values=choices)
        if fields['dest_server'].get() not in choices:
            fields['dest_server'].set(choices[0])
        automatic = fields['destination_mode'].get() == '自動入力'
        self.destination_entry.configure(state='readonly' if automatic else 'normal')
        if automatic:
            fields['destination'].set(COPY_SHARES[frozenset((source, fields['dest_server'].get()))])

    def select_copy_folder(self, key):
        fields = self.fields['copy']
        try:
            server = component(fields['server'].get(), 'サーバー名')
            share = (fields['share'].get().strip() if key == 'share' else
                     component(fields['share'].get().strip(), '共有フォルダ名'))
            experiment = (component(fields['experiment'].get().strip(), '実験フォルダ名')
                          if key == 'samples' else None)
        except ValueError as exc:
            messagebox.showerror('入力を確認してください', str(exc), parent=self)
            return
        parent = f'//{server}' if key == 'share' else f'//{server}/{share}'
        if key == 'samples':
            parent += '/' + experiment
        target_label = {'share': '共有フォルダ', 'experiment': '実験フォルダ', 'samples': 'サンプル'}[key]
        dialog = tk.Toplevel(self)
        dialog.title(f'{target_label}の一覧から選択')
        dialog.geometry('660x470')
        dialog.transient(self)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=12)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text=parent, wraplength=620).pack(anchor='w')
        ttk.Label(panel, text='直下のフォルダ名から選択してください。更新ボタンで再読み込みできます。').pack(anchor='w', pady=8)
        bar = ttk.Frame(panel)
        bar.pack(fill='x')
        status = tk.StringVar(value='「一覧から選択」を押すとフォルダ名を読み込みます。')
        results = queue.Queue()

        def apply(selected):
            if key == 'share' and len(selected.replace('\\', '/').rstrip('/')[2:].split('/')) != 2:
                messagebox.showerror('選択先を確認してください', 'サーバー直下の共有フォルダを選択してください。', parent=dialog)
                return
            accepted = (self.apply_copy_samples(selected if isinstance(selected, list) else [selected], parent)
                        if key == 'samples' else self.apply_copy_folder(key, selected, server, share))
            if accepted:
                dialog.destroy()

        def browse():
            title = f'{target_label}を選択'
            selected = filedialog.askdirectory(parent=dialog, title=title,
                                                initialdir=parent, mustexist=True)
            if selected:
                apply(selected)

        def load():
            load_button.state(['disabled'])
            status.set('フォルダ一覧を読み込み中…')

            def worker():
                try:
                    if key == 'share':
                        names = list_server_shares(server)
                    else:
                        with os.scandir(parent) as entries:
                            names = sorted((entry.name for entry in entries if entry.is_dir()), key=str.casefold)
                    results.put((names, None))
                except OSError as exc:
                    results.put(([], str(exc)))

            threading.Thread(target=worker, daemon=True).start()
            self.after(80, poll)

        def poll():
            if not dialog.winfo_exists():
                return
            try:
                names, error = results.get_nowait()
            except queue.Empty:
                self.after(80, poll)
                return
            load_button.state(['!disabled'])
            listing.delete(0, 'end')
            for name in names:
                listing.insert('end', name)
            status.set(f'読み込めませんでした: {error}' if error else
                       f'{len(names)}件 — 選択すると{target_label}欄に反映します。')

        def choose(_event=None):
            selection = listing.curselection()
            if selection:
                paths = [parent + '/' + listing.get(i) for i in selection]
                apply(paths if key == 'samples' else paths[0])

        ttk.Button(bar, text='フォルダ参照…', command=browse).pack(side='left', padx=(0, 8))
        load_button = ttk.Button(bar, text='一覧から選択 / 更新', command=load)
        load_button.pack(side='left')
        ttk.Label(panel, textvariable=status, wraplength=620).pack(anchor='w', pady=8)
        if key == 'samples':
            ttk.Label(panel, text='Ctrl / Shiftで複数選択できます。選択結果でサンプル名欄を置き換えます。').pack(anchor='w')
        list_frame = ttk.Frame(panel)
        list_frame.pack(fill='both', expand=True)
        listing = tk.Listbox(list_frame, exportselection=False,
                             selectmode='extended' if key == 'samples' else 'browse')
        listing.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=listing.yview)
        scrollbar.pack(side='right', fill='y')
        listing.configure(yscrollcommand=scrollbar.set)
        listing.bind('<Double-Button-1>', choose)
        listing.bind('<Return>', choose)
        bottom = ttk.Frame(panel)
        bottom.pack(fill='x', pady=(10, 0))
        ttk.Button(bottom, text=f'選択した{target_label}を使用', command=choose).pack(side='left')
        ttk.Button(bottom, text='キャンセル', command=dialog.destroy).pack(side='right')
        load()

    def apply_copy_samples(self, selected, parent):
        """指定実験の直下にあるサンプルだけを入力欄へ反映する。"""
        names = []
        for path in selected:
            normalized = path.replace('\\', '/').rstrip('/')
            folder, _, name = normalized.rpartition('/')
            if folder.casefold() != parent.rstrip('/').casefold() or not name:
                messagebox.showerror('選択先を確認してください',
                                     f'{parent} の直下のサンプルフォルダを選択してください。', parent=self)
                return False
            names.append(name)
        if not names:
            return False
        self.fields['copy']['samples'].set(', '.join(dict.fromkeys(names)))
        return True

    def apply_copy_folder(self, key, selected, server, share):
        """共有フォルダと実験フォルダをUNCパスの階層に応じて反映する。"""
        normalized = selected.replace('\\', '/').rstrip('/')
        parts = normalized[2:].split('/') if normalized.startswith('//') else []
        valid = (len(parts) in (2, 3) and parts[0].casefold() == server.casefold())
        if key == 'experiment':
            valid = valid and len(parts) == 3 and parts[1].casefold() == share.casefold()
        if not valid:
            messagebox.showerror('選択先を確認してください',
                                 '指定サーバーの共有フォルダ、または共有フォルダ直下の実験フォルダを選択してください。'
                                 '\n実験フォルダの選択では、現在の共有フォルダ内から選んでください。',
                                 parent=self)
            return False
        fields = self.fields['copy']
        if fields['share'].get() != parts[1]:
            fields['share'].set(parts[1])
        fields['experiment'].set(parts[2] if len(parts) == 3 else '')
        return True

    def rename_tab(self):
        f = self.tab('rename', '名前変更・復元')
        self.field(f, 'rename', 'root', '対象フォルダ', browse=True)
        self.field(f, 'rename', 'mode', '変更方法', '置換', values=('置換', '整形'))
        self.field(f, 'rename', 'old', '置換前の文字列')
        self.field(f, 'rename', 'new', '置換後の文字列（空欄で削除）')
        from_pattern = r'([A-Z@0-9]+)(\d{4}_\d{4}_\d{4})_([A-Za-z0-9#]+).*'
        self.field(f, 'rename', 'pattern', '整形用の正規表現', from_pattern)
        self.field(f, 'rename', 'extension', '整形後の拡張子', '.tdms')
        self.field(f, 'rename', 'limit', '件数制限（空欄で全件）', '2')
        include = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text='置換時はフォルダ名も変更する', variable=include).grid(
            row=len(self.fields['rename']), column=1, sticky='w', pady=4)
        self.fields['rename']['include_folders'] = include
        self.actions(f, 'rename', [('変更予定を表示', lambda: self.submit('rename', True)),
                                   ('名前変更を実行', lambda: self.submit('rename')),
                                   ('履歴CSVから復元…', self.undo)],
                     '置換：配下を再帰的に検索。整形：直下のファイルを「グループ1＋2_3＋拡張子」に変更。\n'
                     'プレビューは変更候補です。実行時に変更先が存在する場合はスキップします。')

    def folder_tab(self):
        f = self.tab('folders', 'フォルダ作成')
        self.shared(f, 'folders')
        self.actions(f, 'folders', [('フォルダ作成', lambda: self.submit('folders'))],
                     '各サンプルに10k/100kのSample・Blank、raw data、T、stocked等の既存構成を作成します。\n'
                     '既存フォルダはそのまま利用します。サンプル名は必須です。')

    def submit(self, kind, preview=False):
        if self.busy:
            return
        data = {k: v.get() for k, v in self.fields[kind].items()}
        # 置換文字列の空白には意味があるので保持する。
        data = {k: v.strip() if isinstance(v, str) and k not in ('old', 'new') else v
                for k, v in data.items()}
        data.update(kind=kind, preview=preview)
        try:
            validate(data)
        except (ValueError, re.error) as exc:
            messagebox.showerror('入力を確認してください', str(exc), parent=self)
            return
        signature = json.dumps({**data, 'preview': True}, ensure_ascii=False, sort_keys=True)
        if kind == 'rename' and not preview and signature != self.preview_signature:
            messagebox.showinfo('変更予定の確認', '現在の設定で「変更予定を表示」を先に実行してください。', parent=self)
            return
        if not preview:
            detail = (data['root'] if kind == 'rename' else
                      f'//{data["server"]}/{data["share"]}/{data["experiment"]}\n'
                      f'サンプル: {data["samples"] or "すべて"}')
            if kind == 'copy':
                detail += (f'\n同期用コピー先: //{data["server"]}/{data["destination"]}/{data["experiment"]}'
                           f'\n同期先サーバー: {data["dest_server"]}')
                if data.get('target_bnal'):
                    detail += '\n\n' + self.bnal_summary.get()
            if not messagebox.askyesno('実行の確認', f'{detail}\n\nこの設定で実行しますか？', parent=self):
                return
        self.start(data, signature if kind == 'rename' and preview else None)

    def undo(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self, initialdir=str(BASE / 'rename_logs'),
                                          filetypes=[('名前変更履歴', '*.csv')])
        if path and messagebox.askyesno('復元の確認', f'{path}\n\nこの履歴の名前変更を元に戻しますか？', parent=self):
            self.start({'kind': 'undo', 'log': path})

    def start(self, data, signature=None):
        self.busy = True
        self.preview_signature = None
        for button in self.buttons:
            button.state(['disabled'])
        self.status.set('予定を確認中…' if data.get('preview') else '処理中…')
        self.progress.configure(mode='indeterminate', value=0)
        self.progress.start(12)
        self.started_at = time.monotonic()
        self.timing.set('対象を確認中… 終了予定は転送開始後に計算します。')
        self.append('\n--- コピー予定の確認 ---\n' if data['kind'] == 'copy' and data.get('preview')
                    else '\n--- 新しい処理 ---\n')

        def worker():
            code = 1
            error_detail = None
            last_lines = []
            try:
                env = dict(os.environ, PYTHONIOENCODING='utf-8')
                with subprocess.Popen([sys.executable, '-u', str(BASE / 'app.py'), '--worker'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                      env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
                    process.stdin.write(json.dumps(data, ensure_ascii=False))
                    process.stdin.close()
                    for line in process.stdout:
                        if line.startswith(PROGRESS_PREFIX):
                            self.events.put(('progress', json.loads(line[len(PROGRESS_PREFIX):])))
                        elif line.startswith(ERROR_PREFIX):
                            error_detail = json.loads(line[len(ERROR_PREFIX):])
                            self.events.put(('log', '\n' + error_detail + '\n'))
                        else:
                            self.events.put(('log', line))
                            last_lines = (last_lines + [line])[-12:]
                    code = process.wait()
            except Exception as exc:
                error_detail = describe_error(exc)
                self.events.put(('log', traceback.format_exc()))
            if code and not error_detail:
                error_detail = f'処理用プロセスが終了しました（終了コード: {code}）。\n' + ''.join(last_lines)
            self.events.put(('done', (code, signature, error_detail)))

        threading.Thread(target=worker, daemon=False).start()

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        self.log.see('end')
        self.log.configure(state='disabled')

    def poll(self):
        for _ in range(150):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == 'log':
                self.append(value)
            elif event == 'progress':
                self.show_copy_progress(value)
            else:
                code, signature, error_detail = value
                self.busy = False
                self.progress.stop()
                elapsed = time.monotonic() - self.started_at
                self.timing.set(f'{"終了" if code == 0 else "停止"}時刻: {datetime.now():%Y-%m-%d %H:%M:%S}'
                                f' / 所要時間: {self.duration(elapsed)}')
                self.preview_signature = signature if code == 0 else None
                for button in self.buttons:
                    button.state(['!disabled'])
                self.status.set('処理終了 — ログを確認してください' if code == 0 else 'エラー — ログを確認してください')
                if code:
                    messagebox.showerror('処理できませんでした', error_detail +
                                         '\n\n途中まで処理されている可能性があります。実行済みの内容はログを確認してください。', parent=self)
        self.after(80, self.poll)

    @staticmethod
    def duration(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'

    def show_copy_progress(self, progress):
        done, total = progress['done'], progress['total']
        finished = progress['completed'] == progress['count']
        percent = 100 if finished else min(99.9, done / total * 100) if total else 0
        self.progress.stop()
        self.progress.configure(mode='determinate', maximum=100, value=percent)
        self.status.set(f'コピー進捗: {percent:.1f}% / {progress["completed"]}/{progress["count"]}件'
                        f' / スキップ: {progress["skipped"]}件')
        elapsed = progress['elapsed']
        speed = done / elapsed if elapsed > 0 else 0
        prefix = f'経過: {self.duration(elapsed)} / {done / 1024**2:.1f}/{total / 1024**2:.1f} MiB'
        if speed and elapsed >= 1 and not finished:
            remaining = max(0, total - done) / speed
            eta = datetime.now() + timedelta(seconds=remaining)
            self.timing.set(prefix + f' / 残り約 {self.duration(remaining)} / 終了見込み: {eta:%m/%d %H:%M:%S}'
                            f' ({speed / 1024**2:.1f} MiB/s)')
        else:
            self.timing.set(prefix + (' / 転送完了' if finished else ' / 残り時間・終了見込み: 計算中…'))

    def close(self):
        if self.busy:
            messagebox.showinfo('処理中', '処理が終わってから画面を閉じてください。', parent=self)
        else:
            self.destroy()


if __name__ == '__main__':
    if '--worker' in sys.argv:
        try:
            run_job(json.load(sys.stdin))
        except Exception as exc:
            print(ERROR_PREFIX + json.dumps(describe_error(exc), ensure_ascii=False), flush=True)
            traceback.print_exc()
            sys.exit(1)
    else:
        App().mainloop()
