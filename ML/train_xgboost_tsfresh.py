"""
TOP_XGboost_rmc.py

【今回の変更点】
以前ここに直接定義していた追加解析関数群（SHAP分析、PCA/UMAP次元削減、
特徴量ごとの統計検定、クラス間距離解析など）を common/ml_analysis.py に
切り出した。TOP_XGboost_rmb.py（波形12点特徴量版）からも同じ関数を使うため。
処理内容・呼び出し方は変更していない（関数の置き場所が変わっただけ）。
"""

import os
import gc
import time
import math
import hashlib
import pickle
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq  # parquetはこれで直接読む（pd.read_parquetの二重登録バグ回避のため）
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm


def _setup_japanese_font():
    """グラフ中の日本語タイトル・ラベルが文字化け（豆腐□）しないよう、
    システムにインストール済みの日本語フォントを探して matplotlib に設定する。
    見つからない場合は警告のみ表示し、インストールコマンドの例を案内する。"""
    candidates = [
        'IPAexGothic', 'IPAPGothic', 'IPAGothic',
        'Noto Sans CJK JP', 'Noto Sans JP', 'Noto Sans Mono CJK JP',
        'TakaoGothic', 'TakaoPGothic',
        'Yu Gothic', 'Meiryo', 'MS Gothic', 'Hiragino Sans', 'Hiragino Kaku Gothic Pro',
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams['font.family'] = name
            plt.rcParams['axes.unicode_minus'] = False
            print(f"✅ 日本語フォントを設定しました: {name}")
            return
    print("⚠ 日本語フォントが見つからないため、グラフ中の日本語が文字化けする可能性があります。")
    print("   例: `sudo apt-get install -y fonts-noto-cjk` または")
    print("       `pip install japanize-matplotlib --break-system-packages` を実行して"
          "再実行してください。")


_setup_japanese_font()

from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, f1_score, classification_report
from sklearn import preprocessing
from sklearn.preprocessing import LabelEncoder, StandardScaler

from imblearn.under_sampling import RandomUnderSampler

import xgboost as xgb

from tsfresh import extract_features, select_features
from tsfresh.feature_extraction import EfficientFCParameters, MinimalFCParameters
from tsfresh.utilities.dataframe_functions import impute

import common.paths as paths

# ============================================================
# 設定
# ============================================================

WAVE_COLUMNS = [f'wave_{i}' for i in range(12)]
# 'relative_signal' = 'signal'（ベースラインからの相対値）
# 'absolute_signal' = 'signal' + 'baseline'（ベースラインを足し戻した絶対値）
# 実際の計算はload_meta_data()内で行う
META_FEATURE_COLUMNS = ['absolute_signal', 'relative_signal', 'duration'] + WAVE_COLUMNS

DURATION_LIMIT = (5, 1000)
BASELINE_LIMIT = (-300, 1000)
SIGNAL_LIMIT = (0, 1000)

# 重要: 全サンプルで必ず同じfc_parametersを使うこと（結合時の列不一致でMemoryErrorになるため）。
# 今回のデータ規模（十万イベント/数百万〜数千万行）では EfficientFCParameters は重すぎるため
# まずは MinimalFCParameters で通すことを推奨。物足りなければ後で変更可。
FC_PARAMETERS = MinimalFCParameters()

USE_TSFRESH_FEATURE_SELECTION = True

# 全データではなく条件を指定してイベントを絞り込みたい場合はここに書く
# （何も絞り込まない場合は FILTERS = None のままでよい）。
# 詳細な条件の書き方は common/filters.py の docstring を参照。
#
# 例:
#   FILTERS = {
#       'file_number': {'min': 1, 'max': 5, 'exclude': [7]},  # 001〜005を使う、007は除外
#       'machine_no':  {'include': [3]},                        # AN#3のみ使う
#       'measured_at': {'min': '2026-07-01', 'max': '2026-07-31'},  # 計測日時の範囲
#   }
FILTERS = None

# 特徴量セット名・アルゴリズム名（common/paths.py のフォルダ命名規則と揃える）
FEATURE_SET = 'rmc'
ALGORITHM = 'xgboost'

# 特徴量データ・tsfreshキャッシュはすべて common/paths.py 経由で決まった
# 場所に保存する（'clips_rmc' のような個別指定はしない。以前ここが
# 'ax_data' とずれていたことがあったが、常にFEATURE_SETから導出するため
# 今後はズレが起きない）。
DATA_ROOT = paths.feature_dir(FEATURE_SET)
CACHE_DIR = paths.cache_dir(FEATURE_SET)

N_JOBS = 4
CHUNKSIZE = 50

# --- サンプル内バッチ分割設定 ---
# tsfreshはextract_featuresに渡された全データに対し、ワーカーへ分配する前に
# メインプロセス側でcolumn_idによるgroupby(内部的にはソート)を1回行う。
# これはn_jobs/chunksizeでは並列化されないため、1サンプルが数万イベント・
# 数百万行に達すると、この「最初のソート」だけでMemoryErrorや長時間停止の
# 原因になる。そのためイベント数がしきい値を超えるサンプルはバッチに分割する。
BATCH_SIZE_EVENTS = 20_000
BATCH_EVENT_THRESHOLD = 30_000

# ============================================================
# 解析結果の保存先フォルダ作成
# ============================================================
# 'clips_tsfresh/YYYYMMDD_HHMMSS_smn1-smn2-.../' のような個別フォルダ名は
# 廃止し、common.paths.new_run() で
# results/<feature_set>/<algorithm>/<timestamp>_<smn1-smn2-...>/ に統一する
# （フォルダ命名規則は common/paths.py に一元化されているため、
#   ここには個別のロジックを持たない）。

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
)
from common.eval_viz import conmtx

