# -*- coding: utf-8 -*-
"""
Created on Mon Jul 13 15:46:17 2026
Extended on Tue Jul 14 2026

@author: ohshi

【このファイルについて】
ml_tsfresh_lgbm_full_analysis.py の baseline除外バージョン。
既存メタ特徴量から 'baseline' を除いた状態（signal, duration,
wave_0..wave_11 の計14特徴量）でモデル比較・分析を行う。
※ 'baseline' は引き続きイベントの品質フィルタ（BASELINE_LIMIT）には
  使用されるが、モデルの入力特徴量からは除外される。

【元にしたファイル】
ml_tsfresh_lgbm_full_analysis.py のベースとなる LightGBM 化スクリプト
（ml_tsfresh_fast_memfix.py → LightGBM版 からの追加拡張）

【今回の追加内容（Step1〜Step5）】

Step1: モデル性能比較
    ・既存メタ特徴量（baseline除く: signal, duration, wave_0..wave_11）
    ・tsfresh選択特徴量
    ・既存メタ＋tsfresh
  の3パターンでモデルを学習し、Macro F1・各クラスF1・混同行列を比較する。

Step2: Permutation Importance
    独立したテストデータ（学習に一切使っていないホールドアウト）で
    permutation importance を計算し、「本当にモデル性能を支えている特徴量」
    をランキングする（学習時の gain importance は過大評価しやすいため）。

Step3: SHAP
    上位特徴量について
    ・値が高いほどどの分子方向へ寄与するか（クラスごとの summary plot）
    ・分子ごとに重要特徴量が異なるか（クラス間の上位特徴量比較）
    ・誤分類時に何が起きているか（正分類 vs 誤分類でのSHAP値比較）
  を確認する。

Step4: 物理量への再分類
    tsfresh特徴量名（value__xxx__...）や既存特徴量名を、
    電流強度 / イベント時間 / 波形非対称性 / 周波数成分 / ピーク数 /
    立ち上がり・立ち下がり / 局所変動 / 自己相関 のいずれかにマッピングし、
    重要度をカテゴリ単位で集計する。

Step5: UMAPとの統合
    UMAP埋め込み上で
    ・正分類 / 誤分類イベント
    ・真のクラス
    ・SHAP値（上位特徴量の絶対値合計）が高いイベント
    ・特定の物理特徴量が高いイベント
  を色分けして可視化し、「どの領域のイベントが、どの波形特徴によって
  分子識別されているか」を示す。

依存パッケージ（追加分）:
    pip install shap umap-learn
"""

import re
import gc
import time
import json
import math
import hashlib
import pickle
import warnings
from pathlib import Path
from datetime import datetime
from collections import OrderedDict

import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, f1_score, classification_report
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.inspection import permutation_importance

from imblearn.under_sampling import RandomUnderSampler

import lightgbm as lgb

from tsfresh import extract_features, select_features
from tsfresh.feature_extraction import MinimalFCParameters
from tsfresh.utilities.dataframe_functions import impute

import common.paths as paths

try:
    import shap
    _HAS_SHAP = True
except ImportError:
    _HAS_SHAP = False
    warnings.warn("shap がインストールされていません。'pip install shap' を実行してください。"
                   " Step3 (SHAP分析) はスキップされます。")

try:
    import umap
    _HAS_UMAP = True
except ImportError:
    _HAS_UMAP = False
    warnings.warn("umap-learn がインストールされていません。'pip install umap-learn' を実行してください。"
                   " Step5 (UMAP統合) はスキップされます。")


# ============================================================
# 表示まわりの設定
# ============================================================

# --- 日本語グラフ対応 ---
# 環境に入っている日本語対応フォントを優先順位順に探し、最初に見つかったものを使う。
# （Windows: Yu Gothic / Meiryo / MS Gothic、Mac: Hiragino、Linux: Noto Sans CJK JP など）
_JP_FONT_CANDIDATES = [
    'Yu Gothic', 'Meiryo', 'MS Gothic', 'MS PGothic',
    'Hiragino Sans', 'Hiragino Kaku Gothic Pro',
    'Noto Sans CJK JP', 'Noto Sans JP', 'IPAexGothic', 'IPAGothic',
]
try:
    import matplotlib.font_manager as fm
    _available_fonts = {f.name for f in fm.fontManager.ttflist}
    _jp_font = next((f for f in _JP_FONT_CANDIDATES if f in _available_fonts), None)
    if _jp_font is not None:
        plt.rcParams['font.family'] = _jp_font
    else:
        warnings.warn(
            "日本語対応フォントが見つかりませんでした。グラフ中の日本語が"
            "文字化け（tofu）する可能性があります。Windowsなら『Meiryo』や"
            "『Yu Gothic』、Linuxなら 'sudo apt install fonts-noto-cjk' 等で"
            "日本語フォントを追加してください。"
        )
    plt.rcParams['axes.unicode_minus'] = False  # 日本語フォント使用時のマイナス記号の文字化け対策
