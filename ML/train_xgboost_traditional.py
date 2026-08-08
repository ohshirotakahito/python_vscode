# -*- coding: utf-8 -*-
"""
train_xgboost_traditional.py

【このファイルについて】
TOP_XGboost_any_rmb_20250221_2.py のリファクタリング版。
tsfresh を使わない、12点波形特徴量（f1〜f12）のみによる、より単純な
XGBoost分類パイプライン（GridSearchCVによるハイパーパラメータ探索付き）。
train_lightgbm_tsfresh.py / train_xgboost_tsfresh.py（tsfresh併用版）とは別系統。

読み込むデータ: extract_features_traditional.py が data/features/rmb/ に保存する *_rmb.npy
（common/tdms_io.py の apick()/collect_events_for_sample() が生成したもの）

【今回の変更点: train_xgboost_tsfresh.py と同じ追加解析を追加】
common/ml_analysis.py に切り出した追加解析関数（SHAP分析、PCA/UMAP次元削減、
特徴量ごとの統計検定、クラス間距離解析など）を、train_xgboost_tsfresh.py と同じ形で
このスクリプトにも組み込んだ。

これらの関数は「生の xgb.Booster（.get_score(importance_type=...)が呼べる
もの）」を受け取る想定で統一されているが、本スクリプトは GridSearchCV で
得た sklearn の XGBClassifier を使っているため、呼び出し時には
clf.get_booster() で内部の Booster を取り出して渡している。

なお、rmc版にある plot_learning_curve()（train/valのmlogloss推移グラフ）は
xgb.train(evals=[...])のearly stoppingで得られるevals_resultが必要だが、
本スクリプトはGridSearchCVのみで学習しており、その形式の学習履歴を
持たないため、ここでは呼び出していない（学習曲線を見たい場合は、
GridSearchCVをやめてrmc版のようなearly stopping方式に変更する必要がある）。

【混同行列の可視化について】
以前はこのファイル専用の簡易 conmtx() を使っていたが、
common/eval_viz.py の conmtx() に統一した。
（理由1: 追加解析の distance_confusion_correlation() が、
  common/eval_viz.conmtx() が返す index=['Actual_<class>',...],
  columns=['Pred_<class>',...] 形式の N_MX を前提にしているため。
 理由2: 旧版の conmtx() は mtx_index に 'pred_'、mtx_columns に 'real_' と
  ラベル付けていたが、実際に confusion_matrix(y_test, y_pred) の行は
  「正解ラベル」・列は「予測ラベル」なので、ラベルが実態と逆になっていた
  （sklearnの仕様上の慣習と食い違っていた）。共通版に切り替えることで
  この食い違いも解消される。）

【重要: 今回の共通化に伴う修正】
common/tdms_io.py への統合時、rmb.py が使う apick() を「event_id付き」の
バージョンに統一した（rmc/rmd系との共通化のため）。これにより *_rmb.npy の
列数が 22列 → 23列（先頭に event_id が追加）に変わっている。
このファイルは元々 event_id を知らない固定22列の columns リストで
位置決め打ち読み込みをしていたため、そのままでは列数不一致でエラーになる。
→ 対策として、columns の先頭に 'event_id' を追加した（学習には使わないので
   data リスト側には追加していない）。

もし過去に保存した「event_id無しの旧rmb.npy」が残っている場合、
そのファイルだけは読み込み時に列数エラーになるので、extract_features_traditional.py で
再生成してから使うこと。

【出力先について】
common/paths.py に統一。読み込み元は data/features/rmb/、結果保存先は
results/rmb/xgboost/<timestamp>_<smns>/ 以下になる。
"""

import pickle
import time

import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split, GridSearchCV
from sklearn.metrics import f1_score
from sklearn import preprocessing
from sklearn.preprocessing import LabelEncoder

from imblearn.under_sampling import RandomUnderSampler

import xgboost as xgb

import common.paths as paths
from common.eval_viz import conmtx

# 追加解析（SHAP・PCA/UMAP・統計検定・クラス間距離解析）は common/ml_analysis.py に
# 切り出したものを使う（train_xgboost_tsfresh.py と共通）。
from common.ml_analysis import (
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
)

# 特徴量セット名・アルゴリズム名（common/paths.py のフォルダ命名規則と揃える）
FEATURE_SET = 'rmb'
ALGORITHM = 'xgboost'


# --- 列定義 ---
# 'event_id' は common/tdms_io.py の apick() が先頭に付与するようになったため追加。
# （学習には使わないので、下の data リストには含めない）
columns = ['event_id',
           'file',
           'Ex_ID',
           'distance',
           'sample',
           'signal_position',
           'signal_intensity',
           'signal_time',
           'signal_start',
           'signal_end',
           'signal_baseline',
           'f1', 'f2', 'f3', 'f4', 'f5', 'f6',
           'f7', 'f8', 'f9', 'f10', 'f11', 'f12']