# 追加解析（SHAP・PCA/UMAP・統計検定・クラス間距離解析・特徴量セット比較・
# Permutation Importance・物理カテゴリ分類・UMAP統合可視化）は
# common/ml_analysis.py に切り出した（TOP_XGboost_rmb.py / TOP_LightGBM_rmc.py
# からも同じ関数を使うため）。
from common.ml_analysis import (
    plot_learning_curve,
    plot_feature_importance,
    run_shap_analysis,
    data_stat,
    plot_dim_reduction,
    feature_group_tests,
    compare_importance_and_stats,
    plot_shap_class_importance,
    compare_shap_and_stats,
    class_distance_heatmap,
    class_centroid_network,
    distance_confusion_correlation,
    BoosterClassifierWrapper,
    compare_feature_sets,
    run_permutation_importance,
    run_shap_class_comparison,
    build_physical_category_table,
    run_umap_integration,
)
from common.data_pipeline import estimate_fc_parameters_cost

dp.DATA_ROOT = DATA_ROOT
dp.CACHE_DIR = CACHE_DIR
dp.DURATION_LIMIT = DURATION_LIMIT
dp.BASELINE_LIMIT = BASELINE_LIMIT
dp.SIGNAL_LIMIT = SIGNAL_LIMIT
dp.N_JOBS = N_JOBS
dp.CHUNKSIZE = CHUNKSIZE
dp.BATCH_SIZE_EVENTS = BATCH_SIZE_EVENTS
dp.BATCH_EVENT_THRESHOLD = BATCH_EVENT_THRESHOLD
dp.FC_PARAMETERS = FC_PARAMETERS
dp.FILTERS = FILTERS











# ============================================================
# tsfresh特徴量抽出（サンプル単位キャッシュ + バッチ分割）
# ============================================================












# ============================================================
# 学習・評価
# ============================================================

