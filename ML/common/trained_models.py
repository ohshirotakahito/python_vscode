# -*- coding: utf-8 -*-
"""
common/trained_models.py

【このモジュールについて】
③ 学習・評価タブで作った学習器（純粋分子の組み合わせごとのモデル）を保存し、
⑤ 混合予測タブで一覧から選んで使えるようにする。

  学習: train_*.py の run_analysis() が save_model() で
        results/<feature_set>/<algorithm>/<timestamp>_train_<smns>/ に
        model.json(XGBoost) / model.txt(LightGBM)・scaler.pkl・label_encoder.pkl・
        feature_columns.txt・model_info.json を保存する。
  予測: list_models() で一覧を作り、predict_mix() で選んだ学習器を使って予測する。
        混合予測では学習しない（学習器が無ければ ③ 学習・評価タブで作る）。
"""

import importlib
import json
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np

import common.paths as paths

MODEL_INFO = 'model_info.json'
MODEL_FILES = {'xgboost': 'model.json', 'lightgbm': 'model.txt'}
PREDICT_MODULES = {'rmc': 'predict_mix_xgboost_tsfresh', 'rmb': 'predict_mix_xgboost_traditional'}


def save_model(run_dir, result, algorithm, feature_set, smns, fc_parameters_mode=None, details=None):
    """学習結果（training_core.train_classifier() の戻り値）を予測に使える形で保存する。

    details: 学習器どうしを比較するための学習条件（特徴量選択・イベント範囲・分割・パラメータなど）。
             解析が途中で止まっても比較できるよう、run_manifest.json とは別にここへ残す。
    """
    run_dir = Path(run_dir)
    if algorithm == 'xgboost':
        result['booster'].save_model(str(run_dir / MODEL_FILES[algorithm]))
    elif algorithm == 'lightgbm':
        result['clf'].booster_.save_model(str(run_dir / MODEL_FILES[algorithm]))
    else:
        raise ValueError(f'不明なアルゴリズムです: {algorithm}')
    with open(run_dir / 'label_encoder.pkl', 'wb') as f:
        pickle.dump(result['le'], f)
    with open(run_dir / 'scaler.pkl', 'wb') as f:
        pickle.dump(result['scaler'], f)
    (run_dir / 'feature_columns.txt').write_text('\n'.join(result['feature_columns']), encoding='utf-8')
    info = {
        'format': 1,
        'algorithm': algorithm,
        'feature_set': feature_set,
        'smns': list(smns),
        'class_names': list(result['class_names']),
        'classification_mode': result.get('classification_mode', 'sample'),
        'sample_groups': result.get('sample_groups', {}),
        'feature_set_name': result.get('name'),
        'n_features': len(result['feature_columns']),
        'fc_parameters_mode': fc_parameters_mode,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'test_macro_f1': float(result['macro_f1']),
        'cv_macro_f1': float(result['cv_macro_f1']),
        'balance': result.get('balance'),
        'selected_candidate': result.get('selected_candidate'),
        'per_class_f1': {k: float(v) for k, v in result.get('per_class_f1', {}).items()},
        'details': {**(details or {}), **_input_summary()},
    }
    (run_dir / MODEL_INFO).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    return info


def _input_summary():
    """学習に使った抽出結果の版とファイル絞り込み（学習中のコンテキストから取得）。"""
    from common.feature_store import training_inputs
    from common.training_file_filter import selection_manifest
    inputs = [{'sample': v.get('sample'), 'run_id': v.get('run_id'), 'ex': v.get('ex'),
               'condition_id': v.get('condition_id'), 'extracted_at': v.get('extracted_at'),
               'n_input_files': len(v.get('inputs') or [])} for v in training_inputs()]
    selection = selection_manifest()
    return {'feature_inputs': inputs,
            'file_filter_rules': [list(rule) for rule in selection.get('rules', [])],
            'file_filter_counts': selection.get('counts', {})}