except Exception as _e:
    warnings.warn(f"日本語フォント設定に失敗しました: {_e}")

# --- LightGBM x sklearn の既知の無害な警告を抑制 ---
# 「X does not have valid feature names, but ... was fitted with feature names」は、
# numpy配列のみで学習・予測していてもLightGBMが内部的に自動生成した特徴量名
# （Column_0等）を持つために発生する既知の挙動で、結果には影響しない。
warnings.filterwarnings(
    'ignore',
    message='X does not have valid feature names',
    category=UserWarning,
)


# ============================================================
# 設定
# ============================================================

WAVE_COLUMNS = [f'wave_{i}' for i in range(12)]
# 'baseline' はモデルの特徴量から除外（品質フィルタ用途では引き続き使用。
#  下記の load_meta_data() 内 BASELINE_LIMIT によるフィルタリングは維持される）
META_FEATURE_COLUMNS = ['signal', 'duration'] + WAVE_COLUMNS

# メタ特徴量の物理カテゴリ対応表（Step4: build_physical_category_table()の
# extra_category_mapとして使う。tsfresh特徴量側はcommon/ml_analysis.pyの
# 既定パターン(DEFAULT_PHYSICAL_CATEGORY_PATTERNS)で自動分類される）
META_PHYSICAL_CATEGORY = {
    'signal': '電流強度',
    'duration': 'イベント時間',
    # 'baseline' はこのバージョンでは特徴量から除外しているため、ここには含めない
}
for _i in range(12):
    META_PHYSICAL_CATEGORY[f'wave_{_i}'] = '局所変動'  # 要ドメイン確認

DURATION_LIMIT = (5, 1000)
BASELINE_LIMIT = (-300, 1000)
SIGNAL_LIMIT = (0, 1000)

# --- tsfresh特徴量セットの選択 ---
# 'minimal'  : 現状（MinimalFCParameters, 列あたり約10特徴量）。最速・最軽量。
# 'curated'  : Step4の物理カテゴリ（電流強度/イベント時間/波形非対称性/周波数成分/
#              ピーク数/立ち上がり立ち下がり/局所変動/自己相関）を横断的にカバーする
#              よう手動で選んだ中間セット（列あたり約60〜70特徴量）。推奨。
# 'efficient': tsfresh標準のEfficientFCParameters（列あたり数百特徴量）。
#              最も網羅的だが、抽出時間・メモリともに大幅増加するため、
#              事前に estimate_fc_parameters_cost() で時間見積もりを取ってから
#              使うことを強く推奨。
FC_PARAMETERS_MODE = 'curated'

CURATED_FC_PARAMETERS = {
    # --- 電流強度 ---
    'abs_energy': None,
    'root_mean_square': None,
    'mean': None,
    'median': None,
    'maximum': None,
    'minimum': None,
    'sum_values': None,
    'quantile': [{'q': 0.1}, {'q': 0.25}, {'q': 0.75}, {'q': 0.9}],
    # --- 波形非対称性 ---
    'skewness': None,
    'kurtosis': None,
    # --- 周波数成分 ---
    'fft_coefficient': [{'coeff': k, 'attr': a} for k in range(5) for a in ['real', 'imag', 'abs']],
    'fft_aggregated': [{'aggtype': a} for a in ['centroid', 'variance', 'skew', 'kurtosis']],
    # --- ピーク数 ---
    'number_peaks': [{'n': n} for n in [1, 3, 5]],
    'number_cwt_peaks': [{'n': n} for n in [1, 5]],
    # --- 立ち上がり・立ち下がり ---
    'linear_trend': [{'attr': a} for a in ['slope', 'intercept', 'rvalue']],
    'agg_linear_trend': [{'attr': 'slope', 'chunk_len': cl, 'f_agg': 'mean'} for cl in [5, 10]],
    'first_location_of_maximum': None,
    'last_location_of_maximum': None,
    'first_location_of_minimum': None,
    'last_location_of_minimum': None,
    'longest_strike_above_mean': None,
    'longest_strike_below_mean': None,
    # --- 局所変動 ---
    'variance': None,
    'standard_deviation': None,
    'mean_abs_change': None,
    'absolute_sum_of_changes': None,
    'variance_larger_than_standard_deviation': None,
    # --- 自己相関 ---
    'autocorrelation': [{'lag': l} for l in [1, 2, 3, 5]],
    'c3': [{'lag': l} for l in [1, 2, 3]],
    'time_reversal_asymmetry_statistic': [{'lag': l} for l in [1, 2, 3]],
}

