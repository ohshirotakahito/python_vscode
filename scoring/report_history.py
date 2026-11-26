"""保存済みの計測ファイルから実行結果を読み取る。"""

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import PureWindowsPath
import re
from zipfile import BadZipFile, ZipFile

from . import experiment_files, machine_data

RUN = re.compile(r'(?:^|_)([A-Z]+)#(\d+)_(\d{8}_\d{4})(?=_|\.|$)')
MODES = {'GPSET', 'FC', 'MA', 'TA', 'AC', 'CAL', 'GAP', 'PSET', 'PSETS', 'CSET', 'CPSET', 'EXSV'}
READ_ERRORS = (OSError, BadZipFile, RuntimeError, NotImplementedError)


@dataclass(frozen=True)
class Report:
    when: str
    operation: str
    summary: str
    details: str


def _collect(paths, read):
    runs = defaultdict(list)
    reports = []
    for path in paths:
        filename = path.rsplit('/', 1)[-1]
        match = RUN.search(filename)
        if match and match[1] in MODES:
            when = machine_data.run_time(match[3])
            if when:
                runs[when, match[1], int(match[2])].append(path)
        elif path.endswith(f'/AutoGG/{filename}') or path == f'AutoGG/{filename}':
            if not filename.startswith('AutoGG_') or not filename.endswith('.json'):
                continue
            try:
                review = json.loads(read(path).decode('utf-8-sig'))
                when = review['finished'].replace('T', ' ')
                score = review['overall'].get('score')
                summary = f'参考スコア {score:.1f} / 100' if isinstance(score, (int, float)) else '参考スコアなし'
                reports.append(Report(when, 'Auto GG', summary,
                                      f'{path}\n\n{json.dumps(review, ensure_ascii=False, indent=2)}'))
            except READ_ERRORS + (ValueError, KeyError, TypeError, AttributeError):
                reports.append(Report('', 'Auto GG', '詳細情報を読み込めません', path))

    for (when, mode, number), files in sorted(runs.items(), reverse=True):
        summary = f'記録ファイル {len(files)} 件'
        details = []
        for path in sorted(files):
            filename = path.rsplit('/', 1)[-1]
            if not filename.startswith('R_') or not filename.lower().endswith('.txt'):
                continue
            try:
                data = read(path)
                try:
                    raw = data.decode('utf-8-sig')
                except UnicodeDecodeError:
                    raw = machine_data.decode(data)
            except READ_ERRORS:
                details.append(f'{path}\n詳細情報を読み込めません')
                continue
            fields = experiment_files.parse_fields(raw)
            metrics = [f'{key.strip()} = {value}' for key, value in fields.items()
                       if value and key.strip().endswith((' QC', ' Score', ' Result'))]
            if not metrics:
                metrics = [f'{key.strip()} = {value}' for key, value in fields.items()
                           if value and key.strip().startswith(f'{mode} ')
                           and '[' in key][:3]
            if metrics:
                summary = ' / '.join(metrics)
            details.append(f'{path}\n{raw.strip()}')
        details.append('記録ファイル\n' + '\n'.join(sorted(files)))
        reports.append(Report(when.strftime('%Y-%m-%d %H:%M'), f'{mode}#{number}',
                              summary, '\n\n'.join(details)))
    return sorted(reports, key=lambda report: report.when, reverse=True)


def load(name, machine_code):
    """(保存場所, 実行結果)。記録を含む最初のバックアップを使う。"""
    if not name or PureWindowsPath(name).name != name or name in ('.', '..'):
        return '', []
    machine = machine_data.machines().get(machine_code, {})
    error = None
    found = ''
    for location in experiment_files.candidates(name, machine):
        try:
            if location.zipped and location.path.is_file():
                with ZipFile(location.path) as archive:
                    members = {member.filename.replace('\\', '/'): member
                               for member in archive.infolist() if not member.is_dir()}
                    reports = _collect(members, lambda path: archive.read(members[path]))
            elif not location.zipped and location.path.is_dir():
                paths = (path.relative_to(location.path).as_posix() for path in location.path.rglob('*')
                         if path.is_file())
                reports = _collect(paths, lambda path: (location.path / path).read_bytes())
            else:
                continue
        except READ_ERRORS as exc:
            error = exc
            continue
        if reports:
            return location.text, reports
        found = found or location.text
    if error is not None:
        raise error
    return found, []
