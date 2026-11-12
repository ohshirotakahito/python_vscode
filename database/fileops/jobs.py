"""ファイル操作（コピー・サンプル名変更・名前変更・フォルダ作成）の処理と入力の検証。

実験ファイルDBの「ファイル操作」タブ（panel.py）が、このファイルを処理用プロセスとして起動する:
    python database/fileops/jobs.py --worker   （標準入力に JSON の指示を渡す）
もとはファイル管理アプリ（filemanager/app.py）。
"""
import json
import errno
import stat
import time
import shutil
import tempfile
import os
from pathlib import Path
import re
import sys
import traceback

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parents[1]))  # リポジトリ直下（shared/ を読むため）
# 既存スクリプト（create_folders.py など）は同じフォルダの名前で互いを読み込む
sys.path.insert(0, str(BASE))
from shared.file_moves import log_dir  # noqa: E402
from job_control import JobCancelled, check_cancel  # noqa: E402

ERROR_PREFIX = '__FILEOPS_ERROR__'
PROGRESS_PREFIX = '__FILEOPS_PROGRESS__'
PLAN_PREFIX = '__FILEOPS_SAMPLE_PLAN__'
# 名前変更・サンプル名変更の履歴（このPCの database/file_logs/rename_logs。Git 管理外）
RENAME_LOGS = log_dir('rename_logs')
# 旧 filemanager/ のほかの記録（ファイル移動）も、シグナル解析を起動しないPCで移るようにここで移す
for _name in ('anal_logs', 'bnal_logs', 't_stock_logs'):
    log_dir(_name)


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
                    dir=os.path.dirname(target), prefix='.fileops-', suffix='.part', delete=False) as writer:
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
        preview_file = RENAME_LOGS / 'previews' / (key + '.json')
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
                sample_rename.execute(planned, RENAME_LOGS / 'samples')
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
        sys.exit('実験ファイルDB（python database/app.py）の「ファイル操作」タブから使います。')