if FC_PARAMETERS_MODE == 'minimal':
    FC_PARAMETERS = MinimalFCParameters()
elif FC_PARAMETERS_MODE == 'curated':
    FC_PARAMETERS = CURATED_FC_PARAMETERS
elif FC_PARAMETERS_MODE == 'efficient':
    from tsfresh.feature_extraction import EfficientFCParameters
    FC_PARAMETERS = EfficientFCParameters()
else:
    raise ValueError(f"未知の FC_PARAMETERS_MODE: {FC_PARAMETERS_MODE}")

USE_TSFRESH_FEATURE_SELECTION = True

# 特徴量セット名・アルゴリズム名（common/paths.py のフォルダ命名規則と揃える）
FEATURE_SET = 'rmc'
ALGORITHM = 'lightgbm'

# 特徴量データ・tsfreshキャッシュ・結果はすべて common/paths.py 経由で
# 決まった場所に保存する（clips_rmc/analysis_outputs のような個別指定はしない）。
CACHE_DIR = paths.cache_dir(FEATURE_SET)
# 実行ごとの結果フォルダのベース（実際の保存先は paths.new_run() が作る
# タイムスタンプ付きサブフォルダ。ここでは関数のデフォルト引数用に使う）。
OUTPUT_DIR = paths.algorithm_base_dir(FEATURE_SET, ALGORITHM)

N_JOBS = 4
CHUNKSIZE = 50

BATCH_SIZE_EVENTS = 20_000
BATCH_EVENT_THRESHOLD = 30_000

# --- LightGBM関連の設定 ---
USE_EARLY_STOPPING = True
VALID_SIZE = 0.15
EARLY_STOPPING_ROUNDS = 30

# --- Step2, Step3, Step5関連の設定 ---
PERMUTATION_N_REPEATS = 10
TOP_N_IMPORTANCE = 30
TOP_N_SHAP = 20
UMAP_N_NEIGHBORS = 15
UMAP_MIN_DIST = 0.1

# --- 実行時間対策：Step2/Step3は計算コストがテストデータ件数に比例して
#     増えるため、必要に応じてサブサンプリングする。
#     （モデル自体はテストデータ全件で学習・評価済みなので、重要度分析の
#       サンプルを間引いても「傾向」への影響は小さい。件数を増やすほど
#       安定するが時間もかかるので、まずは小さめの値で試すことを推奨）
PERM_IMPORTANCE_MAX_SAMPLES = 3000   # Noneにすると全件使用（遅くなる）
SHAP_MAX_SAMPLES = 2000              # Noneにすると全件使用（最も遅くなりやすい）


# ============================================================
# 実行ごとの出力フォルダ管理（上書き対策）
# ============================================================
# results/<feature_set>/<algorithm>/ 直下に実行のたびに固定名で保存すると、
# 再実行のたびに前回の結果が上書きされてしまう。これを防ぐため、実行開始
# 時刻を含むサブフォルダ（例: results/rmc/lightgbm/20260715_153000_.../）を
# common.paths.new_run() で作成し、そのStep1〜5の出力は全てそのサブフォルダ
# 以下に保存する。過去の実行結果はフォルダごとそのまま残るので、後から
# 見比べられる（フォルダ命名規則は common/paths.py に一元化されているため、
# ここには個別のロジックを持たない）。


# ============================================================
# データ読み込み・tsfresh特徴量抽出・評価（共通モジュールに統合）
# ============================================================
# load_meta_data / load_long_data / extract_features_for_sample /
# build_combined_dataset / apply_tsfresh_feature_selection / conmtx などは
# common/data_pipeline.py, common/eval_viz.py に統合済み。
# このスクリプト固有の設定値をモジュール変数として反映してから使用する。

