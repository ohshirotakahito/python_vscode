# -*- coding: utf-8 -*-
"""
common/training_core.py

【このモジュールについて】
3つの学習スクリプト（train_xgboost_tsfresh / train_lightgbm_tsfresh /
train_xgboost_traditional）で共通の「分割 → 交差検証 → （任意）ハイパーパラメータ
探索 → 最終学習 → held-out test評価」を1か所にまとめたもの。
アルゴリズム以外の条件（分割方法・クラスの偏りへの対処・探索の有無）を揃え、
パイプライン同士を公平に比較できるようにする。

【評価の考え方】
  1) イベント（シグナル）単位で、クラス比率を保って 学習用 / テスト用 に分ける。
     ファイルは同じ条件での連続測定なので、ファイル単位で分ける必要はない。
  2) 学習用の中で、イベント単位の層化交差検証（StratifiedKFold）を行い、
     木の本数（early stopping）と、探索する場合はハイパーパラメータを決める。
  3) 学習用すべてで最終モデルを作り、一度も使っていないテスト用で評価する。
  tsfresh特徴量選択は、呼び出し側で学習用の行だけを使って行うこと（テスト用の情報を混ぜない）。

【設定】
画面（app_tk/tab_train.py）から configure() で上書きする。スクリプト単体で
実行した場合は DEFAULT_SETTINGS が使われる。
"""

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler

BALANCE_METHODS = {
    'under': 'アンダーサンプリング（少ないクラスの件数まで間引く）',
    'over': 'オーバーサンプリング（多いクラスの件数まで複製）',
    'weight': 'クラス重み（件数の逆数で重み付け）',
    'sqrt_weight': '弱いクラス重み（件数の逆数の平方根）',
    'none': '対処しない（そのまま学習）',
}

DEFAULT_SETTINGS = {
    'balance': 'under',          # BALANCE_METHODS のキー
    'tune': False,               # ハイパーパラメータをランダム探索するか
    'n_trials': 20,              # 探索する候補数（既定値の1候補に追加）
    'test_size': 0.2,            # テスト用に分けるイベントの割合
    'n_cv_folds': 10,            # 学習用内の交差検証の分割数（従来と同じ10）
    'random_state': 0,
    'max_rounds': 500,           # 木の本数の上限（early stoppingで決める）
    'early_stopping_rounds': 20,
}

SETTINGS = dict(DEFAULT_SETTINGS)


def configure(**changes):
    """学習設定を上書きする（不正な値は ValueError）。"""
    unknown = set(changes) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f'不明な学習設定です: {sorted(unknown)}')
    merged = {**SETTINGS, **changes}
    if merged['balance'] not in BALANCE_METHODS:
        raise ValueError(f"不明なクラスの偏りへの対処です: {merged['balance']}")
    if int(merged['n_trials']) < 1:
        raise ValueError('探索する候補数は1以上にしてください。')
    if not 0 < float(merged['test_size']) < 1:
        raise ValueError('テスト用の割合は0より大きく1未満にしてください。')
    if int(merged['n_cv_folds']) < 2:
        raise ValueError('交差検証の分割数は2以上にしてください。')
    SETTINGS.update(merged)


def current_settings():
    return dict(SETTINGS)


def reset():
    SETTINGS.clear()
    SETTINGS.update(DEFAULT_SETTINGS)


# ============================================================
# イベント単位の分割
# ============================================================
# ファイルは同じ条件での連続測定なので、ファイルごとの違いは実験条件の違いではない。
# そのため、イベント（シグナル）単位で学習用/テスト用に分ければ十分とする。

