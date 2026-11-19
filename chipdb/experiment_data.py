"""ChipID → 実験データの逆引き（読むだけ。取り出しはローカルへのコピー）。

チップ履歴CSVの Experiment 行（実験 ID・操作 FC#5 など）から、その実験が SQserver のどこにあるかを探す。
探す順は scoring/experiment_files（= AXN `axn/data_locations.py`）と同じ Tmp_backup → SQ_backup、それぞれフォルダか zip。
その後に data_stocked/ANn（SQ_backup の data を data_stocked に替えた場所。2021 年ごろの古い実験の zip。Machine_N.csv には
載っていないが、実験ファイルDB は sq_stocked として一覧している）も探す。
SQserver に届かない PC では OUNmain03 が毎時書く一覧（database/Upload の SQ inventory）から「どこにあるはずか」だけ出す。
zip は展開しない: FC・MA・TA・CAL のフォルダは数百 KB なので、そのメンバーだけをコピーする（15 GB の zip でも一瞬）。
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil

from scoring import experiment_files, machine_data

STAGES = ('FC', 'MA', 'TA', 'CAL', 'AC')
CONDITIONS = 'GPSET'          # 実験全体の測定条件（試料・溶媒・カバー・装置など）。履歴の操作に関係なく全部出す
_RUN_RE = re.compile(r'^(?:R|PM)_(?P<run>[A-Za-z]+#\d+_\d{8}_\d{4})')
_DATA_RE = re.compile(r'([\\/])data([\\/][^\\/]+)$', re.IGNORECASE)
_OPERATION_RE = re.compile(r'^(?P<stage>[A-Za-z]+)#(?P<no>\d+)$')


@dataclass
class ChipExperiment:
    name: str                                  # Experimental ID（実験フォルダ名）
    machine: str                               # AXN2 など。読めなければ ''
    operations: list = field(default_factory=list)   # [(stage, no)] 履歴の順
    date: str = ''


@dataclass(frozen=True)
class Found:
    place: str            # Tmp_backup / SQ_backup
    path: Path            # 実験フォルダ、または .zip
    zipped: object        # True / False、一覧だけで分かったときは None（フォルダか zip か不明）
    confirmed: bool       # SQserver で存在を確かめた（False = 一覧に載っているだけ）

    @property
    def kind(self):
        if self.zipped is None:
            return 'SQserverに届かない（一覧上）'
        return 'zip' if self.zipped else 'フォルダ'


def stocked_candidates(name, info):
    """SQ_backup（//SQserver/data/AN8）の data を data_stocked に替えた場所の フォルダ / zip。"""
    text = str(info.get('SQ_backup', '') or '').strip().rstrip('/' + chr(92))
    # UNC では //SQserver/data がドライブ扱いで Path.parent が使えないので、文字列で末尾の data/<ANn> を置き換える
    stocked, count = _DATA_RE.subn(lambda m: f'{m.group(1)}data_stocked{m.group(2)}', text)
    if not count:
        return []
    base = Path(stocked)
    return [experiment_files.Location('data_stocked', base / name),
            experiment_files.Location('data_stocked', base / f'{name}.zip', zipped=True)]


def chip_experiments(rows):
    """チップ履歴CSVの行 → 実験ごとの FC・MA・TA・CAL 操作（最初に出てきた順）。"""
    out = {}
    for row in rows:
        if len(row) < 6 or row[0] != 'Experiment':
            continue
        name = row[4].strip()
        match = _OPERATION_RE.match(row[5].strip())
        if not name or name == '-' or not match or match.group('stage') not in STAGES:
            continue
        item = out.get(name)
        if item is None:
            item = out[name] = ChipExperiment(name, machine_data.machine_of(name) or '', date=row[2].strip())
        op = (match.group('stage'), int(match.group('no')))
        if op not in item.operations:
            item.operations.append(op)
    return list(out.values())


def operation_text(operations):
    return ' '.join(f'{stage}#{no}' for stage, no in operations)


def locate(experiment, machines, inventories, online=True):
    """Found か None。online なら SQserver で確かめ、届かなければ一覧（inventories[machine]）で答える。"""
    info = machines.get(experiment.machine)
    if info is None:
        return None
    listed = inventories.get(experiment.machine, {}).get(experiment.name, '')
    places = [p for p in ('Tmp_backup', 'SQ_backup') if p in listed]
    candidates = experiment_files.candidates(experiment.name, info) + stocked_candidates(experiment.name, info)
    if online:
        # 一覧に載っている場所から先に確かめる（ネットワークへの問い合わせを減らす）。順位は AXN と同じまま
        ordered = sorted(candidates, key=lambda c: c.place not in places) if places else candidates
        reachable = False
        for candidate in ordered:
            try:
                exists = candidate.path.is_file() if candidate.zipped else candidate.path.is_dir()
                reachable = reachable or candidate.path.parent.is_dir()
            except OSError:
                continue
            if exists:
                return Found(candidate.place, candidate.path, candidate.zipped, True)
        if reachable:
            return None
    for candidate in candidates:                   # 届かない: 一覧にある場所だけ答える（フォルダか zip かは不明）
        if candidate.place in places and not candidate.zipped:
            return Found(candidate.place, candidate.path, None, False)
    return None


def renamed_candidates(experiment, inventories):
    """同じ実験日時（先頭の yyyymmdd_hhmm）で一覧に載っている別名の実験（改名された実験の手がかり）。"""
    stamp = experiment.name[:13]
    listed = inventories.get(experiment.machine, {})
    return sorted(n for n in listed if n[:13] == stamp and n != experiment.name)


