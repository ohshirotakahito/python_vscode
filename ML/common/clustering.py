"""Label-free event clustering and reproducible inspection artifacts."""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans, AgglomerativeClustering, DBSCAN
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler, RobustScaler
from sklearn.metrics import silhouette_score
from sklearn.decomposition import PCA

ALGORITHMS = ['K-means', '階層法 (Ward)', 'Gaussian Mixture', 'DBSCAN']
EXCLUDED = {'event_id', 'peak_pos', 'start', 'end', 'sample', 'sample_name',
            'ex_id', 'run_id', 'experiment', 'condition_id', 'extracted_at'}


def feature_columns(events):
    return [c for c in events.select_dtypes(include='number') if c not in EXCLUDED]


def analyze(events, features, algorithm='K-means', k=None, max_k=10,
            scaling='standard', missing='median', eps=.5, min_samples=5,
            max_events=10000, seed=42, progress=lambda *_: None):
    if algorithm not in ALGORITHMS or not features or set(features) - set(feature_columns(events)):
        raise ValueError('使用する数値特徴量と手法を確認してください。')
    if max_events < 3 or max_k < 2 or (k is not None and k < 2):
        raise ValueError('最大イベント数は3以上、クラスタ数は2以上にしてください。')
    if scaling not in ('standard', 'robust', 'none') or missing not in ('median', 'drop'):
        raise ValueError('前処理の指定が不正です。')
    if not np.isfinite(eps) or eps <= 0 or min_samples < 1:
        raise ValueError('epsは正の有限値、min_samplesは1以上にしてください。')
    data = events.copy()
    data.insert(0, 'source_row', np.arange(len(data)))
    raw = data[features].astype(float).replace([np.inf, -np.inf], np.nan)
    usable = [c for c in features if raw[c].notna().any() and raw[c].nunique() > 1]
    if not usable:
        raise ValueError('変動のある有効な特徴量がありません。')
    raw = raw[usable]
    valid = raw.notna().any(axis=1) if missing == 'median' else raw.notna().all(axis=1)
    data, raw = data.loc[valid].copy(), raw.loc[valid].copy()
    eligible = len(data)
    if eligible > max_events:
        positions = np.sort(np.random.default_rng(seed).choice(eligible, max_events, replace=False))
        data, raw = data.iloc[positions].copy(), raw.iloc[positions].copy()
    usable = [c for c in usable if raw[c].notna().any() and raw[c].nunique() > 1]
    if not usable:
        raise ValueError('抽出後に有効な特徴量がありません。イベント上限を増やしてください。')
    raw = raw[usable]
    medians = raw.median()
    raw = raw.fillna(medians)
    if len(raw) < 3:
        raise ValueError('前処理後に3イベント以上が必要です。')
    x = raw.to_numpy()
    scaler = StandardScaler() if scaling == 'standard' else RobustScaler() if scaling == 'robust' else None
    if scaler is not None:
        x = scaler.fit_transform(x)

    def fit(count):
        if algorithm == 'K-means':
            model = KMeans(n_clusters=count, random_state=seed, n_init=10)
        elif algorithm == '階層法 (Ward)':
            model = AgglomerativeClustering(n_clusters=count)
        elif algorithm == 'Gaussian Mixture':
            model = GaussianMixture(n_components=count, random_state=seed, n_init=3)
        else:
            model = DBSCAN(eps=eps, min_samples=min_samples)
        labels = model.fit_predict(x)
        keep = labels != -1
        score = np.nan
        if 1 < len(set(labels[keep])) < keep.sum():
            try:
                score = silhouette_score(x[keep], labels[keep], sample_size=min(2000, keep.sum()), random_state=seed)
            except ValueError:
                pass
        return labels, {'k': count, 'clusters': len(set(labels) - {-1}),
                        'silhouette': score, 'inertia': getattr(model, 'inertia_', np.nan),
                        'bic': model.bic(x) if algorithm == 'Gaussian Mixture' else np.nan,
                        'noise': int((~keep).sum())}

    start_k = 1 if algorithm == 'Gaussian Mixture' else 2
    candidates = [None] if algorithm == 'DBSCAN' else list(range(start_k, min(max_k, len(x)-1, len(np.unique(x, axis=0)))+1))
    if not candidates:
        raise ValueError('クラスタ分割に十分な異なるイベントがありません。')
    rows = []
    for i, count in enumerate(candidates):
        progress(i, len(candidates)+1, f'{algorithm}: 候補数 {count}')
        _, row = fit(count)
        rows.append(row)
    scores = pd.DataFrame(rows)
    criterion = 'bic' if algorithm == 'Gaussian Mixture' else 'silhouette'
    valid_scores = scores.dropna(subset=[criterion])
    recommended = None
    if algorithm != 'DBSCAN' and not valid_scores.empty:
        best = valid_scores[criterion].idxmin() if criterion == 'bic' else valid_scores[criterion].idxmax()
        recommended = int(scores.loc[best, 'k'])
    chosen = None if algorithm == 'DBSCAN' else k if k is not None else recommended
    if algorithm != 'DBSCAN' and (chosen is None or chosen >= len(x) or chosen > len(np.unique(x, axis=0))):
        raise ValueError('自動推定できないか、指定数がデータ数を超えています。候補範囲・手動数を見直してください。')
    labels, final = fit(chosen)
    embedding = PCA(n_components=min(2, x.shape[1]), random_state=seed).fit_transform(x)
    data['cluster'] = labels
    data['PC1'] = embedding[:, 0]
    data['PC2'] = embedding[:, 1] if embedding.shape[1] > 1 else 0.
    note = ('密度条件から決定。-1 はノイズ。クラスタ数の直接指定は使用しません。' if algorithm == 'DBSCAN'
            else f'推奨数: {recommended} / 採用数: {chosen}。' + ('BIC最小（小さいほど良い）。' if criterion == 'bic' else 'シルエット係数最大（大きいほど良い）。'))
    manifest = dict(algorithm=algorithm, features=usable, excluded_features=list(set(features)-set(usable)),
                    recommended_k=recommended, chosen_k=chosen, criterion=criterion, note=note,
                    scaling=scaling, missing=missing, medians=medians.to_dict(), seed=seed,
                    eps=eps, min_samples=min_samples, max_k=max_k, max_events=max_events,
                    input_events=len(events), eligible_events=eligible, analyzed_events=len(data),
                    silhouette_sample_limit=2000, final=final)
    progress(1, 1, '可視化を作成中')
    return data, scores, manifest