import common.data_pipeline as dp
from common.data_pipeline import (
    load_meta_data, load_long_data, extract_features_for_sample,
    build_combined_dataset, apply_tsfresh_feature_selection, clear_cache,
    _run_extract_features,
)
from common.eval_viz import conmtx

# 以前ここに直接定義していた plot_feature_importance / compare_feature_sets /
# run_permutation_importance / run_shap_analysis / categorize_feature_name /
# build_physical_category_table / run_umap_integration / write_run_manifest /
# estimate_fc_parameters_cost は、train_xgboost_tsfresh.py からも同じ関数を使える
# よう common/ml_analysis.py, common/paths.py, common/data_pipeline.py に
# 切り出した。処理内容は変更していない（関数の置き場所が変わっただけ）。
# ※ run_shap_analysis は共通化にあたって run_shap_class_comparison に改名
#   （common/ml_analysis.py には元々 別目的のrun_shap_analysis(waterfall/
#    dependence plot中心)が存在していたため、名前の衝突を避けた）。
from common.ml_analysis import (
    plot_feature_importance,
    compare_feature_sets,
    run_permutation_importance,
    run_shap_class_comparison,
    build_physical_category_table,
    run_umap_integration,
)
from common.data_pipeline import estimate_fc_parameters_cost

dp.DATA_ROOT = paths.feature_dir(FEATURE_SET)
dp.CACHE_DIR = CACHE_DIR
dp.DURATION_LIMIT = DURATION_LIMIT
dp.BASELINE_LIMIT = BASELINE_LIMIT
dp.SIGNAL_LIMIT = SIGNAL_LIMIT
dp.N_JOBS = N_JOBS
dp.CHUNKSIZE = CHUNKSIZE
dp.BATCH_SIZE_EVENTS = BATCH_SIZE_EVENTS
dp.BATCH_EVENT_THRESHOLD = BATCH_EVENT_THRESHOLD
dp.FC_PARAMETERS = FC_PARAMETERS





# ============================================================
# 学習・評価（LightGBM）
# 元の learn_dataset を、Step1〜5で再利用しやすいように
# train_lgbm_classifier として拡張。learn_dataset は後方互換用ラッパー。
# ============================================================

def train_lgbm_classifier(dnf, feature_columns, feature_set_name="model",
                           n_jobs=N_JOBS,
                           use_early_stopping=USE_EARLY_STOPPING,
                           valid_size=VALID_SIZE,
                           early_stopping_rounds=EARLY_STOPPING_ROUNDS,
                           test_size=0.2, random_state=0):
    """
    LightGBM (LGBMClassifier) による学習。Step1〜5で使い回せるよう、
    学習済みモデルに加えて、スケーラー・テストデータ（生値／スケール後）・
    テストデータのインデックス（global_id）などをまとめて返す。
    """
    y_labels = [_.split('_')[0] for _ in dnf['sample']]

    x = dnf[feature_columns]

    le = LabelEncoder()
    le = le.fit(y_labels)
    y = le.transform(y_labels)
    num_class = max(y) + 1

    idx_train, idx_test = train_test_split(
        dnf.index, test_size=test_size, random_state=random_state,
        stratify=y
    )

    x_train_raw = x.loc[idx_train]
    x_test_raw = x.loc[idx_test]
    y_train = y[dnf.index.get_indexer(idx_train)]
    y_test = y[dnf.index.get_indexer(idx_test)]

    scaler = StandardScaler()
    X_train = scaler.fit_transform(x_train_raw)
    X_test = scaler.transform(x_test_raw)

    # --- アンダーサンプリング（元コードと同様、クラス不均衡対策） ---
    cn = [len(y_train[y_train == i]) for i in range(num_class)]
    counts = [min(cn) for _ in range(len(cn))]
    keys = list(range(len(cn)))
    strategy = {key: count for key, count in zip(keys, counts)}

    rus = RandomUnderSampler(random_state=random_state, sampling_strategy=strategy)
    X_train_res, y_train_res = rus.fit_resample(X_train, y_train)

    # --- 早期打ち切り用の検証データ ---
    fit_kwargs = {}
    if use_early_stopping:
        X_fit, X_valid, y_fit, y_valid = train_test_split(
            X_train_res, y_train_res, test_size=valid_size, random_state=random_state,
            stratify=y_train_res
        )
        fit_kwargs['eval_set'] = [(X_valid, y_valid)]
        fit_kwargs['callbacks'] = [
            lgb.early_stopping(stopping_rounds=early_stopping_rounds, verbose=False),
            lgb.log_evaluation(period=0),
        ]
    else:
        X_fit, y_fit = X_train_res, y_train_res

    clf = lgb.LGBMClassifier(
        objective='multiclass',
        num_class=num_class,
        n_estimators=500,
        num_leaves=63,
        max_depth=-1,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=random_state,
        n_jobs=n_jobs,
    )

    t0 = time.time()
    clf.fit(X_fit, y_fit, **fit_kwargs)
    print(f"[{feature_set_name}] LightGBM学習 完了 ({time.time() - t0:.1f}秒, "
          f"best_iteration={getattr(clf, 'best_iteration_', clf.n_estimators)})")

    y_pred = clf.predict(X_test)

    class_names = list(le.classes_)
    macro_f1 = f1_score(y_test, y_pred, average='macro')
    per_class_f1 = f1_score(y_test, y_pred, average=None, labels=range(num_class))
    per_class_f1 = {cls: score for cls, score in zip(class_names, per_class_f1)}

    cm = confusion_matrix(y_test, y_pred, labels=range(num_class))
    cm_df = pd.DataFrame(
        cm,
        index=[f'Actual_{c}' for c in class_names],
        columns=[f'Pred_{c}' for c in class_names],
    )

    print(f"[{feature_set_name}] Macro F1: {macro_f1:.4f}")

    result = {
        'name': feature_set_name,
        'clf': clf,
        'le': le,
        'scaler': scaler,
        'feature_columns': list(feature_columns),
        'class_names': class_names,
        'num_class': num_class,
        'X_train': X_train_res,
        'y_train': y_train_res,
        'X_test': X_test,
        'X_test_raw': x_test_raw,
        'y_test': y_test,
        'y_pred': y_pred,
        'test_global_ids': list(idx_test),
        'macro_f1': macro_f1,
        'per_class_f1': per_class_f1,
        'confusion_matrix': cm_df,
    }
    return result


