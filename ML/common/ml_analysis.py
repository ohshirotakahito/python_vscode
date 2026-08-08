# -*- coding: utf-8 -*-
"""
common/ml_analysis.py

【このモジュールについて】
元々 TOP_XGboost_rmc.py / TOP_LightGBM_rmc.py に別々に直接書かれていた
「追加解析」関数群（SHAP分析、PCA/UMAP次元削減、特徴量ごとの統計検定、
クラス間距離解析、特徴量セット比較、Permutation Importance、
物理カテゴリ分類、UMAP統合可視化）を共通モジュールとして切り出したもの。
XGBoost（rmb/rmc）・LightGBM（rmc）のいずれの学習パイプラインからも
同じ関数をそのまま使えるようにするため、個々の学習ロジックから独立させている。

【モデルの種類を問わない関数について（get_gain_importances/plot_feature_importance等）】
gain重要度を扱う関数は get_gain_importances() を介して、以下のいずれの
モデル型にも対応している（呼び出し側でモデルの種類を意識する必要はない）:
  - 生の xgb.Booster（.get_score(importance_type=...) が呼べるもの）
  - BoosterClassifierWrapper（本モジュール内で定義。Boosterをsklearn互換に
    薄くラップしたもの。.get_score() もそのまま中継している）
  - sklearn API のモデル（XGBClassifier, LGBMClassifier 等。.feature_importances_ を持つ）

【SHAP関連の関数について】
shap.TreeExplainer は上記のいずれのモデル型もそのまま渡せる
（BoosterClassifierWrapperだけは非対応 — SHAPには生のBoosterかsklearn APIの
モデルを渡すこと。run_shap_analysis()の引数名は歴史的経緯で"booster"のままだが、
実際にはXGBClassifier/LGBMClassifierを渡しても動作する）。

【sklearnツール（Permutation Importance等）との連携について】
生の xgb.Booster は sklearn非互換（.predict(numpy配列)を受け付けない）ため、
sklearn.inspection.permutation_importance 等では直接使えない。
BoosterClassifierWrapper で最低限の sklearn インターフェース
（.predict/.get_params/.classes_等）を持たせることで対応している。
学習済みのBoosterをそのままラップするだけで、再学習は一切行わない。

conmtx（混同行列の可視化）は common/eval_viz.py 側にあるため、
このモジュールには含めていない。
"""

import re
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn import preprocessing
from sklearn.decomposition import PCA
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.inspection import permutation_importance
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, f1_score
from scipy import stats
from scipy.spatial.distance import pdist, squareform
from matplotlib.patches import Ellipse

import shap
import xgboost as xgb


# ============================================================
# 学習曲線・特徴量重要度
# ============================================================

def plot_learning_curve(evals_result, save_dir=None):
    """train/valのmloglossの推移をプロットし、過学習が起きていないか目視確認する。

    early stopping・検証データを使った学習（xgb.train(evals=[...])）で得られる
    evals_result が無い場合（例: GridSearchCVのみで学習したモデル）は呼べない
    ので注意。

    save_dirを指定するとそのフォルダにPNGを保存する（未指定ならカレントディレクトリ）。
    """
    train_loss = evals_result['train']['mlogloss']
    val_loss = evals_result['val']['mlogloss']

    save_path = (Path(save_dir) / 'learning_curve.png') if save_dir else Path('learning_curve.png')

    plt.figure(figsize=(8, 5))
    plt.plot(train_loss, label='train mlogloss')
    plt.plot(val_loss, label='val mlogloss')
    plt.xlabel('Boosting round')
    plt.ylabel('mlogloss')
    plt.title('Learning curve')
    plt.legend()
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.show()


def get_gain_importances(model, feature_names):
    """モデルの種類を問わず、特徴量ごとのgain重要度を配列として取得する。

    以下のいずれの型にも対応する（isinstanceではなく、持っているメソッド／属性で判定）:
      - 生の xgb.Booster（.get_score(importance_type='gain') が呼べるもの）
      - BoosterClassifierWrapper（本モジュール内、.get_score() を中継している）
      - sklearn API のモデル（XGBClassifier, LGBMClassifier 等。.feature_importances_ を持つ）
    """
    if hasattr(model, 'get_score'):
        score_dict = model.get_score(importance_type='gain')
        # DMatrixにfeature_namesを渡して学習した場合、get_score()のキーは
        # 実際の特徴量名（'wave_0'等）になる。feature_names未指定で学習した
        # 場合のみ汎用名'f0','f1',...になる。どちらのケースにも対応する。
        if score_dict and all(
            re.fullmatch(r'f\d+', k) for k in score_dict.keys()
        ):
            return np.array([score_dict.get(f"f{i}", 0.0) for i in range(len(feature_names))])
        return np.array([score_dict.get(name, 0.0) for name in feature_names])
    if hasattr(model, 'feature_importances_'):
        return np.asarray(model.feature_importances_, dtype=float)
    raise TypeError(
        f"重要度を取得できないモデル型です: {type(model)}。"
        "get_score()かfeature_importances_のどちらかを持つモデルを渡してください。"
    )


def plot_feature_importance(model, labels, top_n=30, sort=True, save_dir=None):
    """model: 生の xgb.Booster、BoosterClassifierWrapper、または
    sklearn API のモデル（XGBClassifier/LGBMClassifier等）のいずれでもよい。
    save_dirを指定するとそのフォルダにPNGを保存する（未指定ならカレントディレクトリ）。"""
    importances = get_gain_importances(model, labels)
    labels = list(labels)

    if len(importances) != len(labels):
        raise ValueError("importancesとlabelsの長さが一致していない")

    if sort:
        sorted_idx = np.argsort(importances)[::-1]
        importances = importances[sorted_idx]
        labels = [labels[i] for i in sorted_idx]

    if top_n is not None:
        importances = importances[:top_n]
        labels = labels[:top_n]

    plt.figure(figsize=(12, 6))
    plt.bar(range(len(importances)), importances)
    plt.xticks(range(len(importances)), labels, rotation=90)
    plt.xlabel("Feature")
    plt.ylabel("Importance gain")
    plt.title("Feature Importances (Top {})".format(len(importances)))
    plt.tight_layout()
    save_path = (Path(save_dir) / 'feature_importance.png') if save_dir else Path('feature_importance.png')
    plt.savefig(save_path, dpi=150)
    plt.show()


# ============================================================
# SHAPによる識別結果の解釈
# ============================================================

def _normalize_shap_values(shap_values, n_classes):
    """SHAP値を常に (n_samples, n_features, n_classes) の3次元配列として扱えるように正規化する。

    XGBoostは分類クラス数が2つのとき、objectiveを明示的に'multi:softprob'に
    していない限り自動的に'binary:logistic'（2値分類専用モード）を選ぶ。
    この場合 shap.TreeExplainer が返すSHAP値は
    (n_samples, n_features) の2次元配列（正例側=クラスindex1への寄与のみ）になり、
    3次元配列を前提にしたこのモジュールの他の関数では扱えない。

    2値分類のSHAP値には対称性があり、負例側(クラスindex0)への寄与は
    正例側の符号を反転させたものとして扱える。これを利用して、常に
    (n_samples, n_features, n_classes) の3次元配列に揃える。
    """
    arr = np.asarray(shap_values)
    if arr.ndim == 3:
        return arr
    if arr.ndim == 2:
        if n_classes != 2:
            raise ValueError(
                f"SHAP値が2次元で返されましたが、クラス数は{n_classes}です。"
                "2値分類(binary:logistic)以外でこれが起きる場合は、"
                "XGBoost/shapのバージョンを確認してください。"
            )
        # class0(index0) = -shap_values, class1(index1) = shap_values
        return np.stack([-arr, arr], axis=-1)
    raise ValueError(f"想定外のSHAP値の次元数です: {arr.ndim}")


def _normalize_shap_explanation(explanation, n_classes, feature_columns):
    """shap.Explanationオブジェクト（explainer(X)の戻り値）を、
    values/base_valuesが常に (n_samples, n_features, n_classes) /
    (n_samples, n_classes) になるように正規化する（2値分類対応）。
    _normalize_shap_values() のExplanationオブジェクト版。
    """
    values_arr = np.asarray(explanation.values)
    if values_arr.ndim == 3:
        explanation.feature_names = feature_columns
        return explanation

    values_3d = _normalize_shap_values(values_arr, n_classes)

    base_vals = np.asarray(explanation.base_values)
    if base_vals.ndim == 1:
        # 1クラス分(正例側)の base_value しか無いので、負例側は符号反転して複製する
        base_vals_2d = np.stack([-base_vals, base_vals], axis=-1)
    else:
        base_vals_2d = base_vals

    return shap.Explanation(
        values=values_3d,
        base_values=base_vals_2d,
        data=explanation.data,
        feature_names=feature_columns,
    )


