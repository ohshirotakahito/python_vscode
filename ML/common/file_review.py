"""Saved-event inspection, with file filters and pooled event statistics."""
from pathlib import Path

import numpy as np
import pandas as pd

from common.feature_store import resolve_version
from common.tdms_io import META_COLUMNS

KEYS = ['sample', 'sample_name', 'ex_id', 'run_id']
STATS = ['count', 'mean', 'std', 'min', '25%', '50%', '75%', 'max']


def load_events(feature_set, samples, progress, feature_versions=None):
    frames = []
    for i, sample in enumerate(samples):
        selected = (feature_versions or {}).get(sample)
        version = selected if isinstance(selected, dict) else resolve_version(feature_set, sample, selected)
        directory = Path(version['directory'])
        stem = f'{sample}_10k_Sample_ANAL'
        if feature_set == 'rmb':
            array = np.load(directory / f'{stem}_rmb.npy', allow_pickle=True)
            if array.size == 0:
                continue
            if array.ndim != 2 or array.shape[1] < len(META_COLUMNS) - 1:
                raise ValueError(f'{sample}: 特徴量配列の形式が不正です')
            meta_columns = META_COLUMNS[1:] if array.shape[1] == 22 else META_COLUMNS
            frame = pd.DataFrame(array, columns=meta_columns + [
                f'wave_{n}' for n in range(array.shape[1] - len(meta_columns))])
        else:
            parquet = directory / f'{stem}_meta.parquet'
            frame = pd.read_parquet(parquet) if parquet.exists() else pd.read_csv(
                directory / f'{stem}_meta.csv', dtype={'sample_name': str, 'ex_id': str, 'sample': str})
        for name in frame.columns.difference(['sample', 'sample_name', 'ex_id']):
            frame[name] = pd.to_numeric(frame[name], errors='coerce').replace([np.inf, -np.inf], np.nan)
        frame = add_signal_features(frame)
        frame['sample'] = str(sample)
        for name in ['sample_name', 'ex_id']:
            frame[name] = frame[name].fillna('不明').astype(str)
        frame['run_id'] = version['run_id']
        frame['experiment'] = str(version.get('ex') or '不明')
        frame['condition_id'] = str(version.get('condition_id') or '不明')
        frame['extracted_at'] = str(version.get('extracted_at') or '不明')
        frames.append(frame)
        progress(i + 1, len(samples), sample)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def add_signal_features(frame):
    """Use the signal definitions shared by training and existing histograms."""
    frame = frame.copy()
    if 'signal' in frame:
        signal = pd.to_numeric(frame['signal'], errors='coerce').replace([np.inf, -np.inf], np.nan)
        frame['relative_signal'] = signal
        if 'baseline' in frame:
            baseline = pd.to_numeric(frame['baseline'], errors='coerce').replace([np.inf, -np.inf], np.nan)
            frame['absolute_signal'] = (signal + baseline).replace([np.inf, -np.inf], np.nan)
    return frame


def numeric_features(events):
    return [c for c in events.select_dtypes(include='number').columns
            if c not in ['event_id', 'peak_pos', 'start', 'end']]


def file_statistics(events):
    if events.empty:
        return pd.DataFrame()
    groups = events.groupby(KEYS, sort=True, dropna=False)
    result = groups.size().rename('events').to_frame()
    for column in ['experiment', 'condition_id', 'extracted_at']:
        result[column] = groups[column].first()
    for column in numeric_features(events):
        stats = groups[column].agg(['count', 'mean', 'std', 'min', 'median', 'max'])
        result = result.join(stats.add_prefix(column + '.'))
    return result.reset_index()


def filter_files(files, rules):
    mask = pd.Series(True, index=files.index)
    for column, operator, value in rules:
        if not str(value).strip():
            raise ValueError('条件の値を入力してください')
        if operator not in ['含む', '=', '>=', '<=']:
            raise ValueError('不明な比較演算子です')
        if operator == '含む':
            match = files[column].astype(str).str.contains(str(value), regex=False, na=False)
        elif operator == '=' and not pd.api.types.is_numeric_dtype(files[column]):
            match = files[column].astype(str).eq(str(value))
        else:
            values = pd.to_numeric(files[column], errors='coerce')
            limit = float(value)
            if not np.isfinite(limit):
                raise ValueError('有限の数値を入力してください')
            match = (values.eq(limit) if operator == '=' else
                     values.ge(limit) if operator == '>=' else values.le(limit))
        mask &= match
    return files.loc[mask].copy()


def selected_events(events, files):
    if events.empty or files.empty:
        return events.iloc[:0]
    return events.merge(files[KEYS], on=KEYS, how='inner', validate='many_to_one')


def comparison(events, selected):
    rows = []
    for feature in numeric_features(events):
        all_stats = events[feature].describe()
        subset_stats = selected[feature].describe()
        for stat in STATS:
            a, b = all_stats[stat], subset_stats[stat]
            rows.append([feature, stat, a, b, b - a])
    return pd.DataFrame(rows, columns=['特徴量', '統計', '全ファイル', '絞り込み後', '差分'])


def event_histogram(events, selected, feature, bins=50, percent=False, value_range=None, normalize_to=None):
    """Pool raw event values, using the same bin boundaries for both populations."""
    def finite(frame):
        values = pd.to_numeric(frame[feature], errors='coerce').to_numpy(dtype=float)
        return values[np.isfinite(values)]

    all_values, selected_values = finite(events), finite(selected)
    if value_range is not None:
        low, high = value_range
        if not np.isfinite([low, high]).all() or low >= high:
            raise ValueError('範囲は有限の数値で下限 < 上限にしてください')
    edges = np.histogram_bin_edges(all_values, bins=bins, range=value_range)
    all_counts = np.histogram(all_values, bins=edges)[0].astype(float)
    selected_counts = np.histogram(selected_values, bins=edges)[0].astype(float)
    target = normalize_to if normalize_to is not None else 100 if percent else None
    if target is not None:
        all_counts *= target / max(len(all_values), 1)
        selected_counts *= target / max(len(selected_values), 1)
    return edges, all_counts, selected_counts, len(all_values), len(selected_values)


def histogram_markers(frame, feature, edges):
    """Full-population moments/quantiles and modal displayed-bin midpoint."""
    values = pd.to_numeric(frame[feature], errors='coerce').to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return {}
    q1, median, q3 = np.quantile(values, [.25, .5, .75])
    counts = np.histogram(values, bins=edges)[0]
    mode = np.nan
    if counts.max() > 0:
        index = int(counts.argmax())
        mode = (edges[index] + edges[index + 1]) / 2
    return {'Mean': values.mean(), 'Mode*': mode, 'Q1': q1, 'Median': median, 'Q3': q3}
