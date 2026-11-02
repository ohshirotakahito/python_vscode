"""SQserver update selection and saved-folder history comparison."""
from pathlib import PureWindowsPath
import re
import os
from shared import seq_records


def stage(source):
    parts = [p.casefold() for p in source['root'].replace('\\', '/').rstrip('/').split('/')]
    if source['kind'] == 'sq_stocked' or 'data_stocked' in parts:
        return 'Stocked_Data'
    if source['kind'] == 'sq_data' or 'data' in parts:
        return 'Archived_Data'
    if re.fullmatch(r'backup_0[1-8]', parts[-1]) or source['kind'] == 'sq_recent':
        return 'Recent_Data'
    return None


def jobs_for_stage(configured, wanted):
    return [(dict(s), '.') for s in configured
            if s['server'].casefold() == 'sqserver' and s['root'].strip() and stage(s) == wanted]


def enrich(groups, entries):
    try:
        machines = seq_records.machines(seq_records.MACHINE_CSV)
        history = seq_records.history(seq_records.LEGACY_HISTORY, seq_records.LOCAL_HISTORY)
        error = ''
    except (OSError, ValueError, UnicodeError) as exc:
        machines, history, error = {}, [], str(exc)
    history_keys = {(r[2], r[6]) for r in history}
    first_dates = {}
    for record in history:
        # Column 0 is uploaded_at: the first recorded copy marks analysis start.
        key = (record[2], record[6])
        first_dates[key] = min(first_dates.get(key, record[0]), record[0])
    labels = {'Recent data': 'Recent_Data', 'data': 'Archived_Data', 'data_stocked': 'Stocked_Data'}
    for row in [*groups, *entries]:
        row['group'] = labels.get(row['group'], row['group'])
        row['analysis_started_at'] = ''
    for group in groups:
        members = [r for r in entries if (r['root'], r['container'], r['group']) ==
                   (group['root'], group['container'], group['group']) and r['entry_type'] == 'フォルダ']
        group.update(started_count='', pending_count='', started_rate='', history_note='')
        if group['group'] == 'Stocked_Data':
            for row in members:
                row['analysis_state'] = ''
            continue
        container = PureWindowsPath(group['root'])
        relative = group['container']
        if container.name.casefold() != relative.casefold() and container.as_posix().rstrip('/').rsplit('/', 1)[-1].casefold() != relative.casefold():
            container /= relative.replace('/', '\\')
        field = 'Tmp_backup' if group['group'] == 'Recent_Data' else 'SQ_backup'
        codes = [code for code, info in machines.items()
                 if PureWindowsPath(info.get(field, '')) == container]
        valid = bool(codes) and not error
        for row in members:
            row['analysis_state'] = ('着手済み' if any((code, row['name']) in history_keys for code in codes)
                                     else '未着手') if valid else '確認不可'
            dates = [first_dates[(code, row['name'])] for code in codes if (code, row['name']) in first_dates]
            if valid and dates:
                row['analysis_started_at'] = min(dates).replace('/', '-').replace('_', ' ')
        if valid and group['folder_count'] is not None:
            count = sum(r['analysis_state'] == '着手済み' for r in members)
            group.update(started_count=count, pending_count=len(members)-count,
                         started_rate=f'{count / len(members):.1%}' if members else '')
        else:
            group.update(started_count='確認不可', pending_count='確認不可', history_note=error or '装置CSVの対応設定なし')


def acquisition_counts(path, root, relative):
    from shared.catalog import connect
    with connect(path, readonly=True) as db:
        source = db.execute('SELECT id FROM sources WHERE root=?',
                            (os.path.normcase(os.path.normpath(root)),)).fetchone()
        if source is None:
            return '未取得'
        folders = {r[0].casefold(): r[0] for r in db.execute('SELECT relative_path FROM folders WHERE source_id=?', (source[0],))}
        exsv = folders.get((relative + '/EXSV').casefold())
        target = folders.get((relative + '/EXSV/T').casefold())
        if exsv is None:
            return 'EXSVフォルダなし'
        txt = {r[0].rsplit('/', 1)[-1][:-4] for r in db.execute(
            "SELECT relative_path FROM files WHERE source_id=? AND folder=? AND extension='.txt'", (source[0], exsv))
               if r[0].rsplit('/', 1)[-1].startswith('D_')}
        tdms = {r[0].rsplit('/', 1)[-1][:-5] for r in db.execute(
            "SELECT relative_path FROM files WHERE source_id=? AND folder=? AND extension='.tdms'", (source[0], target))} if target else set()
        return (len(txt), len(txt & tdms), len(txt - tdms))