def learn_dataset(dnf, feature_columns, max_depth=6, eta=0.1, num_round=500,
                   early_stopping_rounds=20, val_size=0.15, verbose_eval=False):
    """
    XGBoostによる学習（見直し版）。

    【変更点】
    - max_depth を 100 -> 6 に変更（100は木がほぼ無制限に伸びて過学習必至だったため）
    - eta を 1 -> 0.1 に変更（1は学習率として大きすぎ、過学習・不安定の原因になっていた）
    - eta を下げた分、num_round を 200 -> 500 に増やし、
      early_stopping_rounds で検証データのlossが改善しなくなったら自動的に打ち切るようにした
      （過学習を防ぎつつ、無駄に多いラウンド数を使い切らないようにする）
    - train_test_split を2段階にして、train内からさらにvalidationを切り出し、
      test setは最後の評価にのみ使う（early stoppingにtestを使うとリークになるため）
    """
    y_labels = [_.split('_')[0] for _ in dnf['sample']]

    x = dnf[feature_columns]
    X = preprocessing.scale(x)

    le = LabelEncoder()
    le = le.fit(y_labels)
    y = le.transform(y_labels)

    num_class = max(y) + 1

    test_size = 0.2
    X_train_full, X_test, y_train_full, y_test = train_test_split(
        X, y, test_size=test_size, random_state=0, stratify=y
    )

    # --- train内からさらにvalidationを切り出す（early stopping用） ---
    X_train, X_val, y_train, y_val = train_test_split(
        X_train_full, y_train_full, test_size=val_size, random_state=0,
        stratify=y_train_full
    )

    # --- アンダーサンプリングはtrainのみに適用（val/testはそのままの分布で評価） ---
    cn = [len(y_train[y_train == i]) for i in range(num_class)]
    counts = [min(cn) for _ in range(len(cn))]
    keys = list(range(len(cn)))
    strategy = {key: count for key, count in zip(keys, counts)}

    rus = RandomUnderSampler(random_state=0, sampling_strategy=strategy)
    X_train, y_train = rus.fit_resample(X_train, y_train)

    dtrain = xgb.DMatrix(X_train, label=y_train)
    dval = xgb.DMatrix(X_val, label=y_val)
    dtest = xgb.DMatrix(X_test)

    params = {
        'max_depth': max_depth,
        'eta': eta,
        'eval_metric': 'mlogloss',
        'num_class': num_class,
        'objective': 'multi:softprob',
    }

    evals_result = {}
    bst = xgb.train(
        params, dtrain, num_boost_round=num_round,
        evals=[(dtrain, 'train'), (dval, 'val')],
        early_stopping_rounds=early_stopping_rounds,
        evals_result=evals_result,
        verbose_eval=verbose_eval,
    )

    print(f"early stoppingで停止したラウンド: {bst.best_iteration} / {num_round}")

    y_pred_proba = bst.predict(dtest, iteration_range=(0, bst.best_iteration + 1))
    y_pred = y_pred_proba.argmax(axis=1)

    acc = f1_score(y_test, y_pred, average='micro')
    print('f-measure_value:', acc)

    return X_test, y_test, y_pred, bst, le, evals_result


