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
sys.path.insert(0, str(BASE.parent))
import ui_theme  # noqa: E402
from filemanager.job_control import JobCancelled, check_cancel
ERROR_PREFIX = '__FILEMANAGER_ERROR__'
PROGRESS_PREFIX = '__FILEMANAGER_PROGRESS__'
PLAN_PREFIX = '__FILEMANAGER_SAMPLE_PLAN__'


def sample_plan_group(source, kind):
    parts = source.replace('\\', '/').casefold().split('/')
    if any(p == 'bnal' or p.startswith('bnal@') for p in parts):
        return 'bnal', 'BNAL'
    if 'stocked' in parts:
        return 'stocked', 'stocked（退避）'
    if 'anal' in parts:
        return 'anal', 'ANAL'
    if kind == 'フォルダ':
        return 'folder', 'フォルダ'
    if parts[-1].endswith('.txt'):
        return 'txt', 'TXT'
    if parts[-1].endswith('.tdms'):
        if any(re.search(r'_100k_(sample|blank)$', p) for p in parts[:-1]):
            return '100k', '100kHz TDMS'
        if any(re.search(r'_10k_(sample|blank)$', p) for p in parts[:-1]):
            return '10k', '10kHz TDMS'
    return 'file', 'その他'


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
        check_cancel()
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
        check_cancel()
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
                    check_cancel()
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
    if kind == 'sample_rename':
        for key in ('server', 'share', 'experiment', 'folder', 'old', 'new'):
            component(d[key], key)
        if d['old'] == d['new']:
            raise ValueError('現サンプル名と新サンプル名を別の名前にしてください。')
    if kind == 'sample_restore' and not d.get('history'):
        raise ValueError('復元する履歴JSONを選択してください。')
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
            check_cancel()
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
        check_cancel()
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
    if kind in ('sample_rename', 'sample_restore'):
        import hashlib
        import sample_rename
        settings = {k: v for k, v in d.items() if k != 'preview'}
        key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
        preview_file = BASE / 'rename_logs' / 'previews' / (key + '.json')
        if d.get('preview'):
            if kind == 'sample_restore':
                planned = sample_rename.restore_plan(d['history'])
            else:
                root = common.server_path(d['server'], d['share'], d['experiment'])
                planned = sample_rename.plan(root, d['folder'], d['old'], d['new'])
            if not planned['operations']:
                raise ValueError('変更対象がありません。フォルダ名と現サンプル名を確認してください。')
            for source, target in planned['operations'][:20]:
                check_cancel()
                print(f'{source}\n  → {target}')
            if len(planned['operations']) > 20:
                print('ログは先頭20件です。全件は中央の変更予定表で確認できます。')
            print(f'予定: {len(planned["operations"])}件（子から順に変更／復元は逆順）')
            sample_rename.save(preview_file, planned)
            print(PLAN_PREFIX + json.dumps(str(preview_file)), flush=True)
        else:
            planned = json.loads(preview_file.read_text(encoding='utf-8'))
            preview_file.unlink()
            if kind == 'sample_restore':
                sample_rename.restore(planned)
            else:
                sample_rename.execute(planned, BASE / 'rename_logs' / 'samples')
        return
    if kind in ('copy', 'folders'):
        root = common.server_path(d['server'], d['share'], d['experiment'])
        required = root if kind == 'copy' else common.server_path(d['server'], d['share'])
        require_directory(required)
        samples = d['samples'] or common.list_subfolder_names(root)
        if not samples:
            raise ValueError('対象サンプルがありません。')
        if kind == 'folders':
            os.makedirs(root, exist_ok=True)
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
        check_cancel()
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
        ui_theme.apply(self, ui_theme.FILEMANAGER)
        self.title('File Manager — 実験ファイル管理')
        self.geometry('1000x930')
        self.minsize(820, 800)
        self.events = queue.Queue()
        self.busy = False
        self.preview_signature = None
        self.fields = {}
        self.buttons = []
        ui_theme.header(self, '実験ファイル管理', 'フォルダ作成・転送・stocked 取り出し・リネーム',
                        '\uE8B7', ui_theme.FILEMANAGER, on_close=self.close).pack(fill='x', pady=(0, 10))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='x', padx=12)
        self.copy_tab()
        self.sample_rename_tab()
        from filemanager.anal_manager import AnalPanel
        from filemanager.bnal_manager import BnalPanel
        self.anal_panel = AnalPanel(self)
        self.tabs.add(self.anal_panel, text='ANAL 重複・退避・復帰整理')
        self.bnal_panel = BnalPanel(self)
        self.tabs.add(self.bnal_panel, text='BNAL 重複・退避・復帰整理')
        from filemanager.t_stock_manager import TStockPanel
        self.t_stock_panel = TStockPanel(self)
        self.tabs.add(self.t_stock_panel, text='Data 退避・退避整理')
        self.rename_tab()
        self.folder_tab()
        status = ttk.Frame(self, padding=12)
        status.pack(fill='x')
        self.status = tk.StringVar(value='待機中')
        ttk.Label(status, textvariable=self.status).pack(side='left')
        self.progress = ttk.Progressbar(status, mode='indeterminate', length=200)
        self.progress.pack(side='right')
        self.stop_button = ttk.Button(status, text='中断', command=self.cancel_job, state='disabled')
        self.stop_button.pack(side='right', padx=10)
        self.timing = tk.StringVar(value='経過時間・残り時間・終了予定時刻はコピー開始後に表示します。')
        ttk.Label(self, textvariable=self.timing).pack(anchor='w', padx=12)
        ttk.Label(self, text='実行ログ / 変更予定（名前変更の前に確認してください）').pack(anchor='w', padx=12)
        self.log = ScrolledText(self, height=12, wrap='none', state='disabled')
        ui_theme.style_text(self.log)
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
        server = self.field(frame, group, 'server', 'サーバー名', 'Rackstation', values=COPY_SERVERS)
        self.field(frame, group, 'share', '共有フォルダ名', 'analysis',
                   browse=lambda: self.select_copy_folder('share', group))
        self.field(frame, group, 'experiment', '実験フォルダ名',
                   browse=lambda: self.select_copy_folder('experiment', group))
        self.field(frame, group, 'samples', 'サンプル名（カンマ区切り）')
        fields = self.fields[group]
        server.bind('<<ComboboxSelected>>', lambda _event: fields['share'].set('analysis'))
        fields['share'].trace_add('write', lambda *_: fields['experiment'].set(''))

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

    def select_copy_folder(self, key, group='copy'):
        if self.busy:
            return
        fields = self.fields[group]
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
                        if key == 'samples' else self.apply_copy_folder(key, selected, server, share, group))
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

    def apply_copy_folder(self, key, selected, server, share, group='copy'):
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
        fields = self.fields[group]
        if fields['share'].get() != parts[1]:
            fields['share'].set(parts[1])
        fields['experiment'].set(parts[2] if len(parts) == 3 else '')
        return True

    def sample_rename_tab(self):
        f = self.tab('sample_rename', 'サンプル名一括変更・復元')
        self.field(f, 'sample_rename', 'server', 'サーバー', 'Rackstation', values=COPY_SERVERS)
        self.field(f, 'sample_rename', 'share', '共有フォルダ', 'analysis',
                   browse=lambda: self.select_copy_folder('share', 'sample_rename'))
        self.field(f, 'sample_rename', 'experiment', '実験フォルダ名',
                   browse=lambda: self.select_copy_folder('experiment', 'sample_rename'))
        self.field(f, 'sample_rename', 'folder', '対象サンプルフォルダ名（実験直下）',
                   browse=self.select_rename_sample)
        self.field(f, 'sample_rename', 'old', '現サンプル名（名前内の置換対象）')
        self.field(f, 'sample_rename', 'new', '新サンプル名')
        self.actions(f, 'sample_rename', [
            ('変更予定を表示', lambda: self.submit('sample_rename', True)),
            ('確認して変更を実行', lambda: self.submit('sample_rename')),
            ('履歴を選択・復元予定を表示', lambda: self.sample_undo(True)),
            ('確認して復元を実行', lambda: self.sample_undo(False))],
            '対象：サンプルフォルダ名、10k/100kのSample・Blankフォルダ名、直下txt、stocked直下のファイル名、\n'
            '100k Sample・Blank直下tdms、T直下tdms、T/stocked直下、T/ANAL配下（再帰）、BNAL・BNAL@の名前と直下のフォルダ名。\n'
            'PS・BSフォルダ内は対象外です。BNAL内のファイルや直下フォルダの内部は走査しません。ファイル内容は変更しません。\n'
            '例：対象フォルダ L-aTNA-T-re ／現サンプル名 L-aTNA-T ／新サンプル名 L-aTNA-T-re。\n'
            '英数字・ハイフンが続く別名は置換しません。変更予定の全件を確認してください。\n'
            '履歴はこのPCの filemanager/rename_logs/samples に永続保存します。後日も履歴を選んで復元できます。')
        self.sample_plan_rows = []
        self.sample_plan_filtered = []
        self.sample_plan_page = 0
        self.sample_plan_query = tk.StringVar()
        self.sample_plan_label = tk.StringVar(value='変更予定は未取得です。「変更予定を表示」を押してください。')
        container = ttk.LabelFrame(f, text='変更予定 / 復元予定', padding=6)
        container.grid(row=len(self.fields['sample_rename']) + 3, column=0, columnspan=3, sticky='nsew', pady=(8, 0))
        f.rowconfigure(len(self.fields['sample_rename']) + 3, weight=1)
        tools = ttk.Frame(container)
        tools.pack(fill='x')
        ttk.Label(tools, text='検索').pack(side='left')
        ttk.Entry(tools, textvariable=self.sample_plan_query).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(tools, text='前の300件', command=lambda: self.sample_plan_turn(-1)).pack(side='left', padx=4)
        ttk.Button(tools, text='次の300件', command=lambda: self.sample_plan_turn(1)).pack(side='left')
        ttk.Label(container, textvariable=self.sample_plan_label).pack(anchor='w', pady=4)
        table_frame = ttk.Frame(container)
        table_frame.pack(fill='both', expand=True)
        self.sample_plan_tree = ttk.Treeview(table_frame, columns=('step', 'kind', 'source', 'target', 'group'),
                                           displaycolumns=('step', 'kind', 'group', 'source', 'target'), show='headings', height=7)
        for key, title, width in [('step', '順番', 60), ('kind', '種類', 85), ('group', 'グループ', 140), ('source', '変更前のパス', 700), ('target', '変更後のパス（その操作時点）', 700)]:
            self.sample_plan_tree.heading(key, text=title)
            self.sample_plan_tree.column(key, width=width, stretch=key in ('source', 'target'))
        for tag, color in [('folder', '#25464b'), ('file', '#1b2832'), ('txt', '#53462a'),
                           ('10k', '#24485c'), ('100k', '#3d365d'), ('anal', '#28503d'),
                           ('bnal', '#543750'), ('stocked', '#593d2d')]:
            self.sample_plan_tree.tag_configure(tag, background=color, foreground='white')
        ys = ttk.Scrollbar(table_frame, command=self.sample_plan_tree.yview)
        xs = ttk.Scrollbar(table_frame, orient='horizontal', command=self.sample_plan_tree.xview)
        self.sample_plan_tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.sample_plan_tree.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.sample_plan_query.trace_add('write', lambda *_: self.filter_sample_plan())
        ttk.Label(container, text='行の色：TXT＝黄褐色／10kHz＝青／100kHz＝紫／ANAL＝緑／BNAL＝赤紫／stocked＝茶／フォルダ＝青緑').pack(anchor='w')

    def filter_sample_plan(self):
        query = self.sample_plan_query.get().strip().casefold()
        self.sample_plan_filtered = [r for r in self.sample_plan_rows if query in
                                     (r[2] + ' ' + r[3] + ' ' + sample_plan_group(r[2], r[1])[1]).casefold()]
        self.sample_plan_page = 0
        self.show_sample_plan()

    def sample_plan_turn(self, delta):
        last = max(0, (len(self.sample_plan_filtered) - 1) // 300)
        self.sample_plan_page = min(last, max(0, self.sample_plan_page + delta))
        self.show_sample_plan()

    def show_sample_plan(self):
        self.sample_plan_tree.delete(*self.sample_plan_tree.get_children())
        start = self.sample_plan_page * 300
        page = self.sample_plan_filtered[start:start + 300]
        for row in page:
            tag, label = sample_plan_group(row[2], row[1])
            self.sample_plan_tree.insert('', 'end', values=(*row[:4], label), tags=(tag,))
        self.sample_plan_label.set(f'予定 {len(self.sample_plan_rows):,}件 / 検索結果 {len(self.sample_plan_filtered):,}件'
                                   + (f' / {start+1:,}～{start+len(page):,}件を表示' if page else ' / 表示対象なし'))

    def select_rename_sample(self):
        if self.busy:
            return
        fields = self.fields['sample_rename']
        try:
            parts = [component(fields[k].get().strip(), k) for k in ('server', 'share', 'experiment')]
        except ValueError as exc:
            messagebox.showerror('入力を確認してください', str(exc), parent=self)
            return
        parent = '//' + '/'.join(parts)
        selected = filedialog.askdirectory(parent=self, initialdir=parent, mustexist=True)
        if selected:
            if os.path.normcase(os.path.normpath(os.path.dirname(selected))) != os.path.normcase(os.path.normpath(parent)):
                messagebox.showerror('選択先を確認してください', '指定した実験フォルダの直下から選択してください。', parent=self)
                return
            name = os.path.basename(selected)
            fields['folder'].set(name)
            fields['old'].set(name)

    def sample_undo(self, preview):
        if self.busy:
            return
        if preview:
            path = filedialog.askopenfilename(parent=self,
                initialdir=str(BASE / 'rename_logs' / 'samples'), filetypes=[('サンプル名変更履歴', '*.json')])
            if not path:
                return
            self.sample_history = path
        path = getattr(self, 'sample_history', None)
        if not path:
            messagebox.showinfo('復元予定の確認', '履歴を選んで復元予定を表示してください。', parent=self)
            return
        data = dict(kind='sample_restore', history=path, preview=preview)
        signature = json.dumps({**data, 'preview': True}, ensure_ascii=False, sort_keys=True)
        if not preview:
            if signature != self.preview_signature:
                messagebox.showinfo('復元予定の確認', '履歴を選んで復元予定を再表示してください。', parent=self)
                return
            if not messagebox.askyesno('復元の確認', f'{path}\n表示された予定で復元しますか？', parent=self):
                return
        self.start(data, signature if preview else None)

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
                     '実験フォルダは一覧から選択、または新しい名前を直接入力できます。\n'
                     '未作成の実験フォルダは新規作成し、既存フォルダはそのまま利用します。サンプル名は必須です。')

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
        if kind in ('rename', 'sample_rename') and not preview and signature != self.preview_signature:
            messagebox.showinfo('変更予定の確認', '現在の設定で「変更予定を表示」を先に実行してください。', parent=self)
            return
        if not preview:
            detail = (data['root'] if kind == 'rename' else
                      f'//{data["server"]}/{data["share"]}/{data["experiment"]}/{data["folder"]}\n'
                      f'{data["old"]} → {data["new"]}' if kind == 'sample_rename' else
                      f'//{data["server"]}/{data["share"]}/{data["experiment"]}\n'
                      f'サンプル: {data["samples"] or "すべて"}')
            if kind == 'copy':
                detail += (f'\n同期用コピー先: //{data["server"]}/{data["destination"]}/{data["experiment"]}'
                           f'\n同期先サーバー: {data["dest_server"]}')
                if data.get('target_bnal'):
                    detail += '\n\n' + self.bnal_summary.get()
            if not messagebox.askyesno('実行の確認', f'{detail}\n\nこの設定で実行しますか？', parent=self):
                return
        self.start(data, signature if kind in ('rename', 'sample_rename') and preview else None)

    def undo(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self, initialdir=str(BASE / 'rename_logs'),
                                          filetypes=[('名前変更履歴', '*.csv')])
        if path and messagebox.askyesno('復元の確認', f'{path}\n\nこの履歴の名前変更を元に戻しますか？', parent=self):
            self.start({'kind': 'undo', 'log': path})

    def start(self, data, signature=None):
        if any(getattr(getattr(self, key, None), 'busy', False) for key in ('anal_panel', 'bnal_panel', 't_stock_panel')):
            messagebox.showinfo('処理中', 'ANAL・BNAL整理の処理が終わってから実行してください。', parent=self)
            return
        self.busy = True
        self.active_kind = data['kind']
        self.active_sample_preview = bool(data.get('preview'))
        self.cancel_event = threading.Event()
        self.cancel_directory = tempfile.TemporaryDirectory(prefix='filemanager-cancel-')
        self.cancel_file = Path(self.cancel_directory.name) / 'stop'
        self.active_process = None
        self.active_preview = bool(data.get('preview')) and data.get('stocked_action') != 'restore'
        cancellable = (self.active_preview or data['kind'] in ('sample_rename', 'sample_restore')
                       or data['kind'] == 'copy' and 'target_text' in data)
        self.stop_button.configure(state='normal' if cancellable else 'disabled')
        self.preview_signature = None
        if data['kind'] in ('sample_rename', 'sample_restore'):
            if data.get('preview'):
                self.sample_plan_rows = self.sample_plan_filtered = []
                self.sample_plan_tree.delete(*self.sample_plan_tree.get_children())
            self.sample_plan_label.set('変更予定を取得中…' if data.get('preview') else '変更を実行中… 表は実行前の予定です。')
        for button in self.buttons:
            button.state(['disabled'])
        self.status.set('予定を確認中…' if data.get('preview') else '処理中…')
        self.progress.configure(mode='indeterminate', value=0)
        self.progress.start(12)
        self.started_at = time.monotonic()
        self.timing.set('対象の名前と更新情報を確認中… 確認先・件数はログに表示します。'
                        if data['kind'] in ('sample_rename', 'sample_restore') else
                        '対象を確認中… 終了予定は転送開始後に計算します。')
        self.append('\n--- コピー予定の確認 ---\n' if data['kind'] == 'copy' and data.get('preview')
                    else '\n--- 新しい処理 ---\n')

        def worker():
            code = 1
            error_detail = None
            last_lines = []
            try:
                env = dict(os.environ, PYTHONIOENCODING='utf-8',
                           FILEMANAGER_CANCEL_FILE=str(self.cancel_file))
                with subprocess.Popen([sys.executable, '-u', str(BASE / 'app.py'), '--worker'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                      env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
                    self.active_process = process
                    process.stdin.write(json.dumps(data, ensure_ascii=False))
                    process.stdin.close()
                    if self.cancel_event.is_set() and self.active_preview:
                        process.terminate()
                    for line in process.stdout:
                        if line.startswith(PROGRESS_PREFIX):
                            self.events.put(('progress', json.loads(line[len(PROGRESS_PREFIX):])))
                        elif line.startswith(PLAN_PREFIX):
                            planned = json.loads(Path(json.loads(line[len(PLAN_PREFIX):])).read_text(encoding='utf-8'))
                            rows = [(n, 'フォルダ' if planned['snapshot'].get(source, [0, 0, 0])[2] is None else 'ファイル', source, target)
                                    for n, (source, target) in enumerate(planned['operations'], 1)]
                            self.events.put(('sample_plan', rows))
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
            finally:
                self.active_process = None
            if self.cancel_event.is_set() and self.active_preview:
                code, error_detail = 2, None
            if code not in (0, 2) and not error_detail:
                error_detail = f'処理用プロセスが終了しました（終了コード: {code}）。\n' + ''.join(last_lines)
            self.events.put(('done', (code, signature, error_detail)))

        threading.Thread(target=worker, daemon=False).start()

    def cancel_job(self):
        if not self.busy or self.cancel_event.is_set():
            return
        self.cancel_file.touch()
        self.cancel_event.set()
        self.stop_button.configure(state='disabled')
        self.status.set('停止要求済み — 現在の処理が戻り次第停止します…')
        self.append('\n停止を要求しました。処理済みの変更は残り、履歴から復元できます。\n')
        process = self.active_process
        # A preview never changes server data and can be stopped even during a NAS wait.
        if self.active_preview and process is not None:
            try:
                process.terminate()
            except OSError:
                pass

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
            elif event == 'sample_plan':
                self.sample_plan_rows = value
                self.filter_sample_plan()
            else:
                code, signature, error_detail = value
                self.busy = False
                self.stop_button.configure(state='disabled')
                self.cancel_directory.cleanup()
                self.progress.stop()
                elapsed = time.monotonic() - self.started_at
                self.timing.set(f'{"終了" if code == 0 else "停止"}時刻: {datetime.now():%Y-%m-%d %H:%M:%S}'
                                f' / 所要時間: {self.duration(elapsed)}')
                self.preview_signature = signature if code == 0 else None
                if self.active_kind in ('sample_rename', 'sample_restore'):
                    if self.active_sample_preview and code != 0:
                        self.sample_plan_rows = self.sample_plan_filtered = []
                        self.sample_plan_tree.delete(*self.sample_plan_tree.get_children())
                        self.sample_plan_label.set('予定を取得できませんでした。ログを確認し、再表示してください。')
                    elif not self.active_sample_preview:
                        self.sample_plan_label.set(('実行完了' if code == 0 else '途中停止・ログを確認してください')
                                                   + ' — 表は実行前の予定です。再実行には予定の再取得が必要です。')
                for button in self.buttons:
                    button.state(['!disabled'])
                self.status.set('処理終了 — ログを確認してください' if code == 0 else
                                '中止しました — 変更済みの分は履歴から復元できます' if code == 2 else
                                'エラー — ログを確認してください')
                if code not in (0, 2):
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
        if self.busy or any(getattr(getattr(self, key, None), 'busy', False) for key in ('anal_panel', 'bnal_panel', 't_stock_panel')):
            if self.busy and str(self.stop_button['state']) != 'disabled':
                message = '処理中です。途中で止める場合は、画面下部の「中断」を押してください。\n中断が完了してから画面を閉じてください。'
            else:
                message = '処理または中断が完了してから画面を閉じてください。'
            messagebox.showinfo('処理中', message, parent=self)
        else:
            self.destroy()


if __name__ == '__main__':
    if '--worker' in sys.argv:
        try:
            run_job(json.load(sys.stdin))
        except JobCancelled as exc:
            print(str(exc), flush=True)
            sys.exit(2)
        except Exception as exc:
            print(ERROR_PREFIX + json.dumps(describe_error(exc), ensure_ascii=False), flush=True)
            traceback.print_exc()
            sys.exit(1)
    else:
        App().mainloop()