def list_models(feature_set=None):
    """保存済みの学習器を新しい順に返す。各要素は model_info.json の内容 + 'run_dir'。"""
    models = []
    for info_path in paths.RESULTS_ROOT.glob(f"{feature_set or '*'}/*/*/{MODEL_INFO}"):
        try:
            info = json.loads(info_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        run_dir = info_path.parent
        if not (run_dir / MODEL_FILES.get(info.get('algorithm'), '')).is_file():
            continue
        models.append({**info, 'run_dir': run_dir})
    models.sort(key=lambda m: m['run_dir'].name, reverse=True)
    return models


class LoadedModel:
    """保存した学習器。predict_proba() は生の特徴量（標準化前）を受け取る。"""

    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        info_path = self.run_dir / MODEL_INFO
        if not info_path.is_file():
            raise FileNotFoundError(f'学習器の情報がありません（③ 学習・評価タブで作り直してください）: {info_path}')
        self.info = json.loads(info_path.read_text(encoding='utf-8'))
        self.algorithm = self.info['algorithm']
        self.feature_set = self.info['feature_set']
        self.class_names = list(self.info['class_names'])
        self.feature_columns = (self.run_dir / 'feature_columns.txt').read_text(encoding='utf-8').splitlines()
        with open(self.run_dir / 'scaler.pkl', 'rb') as f:
            self.scaler = pickle.load(f)
        model_path = self.run_dir / MODEL_FILES[self.algorithm]
        if self.algorithm == 'xgboost':
            import xgboost as xgb
            self.model = xgb.Booster()
            self.model.load_model(str(model_path))
        else:
            import lightgbm as lgb
            self.model = lgb.Booster(model_file=str(model_path))

    @property
    def fc_parameters(self):
        """混合サンプルのtsfresh特徴量を学習時と同じ設定で作るための fc_parameters。"""
        mode = self.info.get('fc_parameters_mode')
        if not mode:
            return None
        from common.fc_parameters import fc_parameters_for
        return fc_parameters_for(mode)

    def predict_proba(self, frame):
        missing = [c for c in self.feature_columns if c not in frame.columns]
        if missing:
            raise RuntimeError(f'学習時に使った特徴量が見つかりません（{len(missing)}列）: {missing[:10]}')
        X = self.scaler.transform(frame[self.feature_columns].astype(float))
        if self.algorithm == 'xgboost':
            import xgboost as xgb
            probs = self.model.predict(xgb.DMatrix(X, feature_names=self.feature_columns))
        else:
            probs = self.model.predict(X)
        return np.asarray(probs).reshape(len(X), len(self.class_names))


def load_model(run_dir):
    return LoadedModel(run_dir)


def predict_mix(model_dir, test_smns, threshold=0.8, mix_filters=None,
                progress_callback=None, mix_file_rules=None):
    """選んだ学習器で混合サンプルを予測し、mix_results/ に保存して run_dir を返す（学習はしない）。"""
    model = load_model(model_dir)
    module = importlib.import_module(PREDICT_MODULES[model.feature_set])
    total = 1 + len(test_smns)

    def notify(i, message):
        if progress_callback is not None:
            progress_callback(i, total, message)

    notify(0, f"学習器を読み込みました: {'-'.join(model.class_names)}")
    run_dir, run_ts = paths.new_run(model.feature_set, model.algorithm,
                                  smns=model.info.get('smns') or model.class_names, run_type='mix')
    (run_dir / 'used_model_version.txt').write_text(str(model.run_dir), encoding='utf-8')
    from common.training_file_filter import prediction_file_selection
    with prediction_file_selection(model.feature_set, test_smns, mix_file_rules) as file_selection:
        module.save_group_predictions(test_smns, model, model.class_names, run_dir, threshold=threshold,
                                      filters=mix_filters,
                                      progress_callback=lambda i, _n, msg: notify(1 + i, msg))
    paths.write_run_manifest(run_dir, run_ts, model.class_names, config={
        'run_type': 'mix',
        'test_smns': list(test_smns),
        'mix_file_selection': file_selection,
        'class_names': model.class_names,
        'pmax_threshold': threshold,
        'model_status': 'selected',
        'model_version_dir': str(model.run_dir),
        'model_info': model.info,
    })
    notify(total, '完了')
    return run_dir


# ============================================================
# 学習器の比較（混合予測タブの二段表示・比較画面用）
# ============================================================

def combination_key(model):
    """同じ組み合わせとみなすキー（特徴量の種類 + クラスの集合）。"""
    base = model['feature_set'], tuple(sorted(model['class_names']))
    if model.get('classification_mode') == 'group':
        return base + (tuple(sorted((k, tuple(sorted(v))) for k, v in model['sample_groups'].items())),)
    return base


def group_models(models):
    """{組み合わせキー: [学習器, ...]}（新しい順）。辞書の順は最新の学習器がある組み合わせから。"""
    groups = {}
    for model in models:
        groups.setdefault(combination_key(model), []).append(model)
    return groups


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _per_class_f1_from_confusion(run_dir):
    """古い学習器用: 保存済みの混同行列（件数）からクラス別F1を計算する。"""
    import pandas as pd
    path = Path(run_dir) / 'confusion_matrix.csv'
    if not path.is_file():
        return {}
    table = pd.read_csv(path, index_col=0)
    names = [c.removeprefix('Pred_') for c in table.columns]
    matrix = table.to_numpy(dtype=float)
    result = {}
    for i, name in enumerate(names):
        tp = matrix[i, i]
        precision = tp / matrix[:, i].sum() if matrix[:, i].sum() else 0.0
        recall = tp / matrix[i, :].sum() if matrix[i, :].sum() else 0.0
        result[name] = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return result


def _fmt(value):
    if value is None or value == '' or value == [] or value == {}:
        return '—'
    if isinstance(value, bool):
        return 'あり' if value else 'なし'
    if isinstance(value, float):
        return f'{value:.4g}'
    if isinstance(value, (list, tuple)):
        return ', '.join(_fmt(v) for v in value)
    return str(value)


def model_details(model):
    """比較画面の行: [(区分, 項目, 値の文字列), ...]。古い学習器は run_manifest.json 等で補う。"""
    from common.training_core import BALANCE_METHODS
    run_dir = Path(model['run_dir'])
    manifest = _read_json(run_dir / 'run_manifest.json')
    details = model.get('details') or {}
    training = details.get('training') or manifest.get('training') or {}
    split = training.get('split') or {}
    params = training.get('params') or {}
    rows = [
        ('基本', '分類単位', 'グループ別' if model.get('classification_mode') == 'group' else 'サンプル別'),
        ('基本', '作成日時', model.get('created_at')),
        ('基本', '学習フォルダ', run_dir.name),
        ('基本', 'アルゴリズム', model.get('algorithm')),
        ('基本', '解析の完了', '完了' if manifest else '途中で停止（学習器は保存済み）'),
    ]
    for name, members in model.get('sample_groups', {}).items():
        rows.append(('グループ', name, ', '.join(members)))
    inputs = details.get('feature_inputs') or manifest.get('feature_inputs') or []
    for item in sorted(inputs, key=lambda v: str(v.get('sample'))):
        extracted = str(item.get('extracted_at') or '')[:16].replace('T', ' ')
        files = item.get('n_input_files', len(item.get('inputs') or []))
        rows.append(('データ', f"抽出結果の版：{item.get('sample')}",
                     f"{item.get('ex') or '出どころ不明'} / {extracted or '日時不明'} / "
                     f"{files}ファイル / 条件 {item.get('condition_id') or '—'}"))
    rules = details.get('file_filter_rules')
    if rules is None:
        rules = (manifest.get('training_file_filter') or {}).get('rules')
    limits = details.get('event_limits') or manifest.get('event_limits')
    rows += [
        ('データ', 'ファイル絞り込み', ' AND '.join(' '.join(map(str, r)) for r in rules) if rules else 'なし'),
        ('データ', 'イベント範囲条件', ' / '.join(f'{k} {tuple(v)}' for k, v in limits.items())
         if isinstance(limits, dict) else None),
        ('データ', '分け方', split.get('split_unit')),
        ('データ', '学習用 / テスト用イベント',
         f"{split['train_events']:,} / {split['test_events']:,}" if 'train_events' in split else None),
    ]
    for name, count in (split.get('events_per_class') or {}).items():
        rows.append(('データ', f'イベント数：{name}', f'{count:,}'))
    use_selection = details.get('use_tsfresh_feature_selection', manifest.get('use_tsfresh_feature_selection'))
    rows += [
        ('特徴量', 'tsfresh設定', model.get('fc_parameters_mode')),
        ('特徴量', 'tsfresh特徴量選択', use_selection),
        ('特徴量', '使った特徴量セット', model.get('feature_set_name')),
        ('特徴量', '特徴量の数', model.get('n_features')),
        ('学習', 'クラスの偏りへの対処', BALANCE_METHODS.get(model.get('balance'), model.get('balance'))),
        ('学習', 'ハイパーパラメータ探索', f"あり（{training.get('n_trials')}候補）" if training.get('hyperparameter_tuning')
         else ('なし' if training else None)),
        ('学習', '採用した候補', model.get('selected_candidate')),
        ('学習', '木の本数', training.get('selected_rounds')),
        ('学習', '交差検証の分割数', split.get('n_cv_folds')),
    ]
    for key, value in params.items():
        if key not in ('num_class', 'objective', 'eval_metric', 'verbosity', 'n_jobs', 'random_state'):
            rows.append(('学習', f'パラメータ：{key}', value))
    rows += [
        ('評価', 'テスト Macro F1', model.get('test_macro_f1')),
        ('評価', '交差検証 Macro F1', model.get('cv_macro_f1')),
    ]
    per_class = model.get('per_class_f1') or _per_class_f1_from_confusion(run_dir)
    for name, value in per_class.items():
        rows.append(('評価', f'クラス別F1：{name}', value))
    for name, result in (manifest.get('feature_set_results') or {}).items():
        rows.append(('評価', f'Step1比較：{name}',
                     f"F1 {result.get('macro_f1', 0):.4g}（{result.get('n_features')}列）"))
    return [(group, label, _fmt(value)) for group, label, value in rows]


def comparison_table(models):
    """学習器を列にした比較表 [(区分, 項目, [値...], 違いがあるか)]（項目は全学習器の和集合）。"""
    per_model = [{(g, label): value for g, label, value in model_details(m)} for m in models]
    order = []
    for details in per_model:
        for key in details:
            if key not in order:
                order.append(key)
    table = []
    for group, label in order:
        values = [details.get((group, label), '—') for details in per_model]
        table.append((group, label, values, len(set(values)) > 1))
    return table