def train_xgb_classifier(dnf, feature_columns, feature_set_name="model",
                          n_jobs=N_JOBS,
                          max_depth=6, eta=0.1, num_round=500,
                          early_stopping_rounds=20, val_size=0.15,
                          test_size=0.2, random_state=0, verbose_eval=False):
    """
    XGBoost (Booster, early stopping付き) による学習。

    TOP_LightGBM_rmc.py の train_lgbm_classifier() と同じ形式の結果辞書を返す
    ようにしたもの（Step1: compare_feature_sets / Step2: run_permutation_importance /
    Step5: run_umap_integration 等、common/ml_analysis.py の共通関数から
    そのまま使うため）。

    【learn_dataset()からの主な変更点】
    - スケーリングを preprocessing.scale(全データ) から、
      StandardScaler を train のみで fit する方式に変更した
      （全データでscaleすると test の情報が学習前処理に混ざるリークになるため。
       TOP_XGboost_mix_rmb.py/rmc.py と同じ方針に統一）。
    - 予測に使う生の Booster に加えて、sklearn互換の BoosterClassifierWrapper も
      result['clf'] として返す（Permutation Importance等で必要なため）。
      SHAP分析には result['booster']（生のBooster）を使うこと。
    """
    y_labels = [_.split('_')[0] for _ in dnf['sample']]
    x = dnf[feature_columns]

    le = LabelEncoder()
    le = le.fit(y_labels)
    y = le.transform(y_labels)
    num_class = max(y) + 1
    class_names = list(le.classes_)

    idx_train_full, idx_test = train_test_split(
        dnf.index, test_size=test_size, random_state=random_state, stratify=y
    )
    idxer = dnf.index.get_indexer

    x_train_full_raw = x.loc[idx_train_full]
    x_test_raw = x.loc[idx_test]
    y_train_full = y[idxer(idx_train_full)]
    y_test = y[idxer(idx_test)]

    # --- train内からさらにvalidationを切り出す（early stopping用） ---
    idx_train, idx_val = train_test_split(
        idx_train_full, test_size=val_size, random_state=random_state,
        stratify=y_train_full
    )
    x_train_raw = x.loc[idx_train]
    x_val_raw = x.loc[idx_val]
    y_train = y[idxer(idx_train)]
    y_val = y[idxer(idx_val)]

    # --- スケーラー：train のみで fit ---
    scaler = StandardScaler()
    X_train = scaler.fit_transform(x_train_raw)
    X_val = scaler.transform(x_val_raw)
    X_test = scaler.transform(x_test_raw)

    # --- アンダーサンプリングはtrainのみに適用 ---
    cn = [len(y_train[y_train == i]) for i in range(num_class)]
    counts = [min(cn) for _ in range(len(cn))]
    keys = list(range(len(cn)))
    strategy = {key: count for key, count in zip(keys, counts)}
    rus = RandomUnderSampler(random_state=random_state, sampling_strategy=strategy)
    X_train_res, y_train_res = rus.fit_resample(X_train, y_train)

    feature_columns = list(feature_columns)
    dtrain = xgb.DMatrix(X_train_res, label=y_train_res, feature_names=feature_columns)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=feature_columns)
    dtest = xgb.DMatrix(X_test, feature_names=feature_columns)

    params = {
        'max_depth': max_depth, 'eta': eta, 'eval_metric': 'mlogloss',
        'num_class': num_class, 'objective': 'multi:softprob',
    }

    evals_result = {}
    t0 = time.time()
    bst = xgb.train(
        params, dtrain, num_boost_round=num_round,
        evals=[(dtrain, 'train'), (dval, 'val')],
        early_stopping_rounds=early_stopping_rounds,
        evals_result=evals_result, verbose_eval=verbose_eval,
    )
    print(f"[{feature_set_name}] XGBoost学習 完了 ({time.time() - t0:.1f}秒, "
          f"best_iteration={bst.best_iteration}/{num_round})")

    y_pred_proba = bst.predict(dtest, iteration_range=(0, bst.best_iteration + 1))
    y_pred = y_pred_proba.argmax(axis=1)

    macro_f1 = f1_score(y_test, y_pred, average='macro')
    per_class_f1 = f1_score(y_test, y_pred, average=None, labels=range(num_class))
    per_class_f1 = {cls: score for cls, score in zip(class_names, per_class_f1)}

    cm = confusion_matrix(y_test, y_pred, labels=range(num_class))
    cm_df = pd.DataFrame(cm, index=[f'Actual_{c}' for c in class_names],
                          columns=[f'Pred_{c}' for c in class_names])

    print(f"[{feature_set_name}] Macro F1: {macro_f1:.4f}")

    # sklearn互換ラッパー（Permutation Importance等、numpy配列で.predict()を
    # 呼ぶsklearnツールから使うため。SHAP分析には使わず、result['booster']を使うこと）
    wrapper = BoosterClassifierWrapper(bst, num_class=num_class, feature_names=feature_columns)
    wrapper.fit(X_train_res, y_train_res)  # classes_を設定するだけ、実学習はしない

    result = {
        'name': feature_set_name,
        'clf': wrapper,
        'booster': bst,
        'le': le,
        'scaler': scaler,
        'feature_columns': feature_columns,
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
        'evals_result': evals_result,
    }
    return result


# ============================================================
# メイン処理
# ============================================================
# TOP_LightGBM_rmc.py と同じ Step1〜5構成にした:
#   Step1: 特徴量セット比較（メタ特徴量のみ／tsfresh選択特徴量のみ／両方）
#   Step2: Permutation Importance（独立テストデータ）
#   Step3: SHAPによるクラス別比較・誤分類分析
#   Step4: 物理カテゴリ別の重要度集計
#   Step5: UMAP統合可視化（正誤分類・真クラス・SHAP強度・特定特徴量で色分け）
# 加えて、rmc独自の追加解析（ヒストグラム統計・PCA/UMAP・統計検定・
# クラス間距離解析）もそのまま実行する。

