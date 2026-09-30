# -*- coding: utf-8 -*-
"""
common/eval_viz.py

【このモジュールについて】
LightGBM版・XGBoost版で共通して使える評価・可視化関数を集めたもの。

なお、plot_feature_importance / learn_dataset / run_shap_analysis は
LightGBM（sklearn API: clf.feature_importances_）と
XGBoost（Booster API: clf.get_score()）とでモデルオブジェクトの
扱い方自体が異なるため、無理に共通化せず各スクリプト側に残している。
（共通化すると分岐だらけになり、かえって可読性が落ちるため）
"""

from pathlib import Path

import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix, classification_report


def _confusion_matrix_style(matrix):
    """Return a readable figure size and annotation size for a matrix."""
    rows, columns = matrix.shape
    largest_dimension = max(rows, columns, 1)

    # Keep small matrices compact, while giving larger matrices enough room.
    figure_width = min(20.0, max(6.5, 3.0 + columns * 1.05))
    figure_height = min(18.0, max(5.5, 2.5 + rows * 0.90))

    # Long values need a little more room than one- or two-digit values.
    values = matrix.to_numpy()
    max_chars = max((len(f"{value:.1f}") for value in values.flat), default=1)
    annotation_size = min(24.0, max(8.0, 27.0 - 1.6 * largest_dimension))
    annotation_size *= min(1.0, 4.0 / max_chars)

    tick_size = min(12.0, max(7.0, 14.0 - 0.55 * largest_dimension))
    return (figure_width, figure_height), annotation_size, tick_size


def _plot_confusion_matrix(matrix, title, output_path, fmt):
    """Plot one confusion-matrix DataFrame with class-count-aware sizing."""
    figsize, annotation_size, tick_size = _confusion_matrix_style(matrix)
    fig, ax = plt.subplots(figsize=figsize)
    sns.heatmap(
        matrix,
        annot=True,
        fmt=fmt,
        cmap="YlGnBu",
        ax=ax,
        square=True,
        annot_kws={'fontsize': annotation_size, 'fontweight': 'bold'},
        cbar_kws={'shrink': 0.85},
    )
    ax.set_title(title)
    ax.set_xlabel('Predicted Label')
    ax.set_ylabel('True Label')
    ax.tick_params(axis='both', labelsize=tick_size)
    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha='right')
    ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches='tight')
    plt.close(fig)


def plot_confusion_matrices(MX, N_MX, save_dir):
    """Render count and normalized confusion matrices without model training."""
    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _plot_confusion_matrix(
        MX, 'Confusion Matrix', out_dir / 'confusion_matrix.png', 'd'
    )
    _plot_confusion_matrix(
        N_MX,
        'Normalized Confusion Matrix (%)',
        out_dir / 'confusion_matrix_normalized.png',
        '.1f',
    )


def redraw_confusion_matrices(run_dir):
    """Recreate the PNG files from CSV files already saved in a run directory."""
    run_dir = Path(run_dir)
    matrix_path = run_dir / 'confusion_matrix.csv'
    normalized_path = run_dir / 'confusion_matrix_normalized.csv'
    if not matrix_path.is_file() or not normalized_path.is_file():
        raise FileNotFoundError(
            f"Confusion-matrix CSV files were not found in: {run_dir}"
        )

    matrix = pd.read_csv(matrix_path, index_col=0).astype(int)
    normalized = pd.read_csv(normalized_path, index_col=0).astype(float)
    plot_confusion_matrices(matrix, normalized, run_dir)


def conmtx(y_test, y_pred, le, save_dir=None):
    """混同行列（実数・正規化%）を作成・可視化する。

    save_dirを指定するとそのフォルダにPNG画像・CSV・レポートテキストを保存する
    （未指定ならカレントディレクトリにPNGのみ保存する）。
    """
    class_names = list(le.classes_)

    mtx = confusion_matrix(y_test, y_pred, labels=range(len(class_names)))

    mtx_index = [f'Actual_{name}' for name in class_names]
    mtx_columns = [f'Pred_{name}' for name in class_names]

    MX = pd.DataFrame(mtx, index=mtx_index, columns=mtx_columns)

    n_mtx = (mtx.astype('float') / mtx.sum(axis=1)[:, None]) * 100
    N_MX = pd.DataFrame(n_mtx, index=mtx_index, columns=mtx_columns)

    report = classification_report(y_test, y_pred, target_names=class_names)
    print(report)

    out_dir = Path(save_dir) if save_dir else Path('.')

    plot_confusion_matrices(MX, N_MX, out_dir)

    if save_dir:
        MX.to_csv(out_dir / 'confusion_matrix.csv')
        N_MX.to_csv(out_dir / 'confusion_matrix_normalized.csv')
        (out_dir / 'classification_report.txt').write_text(report, encoding='utf-8')

    return MX, N_MX, report