def run_shap_analysis(booster, X_test, feature_columns, le, y_test, y_pred,
                       save_dir, n_waterfall=5, dependence_top_n=3):
    """
    booster（生の xgb.Booster）の予測をSHAPで解釈し、以下をsave_dir配下に保存する。

      - shap_summary_bar.png                         : 全クラスの平均SHAP重要度（クラス別内訳つき棒グラフ）
      - shap_summary_beeswarm_{class}.png             : クラスごとのbeeswarm（各特徴量の影響方向・大きさ）
      - shap_dependence_{feature}.png                 : SHAP重要度上位の特徴量のdependence plot
      - shap_waterfall_{idx}_true-{T}_pred-{P}.png    : 誤分類サンプルの個別説明（原因の内訳）
      - shap_vs_gain_importance.csv                   : gain重要度とSHAP重要度の比較表

    X_test は学習時と同じ特徴量順（feature_columns順）のスケーリング済み配列を渡すこと。
    クラス数が2つの場合（2値分類）でも、内部で自動的に3次元形式に正規化して扱う。
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    class_names = list(le.classes_)
    n_classes = len(class_names)
    feature_columns = list(feature_columns)
    y_test = np.asarray(y_test)
    y_pred = np.asarray(y_pred)

    print("SHAP値を計算中...(テストデータ全体)")
    t0 = time.time()
    explainer = shap.TreeExplainer(booster)
    shap_values_raw = explainer.shap_values(X_test)
    shap_values = _normalize_shap_values(shap_values_raw, n_classes)
    print(f"SHAP値の計算完了: {shap_values.shape} ({time.time() - t0:.1f}秒)")

    # ------------------------------------------------------------------
    # ①② 全体・クラス単位のSHAP重要度（サマリー/バー）
    # ------------------------------------------------------------------
    plt.figure()
    shap.summary_plot(
        shap_values, X_test, feature_names=feature_columns,
        class_names=class_names, plot_type='bar', show=False
    )
    plt.tight_layout()
    plt.savefig(save_dir / 'shap_summary_bar.png', dpi=150, bbox_inches='tight')
    plt.close()

    for c, cname in enumerate(class_names):
        plt.figure()
        shap.summary_plot(
            shap_values[:, :, c], X_test, feature_names=feature_columns, show=False
        )
        plt.tight_layout()
        safe_cname = str(cname).replace('/', '_')
        plt.savefig(save_dir / f'shap_summary_beeswarm_{safe_cname}.png', dpi=150, bbox_inches='tight')
        plt.close()
    print(f"SHAPサマリー（全体バー + クラス別beeswarm {len(class_names)}枚）を保存しました")

    # ------------------------------------------------------------------
    # ⑤ 既存のgain重要度とSHAP重要度の比較表
    # ------------------------------------------------------------------
    mean_abs_shap_per_class = np.abs(shap_values).mean(axis=0)   # (n_features, n_classes)
    mean_abs_shap_overall = mean_abs_shap_per_class.mean(axis=1)  # (n_features,)

    gain_importance = get_gain_importances(booster, feature_columns)

    comparison = pd.DataFrame({
        'feature': feature_columns,
        'gain_importance': gain_importance,
        'mean_abs_shap': mean_abs_shap_overall,
    })
    for c, cname in enumerate(class_names):
        comparison[f'mean_abs_shap_{cname}'] = mean_abs_shap_per_class[:, c]

    comparison['gain_rank'] = comparison['gain_importance'].rank(ascending=False)
    comparison['shap_rank'] = comparison['mean_abs_shap'].rank(ascending=False)
    comparison = comparison.sort_values('mean_abs_shap', ascending=False).reset_index(drop=True)
    comparison.to_csv(save_dir / 'shap_vs_gain_importance.csv', index=False)
    print(f"gain重要度とSHAP重要度の比較表を保存しました: {save_dir / 'shap_vs_gain_importance.csv'}")

    # ------------------------------------------------------------------
    # ④ 特定の特徴量の詳細分析（dependence plot） — SHAP重要度が高い上位特徴量を自動選定
    # ------------------------------------------------------------------
    top_features = comparison['feature'].head(dependence_top_n).tolist()
    dominant_class = int(np.abs(shap_values).sum(axis=0).sum(axis=0).argmax())
    print(f"dependence plotの代表クラス: {class_names[dominant_class]} "
          f"(全体でSHAPの絶対値合計が最大のクラス)")

    for feat in top_features:
        plt.figure()
        shap.dependence_plot(
            feat, shap_values[:, :, dominant_class], X_test,
            feature_names=feature_columns, show=False
        )
        plt.tight_layout()
        safe_feat = str(feat).replace('/', '_')
        plt.savefig(save_dir / f'shap_dependence_{safe_feat}.png', dpi=150, bbox_inches='tight')
        plt.close()
    print(f"dependence plot（上位{len(top_features)}特徴量: {top_features}）を保存しました")

    # ------------------------------------------------------------------
    # ③ 誤分類サンプルの個別説明（waterfall）
    # ------------------------------------------------------------------
    explanation = explainer(X_test)
    explanation = _normalize_shap_explanation(explanation, n_classes, feature_columns)

    misclassified_idx = np.where(y_test != y_pred)[0]
    n_show = min(n_waterfall, len(misclassified_idx))
    print(f"誤分類サンプル数: {len(misclassified_idx)} 件 / うち{n_show}件をwaterfallで可視化します")

    for i in misclassified_idx[:n_show]:
        true_name = class_names[y_test[i]]
        pred_name = class_names[y_pred[i]]
        plt.figure()
        shap.plots.waterfall(explanation[i, :, y_pred[i]], show=False)
        plt.tight_layout()
        plt.savefig(
            save_dir / f'shap_waterfall_{i}_true-{true_name}_pred-{pred_name}.png',
            dpi=150, bbox_inches='tight'
        )
        plt.close()

    print(f"SHAP分析の結果一式を {save_dir} に保存しました")
    return comparison, shap_values


# ============================================================
# 追加解析①: 特徴量ごとのヒストグラム統計
# ============================================================

def data_stat(dnf, features, bins='auto', xlim=None, show_plot=True, save_dir=None,
              clip_percentiles=(0.5, 99.5), log_scale='auto'):
    """'sample'ごとに指定された特徴量のヒストグラムを描画し、
    そのビンごとの統計をCSV化しやすいDataFrameで返す。

    save_dir を指定すると、
      ・featureごとのヒストグラム画像 (histogram_<feature>.png)
      ・ビンごとの統計 (hist_stats.csv)
      ・サンプルごとの要約統計 (summary_stats.csv)
    を save_dir に保存する。

    xlim=None の場合、特徴量ごとに実データの min/max をそのまま使うのではなく、
    clip_percentiles（デフォルト下位0.5%〜上位99.5%）で外れ値を除いた範囲を
    自動計算する。absolute_signal/duration等、裾の長い分布で少数の外れ値が
    レンジ全体を支配し、ヒストグラムがほぼ1ビンに潰れてしまう問題への対策。
    表示上レンジ外に落ちる外れ値がある場合はその件数を警告表示する。

    bins='auto' の場合、サンプルサイズに応じてビン数を自動決定する
    （np.histogram_bin_edges(..., bins='auto') のFreedman-Diaconis/Sturgesを利用）。
    整数を渡せば従来通り固定ビン数として扱う。

    log_scale='auto' の場合、クリップ後のデータが正の値のみかつ
    最大/最小比が大きい（裾の長い分布）ときはx軸を対数スケールにする。
    True/Falseで明示的に指定することもできる。
    """
    group_col = 'sample'
    groups_all = dnf[group_col].astype(str).apply(lambda s: s.split('_')[0])
    unique_samples = sorted(groups_all.dropna().unique())

    hist_records = []
    summary_records = []

    for feature in features:
        col = pd.to_numeric(dnf[feature], errors='coerce')
        df_feat = pd.DataFrame({'_val': col, '_group': groups_all}).dropna(subset=['_val'])

        if df_feat.empty:
            print(f"[WARN] feature '{feature}' に有効な数値データがありません。スキップします。")
            continue

        all_vals = df_feat['_val'].to_numpy()

        if xlim is None:
            lo_pct, hi_pct = clip_percentiles
            vmin, vmax = np.percentile(all_vals, [lo_pct, hi_pct])
            if vmin == vmax:
                vmin -= 0.5
                vmax += 0.5
            n_clipped = int(((all_vals < vmin) | (all_vals > vmax)).sum())
            if n_clipped > 0:
                print(f"[INFO] '{feature}': 外れ値 {n_clipped}/{len(all_vals)} 件を"
                      f"表示レンジ({vmin:.3g}〜{vmax:.3g})外としてクリップして表示します"
                      f"（統計値の計算には全データを使用）。")
        else:
            vmin, vmax = xlim

        use_log = log_scale
        if use_log == 'auto':
            in_range = all_vals[(all_vals >= vmin) & (all_vals <= vmax)]
            positive = in_range[in_range > 0]
            use_log = (
                positive.size == in_range.size and positive.size > 1
                and positive.max() / max(positive.min(), 1e-12) > 50
            )

        if use_log:
            lo_edge = max(vmin, np.min(all_vals[all_vals > 0])) if vmin <= 0 else vmin
            if isinstance(bins, str):
                n_bins = min(60, max(20, int(np.sqrt(len(all_vals)))))
            else:
                n_bins = bins
            bin_edges = np.logspace(np.log10(lo_edge), np.log10(vmax), n_bins + 1)
        else:
            if isinstance(bins, str):
                bin_edges = np.histogram_bin_edges(
                    df_feat.loc[(df_feat['_val'] >= vmin) & (df_feat['_val'] <= vmax), '_val'],
                    bins=bins, range=(vmin, vmax)
                )
                n_bins = len(bin_edges) - 1
                n_bins = int(np.clip(n_bins, 15, 80))
                bin_edges = np.linspace(vmin, vmax, n_bins + 1)
            else:
                n_bins = bins
                bin_edges = np.linspace(vmin, vmax, n_bins + 1)
        bin_widths = np.diff(bin_edges)

        if show_plot:
            fig, axes = plt.subplots(2, 1, figsize=(10, 10))
            for ax in axes:
                ax.set_xlim(vmin, vmax)
                if use_log:
                    ax.set_xscale('log')

        for sample in unique_samples:
            subset = df_feat.loc[df_feat['_group'] == sample, '_val']
            if subset.empty:
                continue

            n = subset.size
            mean = float(np.mean(subset))
            median = float(np.median(subset))
            std_dev = float(np.std(subset, ddof=0))
            q1, q3 = np.percentile(subset, [25, 75])

            summary_records.append({
                'Feature': feature, 'Sample': sample, 'N': n,
                'Mean': mean, 'Median': median, 'Std': std_dev,
                'Q1': q1, 'Q3': q3
            })

            counts_raw, _ = np.histogram(subset, bins=bin_edges, density=False)
            pdf, _ = np.histogram(subset, bins=bin_edges, density=True)

            pdf_max = pdf.max() if pdf.size and np.isfinite(pdf.max()) and pdf.max() > 0 else 1.0
            norm = pdf / pdf_max

            if show_plot:
                (n_hist, _, patches) = axes[0].hist(
                    subset, bins=bin_edges, alpha=0.4, density=True, label=f'{sample}'
                )
                color = patches[0].get_facecolor() if patches else None
                axes[0].axvline(mean, color=color, linestyle='dashed', linewidth=2,
                                 label=f'Mean {sample}: {mean:.2f}')

                axes[1].bar(bin_edges[:-1], norm, width=bin_widths, alpha=0.4,
                             align='edge', label=f'{sample}')
                axes[1].axvline(mean, color=color, linestyle='dashed', linewidth=2)

            for i in range(n_bins):
                hist_records.append({
                    'Feature': feature,
                    'Sample': sample,
                    'BinLeft': bin_edges[i],
                    'BinRight': bin_edges[i + 1],
                    'Count': counts_raw[i],
                    'PDF': pdf[i],
                    'NormFreq': norm[i],
                })

        if show_plot:
            axes[0].set_xlabel(feature, fontsize=14)
            axes[0].set_ylabel('Density (PDF)', fontsize=14)
            axes[0].set_title(f'Histogram (PDF) of {feature}', fontsize=16)
            axes[0].legend(loc='upper left', bbox_to_anchor=(1.02, 1), borderaxespad=0.)

            axes[1].set_xlabel(feature, fontsize=14)
            axes[1].set_ylabel('Normalized Frequency', fontsize=14)
            axes[1].set_title(f'Normalized Histogram of {feature}', fontsize=16)
            axes[1].legend(loc='upper left', bbox_to_anchor=(1.02, 1), borderaxespad=0., fontsize=12)

            fig.tight_layout(rect=[0, 0, 0.85, 1])

            if save_dir is not None:
                fig.savefig(Path(save_dir) / f"histogram_{feature}.png", dpi=150, bbox_inches='tight')

            plt.close(fig)

    hist_stats_df = pd.DataFrame(hist_records,
                                  columns=['Feature', 'Sample', 'BinLeft', 'BinRight',
                                           'Count', 'PDF', 'NormFreq'])
    summary_stats_df = pd.DataFrame(summary_records,
                                     columns=['Feature', 'Sample', 'N', 'Mean', 'Median', 'Std', 'Q1', 'Q3'])

    if save_dir is not None:
        hist_stats_df.to_csv(Path(save_dir) / "hist_stats.csv", index=False, encoding='utf-8-sig')
        summary_stats_df.to_csv(Path(save_dir) / "summary_stats.csv", index=False, encoding='utf-8-sig')
        print(f"✅ ヒストグラム統計を保存しました: {save_dir}")

    return hist_stats_df, summary_stats_df


# ============================================================
# 追加解析②: PCA / UMAP による次元削減の可視化
# ============================================================

def _add_confidence_ellipse(x, y, ax, confidence=0.95, color='black', **kwargs):
    """2次元の点群 (x, y) の共分散行列から confidence（デフォルト95%）の
    信頼楕円を計算し、ax に描画するヘルパー関数。点数が3点未満の場合はスキップする。"""
    if len(x) < 3:
        return None

    cov = np.cov(x, y)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = eigvals.argsort()[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]

    angle = np.degrees(np.arctan2(eigvecs[1, 0], eigvecs[0, 0]))

    chi2_val = stats.chi2.ppf(confidence, df=2)
    width, height = 2 * np.sqrt(np.clip(eigvals, 0, None) * chi2_val)

    ellipse = Ellipse((np.mean(x), np.mean(y)), width, height, angle=angle,
                       facecolor='none', edgecolor=color, linewidth=2,
                       linestyle='--', alpha=0.9, **kwargs)
    ax.add_patch(ellipse)
    return ellipse


def plot_dim_reduction(dnf, features, group_col='sample', method='pca',
                        max_points=20000, random_state=0, save_path=None,
                        show_centroid=True, show_ellipse=True, confidence=0.95):
    """dnf の指定 features を標準化し、PCA または UMAP で2次元に圧縮して
    group_col ごとに色分けした散布図を描く。method: 'pca' または 'umap'。
    max_points を超える場合はランダムに間引く（Noneなら全点使用）。
    save_path を指定すると図と各クラスの重心座標CSV(<save_path>_centroids.csv)を保存する。
    戻り値: 埋め込み座標とグループ名を含むDataFrame"""
    groups = dnf[group_col].astype(str).apply(lambda s: s.split('_')[0])

    X = dnf[features].astype(float).values
    X = preprocessing.scale(X)

    n = len(X)
    if max_points is not None and n > max_points:
        rng = np.random.default_rng(random_state)
        idx = rng.choice(n, size=max_points, replace=False)
        X_plot = X[idx]
        groups_plot = groups.values[idx]
    else:
        X_plot = X
        groups_plot = groups.values

    if method == 'pca':
        reducer = PCA(n_components=2, random_state=random_state)
        emb = reducer.fit_transform(X_plot)
        var_ratio = reducer.explained_variance_ratio_
        xlabel = f"PC1 ({var_ratio[0]*100:.1f}%)"
        ylabel = f"PC2 ({var_ratio[1]*100:.1f}%)"
        title = "PCA of features"
    elif method == 'umap':
        try:
            import umap
        except ImportError:
            print("⚠ umap-learn がインストールされていません。`pip install umap-learn --break-system-packages` を実行してください。UMAPプロットはスキップします。")
            return None
        reducer = umap.UMAP(n_components=2, random_state=random_state)
        emb = reducer.fit_transform(X_plot)
        xlabel, ylabel = "UMAP1", "UMAP2"
        title = "UMAP of features"
    else:
        raise ValueError("method must be 'pca' or 'umap'")

    df_emb = pd.DataFrame({'Dim1': emb[:, 0], 'Dim2': emb[:, 1], 'Group': groups_plot})

    unique_groups = sorted(df_emb['Group'].unique())
    palette = sns.color_palette(n_colors=len(unique_groups))
    color_map = dict(zip(unique_groups, palette))

    fig, ax = plt.subplots(figsize=(8, 7))
    sns.scatterplot(data=df_emb, x='Dim1', y='Dim2', hue='Group', hue_order=unique_groups,
                     palette=color_map, alpha=0.4, s=15, linewidth=0, ax=ax)

    centroid_records = []
    for g in unique_groups:
        sub = df_emb[df_emb['Group'] == g]
        cx, cy = sub['Dim1'].mean(), sub['Dim2'].mean()
        color = color_map[g]

        if show_ellipse:
            _add_confidence_ellipse(sub['Dim1'].values, sub['Dim2'].values, ax,
                                     confidence=confidence, color=color)

        if show_centroid:
            ax.scatter([cx], [cy], marker='X', s=220, color=[color],
                       edgecolor='black', linewidth=1.5, zorder=5)

        centroid_records.append({'Group': g, 'Centroid_Dim1': cx, 'Centroid_Dim2': cy, 'N': len(sub)})

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    extra = []
    if show_centroid:
        extra.append("重心")
    if show_ellipse:
        extra.append(f"{int(confidence*100)}%信頼楕円")
    full_title = title + (" + " + " + ".join(extra) if extra else "")
    ax.set_title(full_title)
    ax.legend(title=group_col, bbox_to_anchor=(1.02, 1), loc='upper left', borderaxespad=0.)
    fig.tight_layout()

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"✅ {method.upper()}プロット({'+'.join(extra) if extra else '散布図のみ'})を保存しました: {save_path}")

        centroid_df = pd.DataFrame(centroid_records)
        centroid_csv = Path(save_path).with_name(Path(save_path).stem + "_centroids.csv")
        centroid_df.to_csv(centroid_csv, index=False, encoding='utf-8-sig')

    plt.close(fig)
    return df_emb


# ============================================================
# 追加解析③: 特徴量ごとの統計検定 (ANOVA / Kruskal-Wallis)
# ============================================================

def feature_group_tests(dnf, features, group_col='sample', save_path=None):
    """features ごとに ANOVA (f_oneway) と Kruskal-Wallis 検定を行い、
    群間で統計的に有意な差があるかを定量化する。
    効果量: ANOVA は eta二乗、Kruskal-Wallis は epsilon二乗を算出。"""
    groups = dnf[group_col].astype(str).apply(lambda s: s.split('_')[0])
    unique_groups = sorted(groups.unique())

    records = []
    for feature in features:
        vals = pd.to_numeric(dnf[feature], errors='coerce')
        tmp = pd.DataFrame({'val': vals, 'group': groups}).dropna()

        samples = [tmp.loc[tmp['group'] == g, 'val'].values for g in unique_groups]
        samples = [s for s in samples if len(s) > 0]

        if len(samples) < 2:
            print(f"[WARN] feature '{feature}' はグループ数が不足しているためスキップします。")
            continue

        f_stat, p_anova = stats.f_oneway(*samples)

        grand_mean = tmp['val'].mean()
        ss_between = sum(len(s) * (np.mean(s) - grand_mean) ** 2 for s in samples)
        ss_total = float(((tmp['val'] - grand_mean) ** 2).sum())
        eta_sq = ss_between / ss_total if ss_total > 0 else np.nan

        h_stat, p_kw = stats.kruskal(*samples)

        n_total = len(tmp)
        k = len(samples)
        eps_sq = (h_stat - k + 1) / (n_total - k) if n_total > k else np.nan

        records.append({
            'Feature': feature,
            'ANOVA_F': f_stat, 'ANOVA_p': p_anova, 'ANOVA_eta_sq': eta_sq,
            'KW_H': h_stat, 'KW_p': p_kw, 'KW_epsilon_sq': eps_sq,
        })

    result_df = pd.DataFrame(records).sort_values('KW_p').reset_index(drop=True)

    if save_path is not None:
        result_df.to_csv(save_path, index=False, encoding='utf-8-sig')
        print(f"✅ 統計検定結果を保存しました: {save_path}")

    return result_df


def compare_importance_and_stats(model, features, stats_df, save_path=None):
    """model: 生の xgb.Booster、BoosterClassifierWrapper、または
    sklearn API のモデル（XGBClassifier/LGBMClassifier等）のいずれでもよい。
    gain重要度の順位と、Kruskal-Wallis検定の統計量(KW_H)の順位を比較する。
    Rank_diffが0に近いほど、重要度の高い特徴＝群間差が大きい特徴が一致している。"""
    importances = get_gain_importances(model, features)

    imp_df = pd.DataFrame({'Feature': features, 'Importance_gain': importances})
    imp_df['Importance_rank'] = imp_df['Importance_gain'].rank(ascending=False)

    merged = imp_df.merge(
        stats_df[['Feature', 'KW_H', 'KW_p', 'KW_epsilon_sq']],
        on='Feature', how='left'
    )
    merged['KW_rank'] = merged['KW_H'].rank(ascending=False)
    merged['Rank_diff'] = merged['Importance_rank'] - merged['KW_rank']
    merged = merged.sort_values('Importance_rank').reset_index(drop=True)

    if save_path is not None:
        merged.to_csv(save_path, index=False, encoding='utf-8-sig')
        print(f"✅ 重要度と統計検定の比較表を保存しました: {save_path}")

    return merged


# ============================================================
# 追加解析④: SHAPのクラス別重要度ヒートマップ
# ============================================================

def _shap_values_per_class(shap_values, n_classes=None):
    """shap_values（(n_samples, n_features, n_classes) の3次元配列、
    (n_samples, n_features) の2次元配列（2値分類）、またはクラスごとのリスト）を、
    常にクラスごとの2次元配列のリストとして扱えるようにする。"""
    if isinstance(shap_values, list):
        return shap_values
    arr = np.asarray(shap_values)
    if arr.ndim == 2 and n_classes == 2:
        arr = _normalize_shap_values(arr, n_classes)
    if arr.ndim == 3:
        return [arr[:, :, c] for c in range(arr.shape[2])]
    return [arr]


def plot_shap_class_importance(shap_values, X_shap, feature_names, class_names, save_path=None):
    """クラスごとの平均|SHAP値|を、特徴量×クラスのヒートマップとして可視化する。
    どの特徴量がどのクラスの判別に効いているかをクラスごとに切り分けて確認できる。
    戻り値: 特徴量×クラスの平均|SHAP値|をまとめたDataFrame（index=Feature, columns=Class）"""
    shap_values_per_class = _shap_values_per_class(shap_values, n_classes=len(class_names))

    records = {}
    for i, cls in enumerate(class_names):
        if i >= len(shap_values_per_class):
            continue
        mean_abs = np.abs(shap_values_per_class[i]).mean(axis=0)
        records[cls] = mean_abs

    imp_df = pd.DataFrame(records, index=list(feature_names))

    fig, ax = plt.subplots(figsize=(max(6, len(records) * 0.9), max(6, len(feature_names) * 0.4)))
    sns.heatmap(imp_df, annot=True, fmt=".3f", cmap="viridis", ax=ax,
                cbar_kws={'label': 'mean |SHAP value|'})
    ax.set_title('クラス別 SHAP重要度 (特徴量 × クラス)')
    ax.set_xlabel('Class')
    ax.set_ylabel('Feature')
    fig.tight_layout()

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        csv_path = Path(save_path).with_suffix('.csv')
        imp_df.to_csv(csv_path, encoding='utf-8-sig')
        print(f"✅ クラス別SHAP重要度ヒートマップを保存しました: {save_path}")

    plt.close(fig)
    return imp_df


def compare_shap_and_stats(shap_importance_df, importance_compare_df, save_path=None):
    """SHAPベースの重要度をクラス方向に平均して1次元化し、gainベース重要度・
    Kruskal-Wallis検定の統計量(compare_importance_and_statsの戻り値)と順位を比較する。"""
    shap_mean = shap_importance_df.mean(axis=1).rename('SHAP_mean_abs')
    merged = importance_compare_df.set_index('Feature').join(shap_mean, how='left').reset_index()
    merged['SHAP_rank'] = merged['SHAP_mean_abs'].rank(ascending=False)
    merged['Rank_diff_SHAP_vs_Importance'] = merged['SHAP_rank'] - merged['Importance_rank']
    merged = merged.sort_values('SHAP_rank').reset_index(drop=True)

    if save_path is not None:
        merged.to_csv(save_path, index=False, encoding='utf-8-sig')
        print(f"✅ SHAP重要度と他指標(gain/KW)の比較表を保存しました: {save_path}")

    return merged


# ============================================================
# 追加解析⑤: クラス間距離ヒートマップ・中心ネットワーク・誤分類相関
# ============================================================

def class_distance_heatmap(dnf, features, group_col='sample', save_path=None):
    """標準化した特徴空間における各クラスの重心を求め、クラス間のユークリッド距離を
    ヒートマップとして可視化する。値が小さいほど、その2クラスは特徴空間上で近い(似ている)。
    戻り値: クラス間距離行列(index/columnsはクラス名)"""
    groups = dnf[group_col].astype(str).apply(lambda s: s.split('_')[0])
    X = dnf[features].astype(float).values
    X = preprocessing.scale(X)

    unique_groups = sorted(groups.unique())
    centroids = np.array([X[groups.values == g].mean(axis=0) for g in unique_groups])

    dist_mtx = squareform(pdist(centroids, metric='euclidean'))
    dist_df = pd.DataFrame(dist_mtx, index=unique_groups, columns=unique_groups)

    fig, ax = plt.subplots(figsize=(8, 7))
    sns.heatmap(dist_df, annot=True, fmt=".2f", cmap="magma_r", square=True,
                cbar_kws={'label': 'Euclidean distance (standardized feature space)'}, ax=ax)
    ax.set_title(f'クラス間距離ヒートマップ ({len(features)}次元特徴空間・重心間)')
    fig.tight_layout()

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        dist_csv = Path(save_path).with_suffix('.csv')
        dist_df.to_csv(dist_csv, encoding='utf-8-sig')
        print(f"✅ クラス間距離ヒートマップを保存しました: {save_path}")

    plt.close(fig)
    return dist_df


def class_centroid_network(dist_df, pathway_edges=None, distance_threshold=None,
                            layout='spring', save_path=None):
    """クラス間距離行列(class_distance_heatmapの戻り値)からクラス重心ネットワークを構築して
    可視化する。全クラスペアを距離の逆数を重みとしたエッジ(灰色)で結ぶ。
    pathway_edges に [('A', 'B'), ...] のように既知の反応経路等を渡すと赤い矢印で強調表示する。
    layout: 'spring' または 'mds'。networkxが未インストールの場合はスキップする。
    戻り値: networkx.Graph オブジェクト（未インストール時はNone）"""
    try:
        import networkx as nx
    except ImportError:
        print("⚠ networkx がインストールされていません。`pip install networkx --break-system-packages` を実行してください。クラス中心ネットワークはスキップします。")
        return None

    labels = list(dist_df.index)
    G = nx.Graph()
    G.add_nodes_from(labels)

    for i, a in enumerate(labels):
        for j, b in enumerate(labels):
            if j <= i:
                continue
            d = float(dist_df.loc[a, b])
            if distance_threshold is None or d <= distance_threshold:
                G.add_edge(a, b, distance=d, weight=1.0 / (d + 1e-6))

    if layout == 'mds':
        from sklearn.manifold import MDS
        mds = MDS(n_components=2, dissimilarity='precomputed', random_state=0,
                  normalized_stress='auto')
        coords = mds.fit_transform(dist_df.values)
        pos = {label: coords[i] for i, label in enumerate(labels)}
    else:
        pos = nx.spring_layout(G, weight='weight', seed=0)

    fig, ax = plt.subplots(figsize=(9, 8))

    edge_distances = [G[u][v]['distance'] for u, v in G.edges()]
    max_d = max(edge_distances) if edge_distances else 1.0
    edge_widths = [3.0 * (1 - d / max_d) + 0.3 for d in edge_distances]

    nx.draw_networkx_edges(G, pos, ax=ax, width=edge_widths, edge_color='lightgray')

    palette = sns.color_palette(n_colors=len(labels))
    for i, label in enumerate(labels):
        ax.scatter(*pos[label], s=900, color=[palette[i]], edgecolor='black',
                   linewidth=1.5, zorder=3)
    nx.draw_networkx_labels(G, pos, ax=ax, font_size=11, font_weight='bold')

    for (u, v) in G.edges():
        d = G[u][v]['distance']
        x = (pos[u][0] + pos[v][0]) / 2
        y = (pos[u][1] + pos[v][1]) / 2
        ax.text(x, y, f"{d:.2f}", fontsize=8, color='dimgray',
                ha='center', va='center',
                bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', pad=0.5))

    if pathway_edges:
        for (src, dst) in pathway_edges:
            if src not in pos or dst not in pos:
                print(f"⚠ 経路のノードが見つかりません(スキップ): {src} → {dst}")
                continue
            ax.annotate(
                "", xy=pos[dst], xytext=pos[src],
                arrowprops=dict(arrowstyle="-|>", color="crimson", lw=2.5,
                                 shrinkA=20, shrinkB=20, connectionstyle="arc3,rad=0.12"),
                zorder=4
            )

    ax.set_title('クラス中心ネットワーク\n(灰:特徴空間での距離 / 赤矢印:指定した経路)')
    ax.axis('off')
    fig.tight_layout()

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"✅ クラス中心ネットワークを保存しました: {save_path}")

    plt.close(fig)
    return G


def distance_confusion_correlation(dist_df, N_MX, class_labels, save_path=None):
    """クラス間の特徴空間距離(class_distance_heatmapの戻り値)と、混同行列(conmtx()が返す
    正規化済み混同行列N_MX)の誤分類率との相関を、Pearson・Spearmanの両方で定量評価し、
    散布図+回帰直線として可視化する。
    誤分類率は Actual_A→Pred_B と Actual_B→Pred_A の平均値(%)を用いる。
    ※ N_MX は common/eval_viz.py の conmtx() が返す
      index=['Actual_<class>',...], columns=['Pred_<class>',...] 形式のものを渡すこと。
    戻り値: (corr_df, corr_stats)"""
    records = []
    for i, a in enumerate(class_labels):
        for j, b in enumerate(class_labels):
            if i >= j:
                continue
            if a not in dist_df.index or b not in dist_df.columns:
                continue
            d = float(dist_df.loc[a, b])

            key_ab = (f'Actual_{a}', f'Pred_{b}')
            key_ba = (f'Actual_{b}', f'Pred_{a}')
            if key_ab[0] not in N_MX.index or key_ab[1] not in N_MX.columns:
                continue

            rate_ab = N_MX.loc[key_ab[0], key_ab[1]]
            rate_ba = N_MX.loc[key_ba[0], key_ba[1]]
            confusion_rate = (rate_ab + rate_ba) / 2

            records.append({
                'Class_A': a, 'Class_B': b,
                'Distance': d, 'Confusion_rate_%': confusion_rate
            })

    corr_df = pd.DataFrame(records)

    if len(corr_df) < 3:
        print("⚠ クラスペア数が少なすぎるため、相関解析をスキップします。")
        return corr_df, {}

    pearson_r, pearson_p = stats.pearsonr(corr_df['Distance'], corr_df['Confusion_rate_%'])
    spearman_r, spearman_p = stats.spearmanr(corr_df['Distance'], corr_df['Confusion_rate_%'])

    print(f"Pearson  r = {pearson_r:.3f} (p = {pearson_p:.4f})")
    print(f"Spearman r = {spearman_r:.3f} (p = {spearman_p:.4f})")
    print("→ 負の相関が強いほど「特徴空間で近いクラス同士ほど誤分類されやすい」ことを示す")

    fig, ax = plt.subplots(figsize=(7, 6))
    sns.regplot(data=corr_df, x='Distance', y='Confusion_rate_%', ax=ax,
                scatter_kws={'s': 60, 'alpha': 0.7}, line_kws={'color': 'crimson'})
    for _, row in corr_df.iterrows():
        ax.annotate(f"{row['Class_A']}-{row['Class_B']}",
                    (row['Distance'], row['Confusion_rate_%']),
                    fontsize=8, xytext=(3, 3), textcoords='offset points')
    ax.set_xlabel('クラス間距離(標準化特徴空間・重心間)')
    ax.set_ylabel('誤分類率(%, 双方向平均)')
    ax.set_title(f'クラス間距離 vs 誤分類率\n'
                 f'Pearson r={pearson_r:.2f} (p={pearson_p:.3f}), '
                 f'Spearman r={spearman_r:.2f} (p={spearman_p:.3f})')
    fig.tight_layout()

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        corr_csv = Path(save_path).with_suffix('.csv')
        corr_df.to_csv(corr_csv, index=False, encoding='utf-8-sig')
        print(f"✅ 距離-誤分類率相関解析を保存しました: {save_path}")

    plt.close(fig)

    corr_stats = {
        'pearson_r': pearson_r, 'pearson_p': pearson_p,
        'spearman_r': spearman_r, 'spearman_p': spearman_p,
    }
    return corr_df, corr_stats


# ============================================================
# 追加解析⑥: 生のxgb.Boosterをsklearn互換にする薄いラッパー
# ============================================================

class BoosterClassifierWrapper(BaseEstimator, ClassifierMixin):
    """生の xgb.Booster を、sklearn互換の分類器として扱えるようにする薄いラッパー。

    学習は一切行わない（既に学習済みのBoosterを包むだけ）。
    sklearn.inspection.permutation_importance のように、.predict(X)を
    numpy配列で直接呼び出すsklearnツールから、生のBoosterをそのまま
    使うために用意した。

    使い方:
        wrapper = BoosterClassifierWrapper(bst, num_class=2, feature_names=feature_columns)
        perm = permutation_importance(wrapper, X_test, y_test, scoring='f1_macro')

    注意: shap.TreeExplainer にはこのラッパーではなく、元の生のBoosterを
    渡すこと（SHAPはモデル型を内部で判別するため、ラッパーは非対応）。
    """

    def __init__(self, booster, num_class, feature_names=None):
        self.booster = booster
        self.num_class = num_class
        self.feature_names = feature_names

    def fit(self, X, y=None):
        # 既に学習済みのBoosterをラップするだけなので、fitは何もしない
        self.classes_ = np.arange(self.num_class)
        return self

    def __sklearn_is_fitted__(self):
        return True

    def _to_dmatrix(self, X):
        return xgb.DMatrix(np.asarray(X), feature_names=self.feature_names)

    def predict(self, X):
        raw = np.asarray(self.booster.predict(self._to_dmatrix(X)))
        if raw.ndim == 2:
            # multi:softprob（確率配列）で返ってくる場合はargmaxしてラベルに変換
            return raw.argmax(axis=1)
        return raw.astype(int)

    def predict_proba(self, X):
        raw = np.asarray(self.booster.predict(self._to_dmatrix(X)))
        if raw.ndim == 2:
            return raw
        # multi:softmax（ラベルのみ）の場合は one-hot で代用する
        proba = np.zeros((len(raw), self.num_class))
        proba[np.arange(len(raw)), raw.astype(int)] = 1.0
        return proba

    def get_score(self, importance_type='gain'):
        """common/ml_analysis.py の get_gain_importances() から呼べるように、
        Boosterのget_score()をそのまま中継する。"""
        return self.booster.get_score(importance_type=importance_type)

    @property
    def classes_(self):
        return getattr(self, '_classes_', np.arange(self.num_class))

    @classes_.setter
    def classes_(self, value):
        self._classes_ = value


# ============================================================
# 追加解析⑦: 特徴量セット比較 (Step1)
# ============================================================

def compare_feature_sets(dnf, meta_feature_cols, tsfresh_feature_cols, train_fn,
                          n_jobs=None, output_dir=None):
    """
    「メタ特徴量のみ」「tsfresh特徴量のみ」「メタ+tsfresh」の3パターンで
    train_fn を使ってモデルを学習し、Macro F1・各クラスF1・混同行列を比較する。

    train_fn: (dnf, feature_columns, feature_set_name, [n_jobs=...]) -> result(dict) を
              受け取る関数。result は少なくとも以下のキーを持つこと:
                'clf', 'le', 'feature_columns', 'y_test', 'y_pred', 'macro_f1', 'per_class_f1'
              （TOP_XGboost_*.py / TOP_LightGBM_rmc.py 側の
                train_xgb_classifier() / train_lgbm_classifier() を渡す想定）
    output_dir: 結果（比較表CSV・比較グラフPNG・クラスごとの混同行列）の保存先。

    戻り値:
        results        : {セット名: train_fnの戻り値dict}
        comparison_df  : Macro F1・各クラスF1をまとめた比較表
        feature_set_names : {'meta': ..., 'tsfresh': ..., 'combined': ...}（列数から動的生成された実際の名前）
    """
    output_dir = Path(output_dir) if output_dir else Path('.')
    output_dir.mkdir(parents=True, exist_ok=True)

    meta_name = f'既存{len(meta_feature_cols)}特徴量'
    tsfresh_name = 'tsfresh選択特徴量'
    combined_name = f'既存{len(meta_feature_cols)}+tsfresh'

    feature_sets = OrderedDict([
        (meta_name, list(meta_feature_cols)),
        (tsfresh_name, list(tsfresh_feature_cols)),
        (combined_name, list(meta_feature_cols) + list(tsfresh_feature_cols)),
    ])

    results = {}
    rows = []

    for name, cols in feature_sets.items():
        print(f"\n===== Step1: 特徴量セット『{name}』（{len(cols)}列）で学習 =====")
        kwargs = {'feature_set_name': name}
        if n_jobs is not None:
            kwargs['n_jobs'] = n_jobs
        result = train_fn(dnf, cols, **kwargs)
        results[name] = result

        row = {'feature_set': name, 'n_features': len(cols), 'macro_f1': result['macro_f1']}
        row.update({f'f1_{cls}': score for cls, score in result['per_class_f1'].items()})
        rows.append(row)

        # 特徴量セットごとに専用サブフォルダへ保存（conmtxはsave_dir配下に
        # 固定ファイル名で保存するため、セット名ごとに分けて上書きを防ぐ）
        from common.eval_viz import conmtx
        cm_dir = output_dir / f"confusion_matrix_{re.sub(r'[^0-9A-Za-z]+', '_', name)}"
        cm_dir.mkdir(parents=True, exist_ok=True)
        conmtx(result['y_test'], result['y_pred'], result['le'], save_dir=cm_dir)

    comparison_df = pd.DataFrame(rows).set_index('feature_set')
    comparison_df.to_csv(output_dir / 'step1_feature_set_comparison.csv', encoding='utf-8-sig')

    print("\n===== Step1: 特徴量セット比較表 =====")
    print(comparison_df)

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(comparison_df.index, comparison_df['macro_f1'])
    ax.set_ylabel('Macro F1')
    ax.set_title('特徴量セット別 Macro F1 比較')
    plt.xticks(rotation=15)
    plt.tight_layout()
    plt.savefig(output_dir / 'step1_macro_f1_comparison.png', dpi=150)
    plt.show()
    plt.close(fig)

    f1_cols = [c for c in comparison_df.columns if c.startswith('f1_')]
    if f1_cols:
        class_names = [c.replace('f1_', '') for c in f1_cols]
        n_sets = len(comparison_df)
        n_classes = len(class_names)
        x = np.arange(n_classes)
        width = 0.8 / max(n_sets, 1)

        fig, ax = plt.subplots(figsize=(max(8, n_classes * 1.2), 5))
        for i, (set_name, row) in enumerate(comparison_df.iterrows()):
            ax.bar(x + i * width, row[f1_cols].values, width, label=set_name)
        ax.set_xticks(x + width * (n_sets - 1) / 2)
        ax.set_xticklabels(class_names, rotation=45, ha='right')
        ax.set_ylabel('F1 score')
        ax.set_title('クラスごとのF1比較（特徴量セット別）')
        ax.legend()
        plt.tight_layout()
        plt.savefig(output_dir / 'step1_per_class_f1_comparison.png', dpi=150)
        plt.show()
        plt.close(fig)

    feature_set_names = {'meta': meta_name, 'tsfresh': tsfresh_name, 'combined': combined_name}
    return results, comparison_df, feature_set_names


# ============================================================
# 追加解析⑧: Permutation Importance (Step2)
# ============================================================

def run_permutation_importance(result, n_repeats=10, n_jobs=None, top_n=30,
                                scoring='f1_macro', random_state=0,
                                output_dir=None, max_samples=None):
    """
    result['X_test'] / result['y_test']（学習に一切使っていない独立データ）で
    permutation importance を計算する。

    result: train_xgb_classifier() / train_lgbm_classifier() 等が返す辞書。
            result['clf'] は sklearn互換（.predict(numpy配列)が呼べる）である必要がある。
            生のxgb.Boosterの場合は BoosterClassifierWrapper で包んでから
            result['clf'] に入れておくこと。
    """
    output_dir = Path(output_dir) if output_dir else Path('.')
    output_dir.mkdir(parents=True, exist_ok=True)

    X_test = result['X_test']
    y_test = result['y_test']

    if max_samples is not None and len(y_test) > max_samples:
        idx_sub, _ = train_test_split(
            np.arange(len(y_test)), train_size=max_samples,
            random_state=random_state, stratify=y_test,
        )
        X_test = X_test[idx_sub]
        y_test = y_test[idx_sub]
        print(f"[{result['name']}] Permutation Importance: "
              f"テストデータを {len(result['y_test'])} → {len(y_test)} 件にサブサンプリングして計算します")

    print(f"\n===== Step2: Permutation Importance ({result['name']}) を計算中... "
          f"(n_samples={len(y_test)}, n_repeats={n_repeats}) =====")
    t0 = time.time()
    perm = permutation_importance(
        result['clf'], X_test, y_test,
        scoring=scoring, n_repeats=n_repeats, n_jobs=n_jobs,
        random_state=random_state,
    )
    print(f"Permutation Importance 完了 ({time.time() - t0:.1f}秒)")

    df = pd.DataFrame({
        'feature': result['feature_columns'],
        'importance_mean': perm.importances_mean,
        'importance_std': perm.importances_std,
    }).sort_values('importance_mean', ascending=False).reset_index(drop=True)

    df.to_csv(output_dir / f"step2_permutation_importance_{result['name']}.csv",
               index=False, encoding='utf-8-sig')

    top_df = df.head(top_n)
    fig, ax = plt.subplots(figsize=(10, max(6, 0.3 * len(top_df))))
    ax.barh(top_df['feature'][::-1], top_df['importance_mean'][::-1],
            xerr=top_df['importance_std'][::-1])
    ax.set_xlabel(f'Permutation importance ({scoring}低下量)')
    ax.set_title(f'Permutation Importance Top {len(top_df)} ({result["name"]})')
    plt.tight_layout()
    plt.savefig(output_dir / f"step2_permutation_importance_{result['name']}.png", dpi=150)
    plt.show()
    plt.close(fig)

    print(f"\n上位{min(top_n, len(df))}特徴量（Permutation Importance）:")
    print(top_df.to_string(index=False))

    return df


# ============================================================
# 追加解析⑨: 物理量への再分類 (Step4)
# ============================================================

# 既定の物理カテゴリマッピング（tsfresh特徴量名のパターンベース）。
# meta特徴量（signal, duration, absolute_signal 等）は呼び出し側で
# extra_category_map として個別に渡すこと（rmb/rmc/baseline有無等で
# メタ特徴量の構成が異なるため、ここでは固定しない）。
DEFAULT_PHYSICAL_CATEGORY_PATTERNS = [
    (r'number_peaks|number_cwt_peaks|number_crossing_m', 'ピーク数'),
    (r'fft_coefficient|fft_aggregated|spkt_welch_density|cwt_coefficients', '周波数成分'),
    (r'autocorrelation|partial_autocorrelation|agg_autocorrelation|'
     r'time_reversal_asymmetry|(^|_)c3(_|$)', '自己相関'),
    (r'skewness|kurtosis|symmetry_looking|ratio_beyond_r_sigma', '波形非対称性'),
    (r'linear_trend|agg_linear_trend|first_location_of_maximum|last_location_of_maximum|'
     r'first_location_of_minimum|last_location_of_minimum|longest_strike', '立ち上がり・立ち下がり'),
    (r'variance|standard_deviation|mean_abs_change|mean_change|absolute_sum_of_changes|'
     r'variance_larger_than_standard_deviation|ratio_value_number_to_time_series_length|'
     r'mean_second_derivative', '局所変動'),
    (r'abs_energy|sum_values|root_mean_square|maximum|minimum|median|(^|_)mean(_|$)|'
     r'quantile|sum_of_reoccurring|large_standard_deviation|value_count', '電流強度'),
    (r'length', 'イベント時間'),
]


def categorize_feature_name(name, extra_category_map=None,
                             patterns=DEFAULT_PHYSICAL_CATEGORY_PATTERNS):
    """特徴量名を物理的カテゴリへマッピングする。該当なしは'その他'。

    extra_category_map: {'signal': '電流強度', 'wave_0': '局所変動', ...} のように、
                         パターンマッチでは拾えないメタ特徴量を個別指定する辞書。
                         呼び出し側（rmb/rmc）の特徴量構成に合わせて渡すこと。
    """
    if extra_category_map and name in extra_category_map:
        return extra_category_map[name]

    lname = str(name).lower()
    for pattern, category in patterns:
        if re.search(pattern, lname):
            return category
    return 'その他'


def build_physical_category_table(importance_df, feature_col='feature',
                                   importance_col='importance_mean',
                                   extra_category_map=None,
                                   title='重要度の物理カテゴリ別集計',
                                   output_path=None):
    """
    特徴量重要度の表に物理カテゴリ列を付与し、カテゴリ単位で重要度を集計・可視化する。
    """
    df = importance_df.copy()
    df['physical_category'] = df[feature_col].apply(
        lambda n: categorize_feature_name(n, extra_category_map=extra_category_map)
    )

    cat_summary = (
        df.groupby('physical_category')[importance_col]
        .agg(['sum', 'mean', 'count'])
        .sort_values('sum', ascending=False)
    )
    cat_summary.columns = ['importance_sum', 'importance_mean', 'n_features']

    print(f"\n----- {title} -----")
    print(cat_summary)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(cat_summary.index, cat_summary['importance_sum'])
    ax.set_ylabel('重要度合計')
    ax.set_title(title)
    plt.xticks(rotation=30, ha='right')
    plt.tight_layout()
    if output_path is not None:
        plt.savefig(output_path, dpi=150)
    plt.show()
    plt.close(fig)

    return df, cat_summary


# ============================================================
# 追加解析⑩: UMAPとの統合可視化 (Step5)
# ============================================================

def run_umap_integration(result, shap_result=None, top_feature=None,
                          top_n_shap_for_color=20,
                          n_neighbors=15, min_dist=0.1,
                          random_state=0, output_dir=None):
    """
    UMAP埋め込み上で、正分類/誤分類、真のクラス、SHAP値の高さ、
    特定物理特徴量の高さ、をそれぞれ色分けした4パネル図を作成する。

    result: train_xgb_classifier() / train_lgbm_classifier() 等が返す辞書
            （'X_test', 'X_test_raw', 'y_test', 'y_pred', 'class_names',
              'feature_columns', 'name' を持つこと）。
    shap_result: run_shap_analysis()に相当する解析結果の辞書（省略可）。
                 'sample_index', 'top_features_overall', 'shap_values_list' を
                 持つ場合、SHAP値でのサブサンプリング・色分けに利用する。
    """
    try:
        import umap
    except ImportError:
        print("⚠ umap-learn がインストールされていません。"
              "`pip install umap-learn --break-system-packages` を実行してください。"
              "Step5(UMAP統合)はスキップします。")
        return None

    output_dir = Path(output_dir) if output_dir else Path('.')
    output_dir.mkdir(parents=True, exist_ok=True)

    if shap_result is not None and 'sample_index' in shap_result:
        sidx = shap_result['sample_index']
        X_test_for_umap = result['X_test'][sidx]
        y_test = result['y_test'][sidx]
        y_pred = result['y_pred'][sidx]
        X_test_raw_for_umap = result['X_test_raw'].iloc[sidx]
    else:
        X_test_for_umap = result['X_test']
        y_test = result['y_test']
        y_pred = result['y_pred']
        X_test_raw_for_umap = result['X_test_raw']

    print(f"\n===== Step5: UMAP埋め込みを計算中 ({result['name']}, "
          f"n_samples={len(y_test)}) =====")
    t0 = time.time()
    reducer = umap.UMAP(n_neighbors=n_neighbors, min_dist=min_dist,
                         random_state=random_state)
    embedding = reducer.fit_transform(X_test_for_umap)
    print(f"UMAP計算 完了 ({time.time() - t0:.1f}秒)")

    class_names = result['class_names']
    correct_mask = (y_pred == y_test)

    shap_intensity = None
    if shap_result is not None:
        top_feats_for_color = shap_result['top_features_overall'][:top_n_shap_for_color]
        feature_idx = [result['feature_columns'].index(f) for f in top_feats_for_color]
        shap_values_list = shap_result['shap_values_list']
        shap_intensity = np.array([
            np.abs(shap_values_list[p][i, feature_idx]).sum()
            for i, p in enumerate(y_pred)
        ])

    if top_feature is None and shap_result is not None and shap_result.get('top_features_overall'):
        top_feature = shap_result['top_features_overall'][0]
    elif top_feature is None:
        top_feature = result['feature_columns'][0]

    feature_values_raw = None
    if top_feature in X_test_raw_for_umap.columns:
        feature_values_raw = X_test_raw_for_umap[top_feature].values

    fig, axes = plt.subplots(2, 2, figsize=(14, 12))

    ax = axes[0, 0]
    ax.scatter(embedding[correct_mask, 0], embedding[correct_mask, 1],
               c='tab:green', s=8, alpha=0.5, label='正分類')
    ax.scatter(embedding[~correct_mask, 0], embedding[~correct_mask, 1],
               c='tab:red', s=14, alpha=0.8, label='誤分類')
    ax.set_title('(a) 正分類 / 誤分類')
    ax.legend()

    ax = axes[0, 1]
    cmap = plt.get_cmap('tab10')
    for i, cname in enumerate(class_names):
        mask = (y_test == i)
        ax.scatter(embedding[mask, 0], embedding[mask, 1],
                   s=8, alpha=0.6, color=cmap(i % 10), label=str(cname))
    ax.set_title('(b) 真のクラス（分子種）')
    ax.legend(fontsize=8, ncol=2)

    ax = axes[1, 0]
    if shap_intensity is not None:
        sc = ax.scatter(embedding[:, 0], embedding[:, 1], c=shap_intensity,
                         cmap='viridis', s=10)
        plt.colorbar(sc, ax=ax, label=f'|SHAP|合計（上位{top_n_shap_for_color}特徴量, 予測クラス基準）')
        ax.set_title('(c) SHAP値が高いイベント')
    else:
        ax.text(0.5, 0.5, 'SHAP結果がないため非表示\n(先にSHAP分析を実行してください)',
                ha='center', va='center')
        ax.set_title('(c) SHAP値が高いイベント（データなし）')

    ax = axes[1, 1]
    if feature_values_raw is not None:
        sc = ax.scatter(embedding[:, 0], embedding[:, 1], c=feature_values_raw,
                         cmap='plasma', s=10)
        plt.colorbar(sc, ax=ax, label=f'{top_feature}（元スケール）')
        ax.set_title(f'(d) 特徴量「{top_feature}」が高いイベント')
    else:
        ax.text(0.5, 0.5, f'特徴量 {top_feature} が\nX_test_raw に見つかりません',
                ha='center', va='center')
        ax.set_title('(d) 特定特徴量が高いイベント（データなし）')

    for ax in axes.flat:
        ax.set_xlabel('UMAP-1')
        ax.set_ylabel('UMAP-2')

    plt.suptitle(f'UMAP統合可視化 ({result["name"]})', fontsize=14)
    plt.tight_layout()
    safe_name = re.sub(r'[^0-9A-Za-z]+', '_', result['name'])
    plt.savefig(output_dir / f"step5_umap_integration_{safe_name}.png", dpi=150)
    plt.show()
    plt.close(fig)

    return {
        'embedding': embedding,
        'correct_mask': correct_mask,
        'shap_intensity': shap_intensity,
        'color_feature': top_feature,
    }


# ============================================================
# 追加解析⑪: クラス別SHAP比較・誤分類分析 (Step3, run_umap_integration用)
# ============================================================
# run_shap_analysis()（waterfall/dependence plot中心）とは目的が異なる。
# こちらは「クラスごとに効いている特徴量が違うか」「正分類/誤分類でSHAPの
# 寄与がどう変わるか」を分析し、run_umap_integration()にそのまま渡せる
# 形式（shap_values_list, top_features_overall, sample_index等）で返す。

def _get_shap_values_per_class(explainer, X_df, n_classes):
    """shapのバージョン差異（list返却 / 3次元ndarray返却）を吸収するヘルパー"""
    shap_out = explainer.shap_values(X_df)

    if isinstance(shap_out, list):
        return shap_out

    shap_out = np.asarray(shap_out)
    if shap_out.ndim == 3:
        n_samples = X_df.shape[0]
        if shap_out.shape[0] == n_samples:
            return [shap_out[:, :, c] for c in range(shap_out.shape[2])]
        else:
            return [shap_out[c] for c in range(shap_out.shape[0])]

    return [shap_out]


def run_shap_class_comparison(result, top_n=20, output_dir=None, max_samples=2000,
                               model_for_shap=None):
    """
    上位特徴量について、
    ・値が高いほどどのクラス方向へ寄与するか（クラス別 summary plot）
    ・クラスごとに重要特徴量が異なるか（クラス間の上位特徴量比較）
    ・誤分類時に何が起きているか（正分類 vs 誤分類でのSHAP比較）
    を確認する。

    result         : train_xgb_classifier() / train_lgbm_classifier() 等の戻り値辞書。
    model_for_shap : SHAP計算に使うモデル。省略時は result['booster']（あれば）、
                     無ければ result['clf'] を使う。
                     XGBoost: 生のBoosterを渡すこと（result['booster']）。
                     LightGBM: LGBMClassifierをそのまま渡せる（result['clf']）。
    """
    output_dir = Path(output_dir) if output_dir else Path('.')
    output_dir.mkdir(parents=True, exist_ok=True)

    if model_for_shap is None:
        model_for_shap = result.get('booster', result['clf'])

    feature_columns = result['feature_columns']
    class_names = result['class_names']
    num_class = result['num_class']

    X_test = result['X_test']
    y_test = result['y_test']
    y_pred = result['y_pred']

    sample_index = np.arange(len(result['y_test']))
    if max_samples is not None and len(y_test) > max_samples:
        idx_sub, _ = train_test_split(
            np.arange(len(y_test)), train_size=max_samples,
            random_state=0, stratify=y_test,
        )
        X_test = X_test[idx_sub]
        y_test = y_test[idx_sub]
        y_pred = y_pred[idx_sub]
        sample_index = sample_index[idx_sub]
        print(f"[{result['name']}] SHAP分析: "
              f"テストデータを {len(result['y_test'])} → {len(y_test)} 件にサブサンプリングして計算します")

    X_test_df = pd.DataFrame(X_test, columns=feature_columns)

    print(f"\n===== Step3: SHAP分析 ({result['name']}, n_samples={len(y_test)}) =====")
    t0 = time.time()
    explainer = shap.TreeExplainer(model_for_shap)
    shap_values_list = _get_shap_values_per_class(explainer, X_test_df, num_class)
    print(f"SHAP値の計算 完了 ({time.time() - t0:.1f}秒)")

    mean_abs_per_class = pd.DataFrame(
        {class_names[c]: np.abs(shap_values_list[c]).mean(axis=0)
         for c in range(num_class)},
        index=feature_columns,
    )
    mean_abs_per_class['overall'] = mean_abs_per_class.mean(axis=1)
    mean_abs_per_class = mean_abs_per_class.sort_values('overall', ascending=False)
    mean_abs_per_class.to_csv(output_dir / f"step3_mean_abs_shap_{result['name']}.csv",
                               encoding='utf-8-sig')

    mean_abs_shap_overall = (
        mean_abs_per_class['overall']
        .reset_index()
        .rename(columns={'index': 'feature', 'overall': 'mean_abs_shap'})
    )

    top_features_overall = mean_abs_per_class.head(top_n).index.tolist()

    for c, cname in enumerate(class_names):
        try:
            plt.figure()
            shap.summary_plot(
                shap_values_list[c][:, [feature_columns.index(f) for f in top_features_overall]],
                X_test_df[top_features_overall],
                show=False,
            )
            plt.title(f'SHAP Summary - class "{cname}" ({result["name"]})')
            plt.tight_layout()
            safe_name = re.sub(r'[^0-9A-Za-z]+', '_', str(cname))
            plt.savefig(output_dir / f"step3_shap_summary_{safe_name}.png", dpi=150,
                        bbox_inches='tight')
            plt.show()
            plt.close()
        except Exception as e:
            print(f"⚠ クラス'{cname}'のSHAP summary_plot作成に失敗: {type(e).__name__}: {e}")

    print("\n----- クラスごとの上位特徴量（mean|SHAP| 上位10）-----")
    top_per_class = {}
    for cname in class_names:
        top10 = mean_abs_per_class[cname].sort_values(ascending=False).head(10)
        top_per_class[cname] = list(top10.index)
        print(f"[{cname}] {list(top10.index)}")

    common_across_classes = set.intersection(*[set(v) for v in top_per_class.values()]) \
        if top_per_class else set()
    print(f"\n全クラス共通で上位10に入る特徴量: {sorted(common_across_classes) if common_across_classes else 'なし'}")
    print("→ 上記が少ない/空の場合、クラスごとに『効いている特徴量』が大きく異なることを示唆します。")

    correct_mask = (y_pred == y_test)
    wrong_mask = ~correct_mask

    print(f"\n----- 誤分類分析（誤分類 {wrong_mask.sum()} / {len(y_test)} 件）-----")
    misclass_rows = []
    if wrong_mask.sum() > 0:
        for feat in top_features_overall:
            fi = feature_columns.index(feat)
            shap_pred_channel = np.array([
                shap_values_list[p][i, fi] for i, p in enumerate(y_pred)
            ])
            mean_correct = shap_pred_channel[correct_mask].mean() if correct_mask.sum() else np.nan
            mean_wrong = shap_pred_channel[wrong_mask].mean() if wrong_mask.sum() else np.nan
            misclass_rows.append({
                'feature': feat,
                'mean_shap_predicted_class_correct': mean_correct,
                'mean_shap_predicted_class_wrong': mean_wrong,
                'gap': (mean_wrong - mean_correct) if pd.notna(mean_correct) and pd.notna(mean_wrong) else np.nan,
            })

        misclass_df = pd.DataFrame(misclass_rows).sort_values('gap', key=lambda s: s.abs(),
                                                                ascending=False)
        misclass_df.to_csv(output_dir / f"step3_misclassification_shap_{result['name']}.csv",
                            index=False, encoding='utf-8-sig')
        print(misclass_df.to_string(index=False))
        print("\n→ gapの絶対値が大きい特徴量ほど、『正しく分類できた時』と『誤分類した時』とで、"
              "予測クラスへのSHAP寄与の仕方が異なっている＝誤分類の原因になっている可能性が高い。")
    else:
        misclass_df = pd.DataFrame(columns=['feature', 'mean_shap_predicted_class_correct',
                                             'mean_shap_predicted_class_wrong', 'gap'])
        print("誤分類サンプルがないため、誤分類分析は省略します。")

    return {
        'explainer': explainer,
        'shap_values_list': shap_values_list,
        'X_test_df': X_test_df,
        'mean_abs_per_class': mean_abs_per_class,
        'mean_abs_shap_overall': mean_abs_shap_overall,
        'top_features_overall': top_features_overall,
        'top_per_class': top_per_class,
        'misclassification_df': misclass_df,
        'correct_mask': correct_mask,
        'sample_index': sample_index,
    }


