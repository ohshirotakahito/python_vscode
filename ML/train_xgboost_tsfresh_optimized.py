# -*- coding: utf-8 -*-
"""精度改善実験。元の学習スクリプト・既存キャッシュは変更しない。

実行: python ML/train_xgboost_tsfresh_optimized.py
事前確認: python ML/train_xgboost_tsfresh_optimized.py --check-only
詳細は README_xgboost_optimized.md を参照。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import tsfresh
import xgboost as xgb
from sklearn.metrics import classification_report, confusion_matrix, f1_score, log_loss
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import LabelEncoder
from tsfresh import extract_features, select_features
from tsfresh.feature_extraction import (
    MinimalFCParameters, EfficientFCParameters, ComprehensiveFCParameters,
)

ROOT = Path(__file__).resolve().parent
SAMPLES = ['ALTNA', 'GLTNA', 'CLTNA', 'TLTNA']
META = ['absolute_signal', 'relative_signal', 'duration'] + [f'wave_{i}' for i in range(12)]
MODES = {'minimal': MinimalFCParameters, 'efficient': EfficientFCParameters,
         'comprehensive': ComprehensiveFCParameters}
CACHE_VERSION = 1


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def table_path(root, name):
    parquet = root / (name + '.parquet')
    return parquet if parquet.exists() else root / (name + '.csv')


def read_table(path):
    return pd.read_parquet(path) if path.suffix == '.parquet' else pd.read_csv(path)


def load_metadata(args):
    frames, sources = [], {}
    for sample in args.samples:
        stem = sample + '_10k_Sample_ANAL'
        meta_path = table_path(args.data_root, stem + '_meta')
        long_path = table_path(args.data_root, stem + '_tsfresh_input')
        if not meta_path.exists() or not long_path.exists():
            raise FileNotFoundError(f'{sample}: {meta_path} / {long_path}')
        frame = read_table(meta_path)
        for col in ['duration', 'baseline', 'signal', 'distance'] + [f'wave_{i}' for i in range(12)]:
            frame[col] = pd.to_numeric(frame[col], errors='raise')
        mask = (frame.duration.between(6, 999) & frame.baseline.between(-299, 999)
                & (frame.signal > 0) & (frame.signal < 1000))
        if args.distance is not None:
            mask &= np.isclose(frame.distance, args.distance, rtol=1e-7, atol=1e-9)
        frame = frame.loc[mask].copy()
        if frame.empty:
            raise ValueError(f'{sample}: 条件を満たすイベントがありません')
        if not frame['sample'].astype(str).eq(sample).all():
            raise ValueError(f'{sample}: metaのsample列と指定クラスが一致しません')
        if frame[args.group_column].isna().any():
            raise ValueError(f'{sample}: グループ列に欠損があります')
        # ファイル番号は測定間で再利用されるため測定IDとの組を使う。
        frame['group'] = frame[args.group_column].astype(str)
        if args.group_column == 'sample_name':
            if frame['ex_id'].isna().any():
                raise ValueError(f'{sample}: ex_idに欠損があります')
            frame['group'] = frame['ex_id'].astype(str) + '::' + frame['group']
        frame['global_id'] = sample + '__' + frame.event_id.astype(str)
        frame['relative_signal'] = frame.signal
        frame['absolute_signal'] = frame.signal + frame.baseline
        frames.append(frame.set_index('global_id'))
        sources[sample] = (meta_path, long_path)
    meta = pd.concat(frames)
    if not meta.index.is_unique:
        raise ValueError('重複イベントIDがあります。入力を確認してください')
    return meta, sources


def group_splits(y, groups, folds, seed):
    counts = [len(np.unique(groups[y == c])) for c in np.unique(y)]
    folds = min(folds, min(counts))
    if folds < 2:
        raise ValueError('各クラスに独立測定が不足しています。測定を追加してください')
    splits = list(StratifiedGroupKFold(folds, shuffle=True, random_state=seed).split(
        np.zeros(len(y)), y, groups))
    classes = set(y)
    for tr, va in splits:
        if set(y[tr]) != classes or set(y[va]) != classes:
            raise ValueError('分割後にクラスが欠落します。fold数または測定構成を見直してください')
        assert not set(groups[tr]) & set(groups[va])
    return splits


def waveform_features(part, meta):
    """正規化波形の形状・差分・区間特徴。振幅表現は別列として保存。"""
    records = []
    for event, series in part.groupby('id', sort=False):
        values = series.sort_values('time').value.to_numpy(dtype=float)
        if len(values) < 2 or not np.isfinite(values).all():
            raise ValueError(f'{event}: 不正な波形')
        diff = np.diff(values)
        row = {'global_id': event, 'shape__diff_std': diff.std(),
               'shape__variation': np.abs(diff).sum(),
               'shape__peak_fraction': float(np.argmax(values) / (len(values) - 1)),
               'shape__area_samples': float((values[:-1] + values[1:]).sum() / 2),
               'shape__half_height_fraction': float(np.mean(values >= (values.min() + np.ptp(values) / 2)))}
        for j, segment in enumerate(np.array_split(values, 3)):
            row[f'shape__segment{j}_mean'] = segment.mean() if len(segment) else np.nan
            row[f'shape__segment{j}_std'] = segment.std() if len(segment) else np.nan
        for points in (64, 128, 256):
            scaled = np.interp(np.linspace(0, 1, points), np.linspace(0, 1, len(values)), values)
            row.update({f'shape__resample{points}_{j}': v for j, v in enumerate(scaled)})
        amplitude = float(meta.loc[event, 'signal'])
        row['amplitude__std'] = values.std() * abs(amplitude)
        row['amplitude__diff_std'] = diff.std() * abs(amplitude)
        row['amplitude__area_samples'] = row['shape__area_samples'] * amplitude
        records.append(row)
    return pd.DataFrame(records).set_index('global_id')


def build_features(meta, sources, args):
    cache = args.data_root / '_cache_optimized_v1'
    cache.mkdir(exist_ok=True)
    all_frames = []
    for sample, (meta_path, long_path) in sources.items():
        subset = meta.loc[meta['sample'].eq(sample)]
        fingerprints = [(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns)
                        for p in (meta_path, long_path)]
        key = hashlib.sha256(repr((CACHE_VERSION, fingerprints, args.fc_mode,
                                  tsfresh.__version__, list(subset.index))).encode()).hexdigest()[:24]
        sample_cache = cache / key
        sample_cache.mkdir(exist_ok=True)
        long_df = None
        batches = []
        ids = subset.index.to_numpy()
        for start in range(0, len(ids), args.batch_events):
            batch_ids = ids[start:start + args.batch_events]
            batch_hash = hashlib.sha256('|'.join(batch_ids).encode()).hexdigest()[:20]
            file = sample_cache / f'{batch_hash}.pkl'
            if file.exists():
                feats = pd.read_pickle(file)
            else:
                if long_df is None:
                    print(f'[{sample}] 波形読み込み: {long_path}', flush=True)
                    long_df = read_table(long_path)
                    long_df['id'] = sample + '__' + long_df.id.astype(str)
                    long_df = long_df.loc[long_df.id.isin(ids)]
                part = long_df.loc[long_df.id.isin(batch_ids)]
                if set(part.id) != set(batch_ids):
                    raise ValueError(f'{sample}: metaに対応する波形が不足しています')
                feats = extract_features(part, column_id='id', column_sort='time',
                                         column_value='value', default_fc_parameters=MODES[args.fc_mode](),
                                         n_jobs=args.jobs, chunksize=50, disable_progressbar=True,
                                         impute_function=None)
                feats = feats.join(waveform_features(part, subset))
                # 統計的補完をここで行わない。クラス別・バッチ別の補完を避ける。
                feats = feats.replace([np.inf, -np.inf], np.nan)
                temp = file.with_suffix('.tmp')
                feats.to_pickle(temp)
                temp.replace(file)
            batches.append(feats.reindex(batch_ids))
            print(f'[{sample}] 特徴抽出 {min(start + args.batch_events, len(ids))}/{len(ids)}', flush=True)
        all_frames.append(pd.concat(batches))
    extra = pd.concat(all_frames).reindex(meta.index)
    return meta[META].join(extra).astype(np.float32)


def prepare(train, valid, y, selection, feature_set, absolute):
    cols = list(train.columns)
    ts_cols = [c for c in cols if c.startswith('value__')]
    if feature_set == 'minimal':
        minimal = set(MinimalFCParameters())
        cols = [c for c in cols if c in META or (c in ts_cols and c.split('__')[1] in minimal)]
    elif feature_set == 'tsfresh':
        cols = [c for c in cols if c in META or c in ts_cols]
    elif feature_set == 'meta':
        cols = list(META)
    if not absolute:
        cols = [c for c in cols if c != 'absolute_signal']
    train = train[cols].replace([np.inf, -np.inf], np.nan)
    valid = valid[cols].replace([np.inf, -np.inf], np.nan)
    # 学習foldだけで列の除去・中央値補完・特徴選択を決定。
    cols = list(train.columns[train.nunique(dropna=True) > 1])
    medians = train[cols].median().fillna(0)
    train = train[cols].fillna(medians)
    valid = valid[cols].fillna(medians)
    if selection:
        selectable = [c for c in cols if c in ts_cols]
        if selectable:
            selected = select_features(train[selectable], pd.Series(y, index=train.index),
                                       multiclass=True, n_significant=1, n_jobs=0)
            cols = [c for c in cols if c not in selectable or c in selected.columns]
    if not cols:
        raise ValueError('有効な特徴量がありません')
    return train[cols], valid[cols], {'columns': cols, 'medians': medians[cols]}


def training_data(frame, y, balance, seed):
    rng = np.random.default_rng(seed)
    counts = np.bincount(y)
    if balance == 'under':
        idx = np.concatenate([rng.choice(np.flatnonzero(y == c), counts.min(), replace=False)
                              for c in range(len(counts))])
        return xgb.DMatrix(frame.iloc[idx], label=y[idx])
    alpha = float(balance)
    weights = (len(y) / (len(counts) * counts[y])) ** alpha
    return xgb.DMatrix(frame, label=y, weight=weights / weights.mean())


def candidates(args):
    base = {'max_depth': 6, 'eta': .1, 'min_child_weight': 1,
            'subsample': 1., 'colsample_bytree': 1., 'lambda': 1., 'alpha': 0., 'gamma': 0.}
    configs = []
    for features, balance in [('minimal', 'under'), ('minimal', '0'), ('minimal', '0.5'),
                              ('tsfresh', '0'), ('tsfresh', '0.5'), ('all', '0.5')]:
        configs.append(dict(params=base.copy(), features=features, balance=balance,
                            selection=False, absolute=True))
    rng = np.random.default_rng(args.seed)
    for _ in range(args.trials):
        params = {'max_depth': int(rng.integers(3, 11)), 'eta': float(np.exp(rng.uniform(np.log(.01), np.log(.15)))),
                  'min_child_weight': float(np.exp(rng.uniform(0, np.log(100)))),
                  'subsample': float(rng.uniform(.6, 1)), 'colsample_bytree': float(rng.uniform(.5, 1)),
                  'lambda': float(np.exp(rng.uniform(np.log(.1), np.log(100)))),
                  'alpha': float(rng.choice([0., .0001, .01, .1, 1., 10.])),
                  'gamma': float(rng.choice([0., .1, .5, 1., 5.]))}
        configs.append(dict(params=params, features=str(rng.choice(['meta', 'minimal', 'tsfresh', 'all'])),
                            balance=str(rng.choice(['0', '0.5', '1'])),
                            selection=bool(rng.integers(2)), absolute=bool(rng.integers(2))))
    return configs


def run(args):
    meta, sources = load_metadata(args)
    encoder = LabelEncoder().fit(meta['sample'])
    y = encoder.transform(meta['sample'])
    groups = meta.group.to_numpy()
    dev_idx, test_idx = group_splits(y, groups, 5, args.seed)[0]
    folds = group_splits(y[dev_idx], groups[dev_idx], args.folds, args.seed + 1)
    summary = meta.groupby('sample').agg(events=('sample', 'size'), groups=('group', 'nunique'))
    print(summary.to_string(), flush=True)
    print(f'開発={len(dev_idx)}, 最終テスト={len(test_idx)}, CV={len(folds)} folds', flush=True)
    if args.check_only:
        return
    out = ROOT / 'results' / 'rmc' / 'xgboost_optimized' / time.strftime('%Y%m%d_%H%M%S')
    out.mkdir(parents=True, exist_ok=False)
    print(f'保存先: {out}', flush=True)
    write_json(out / 'config.json', {**vars(args), 'versions': {'xgboost': xgb.__version__,
                'tsfresh': tsfresh.__version__, 'sklearn': sklearn.__version__},
                'sources': {s: [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files]
                            for s, files in sources.items()}})
    split = meta[['sample', 'group']].copy()
    split['partition'] = 'development'
    split.loc[meta.index[test_idx], 'partition'] = 'test'
    split['cv_fold'] = -1
    for i, (_, va) in enumerate(folds):
        split.loc[meta.index[dev_idx[va]], 'cv_fold'] = i
    split.to_csv(out / 'splits.csv')
    X = build_features(meta, sources, args)
    dev, target = X.iloc[dev_idx], y[dev_idx]
    configs = candidates(args)
    write_json(out / 'candidates.json', configs)
    scores, best = [], []
    for number, config in enumerate(configs):
        oof = np.zeros((len(dev), len(encoder.classes_)), dtype=np.float32)
        rounds, fold_scores = [], []
        for fold, (tr, va) in enumerate(folds):
            a, b, _ = prepare(dev.iloc[tr], dev.iloc[va], target[tr], config['selection'],
                              config['features'], config['absolute'])
            params = {**config['params'], 'objective': 'multi:softprob', 'num_class': len(encoder.classes_),
                      'eval_metric': 'mlogloss', 'tree_method': 'hist', 'nthread': args.jobs,
                      'seed': args.seed + fold}
            dtrain = training_data(a, target[tr], config['balance'], args.seed + fold)
            dvalid = xgb.DMatrix(b, label=target[va])
            model = xgb.train(params, dtrain, num_boost_round=args.rounds, evals=[(dvalid, 'val')],
                              early_stopping_rounds=args.patience, verbose_eval=False)
            oof[va] = model.predict(dvalid, iteration_range=(0, model.best_iteration + 1))
            rounds.append(model.best_iteration + 1)
            score = f1_score(target[va], oof[va].argmax(1), average='macro')
            fold_scores.append(score)
            print(f'候補 {number + 1}/{len(configs)}, fold {fold + 1}: F1={score:.5f}, rounds={rounds[-1]}', flush=True)
        score = f1_score(target, oof.argmax(1), average='macro')
        record = {'candidate': number, 'oof_macro_f1': score, 'fold_std': float(np.std(fold_scores)),
                  'fold_scores': fold_scores, 'rounds': rounds, **config}
        scores.append(record)
        write_json(out / 'trials.json', scores)
        pd.DataFrame([{k: v for k, v in row.items() if k not in ('params', 'fold_scores', 'rounds')}
                      for row in scores]).to_csv(out / 'trials.csv', index=False)
        best.append((score, number, oof, rounds, config))
        best.sort(key=lambda row: row[0], reverse=True)
        best = best[:3]
    # 開発OOFのみで単体 / 上位2 / 上位3の等重み平均を選択。
    blend_scores = [f1_score(target, np.mean([b[2] for b in best[:k]], axis=0).argmax(1), average='macro')
                    for k in range(1, len(best) + 1)]
    chosen = best[:int(np.argmax(blend_scores)) + 1]
    write_json(out / 'selection.json', {'blend_oof_macro_f1': blend_scores,
               'selected_candidates': [b[1] for b in chosen],
               'note': 'OOF scores are for model selection; final test is the independent estimate.'})
    probabilities = []
    artifacts = []
    for _, number, _, rounds, config in chosen:
        a, b, prep = prepare(dev, X.iloc[test_idx], target, config['selection'], config['features'], config['absolute'])
        params = {**config['params'], 'objective': 'multi:softprob', 'num_class': len(encoder.classes_),
                  'eval_metric': 'mlogloss', 'tree_method': 'hist', 'nthread': args.jobs, 'seed': args.seed}
        n_rounds = max(1, int(round(np.median(rounds))))
        model = xgb.train(params, training_data(a, target, config['balance'], args.seed), num_boost_round=n_rounds)
        filename = f'model_{number}.json'
        model.save_model(out / filename)
        probabilities.append(model.predict(xgb.DMatrix(b)))
        artifacts.append({'model': filename, 'preprocessing': prep, 'rounds': n_rounds})
    with (out / 'pipeline.pkl').open('wb') as file:
        pickle.dump({'label_encoder': encoder, 'models': artifacts, 'fc_mode': args.fc_mode,
                     'cache_version': CACHE_VERSION, 'aggregation': 'mean_probability'}, file)
    proba = np.mean(probabilities, axis=0)
    pred = proba.argmax(1)
    truth = y[test_idx]
    metrics = {'macro_f1': f1_score(truth, pred, average='macro'),
               'accuracy': float(np.mean(truth == pred)), 'log_loss': log_loss(truth, proba),
               'classification_report': classification_report(truth, pred, target_names=encoder.classes_,
                                                               output_dict=True, zero_division=0)}
    write_json(out / 'test_metrics.json', metrics)
    pd.DataFrame(confusion_matrix(truth, pred), index=encoder.classes_, columns=encoder.classes_).to_csv(out / 'confusion_matrix.csv')
    predictions = meta.iloc[test_idx][['sample', 'group']].copy()
    predictions['prediction'] = encoder.inverse_transform(pred)
    for i, name in enumerate(encoder.classes_):
        predictions[f'probability_{name}'] = proba[:, i]
    predictions.to_csv(out / 'test_predictions.csv')
    print(f'完了: test Macro F1={metrics["macro_f1"]:.5f}; {out}', flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', nargs='+', default=SAMPLES)
    parser.add_argument('--data-root', type=Path, default=ROOT / 'data' / 'features' / 'rmc')
    parser.add_argument('--distance', type=float, default=.58)
    parser.add_argument('--group-column', choices=['sample_name', 'ex_id'], default='sample_name',
                        help='sample_name: 測定ID＋ファイル / ex_id: 測定全体')
    parser.add_argument('--fc-mode', choices=MODES, default='efficient')
    parser.add_argument('--folds', type=int, default=5)
    parser.add_argument('--trials', type=int, default=100, help='固定6候補に追加するランダム探索数')
    parser.add_argument('--rounds', type=int, default=3000)
    parser.add_argument('--patience', type=int, default=150)
    parser.add_argument('--batch-events', type=int, default=1000)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    if min(args.rounds, args.patience, args.batch_events, args.jobs) < 1 or args.folds < 2 or args.trials < 0:
        parser.error('回数・並列数は正、foldsは2以上、trialsは0以上にしてください')
    return args


if __name__ == '__main__':
    run(parse_args())
