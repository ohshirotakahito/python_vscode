"""実験フォルダ（Experimental ID）の結果ファイル・ログを、置き場所を探して読む（読むだけ）。

探す順（引き継ぎ資料、AXN の `axn/data_locations.py` と同じ）: Machine_N.csv の `PC Directroy`（この PC がその装置の
ときだけ）→ `Tmp_backup` → `SQ_backup`。それぞれフォルダか `.zip`。15 GB 級の実験もあるので、zip は展開せず
必要なメンバーだけを `zipfile` で読む（中央ディレクトリの読み込みは一瞬、R_/PM_ の 1 ファイルも一瞬）。

結果ファイル `R_<工程>#<n>_<yyyymmdd_hhmm>_<Experimental ID>.txt` は「名前,値」の行、
50 ms ログ `PM_<工程>#<n>_<yyyymmdd_hhmm>_*.txt`、EXSV の 0.5 s ログ `PML_EXSV#<n>_<yyyymmdd_hhmm>_*.txt` は
1 行目が列名の CSV。LabVIEW 時代と Python 時代で形式は同じ（2026-11-15 に SQserver で確認）。
"""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re
import zipfile

from . import machine_data

RESULT_RE = re.compile(r'^R_(?P<stage>[A-Za-z]+)#(?P<no>\d+)_(?P<when>\d{8}_\d{4})_')
PLACES = (('PC', 'PC Directroy'), ('Tmp_backup', 'Tmp_backup'), ('SQ_backup', 'SQ_backup'))


@dataclass(frozen=True)
class Location:
    place: str          # PC / Tmp_backup / SQ_backup
    path: Path          # 実験フォルダ、または .zip
    zipped: bool = False

    @property
    def text(self):
        return str(self.path)


@dataclass(frozen=True)
class Result:
    stage: str
    no: int
    when: str                     # yyyymmdd_hhmm（ファイル名のもの）
    fields: "OrderedDict[str, str]"
    filename: str

    @property
    def time(self):
        return machine_data.run_time(self.when)


def parse_fields(text):
    """「名前,値」の行 → OrderedDict（名前は元のまま。末尾の空白も AXN と同じく残す）。"""
    fields = OrderedDict()
    for line in text.splitlines():
        if ',' in line:
            name, value = line.split(',', 1)
            fields[name] = value.strip()
    return fields


def parse_result_name(filename):
    m = RESULT_RE.match(filename)
    return (m.group('stage'), int(m.group('no')), m.group('when')) if m else None


def _rows(text):
    """CSV ログ → 1 行目を除いた行（カンマで split、空行は除く）。"""
    return [line.split(',') for line in text.splitlines()[1:] if line.strip()]


class Experiment:
    """1 つの実験フォルダ（または zip）。工程ごとの結果とログを読む。"""

    def __init__(self, name, location):
        self.name = name
        self.location = location
        self._zip = None
        self._index = None        # zip: {フォルダ: [(ファイル名, メンバー名)]}
        self._names = {}
        self._results = {}

    def close(self):
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- 読み方（フォルダ / zip） ---
    def _zipfile(self):
        if self._zip is None:
            self._zip = zipfile.ZipFile(self.location.path)
            index = {}
            for member in self._zip.namelist():
                if member.endswith('/'):
                    continue
                folder, _, filename = member.replace('\\', '/').rpartition('/')
                index.setdefault(folder, []).append((filename, member))
            self._index = index
        return self._zip

    def _zip_folder(self, folder):
        """zip の中の <folder>（先頭に実験名などのフォルダが 1 段あってもよい）。"""
        self._zipfile()
        matches = [key for key in self._index if key == folder or key.endswith('/' + folder)]
        return min(matches, key=len) if matches else None

    def names(self, folder):
        """<folder> 直下のファイル名。フォルダが無ければ []。"""
        if folder in self._names:
            return self._names[folder]
        if self.location.zipped:
            key = self._zip_folder(folder)
            names = sorted(filename for filename, _ in self._index.get(key, [])) if key is not None else []
        else:
            path = self.location.path / folder
            try:
                names = sorted(p.name for p in path.iterdir() if p.is_file()) if path.is_dir() else []
            except OSError:
                names = []
        self._names[folder] = names
        return names

    def has_folder(self, folder):
        if self.location.zipped:
            return self._zip_folder(folder) is not None
        return (self.location.path / folder).is_dir()

    def read_text(self, folder, filename):
        if self.location.zipped:
            key = self._zip_folder(folder)
            member = next(m for f, m in self._index.get(key, []) if f == filename)
            data = self._zipfile().read(member)
        else:
            data = (self.location.path / folder / filename).read_bytes()
        return machine_data.decode(data)

    # --- 工程の結果とログ ---
    def results(self, stage):
        """R_<stage>#n_... の結果（時刻・番号順）。読んだものは覚えておく。"""
        if stage in self._results:
            return self._results[stage]
        out = []
        for filename in self.names(stage):
            parsed = parse_result_name(filename)
            if parsed is None or parsed[0] != stage:
                continue
            _, no, when = parsed
            out.append(Result(stage, no, when, parse_fields(self.read_text(stage, filename)), filename))
        self._results[stage] = sorted(out, key=lambda r: (r.when, r.no))
        return self._results[stage]

    def log_rows(self, folder, prefix):
        """<folder> の prefix で始まる最初のログの行（1 行目の列名は除く）。無ければ None。"""
        filename = next((n for n in self.names(folder) if n.startswith(prefix)), None)
        return _rows(self.read_text(folder, filename)) if filename else None

    def pm_log(self, stage, no, when):
        """50 ms ログ PM_<stage>#<no>_<when>_*.txt の行。"""
        return self.log_rows(stage, f'PM_{stage}#{no}_{when}_')

    def exsv_logs(self, no, when):
        """EXSV の (0.5 s ログ PML_ の行, 50 ms ログ PM_ の行)。無いものは None。"""
        return self.log_rows('EXSV', f'PML_EXSV#{no}_{when}_'), self.log_rows('EXSV', f'PM_EXSV#{no}_{when}_')

    def device_code(self):
        """Device Code（R_FC か R_GPSET に書かれている）。無ければ ''。"""
        for stage in ('FC', 'GPSET'):
            for result in self.results(stage):
                if result.fields.get('Device Code'):
                    return result.fields['Device Code']
        return ''


def candidates(name, machine, machine_code=None, this_machine=None):
    """探す場所の候補を順に（存在は見ない）。machine は Machine_N.csv の 1 行。

    AXN の `axn/data_locations.py` `candidates` と同じ順: その PC の `PC Directroy`（この PC がその装置のときだけ。
    this_machine に装置コードを渡す）→ Tmp_backup のフォルダ → その zip → SQ_backup のフォルダ → その zip。
    順番が同じことは test_scoring が AXN の関数と突き合わせる。"""
    out = []
    for place, column in PLACES:
        root = str(machine.get(column, '') or '').strip()
        if not root:
            continue
        if place == 'PC':
            if this_machine is not None and machine_code == this_machine:
                out.append(Location(place, Path(root) / name))
            continue
        out.append(Location(place, Path(root) / name))
        out.append(Location(place, Path(root) / f'{name}.zip', zipped=True))
    return out


def locate(name, machine, machine_code=None, this_machine=None):
    """実験の置き場所を探して Experiment を返す。どこにも無ければ None。"""
    for location in candidates(name, machine, machine_code, this_machine):
        try:
            if location.zipped and location.path.is_file() or not location.zipped and location.path.is_dir():
                return Experiment(name, location)
        except OSError:
            continue
    return None