# 訓練で用いるカラムリスト。
# common/data_pipeline.py（rmc/tsfresh側）と同じ考え方に合わせ、
# signal_baseline を直接特徴量として使うのをやめ、
#   relative_signal = signal_intensity（ベースラインからの相対値）
#   absolute_signal = signal_intensity + signal_baseline（ベースラインを足し戻した絶対値）
# の2つの派生特徴量を使う（dfdata()内で計算して追加する）。
# 追加解析（data_stat/plot_dim_reduction/class_distance_heatmap等）にも
# そのまま feature_columns として渡す。
data = ['relative_signal',
        'absolute_signal',
        'signal_time',
        'f1', 'f2', 'f3', 'f4', 'f5', 'f6',
        'f7', 'f8', 'f9', 'f10', 'f11', 'f12']


def dfdata(smns, data_root=None):
    # data_root未指定時は data/features/rmb/ を使う（common/paths.py 参照）
    if data_root is None:
        data_root = paths.feature_dir(FEATURE_SET)
    # 条件選択(シグナル時間範囲)
    (st_l_lmt, st_h_lmt) = (10, 1000)

    # 条件選択(シグナルベースライン範囲)
    (sb_l_lmt, sb_h_lmt) = (-300, 300)

    # 条件選択(シグナル強度範囲)
    (si_l_lmt, si_h_lmt) = (10, 1000)

    # データ読み込み
    dnf = pd.DataFrame(columns=columns)
    for smn in smns:
        sam = smn + '_10k_Sample_ANAL_rmb'
        datum = np.load(f'{data_root}/{sam}.npy', allow_pickle=True)
        df = pd.DataFrame(data=datum, columns=columns)
        df = df[df['signal_time'].astype(int) > st_l_lmt]
        df = df[df['signal_time'].astype(int) < st_h_lmt]
        df = df[df['signal_baseline'].astype(float) < sb_h_lmt]
        df = df[df['signal_baseline'].astype(float) > sb_l_lmt]
        df = df[df['signal_intensity'].astype(float) < si_h_lmt]
        df = df[df['signal_intensity'].astype(float) > si_l_lmt]

        # --- 派生特徴量の計算（common/data_pipeline.load_meta_data()と同じ定義） ---
        signal_intensity = df['signal_intensity'].astype(float)
        signal_baseline = df['signal_baseline'].astype(float)
        df = df.copy()
        df['relative_signal'] = signal_intensity
        df['absolute_signal'] = signal_intensity + signal_baseline

        dnf = pd.concat([dnf, df], axis=0)
        print(smn, ':', len(df))

    return dnf


def optimize_xgboost(X_train, y_train):
    xgb_model = xgb.XGBClassifier(eval_metric='mlogloss', use_label_encoder=False)

    param_grid = {
        'max_depth': [3, 6, 10, 20],
        'eta': [0.1, 0.3, 0.5, 0.7, 1],
        'n_estimators': [50, 100, 200]
    }

    grid_search = GridSearchCV(estimator=xgb_model, param_grid=param_grid, cv=3,
                                scoring='accuracy', verbose=1, n_jobs=-1)
    grid_search.fit(X_train, y_train)

    print("Best parameters found: ", grid_search.best_params_)
    return grid_search.best_estimator_


def learn_dataset(dnf):
    """学習する。

    戻り値に X_test, clf, le を追加した（元は y_test, y_pred, acc のみ）。
    追加解析（SHAP・PCA/UMAP等）で必要になるため。
    """
    y = [_.split('_')[0] for _ in dnf['sample']]

    x = dnf[data]
    X = preprocessing.scale(x)

    le = LabelEncoder()
    le = le.fit(y)
    y = le.transform(y)

    num_class = max(y) + 1

    test_size = 0.2
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=0)

    cn = [len(y_train[y_train == i]) for i in range(num_class)]
    counts = [min(cn) for _ in range(len(cn))]
    keys = [_ for _ in range(len(cn))]
    strategy = {key: count for key, count in zip(keys, counts)}

    rus = RandomUnderSampler(random_state=0, sampling_strategy=strategy)
    X_resampled, y_resampled = rus.fit_resample(X_train, y_train)

    clf = optimize_xgboost(X_resampled, y_resampled)

    y_pred = clf.predict(X_test)

    acc = f1_score(y_test, y_pred, average="micro")
    print('f-measure_value:', acc)

    return X_test, y_test, y_pred, clf, le


def mapping(data, bst):
    mapper = {'f{0}'.format(i): v for i, v in enumerate(data)}
    mapped = {mapper[k]: v for k, v in bst.get_fscore().items()}
    xgb.plot_importance(mapped)