def learn_dataset(dnf, feature_columns, n_jobs=N_JOBS,
                   use_early_stopping=USE_EARLY_STOPPING,
                   valid_size=VALID_SIZE,
                   early_stopping_rounds=EARLY_STOPPING_ROUNDS):
    """後方互換用ラッパー。元のシグネチャ・戻り値(y_test, y_pred, clf, le)を維持する。"""
    result = train_lgbm_classifier(
        dnf, feature_columns, feature_set_name="model", n_jobs=n_jobs,
        use_early_stopping=use_early_stopping, valid_size=valid_size,
        early_stopping_rounds=early_stopping_rounds,
    )
    return result['y_test'], result['y_pred'], result['clf'], result['le']


# ============================================================
# メイン処理
# ============================================================

if __name__ == '__main__':
    #smns = ['Lys','M1Lys','M2Lys','M3Lys']
    #smns = ['T2', 'T3','T4']
    smns = ['glucose','DGl', 'GDL','GlcA']
    data_root = paths.feature_dir(FEATURE_SET)

    # --- 実行ごとにタイムスタンプ付きフォルダを作成（上書き防止） ---
    # 例: results/rmc/lightgbm/20260715_153000_.../ 以下にこの回のStep1〜5
    # 出力を全て保存する。tsfresh特徴量抽出のキャッシュ
    # （data/features/rmc/_cache/）は内容ハッシュで管理されているため対象外
    # （従来通りCACHE_DIRを共有・再利用する）。
    RUN_OUTPUT_DIR, RUN_TIMESTAMP = paths.new_run(FEATURE_SET, ALGORITHM, smns=smns, run_type='train')
    print(f"今回の出力先: {RUN_OUTPUT_DIR.resolve()}")

    RUN_DRY_RUN_ESTIMATE = False
    if RUN_DRY_RUN_ESTIMATE:
        estimate_fc_parameters_cost(smns, data_root=data_root, fc_parameters=FC_PARAMETERS,
                                     n_event_sample=2000, n_jobs=N_JOBS, chunksize=CHUNKSIZE)

    start_time = time.time()

    dnf, tsfresh_feature_cols_all = build_combined_dataset(
        smns, data_root=data_root, use_cache=True,
        n_jobs=N_JOBS, chunksize=CHUNKSIZE
    )

    if USE_TSFRESH_FEATURE_SELECTION:
        tsfresh_feature_cols = apply_tsfresh_feature_selection(
            dnf, tsfresh_feature_cols_all, n_jobs=N_JOBS, chunksize=CHUNKSIZE
        )
    else:
        tsfresh_feature_cols = tsfresh_feature_cols_all

    # ---------------------------------------------------------
    # Step1: 既存15特徴量 / tsfresh選択特徴量 / 既存15+tsfresh の性能比較
    # ---------------------------------------------------------
    step1_results, step1_comparison_df, step1_feature_set_names = compare_feature_sets(
        dnf, META_FEATURE_COLUMNS, tsfresh_feature_cols, train_fn=train_lgbm_classifier,
        n_jobs=N_JOBS, output_dir=RUN_OUTPUT_DIR,
    )

    # '既存14+tsfresh' のような、実際に使われた列数から生成された名前を使う。
    main_result = step1_results[step1_feature_set_names['combined']]
    plot_feature_importance(main_result['clf'], main_result['feature_columns'], top_n=30,
                             save_dir=RUN_OUTPUT_DIR)

    # ---------------------------------------------------------
    # Step2: Permutation Importance（独立テストデータ）
    # ---------------------------------------------------------
    perm_importance_df = run_permutation_importance(
        main_result, n_repeats=PERMUTATION_N_REPEATS, n_jobs=N_JOBS,
        top_n=TOP_N_IMPORTANCE, output_dir=RUN_OUTPUT_DIR,
        max_samples=PERM_IMPORTANCE_MAX_SAMPLES,
    )

    # ---------------------------------------------------------
    # Step3: SHAP分析
    # ---------------------------------------------------------
    shap_result = run_shap_class_comparison(main_result, top_n=TOP_N_SHAP, output_dir=RUN_OUTPUT_DIR,
                                             max_samples=SHAP_MAX_SAMPLES)

    # ---------------------------------------------------------
    # Step4: 物理量への再分類（Permutation Importance / SHAP の両方に適用）
    # ---------------------------------------------------------
    perm_with_category, perm_category_summary = build_physical_category_table(
        perm_importance_df, feature_col='feature', importance_col='importance_mean',
        extra_category_map=META_PHYSICAL_CATEGORY,
        title='Permutation Importance の物理カテゴリ別集計',
        output_path=RUN_OUTPUT_DIR / 'step4_permutation_importance_by_category.png',
    )

    if shap_result is not None:
        shap_with_category, shap_category_summary = build_physical_category_table(
            shap_result['mean_abs_shap_overall'], feature_col='feature',
            importance_col='mean_abs_shap',
            extra_category_map=META_PHYSICAL_CATEGORY,
            title='SHAP重要度（mean|SHAP|）の物理カテゴリ別集計',
            output_path=RUN_OUTPUT_DIR / 'step4_shap_importance_by_category.png',
        )
    else:
        shap_with_category, shap_category_summary = None, None

    # ---------------------------------------------------------
    # Step5: UMAPとの統合可視化
    # ---------------------------------------------------------
    top_feature_for_color = perm_importance_df.iloc[0]['feature'] \
        if len(perm_importance_df) else None

    umap_result = run_umap_integration(
        main_result, shap_result=shap_result, top_feature=top_feature_for_color,
        top_n_shap_for_color=TOP_N_SHAP, n_neighbors=UMAP_N_NEIGHBORS, min_dist=UMAP_MIN_DIST,
        output_dir=RUN_OUTPUT_DIR,
    )

    execution_time = time.time() - start_time
    print(f"\n実行時間: {execution_time:.2f}秒")
    print(f"各種出力（CSV/PNG）は '{RUN_OUTPUT_DIR.resolve()}' に保存されています。")

    # --- 実行条件をmanifestとして保存（後から「どの設定の結果か」を追跡するため） ---
    paths.write_run_manifest(
        RUN_OUTPUT_DIR, RUN_TIMESTAMP, smns,
        config={
            'fc_parameters_mode': FC_PARAMETERS_MODE,
            'use_tsfresh_feature_selection': USE_TSFRESH_FEATURE_SELECTION,
            'n_meta_features': len(META_FEATURE_COLUMNS),
        },
        execution_time=execution_time,
        comparison_df=step1_comparison_df,
        extra_info={'feature_set_names': step1_feature_set_names},
    )