def save_result(result, root):
    from matplotlib.figure import Figure
    data, scores, manifest = result
    directory = Path(root) / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True, exist_ok=False)
    data.to_csv(directory / 'events.csv', index=False, encoding='utf-8-sig')
    scores.to_csv(directory / 'candidate_scores.csv', index=False)
    pd.crosstab(data['sample'], data['cluster']).to_csv(directory / 'sample_clusters.csv', encoding='utf-8-sig')
    data.groupby('cluster')[manifest['features']].agg(['count', 'mean', 'std', 'median']).to_csv(directory / 'feature_summary.csv')
    (directory / 'conditions.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    fig = Figure(figsize=(10, 5))
    ax = fig.add_subplot(121)
    for cluster, group in data.groupby('cluster'):
        ax.scatter(group.PC1, group.PC2, s=5, alpha=.5, label=str(cluster))
    ax.set(xlabel='PC1', ylabel='PC2', title='Clusters (PCA; -1 = noise)')
    ax.legend()
    ax = fig.add_subplot(122)
    if manifest['algorithm'] != 'DBSCAN':
        metric = manifest['criterion']
        ax.plot(scores.k, scores[metric], 'o-')
        ax.set(xlabel='Candidate k', ylabel=metric)
        if manifest['recommended_k'] is not None:
            ax.axvline(manifest['recommended_k'], linestyle='--')
    else:
        ax.bar(['Clustered', 'Noise'], [(data.cluster != -1).sum(), (data.cluster == -1).sum()])
    fig.tight_layout()
    fig.savefig(directory / 'clustering.png', dpi=140)
    fig = Figure(figsize=(10, 7))
    columns = manifest['features'][:6]
    for i, feature in enumerate(columns):
        ax = fig.add_subplot(3, 2, i+1)
        values = pd.to_numeric(data[feature], errors='coerce').replace([np.inf, -np.inf], np.nan)
        edges = np.histogram_bin_edges(values.dropna(), bins=30)
        for cluster, group in data.groupby('cluster'):
            ax.hist(pd.to_numeric(group[feature], errors='coerce').replace([np.inf, -np.inf], np.nan).dropna(),
                    bins=edges, histtype='step', label=str(cluster))
        ax.set_title(feature)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(directory / 'feature_distributions.png', dpi=120)
    return directory