def make_split(dnf, settings=None):
    """学習用/テスト用（クラス比率を保ったイベント単位）と、学習用内のCV分割を1回だけ決める。

    全特徴量セットで同じ分割を使い、tsfresh特徴量選択も学習用の行だけで行えるようにする。
    """
    cfg = settings or current_settings()
    encoder = LabelEncoder().fit(dnf['sample'].astype(str))
    y = encoder.transform(dnf['sample'].astype(str))
    class_names = list(encoder.classes_)
    counts = np.bincount(y)
    if len(counts) < 2:
        raise ValueError(f'学習には2クラス以上が必要です（読み込めたクラス: {class_names}）。')
    if counts.min() < 3:
        raise ValueError('各クラスに3イベント以上必要です（'
                         + ', '.join(f'{c}={n}' for c, n in zip(class_names, counts)) + '）。')
    seed = cfg['random_state']
    train, test = train_test_split(np.arange(len(y)), test_size=cfg['test_size'],
                                   random_state=seed, stratify=y)
    n_folds = max(2, min(int(cfg['n_cv_folds']), int(np.bincount(y[train]).min())))
    cv = list(StratifiedKFold(n_folds, shuffle=True, random_state=seed).split(np.zeros(len(train)), y[train]))
    info = {
        'split_unit': 'イベント（クラス比率を保って無作為）',
        'train_events': int(len(train)), 'test_events': int(len(test)),
        'test_event_fraction': round(len(test) / len(y), 4),
        'events_per_class': {c: int(n) for c, n in zip(class_names, counts)},
        'n_cv_folds': len(cv),
    }
    print(f"イベント単位の分割: 学習用 {info['train_events']:,} / テスト用 {info['test_events']:,}イベント / "
          f"CV {len(cv)}分割")
    return {'train': train, 'test': test, 'cv': cv, 'info': info}


# ============================================================
# クラスの偏りへの対処
# ============================================================

def balance_training(X, y, method, seed):
    """(X, y, sample_weight) を返す。重み付けでは件数を変えない。"""
    counts = np.bincount(y)
    rng = np.random.default_rng(seed)
    if method == 'under':
        index = np.concatenate([rng.choice(np.flatnonzero(y == c), counts.min(), replace=False)
                                for c in range(len(counts))])
    elif method == 'over':
        index = np.concatenate([np.concatenate([np.flatnonzero(y == c),
                                                rng.choice(np.flatnonzero(y == c), counts.max() - counts[c])])
                                for c in range(len(counts))])
    elif method in ('weight', 'sqrt_weight'):
        weights = len(y) / (len(counts) * counts[y])
        if method == 'sqrt_weight':
            weights = np.sqrt(weights)
        return X, y, weights / weights.mean()
    elif method == 'none':
        return X, y, None
    else:
        raise ValueError(f'不明なクラスの偏りへの対処です: {method}')
    index = np.sort(index)
    return X[index], y[index], None


# ============================================================
# モデル（XGBoost / LightGBM）
# ============================================================

ALGORITHMS = ('xgboost', 'lightgbm')


def base_params(algorithm, num_class, n_jobs, seed):
    """探索しない場合の既定値（従来の各スクリプトの値）。"""
    if algorithm == 'xgboost':
        return {'max_depth': 6, 'eta': 0.1, 'eval_metric': 'mlogloss',
                'num_class': num_class, 'objective': 'multi:softprob'}
    if algorithm == 'lightgbm':
        return {'objective': 'multiclass', 'num_class': num_class, 'num_leaves': 63,
                'max_depth': -1, 'learning_rate': 0.05, 'subsample': 0.8,
                'colsample_bytree': 0.8, 'random_state': seed, 'n_jobs': n_jobs, 'verbosity': -1}
    raise ValueError(f'不明なアルゴリズムです: {algorithm}')


def _log_uniform(rng, low, high):
    return float(np.exp(rng.uniform(np.log(low), np.log(high))))


def sample_params(algorithm, rng):
    """ランダム探索の候補を1つ作る（過学習を抑える方向の正則化も含める）。"""
    if algorithm == 'xgboost':
        return {'max_depth': int(rng.integers(3, 11)), 'eta': _log_uniform(rng, 0.01, 0.3),
                'min_child_weight': _log_uniform(rng, 1, 100),
                'subsample': float(rng.uniform(0.6, 1)), 'colsample_bytree': float(rng.uniform(0.5, 1)),
                'lambda': _log_uniform(rng, 0.1, 100),
                'alpha': float(rng.choice([0., 0.001, 0.01, 0.1, 1., 10.])),
                'gamma': float(rng.choice([0., 0.1, 0.5, 1., 5.]))}
    return {'num_leaves': int(round(_log_uniform(rng, 15, 255))),
            'learning_rate': _log_uniform(rng, 0.01, 0.2),
            'min_child_samples': int(round(_log_uniform(rng, 5, 200))),
            'subsample': float(rng.uniform(0.6, 1)), 'subsample_freq': 1,
            'colsample_bytree': float(rng.uniform(0.5, 1)),
            'reg_lambda': _log_uniform(rng, 0.001, 100),
            'reg_alpha': float(rng.choice([0., 0.001, 0.01, 0.1, 1., 10.]))}


