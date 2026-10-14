"""Apply inspection file rules before training preprocessing and feature extraction."""
from contextvars import ContextVar

_selection = ContextVar('training_file_selection', default=None)


def prepare_selection(feature_set, samples, versions, rules):
    from common.file_review import load_events, file_statistics, filter_files

    events = load_events(feature_set, samples, lambda *_: None, versions)
    files = file_statistics(events)
    if files.empty:
        raise ValueError('絞り込み対象のファイルがありません。')
    selected = filter_files(files, rules)
    keys = {}
    counts = {}
    for sample in samples:
        all_files = files[files['sample'] == sample]
        subset = selected[selected['sample'] == sample]
        if subset.empty:
            raise ValueError(f'{sample}: 条件に一致するファイルがありません。条件を見直してください。')
        keys[sample] = list(zip(subset['sample_name'], subset['ex_id']))
        counts[sample] = {'all_files': len(all_files), 'selected_files': len(subset),
                          'selected_events': int(subset['events'].sum())}
    return {'rules': list(rules), 'keys': keys, 'counts': counts}


def selection_manifest():
    selection = _selection.get()
    return selection or {}


def filter_training_rows(frame, sample, traditional=False):
    selection = _selection.get()
    if selection is None:
        return frame
    file_column, ex_column = ('file', 'Ex_ID') if traditional else ('sample_name', 'ex_id')
    pairs = set(selection['keys'][sample])
    def text(value):
        import pandas as pd
        return '不明' if pd.isna(value) else str(value)
    mask = [(text(name), text(ex)) in pairs
            for name, ex in zip(frame[file_column], frame[ex_column])]
    result = frame.loc[mask].copy()
    if result.empty:
        raise ValueError(f'{sample}: 学習データに条件一致ファイルがありません。')
    return result
