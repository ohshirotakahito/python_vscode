"""Domain views derived entirely from the saved inventory (no server access)."""
from collections import defaultdict
from pathlib import PurePosixPath
from datetime import datetime
import re
import unicodedata

try:
    from .catalog import DEFAULT_DB, connect, scope_state
except ImportError:
    from catalog import DEFAULT_DB, connect, scope_state

ARCHIVES = ('.tar.gz', '.tar.bz2', '.tar.xz', '.zip', '.7z', '.rar', '.tar', '.tgz', '.gz', '.bz2', '.xz', '.lzh')
SQ_KINDS = ('experiment', 'sq_recent', 'sq_data', 'sq_stocked')


def archive_stem(name):
    for suffix in ARCHIVES:
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return None


def parse_experiment_name(name, archive=False):
    """Keep unknown formats explicit; normalize full-width characters for parsing."""
    stem = archive_stem(name) if archive else name
    text = unicodedata.normalize('NFKC', stem if stem is not None else name)
    result = dict(experiment_name='形式不明', experiment_at='', operator='', machine='',
                  experiment_number='', repeat_number='', parse_status='形式不明')
    match = re.search(r'(?<![A-Za-z])([A-Za-z]+?)(AN#\d+)Pex(\d+)n(\d+)(?:_(.*))?$', text, re.I)
    if match:
        result.update(operator=match[1], machine=match[2].upper(), experiment_number=int(match[3]),
                      repeat_number=int(match[4]), experiment_name=match[5] if match[5] and match[5].strip() else '無名',
                      parse_status='日時不明')
    prefix = text[:match.start()] if match else text
    date = re.search(r'(?<!\d)(\d{8})[_ -](\d{4})(\d{2})?(?!\d)', prefix)
    if date:
        try:
            parsed = datetime.strptime(date[1] + date[2] + (date[3] or ''), '%Y%m%d%H%M%S' if date[3] else '%Y%m%d%H%M')
            result['experiment_at'] = parsed.isoformat(sep=' ', timespec='seconds' if date[3] else 'minutes')
            if match:
                result['parse_status'] = '解析済み'
        except ValueError:
            pass
    return result


def _snapshots(path, kinds):
    # One read transaction keeps folder/file counts consistent during a refresh.
    with connect(path, readonly=True) as db:
        db.execute('BEGIN')
        for source in db.execute('SELECT * FROM sources ORDER BY server,root').fetchall():
            if source['kind'] not in kinds:
                continue
            folders = {r[0] for r in db.execute('SELECT relative_path FROM folders WHERE source_id=?', (source['id'],))}
            counts = {r['folder']: dict(r) for r in db.execute('''
                SELECT folder, COUNT(*) AS total,
                SUM(CASE WHEN extension='.tdms' THEN 1 ELSE 0 END) AS tdms
                FROM files WHERE source_id=? GROUP BY folder''', (source['id'],))}
            source = dict(source)
            source['_updates'] = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='updates'").fetchone():
                source['_updates'] = [dict(r) for r in db.execute('SELECT * FROM updates WHERE source_id=? ORDER BY id DESC', (source['id'],))]
            yield source, folders, counts, db


def _children(folders):
    children = defaultdict(list)
    for folder in sorted(folders):
        if folder != '.':
            children[str(PurePosixPath(folder).parent)].append(folder)
    return children


def _count(folders, counts, folder, metric='tdms'):
    return counts.get(folder, {}).get(metric, 0) if folder in folders else None


def _matches(row, search):
    return search.casefold() in ' '.join(str(v) for v in row.values()).casefold()


def analysis_inventory(path=DEFAULT_DB, search=''):
    rows = []
    for source, folders, counts, _ in _snapshots(path, ('analysis',)):
        children = _children(folders)
        for sample_path in sorted(f for f in folders if len(PurePosixPath(f).parts) == 2):
            experiment, sample = PurePosixPath(sample_path).parts
            variants = {}
            raw = []
            for folder in children[sample_path]:
                name = PurePosixPath(folder).name
                match = re.search(r'(?:^|_)(10k|100k)_(Sample|Blank)$', name, re.I)
                if match:
                    variants[folder] = (match[1].lower(), match[2].capitalize())
                elif name.lower() == 'raw data':
                    raw.append(folder)
            observed = set(variants.values())
            for frequency in ('10k', '100k'):
                for specimen in ('Sample', 'Blank'):
                    if (frequency, specimen) not in observed:
                        variants[f'{sample_path}/{sample}_{frequency}_{specimen}'] = (frequency, specimen)
            for folder, (frequency, specimen) in sorted(variants.items()):
                t = folder + '/T'
                # Resolve case-insensitively while keeping actual spellings in output.
                lookup = {f.casefold(): f for f in children[folder]}
                t = lookup.get(t.casefold(), t)
                t_children = {PurePosixPath(f).name.casefold(): f for f in children[t]}
                anal = t_children.get('anal', t + '/ANAL')
                anal_children = {PurePosixPath(f).name.casefold(): f for f in children[anal]}
                bnals = [f for f in children[t] if PurePosixPath(f).name.lower().startswith('bnal@')]
                row = dict(server=source['server'], root=source['root'], experiment=experiment, sample=sample,
                           folder=folder, frequency=frequency, specimen=specimen,
                           folder_state='存在' if folder in folders else '未作成',
                           raw_folders='; '.join(raw) if raw else '未作成',
                           t_tdms=_count(folders, counts, t),
                           sibling_stocked=_count(folders, counts, lookup.get((folder + '/stocked').casefold(), folder + '/stocked'), 'total'),
                           t_stocked=_count(folders, counts, t_children.get('stocked', t + '/stocked'), 'total'),
                           anal_tdms=_count(folders, counts, anal),
                           anal_stocked=_count(folders, counts, anal_children.get('stocked', anal + '/stocked'), 'total'),
                           bnal_folders=len(bnals) if t in folders else None,
                           bnal_tdms=sum(counts.get(f, {}).get('tdms', 0) for f in bnals) if t in folders else None,
                           bnal_details='; '.join(f'{PurePosixPath(f).name}: {counts.get(f, {}).get("tdms", 0)}' for f in bnals),
                           **scope_state(source, sample_path, source['_updates']))
                if _matches(row, search):
                    rows.append(row)
    return rows