def _fit(algorithm, params, X, y, weight, feature_names, rounds,
         X_valid=None, y_valid=None, early_stopping_rounds=None):
    """(model, 使う木の本数, evals_result) を返す。検証データがあれば early stopping する。"""
    if algorithm == 'xgboost':
        import xgboost as xgb
        dtrain = xgb.DMatrix(X, label=y, weight=weight, feature_names=feature_names)
        evals = [(dtrain, 'train')]
        if X_valid is not None:
            evals.append((xgb.DMatrix(X_valid, label=y_valid, feature_names=feature_names), 'val'))
        evals_result = {}
        booster = xgb.train(params, dtrain, num_boost_round=rounds, evals=evals,
                            early_stopping_rounds=early_stopping_rounds if X_valid is not None else None,
                            evals_result=evals_result, verbose_eval=False)
        best = booster.best_iteration + 1 if X_valid is not None else rounds
        return booster, best, evals_result
    import lightgbm as lgb
    model = lgb.LGBMClassifier(**params, n_estimators=rounds)
    kwargs = {}
    if X_valid is not None:
        kwargs = {'eval_set': [(X_valid, y_valid)],
                  'callbacks': [lgb.early_stopping(early_stopping_rounds, verbose=False),
                                lgb.log_evaluation(period=0)]}
    model.fit(X, y, sample_weight=weight, **kwargs)
    best = (model.best_iteration_ or rounds) if X_valid is not None else rounds
    return model, int(best), None


def predict_proba(algorithm, model, X, feature_names, rounds):
    if algorithm == 'xgboost':
        import xgboost as xgb
        return model.predict(xgb.DMatrix(X, feature_names=feature_names), iteration_range=(0, rounds))
    return model.predict_proba(X, num_iteration=rounds)


def cross_validate(algorithm, params, x_train_raw, y_train, cv_splits, feature_names, cfg, num_class):
    """学習用内の層化CV（イベント単位）。(平均Macro F1, 各foldのF1, 各foldの最良本数)。"""
    scores, rounds = [], []
    for fold_train, fold_valid in cv_splits:
        scaler = StandardScaler().fit(x_train_raw.iloc[fold_train])
        X_fold = scaler.transform(x_train_raw.iloc[fold_train])
        X_valid = scaler.transform(x_train_raw.iloc[fold_valid])
        X_bal, y_bal, weight = balance_training(X_fold, y_train[fold_train], cfg['balance'], cfg['random_state'])
        model, best, _ = _fit(algorithm, params, X_bal, y_bal, weight, feature_names, cfg['max_rounds'],
                              X_valid, y_train[fold_valid], cfg['early_stopping_rounds'])
        pred = predict_proba(algorithm, model, X_valid, feature_names, best).argmax(axis=1)
        scores.append(f1_score(y_train[fold_valid], pred, average='macro', labels=range(num_class)))
        rounds.append(best)
    return float(np.mean(scores)), scores, rounds