if __name__ == '__main__':
    List = [['Guanine', 'OMeG']]  # 学習したいクラスの組み合わせをここに書く
    start_time = time.time()

    X = []
    for smns in List:
        dnf = dfdata(smns)

        X_test, y_test, y_pred, clf, le = learn_dataset(dnf)
        acc = f1_score(y_test, y_pred, average="micro")

        X.append((smns, acc))

        # 結果保存フォルダ（results/rmb/xgboost/<timestamp>_<smns>/）を作成し、
        # 混同行列・追加解析の結果一式をそこに保存する
        RUN_DIR, RUN_TIMESTAMP = paths.new_run(FEATURE_SET, ALGORITHM, smns=smns, run_type='train')

        MX, N_MX, report = conmtx(y_test, y_pred, le, save_dir=RUN_DIR)

        # sklearnのXGBClassifierから、追加解析関数が要求する生のBoosterを取り出す
        booster = clf.get_booster()
        class_names = list(le.classes_)
        feature_columns = data  # 学習に使った15特徴量

        # --- 特徴量重要度 ---
        plot_feature_importance(booster, feature_columns, top_n=30, save_dir=RUN_DIR)

        # --- SHAPによる識別結果の解釈（テストデータ全体で計算） ---
        shap_comparison, shap_values = run_shap_analysis(
            booster, X_test, feature_columns, le, y_test, y_pred, save_dir=RUN_DIR
        )

        # --- モデル・ラベルエンコーダ・使用特徴量一覧も同じフォルダにまとめて保存 ---
        booster.save_model(str(RUN_DIR / 'model.json'))
        with open(RUN_DIR / 'label_encoder.pkl', 'wb') as f:
            pickle.dump(le, f)
        (RUN_DIR / 'feature_columns.txt').write_text('\n'.join(feature_columns), encoding='utf-8')

        # ============================================================
        # 追加解析（train_xgboost_tsfresh.py と同じ一式）
        # ============================================================

        # --- ヒストグラム・統計データの作成と保存 ---
        hist_stats_df, summary_stats_df = data_stat(dnf, feature_columns, save_dir=RUN_DIR)

        # --- PCA / UMAP による次元削減の可視化 ---
        plot_dim_reduction(dnf, feature_columns, method='pca', save_path=RUN_DIR / "pca_2d.png")
        plot_dim_reduction(dnf, feature_columns, method='umap', save_path=RUN_DIR / "umap_2d.png")

        # --- 特徴量ごとの統計検定 (ANOVA / Kruskal-Wallis) ---
        stats_df = feature_group_tests(dnf, feature_columns, save_path=RUN_DIR / "feature_group_tests.csv")

        # --- 重要度と統計的有意性の比較 ---
        compare_df = compare_importance_and_stats(
            booster, feature_columns, stats_df, save_path=RUN_DIR / "importance_vs_stats.csv"
        )

        # --- SHAPのクラス別重要度ヒートマップ（run_shap_analysisで計算済みのshap_valuesを再利用） ---
        shap_importance_df = plot_shap_class_importance(
            shap_values, X_test, feature_columns, class_names,
            save_path=RUN_DIR / "shap_class_importance.png"
        )

        # --- SHAP順位とgainベース重要度・KW検定順位の比較 ---
        shap_compare_df = compare_shap_and_stats(
            shap_importance_df, compare_df, save_path=RUN_DIR / "shap_vs_importance_vs_stats.csv"
        )

        # --- メタ特徴空間でのクラス間距離ヒートマップ ---
        dist_df = class_distance_heatmap(dnf, feature_columns, save_path=RUN_DIR / "class_distance_heatmap.png")

        # --- クラス中心ネットワーク ---
        # pathway_edges には既知の反応・変換経路を (始点クラス名, 終点クラス名) のタプルで指定できる。
        pathway_edges = []  # 必要に応じて書き換える
        class_centroid_network(dist_df, pathway_edges=pathway_edges,
                                save_path=RUN_DIR / "class_centroid_network.png")

        # --- クラス間距離と混同行列(誤分類率)の相関解析 ---
        corr_df, corr_stats = distance_confusion_correlation(
            dist_df, N_MX, smns, save_path=RUN_DIR / "distance_confusion_correlation.png"
        )

        # --- 実行条件・結果のサマリーをテキストで保存 ---
        with open(RUN_DIR / "run_summary.txt", "w", encoding="utf-8") as f:
            f.write(f"実行フォルダ: {RUN_DIR.name}\n")
            f.write(f"クラス(smns): {smns}\n")
            f.write(f"特徴量総数: {len(feature_columns)}\n")
            f.write(f"micro-F1: {acc:.4f}\n")

        print(f"\n今回の結果は {RUN_DIR} にまとめて保存しました。")

    print(X)
    end_time = time.time()

    execution_time = end_time - start_time
    print(f"実行時間: {execution_time:.2f}秒")