def _group(name):
    name = name.casefold().replace(' ', '_')
    if name.startswith('backup_') or name in ('recent_data', 'recent'):
        return 'Recent data'
    return {'data': 'data', 'data_stocked': 'data_stocked'}.get(name)


def sq_inventory(path=DEFAULT_DB, search=''):
    """Return (container summaries, named entries), including empty containers.

    Automatic roots may contain the three groups or point to a group directly.
    Explicit sq_* kinds also support shares with otherwise unrecognizable names.
    """
    summaries, entries = [], []
    for source, folders, counts, db in _snapshots(path, SQ_KINDS):
        children = _children(folders)
        root_name = PurePosixPath(source['root'].replace('\\', '/').rstrip('/')).name
        anchors = {}
        explicit = {'sq_recent': 'Recent data', 'sq_data': 'data', 'sq_stocked': 'data_stocked'}.get(source['kind'])
        if explicit or _group(root_name):
            anchors['.'] = explicit or _group(root_name)
        # Groups are accepted only at the root or immediately beneath it.
        # backup_* inside a Recent data container is a separate container.
        for folder in children['.']:
            group = _group(PurePosixPath(folder).name)
            if group:
                anchors[folder] = group
        for anchor, group in list(anchors.items()):
            if group == 'Recent data':
                backups = [f for f in children[anchor] if PurePosixPath(f).name.lower().startswith('backup_')]
                if backups:
                    del anchors[anchor]
                    anchors.update({f: group for f in backups})
        archive_files = defaultdict(list)
        for record in db.execute('SELECT relative_path,folder FROM files WHERE source_id=? ORDER BY relative_path', (source['id'],)):
            if archive_stem(PurePosixPath(record['relative_path']).name) is not None:
                archive_files[record['folder']].append(record['relative_path'])
        containers = []
        for anchor, group in anchors.items():
            if group == 'Recent data':
                containers.append((anchor, group))
            else:
                containers.extend((f, group) for f in children[anchor] if re.fullmatch(r'AN#?\d+', PurePosixPath(f).name, re.I))
        # An AN folder itself can also be configured as a source with an explicit kind.
        if explicit in ('data', 'data_stocked') and re.fullmatch(r'AN#?\d+', root_name, re.I):
            containers = [('.', explicit)]
        for container, group in sorted(set(containers)):
            common = dict(server=source['server'], root=source['root'], group=group,
                          container=root_name if container == '.' else container,
                          last_success=source['last_success'], status=source['status'])
            folder_names = children[container]
            compressed = archive_files[container]
            group_entries = []
            for relative, entry_type in [(f, 'フォルダ') for f in folder_names] + [(f, '圧縮ファイル') for f in compressed]:
                name = PurePosixPath(relative).name
                row = dict(common, name=name, relative_path=relative, entry_type=entry_type,
                           **parse_experiment_name(name, entry_type == '圧縮ファイル'))
                row.update(scope_state(source, relative, source['_updates']))
                row.update(exsv_tdms='', exsv_t_tdms='')
                if group == 'Recent data' and entry_type == 'フォルダ':
                    direct = {PurePosixPath(f).name.casefold(): f for f in children[relative]}
                    exsv = direct.get('exsv', relative + '/EXSV')
                    inside = {PurePosixPath(f).name.casefold(): f for f in children[exsv]}
                    row.update(exsv_tdms=_count(folders, counts, exsv),
                               exsv_t_tdms=_count(folders, counts, inside.get('t', exsv + '/T')))
                group_entries.append(row)
            summary = dict(common, folder_count=len(folder_names), archive_count=len(compressed),
                           folder_names='; '.join(PurePosixPath(f).name for f in folder_names),
                           archive_names='; '.join(PurePosixPath(f).name for f in compressed))
            summary.update(scope_state(source, container, source['_updates']))
            if _matches(summary, search) or any(_matches(r, search) for r in group_entries):
                summaries.append(summary)
            entries.extend(r for r in group_entries if _matches(r, search))
        if not containers:
            summary = dict(server=source['server'], root=source['root'], group=explicit or '分類未確定',
                           container=root_name, folder_count=None, archive_count=None,
                           folder_names='', archive_names='', last_success=source['last_success'], status='対象コンテナなし・設定を確認')
            if _matches(summary, search):
                summaries.append(summary)
    return summaries, entries