def train_classifier(dnf, feature_columns, algorithm, split, feature_set_name='model',
                     n_jobs=4, settings=None):
    """分割済みのデータで学習・評価する。戻り値の辞書は従来の各スクリプトと同じキーを持つ。"""
    cfg = settings or current_settings()
    feature_columns = list(feature_columns)
    labels = dnf['sample'].astype(str)
    le = LabelEncoder().fit(labels)
    y = le.transform(labels)
    num_class = len(le.classes_)
    class_names = list(le.classes_)
    x = dnf[feature_columns].astype(float)
    idx_train, idx_test = split['train'], split['test']
    x_train_raw, x_test_raw = x.iloc[idx_train], x.iloc[idx_test]
    y_train, y_test = y[idx_train], y[idx_test]

    seed = cfg['random_state']
    base = base_params(algorithm, num_class, n_jobs, seed)
    candidates = [('既定値', base)]
    if cfg['tune']:
        rng = np.random.default_rng(seed)
        candidates += [(f'探索{i}', {**base, **sample_params(algorithm, rng)})
                       for i in range(1, int(cfg['n_trials']) + 1)]

    rows, best = [], None
    for number, (name, params) in enumerate(candidates, start=1):
        mean_f1, scores, rounds = cross_validate(algorithm, params, x_train_raw, y_train, split['cv'],
                                                 feature_columns, cfg, num_class)
        rows.append({'candidate': name, 'cv_macro_f1': mean_f1, 'cv_macro_f1_std': float(np.std(scores)),
                     'median_rounds': int(round(np.median(rounds))),
                     **{k: v for k, v in params.items() if k not in ('num_class', 'objective', 'eval_metric',
                                                                      'n_jobs', 'verbosity', 'random_state')}})
        if best is None or mean_f1 > best[0]:
            best = (mean_f1, name, params, rounds)
        if len(candidates) > 1:
            print(f'[{feature_set_name}] 探索 {number}/{len(candidates)} {name}: CV Macro F1={mean_f1:.4f}')
    cv_macro_f1, best_name, params, fold_best_rounds = best
    selected_rounds = max(1, int(round(np.median(fold_best_rounds))))
    print(f"[{feature_set_name}] 学習用内 {len(split['cv'])}分割CV: "
          f"Macro F1={cv_macro_f1:.4f}, 採用={best_name}, 木の本数={selected_rounds}")

    scaler = StandardScaler().fit(x_train_raw)
    X_train = scaler.transform(x_train_raw)
    X_test = scaler.transform(x_test_raw)
    X_bal, y_bal, weight = balance_training(X_train, y_train, cfg['balance'], seed)
    model, _, evals_result = _fit(algorithm, params, X_bal, y_bal, weight, feature_columns, selected_rounds)
    y_pred = predict_proba(algorithm, model, X_test, feature_columns, selected_rounds).argmax(axis=1)

    macro_f1 = f1_score(y_test, y_pred, average='macro', labels=range(num_class))
    per_class = f1_score(y_test, y_pred, average=None, labels=range(num_class))
    cm = confusion_matrix(y_test, y_pred, labels=range(num_class))
    print(f'[{feature_set_name}] テスト用 Macro F1: {macro_f1:.4f}')

    result = {
        'name': feature_set_name,
        'le': le,
        'scaler': scaler,
        'feature_columns': feature_columns,
        'class_names': class_names,
        'num_class': num_class,
        'X_train': X_bal,
        'y_train': y_bal,
        'X_test': X_test,
        'X_test_raw': x_test_raw,
        'y_test': y_test,
        'y_pred': y_pred,
        'test_global_ids': list(dnf.index[idx_test]),
        'macro_f1': macro_f1,
        'per_class_f1': dict(zip(class_names, per_class)),
        'confusion_matrix': pd.DataFrame(cm, index=[f'Actual_{c}' for c in class_names],
                                         columns=[f'Pred_{c}' for c in class_names]),
        'evals_result': evals_result,
        'cv_macro_f1': cv_macro_f1,
        'n_cv_folds': len(split['cv']),
        'cv_best_rounds': fold_best_rounds,
        'selected_rounds': selected_rounds,
        'params': params,
        'selected_candidate': best_name,
        'tuning': pd.DataFrame(rows),
        'balance': cfg['balance'],
    }
    if algorithm == 'xgboost':
        from common.ml_analysis import BoosterClassifierWrapper
        result['booster'] = model
        result['clf'] = BoosterClassifierWrapper(model, num_class=num_class,
                                                 feature_names=feature_columns).fit(X_bal, y_bal)
    else:
        result['clf'] = model
    return result


def save_training_details(results, run_dir):
    """特徴量セットごとのハイパーパラメータ候補とCVスコアをCSVに保存する。"""
    import re
    for name, result in results.items():
        table = result.get('tuning')
        if table is not None:
            safe = re.sub(r'[\/:*?"<>|\s+]+', '_', name).strip('_') or 'model'
            table.sort_values('cv_macro_f1', ascending=False).to_csv(
                run_dir / f'hyperparameter_cv_{safe}.csv', index=False, encoding='utf-8-sig')


def manifest_config(split, main_result, settings=None):
    """manifestに残す学習条件（分割・偏りへの対処・探索・採用パラメータ）。"""
    cfg = settings or current_settings()
    return {
        'split': split['info'],
        'balance': cfg['balance'],
        'balance_label': BALANCE_METHODS[cfg['balance']],
        'hyperparameter_tuning': bool(cfg['tune']),
        'n_trials': int(cfg['n_trials']) if cfg['tune'] else 0,
        'selected_candidate': main_result.get('selected_candidate'),
        'params': {k: v for k, v in main_result.get('params', {}).items()
                   if isinstance(v, (int, float, str, bool))},
        'selected_rounds': main_result.get('selected_rounds'),
        'cv_macro_f1': main_result.get('cv_macro_f1'),
        'held_out_test_macro_f1': main_result.get('macro_f1'),
    }
