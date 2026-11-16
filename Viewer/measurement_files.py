"""Read-only inventory for a measurement's EXSV/T and its saved ANAL versions."""
import json
import math
import os
from pathlib import Path
import re
import time


def measurement_t_folder(value):
    path = Path(value.strip().strip('"')).expanduser()
    candidates = [path / 'EXSV' / 'T', path / 'T', path]
    for candidate in candidates:
        if candidate.name.casefold() == 't' and candidate.parent.name.casefold() == 'exsv' and candidate.is_dir():
            return candidate.resolve()
    raise ValueError('計測フォルダ、EXSV、またはEXSV/Tを指定してください。')


def signature(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def direct_tdms(folder):
    if not folder.exists():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() == '.tdms'),
                  key=lambda p: natural_key(p.name))


def natural_key(name):
    return [(1, int(part)) if part.isdigit() else (0, part.casefold()) for part in re.split(r'(\d+)', name)]


def source_name(value):
    # TDMS stores Windows/LabVIEW paths even when opened on another OS.
    return str(value).replace('\\', '/').rsplit('/', 1)[-1].casefold()


def scan_measurement(value, progress=None, settle_seconds=.35, min_age_seconds=2):
    from nptdms import TdmsFile
    folder = measurement_t_folder(value)
    # Include set-aside originals/results so analysis does not make a measurement disappear.
    raw = [p for d in (folder, folder / 'stocked') for p in direct_tdms(d)
           if not p.name.upper().startswith(('A@@', 'B@@'))]
    measurement_root = folder.parent.parent
    anal_roots = (folder / 'ANAL', folder.parent / 'ANAL', measurement_root / 'ANAL')
    analyzed = [p for base in anal_roots for d in (base, base / 'stocked') for p in direct_tdms(d)
                if p.name.upper().startswith('A@@')]
    before = {}
    for p in raw + analyzed:
        try:
            before[str(p)] = signature(p)
        except FileNotFoundError:
            pass
    time.sleep(settle_seconds)
    rows = []
    by_name = {}
    unmatched = {}
    for p in raw:
        try:
            current = signature(p)
            ready = (current[0] > 0 and current == before.get(str(p)) and
                     time.time_ns() - current[1] >= min_age_seconds * 1e9)
        except FileNotFoundError:
            continue
        row = dict(path=str(p), name=p.name, versions=[], signature=current, ready=ready,
                   status='未解析' if ready else '計測中・更新中（再確認してください）',
                   location='T/stocked' if p.parent.name == 'stocked' else 'T')
        rows.append(row)
        by_name.setdefault(source_name(p.name), []).append(row)
    for index, p in enumerate(analyzed):
        if progress:
            progress(f'ANAL対応を確認中 {index + 1}/{len(analyzed)}: {p.name}')
        try:
            current = signature(p)
            if current[0] == 0 or current != before.get(str(p)):
                continue
            with TdmsFile.open(p) as tdms:
                original = tdms['AR Table']['Filename'][:1][0]
                if isinstance(original, bytes):
                    original = original.decode('utf-8')
                properties = tdms.properties
                version = dict(path=str(p), signature=current,
                               label=f'{p.name} [{p.parent.relative_to(measurement_root)}]',
                               condition=str(properties.get('ANAL Condition Set', '')))
            if signature(p) != current:
                continue
            matches = by_name.get(source_name(original), [])
            if len(matches) == 1:
                matches[0]['versions'].append(version)
            else:
                # Do not guess from EXSV# alone, or choose between duplicate originals.
                key = source_name(original)
                if key not in unmatched:
                    unmatched[key] = dict(path='', name=str(original), versions=[], ready=False,
                                          signature=None, status='元TDMSなし・または同名が複数（ANALのみ表示可）', location='ANAL')
                    rows.append(unmatched[key])
                unmatched[key]['versions'].append(version)
        except (OSError, KeyError, IndexError, ValueError, UnicodeError) as error:
            rows.append(dict(path='', name=p.name, versions=[], ready=False, signature=None,
                             status=f'ANALを確認できません: {error}', location='ANAL'))
    for row in rows:
        row['versions'].sort(key=lambda v: Path(v['path']).name, reverse=True)
        if row['versions'] and row['ready']:
            row['status'] = f"解析済み（{len(row['versions'])}版）"
    return folder, sorted(rows, key=lambda row: natural_key(row['name']))


def channel_metadata(path, group, channel, auto_select=False):
    """Read channel lengths/properties, never load continuous waveform data."""
    from nptdms import TdmsFile
    tdms = TdmsFile.read_metadata(path)
    channels = [(g.name, c.name) for g in tdms.groups() for c in g.channels()]
    if (group, channel) not in channels:
        import numpy as np
        candidates = [(g.name, c.name) for g in tdms.groups() if 'table' not in g.name.casefold()
                      for c in g.channels() if len(c) and np.issubdtype(c.dtype, np.number)]
        same_channel = [pair for pair in candidates if pair[1] == channel]
        possible = same_channel or candidates
        if auto_select and len(possible) == 1:
            group, channel = possible[0]
        else:
            choices = candidates or channels
            preview = '、'.join(f'{g}/{c}' for g, c in choices[:6])
            suffix = f' ほか{len(choices) - 6}件' if len(choices) > 6 else ''
            raise ValueError(f'{group}/{channel} がありません。波形チャンネルを指定してください。候補: {preview}{suffix}')
    selected = tdms[group][channel]
    increment = selected.properties.get('wf_increment')
    try:
        increment = float(increment)
        hz = 1 / increment if math.isfinite(increment) and increment > 0 else None
    except (ValueError, TypeError):
        hz = None
    return dict(hz=hz, points=len(selected), channels=channels, group=group, channel=channel)


def preferences_path():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.config'))) / 'QuantumWorkbench' / 'viewer_measurement.json'


def load_preferences():
    try:
        value = json.loads(preferences_path().read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_preferences(folder, settings):
    path = preferences_path()
    previous = load_preferences()
    recent = [str(folder), *(v for v in previous.get('recent', []) if v != str(folder))][:10]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(recent=recent, settings=settings), ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)