# XGBoost用のメタ特徴量物理カテゴリ対応表（Step4で使用）。
# LightGBM版はbaselineを除外して['signal','duration']だが、
# rmc(XGBoost)側は absolute_signal/relative_signal/duration を使っているため、
# それに合わせたマッピングを用意する。
META_PHYSICAL_CATEGORY = {
    'absolute_signal': '電流強度',
    'relative_signal': '電流強度',
    'duration': 'イベント時間',
}
for _i in range(12):
    META_PHYSICAL_CATEGORY[f'wave_{_i}'] = '局所変動'  # 要ドメイン確認


if __name__ == '__main__':
    smns = ['26I','2I','3I','4I','246I','P']  # 必要に応じて書き換える
    data_root = DATA_ROOT

    # fc_parametersや前処理ロジックを変更した場合、古いキャッシュを使い回さないよう
    # 必要に応じて呼ぶ（サンプル単位・バッチ単位のキャッシュを全削除）
    # clear_cache()

    # 今回の解析結果一式を保存するフォルダ
    # （results/rmc/xgboost/YYYYMMDD_HHMMSS_train_smn1-smn2-.../ ）を作成
    RUN_DIR, RUN_TIMESTAMP = paths.new_run(FEATURE_SET, ALGORITHM, smns=smns, run_type='train')

    # 本番の全データ抽出前に所要時間を見積もりたい場合は True にする
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
    # Step1: メタ特徴量のみ / tsfresh選択特徴量のみ / メタ+tsfresh の性能比較
    # ---------------------------------------------------------
    step1_results, step1_comparison_df, step1_feature_set_names = compare_feature_sets(
        dnf, META_FEATURE_COLUMNS, tsfresh_feature_cols, train_fn=train_xgb_classifier,
        n_jobs=N_JOBS, output_dir=RUN_DIR,
    )

    # '既存15+tsfresh' のような決め打ちではなく、実際に使われた列数から
    # 生成された名前を使う
    main_result = step1_results[step1_feature_set_names['combined']]
    feature_columns = main_result['feature_columns']
    le = main_result['le']
    class_names = main_result['class_names']
    X_test = main_result['X_test']
    y_test = main_result['y_test']
    y_pred = main_result['y_pred']
    bst = main_result['booster']

    plot_learning_curve(main_result['evals_result'], save_dir=RUN_DIR)
    plot_feature_importance(main_result['clf'], feature_columns, top_n=30, save_dir=RUN_DIR)

    MX, N_MX, report = conmtx(y_test, y_pred, le, save_dir=RUN_DIR)

    # --- モデル・ラベルエンコーダ・使用特徴量一覧も同じフォルダにまとめて保存 ---
    bst.save_model(str(RUN_DIR / 'model.json'))
    with open(RUN_DIR / 'label_encoder.pkl', 'wb') as f:
        pickle.dump(le, f)
    (RUN_DIR / 'feature_columns.txt').write_text('\n'.join(feature_columns), encoding='utf-8')

    # ---------------------------------------------------------
    # Step2: Permutation Importance（独立テストデータ）
    # ---------------------------------------------------------
    perm_importance_df = run_permutation_importance(
        main_result, n_repeats=10, n_jobs=N_JOBS, top_n=30, output_dir=RUN_DIR,
    )

    # ---------------------------------------------------------
    # Step3: SHAPによるクラス別比較・誤分類分析
    # ---------------------------------------------------------
    shap_result = run_shap_class_comparison(main_result, top_n=20, output_dir=RUN_DIR)

    # ---------------------------------------------------------
    # Step4: 物理量への再分類（Permutation Importance / SHAP の両方に適用）
    # ---------------------------------------------------------
    perm_with_category, perm_category_summary = build_physical_category_table(
        perm_importance_df, feature_col='feature', importance_col='importance_mean',
        extra_category_map=META_PHYSICAL_CATEGORY,
        title='Permutation Importance の物理カテゴリ別集計',
        output_path=RUN_DIR / 'step4_permutation_importance_by_category.png',
    )

    shap_with_category, shap_category_summary = build_physical_category_table(
        shap_result['mean_abs_shap_overall'], feature_col='feature',
        importance_col='mean_abs_shap',
        extra_category_map=META_PHYSICAL_CATEGORY,
        title='SHAP重要度（mean|SHAP|）の物理カテゴリ別集計',
        output_path=RUN_DIR / 'step4_shap_importance_by_category.png',
    )

    # ---------------------------------------------------------
    # Step5: UMAPとの統合可視化
    # ---------------------------------------------------------
    top_feature_for_color = perm_importance_df.iloc[0]['feature'] \
        if len(perm_importance_df) else None

    umap_result = run_umap_integration(
        main_result, shap_result=shap_result, top_feature=top_feature_for_color,
        top_n_shap_for_color=20, output_dir=RUN_DIR,
    )

    # ============================================================
    # rmc独自の追加解析（ヒストグラム統計・PCA/UMAP・統計検定・
    # SHAPクラス別重要度ヒートマップ・クラス間距離解析）
    # ============================================================

    # --- ヒストグラム・統計データの作成と保存 ---
    hist_stats_df, summary_stats_df = data_stat(dnf, META_FEATURE_COLUMNS, save_dir=RUN_DIR)

    # --- PCA / UMAP による次元削減の可視化 ---
    plot_dim_reduction(dnf, META_FEATURE_COLUMNS, method='pca', save_path=RUN_DIR / "pca_2d.png")
    plot_dim_reduction(dnf, META_FEATURE_COLUMNS, method='umap', save_path=RUN_DIR / "umap_2d.png")

    # --- 特徴量ごとの統計検定 (ANOVA / Kruskal-Wallis) ---
    stats_df = feature_group_tests(dnf, META_FEATURE_COLUMNS, save_path=RUN_DIR / "feature_group_tests.csv")

    # --- 重要度と統計的有意性の比較 ---
    compare_df = compare_importance_and_stats(
        bst, feature_columns, stats_df, save_path=RUN_DIR / "importance_vs_stats.csv"
    )

    # --- SHAPのクラス別重要度ヒートマップ（Step3で計算済みのshap_values_listを再利用） ---
    shap_importance_df = plot_shap_class_importance(
        shap_result['shap_values_list'], X_test, feature_columns, class_names,
        save_path=RUN_DIR / "shap_class_importance.png"
    )

    # --- SHAP順位とgainベース重要度・KW検定順位の比較 ---
    shap_compare_df = compare_shap_and_stats(
        shap_importance_df, compare_df, save_path=RUN_DIR / "shap_vs_importance_vs_stats.csv"
    )

    # --- メタ特徴空間でのクラス間距離ヒートマップ ---
    dist_df = class_distance_heatmap(dnf, META_FEATURE_COLUMNS, save_path=RUN_DIR / "class_distance_heatmap.png")

    # --- クラス中心ネットワーク ---
    # pathway_edges には既知の反応・変換経路を (始点クラス名, 終点クラス名) のタプルで指定できる。
    pathway_edges = []  # 必要に応じて書き換える
    class_centroid_network(dist_df, pathway_edges=pathway_edges,
                            save_path=RUN_DIR / "class_centroid_network.png")

    # --- クラス間距離と混同行列(誤分類率)の相関解析 ---
    corr_df, corr_stats = distance_confusion_correlation(
        dist_df, N_MX, smns, save_path=RUN_DIR / "distance_confusion_correlation.png"
    )

    execution_time = time.time() - start_time
    print(f"\n実行時間: {execution_time:.2f}秒")

    # --- 実行条件をmanifestとして保存（後から「どの設定の結果か」を追跡するため） ---
    paths.write_run_manifest(
        RUN_DIR, RUN_TIMESTAMP, smns,
        config={
            'fc_parameters_mode': type(FC_PARAMETERS).__name__,
            'use_tsfresh_feature_selection': USE_TSFRESH_FEATURE_SELECTION,
            'n_meta_features': len(META_FEATURE_COLUMNS),
        },
        execution_time=execution_time,
        comparison_df=step1_comparison_df,
        extra_info={'feature_set_names': step1_feature_set_names},
    )

    # --- 保存フォルダをZIP圧縮してダウンロードしやすくする ---
    zip_path = shutil.make_archive(
        base_name=str(RUN_DIR), format="zip",
        root_dir=RUN_DIR.parent, base_dir=RUN_DIR.name
    )
    print(f"🗜️ 結果フォルダをZIP化しました: {zip_path}")

    print(f"\n今回の結果は {RUN_DIR} にまとめて保存しました。")