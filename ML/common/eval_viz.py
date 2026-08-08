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

    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(MX, annot=True, fmt="d", cmap="YlGnBu", annot_kws={'size': 12})
    ax.set_title('Confusion Matrix')
    ax.set_xlabel('Predicted Label')
    ax.set_ylabel('True Label')
    plt.tight_layout()
    plt.savefig(out_dir / 'confusion_matrix.png', dpi=150)
    plt.show()

    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(N_MX, annot=True, fmt="1.1f", cmap="YlGnBu", annot_kws={'size': 12})
    ax.set_title('Normalized Confusion Matrix (%)')
    ax.set_xlabel('Predicted Label')
    ax.set_ylabel('True Label')
    plt.tight_layout()
    plt.savefig(out_dir / 'confusion_matrix_normalized.png', dpi=150)
    plt.show()

    if save_dir:
        MX.to_csv(out_dir / 'confusion_matrix.csv')
        N_MX.to_csv(out_dir / 'confusion_matrix_normalized.csv')
        (out_dir / 'classification_report.txt').write_text(report, encoding='utf-8')

    return MX, N_MX, report