def stage_files(found, experiment_name, operations):
    """[(stage#no, ファイル名, 開ける場所の文字列)]: その操作の R_ / PM_ ファイル。"""
    out = []

    def where(stage, name):
        return f'{found.path} :: {stage}/{name}' if found.zipped else str(found.path / stage / name)
    with _open(found, experiment_name) as exp:
        for name in exp.names(CONDITIONS):
            out.append((CONDITIONS, name, where(CONDITIONS, name)))
        for stage, no in operations:
            names = sorted((n for n in exp.names(stage) if n.startswith((f'R_{stage}#{no}_', f'PM_{stage}#{no}_'))),
                           key=lambda n: (run_of(n), n))       # やり直しが日時ごとに PM_・R_ の組で並ぶ
            for name in names:
                out.append((f'{stage}#{no}', name, where(stage, name)))
            if not names:
                out.append((f'{stage}#{no}', '（ファイルなし）', ''))
    return out


def run_of(filename):
    """R_AC#1_20261015_1205_… / PM_AC#1_20261015_1205_.txt → 'AC#1_20261015_1205'（同じ操作のやり直しを区別する）。"""
    match = _RUN_RE.match(filename)
    return match.group('run') if match else ''


def _open(found, experiment_name):
    return experiment_files.Experiment(experiment_name, experiment_files.Location(found.place, found.path, found.zipped))


def read_log(found, experiment_name, stage, filename):
    """PM_ ログ（1 行目が列名の CSV）→ {列名: [値]}。数値でない欄は nan。"""
    with _open(found, experiment_name) as exp:
        lines = exp.read_text(stage, filename).splitlines()
    return parse_log(lines)


def parse_log(lines):
    if not lines:
        return {}
    names = [n.strip() for n in lines[0].split(',')]
    columns = {n: [] for n in names}
    for line in lines[1:]:
        cells = line.split(',')
        if len(cells) < len(names) or not line.strip():
            continue
        for name, cell in zip(names, cells):
            try:
                columns[name].append(float(cell))
            except ValueError:
                columns[name].append(float('nan'))
    return columns


def read_result(found, experiment_name, stage, filename):
    """R_ 結果ファイル → OrderedDict（名前, 値）。"""
    with _open(found, experiment_name) as exp:
        return experiment_files.parse_fields(exp.read_text(stage, filename))


def extract_root():
    """ダブルクリックで開くための作業場所（PC のローカル）。"""
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'ChipAtlas' / 'experiments'


def _downloads_settings():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.config')) / 'QuantumWorkbench' / 'chip_atlas_downloads.json'


def default_download_root():
    return Path.home() / 'Downloads' / 'ChipAtlas'


def download_root():
    """保存先（PC ごと）。決めていなければ ダウンロード/ChipAtlas。"""
    try:
        value = json.loads(_downloads_settings().read_text(encoding='utf-8'))['download_root']
        if isinstance(value, str) and value and not is_server_path(value):
            return Path(value)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return default_download_root()


def set_download_root(folder):
    if is_server_path(folder):
        raise ValueError('サーバー（ネットワーク上のフォルダ）には保存できません。この PC のフォルダを選んでください。')
    path = _downloads_settings()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'download_root': str(Path(folder))}, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def is_server_path(folder):
    """UNC（//SQserver/…）か、ネットワークドライブに割り当てた文字（Z: など）なら True。"""
    text = str(folder).strip().replace('/', chr(92))
    if text.startswith(chr(92) * 2):
        return True
    drive = os.path.splitdrive(text)[0]
    if os.name == 'nt' and len(drive) == 2 and drive[1] == ':':
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(drive + chr(92)) == 4       # DRIVE_REMOTE
    return False


def _copy(exp, found, stage, name, out):
    """1 ファイルを out にコピー（同じ大きさのものがあれば飛ばす）。返り値: コピーしたら True。"""
    if found.zipped:
        key = exp._zip_folder(stage)
        member = next(m for f, m in exp._index[key] if f == name)
        info = exp._zipfile().getinfo(member)
        if out.is_file() and out.stat().st_size == info.file_size:
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        partial = out.with_name(out.name + '.part')
        with exp._zipfile().open(info) as source, open(partial, 'wb') as sink:
            shutil.copyfileobj(source, sink, 1 << 20)
        partial.replace(out)
        return True
    source = found.path / stage / name
    if out.is_file() and out.stat().st_size == source.stat().st_size:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, out)
    return True


def save_files(found, experiment_name, files, destination=None):
    """[(stage, ファイル名)] を destination/<実験ID>/ に（フォルダを分けずに）コピーする。返り値: (フォルダ, コピー数)。"""
    root = Path(destination or download_root())
    if is_server_path(root):
        raise ValueError('サーバーには保存できません')
    target = root / experiment_name
    copied = 0
    with _open(found, experiment_name) as exp:
        for stage, name in files:
            copied += _copy(exp, found, stage, name, target / name)
    target.mkdir(parents=True, exist_ok=True)
    return target, copied


def extract(found, experiment_name, destination=None, stages=STAGES + (CONDITIONS,)):
    """FC・MA・TA・CAL・AC・GPSET のフォルダを destination/<実験名>/<stage>/ にコピーする。返り値: フォルダ。"""
    target = Path(destination or extract_root()) / experiment_name
    with _open(found, experiment_name) as exp:
        for stage in stages:
            for name in exp.names(stage):
                _copy(exp, found, stage, name, target / stage / name)
    return target
