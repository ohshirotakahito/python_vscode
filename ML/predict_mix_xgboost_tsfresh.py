# -*- coding: utf-8 -*-
"""
predict_mix_xgboost_tsfresh.py

【このスクリプトについて】
predict_mix_xgboost_traditional.py（rmb形式・f1〜f12の固定長特徴量版）の考え方を、
tsfresh特徴量（rmc形式）に対応させたもの。

  1. 純粋分子サンプル（例: Guanine, OxoG）のtsfresh特徴量で分類器を学習
  2. 学習済みモデル一式（Booster + StandardScaler + LabelEncoder + 使用した
     feature_columns）を保存し、学習に使ったmeta.csv/tsfresh_input.csvの
     「指紋」（サイズ+更新日時）が変わっていなければ次回以降は再学習をスキップ
     して再利用する。指紋が変わっていれば（＝別日の計測データで再抽出された）
     自動的に新しいバージョンとして再学習する（解析者が手動でバージョンを
     意識・指定する必要はない）。
     モデルは "models/rmc/xgboost/<smns>/<timestamp>/" に上書きせず毎回新規保存され、
     "models/rmc/xgboost/<smns>/latest.txt" が「今使うべき最新版」を指す。
     （results/ とは別ツリーにして、「毎回の実行結果（消しても再現可能）」と
      「学習済みモデルという資産（残すべきもの）」を分離している）
  3. 別途 extract_features_tsfresh.py で特徴量抽出しておいた「混合サンプル」
     （例: GO6MeG1-1）のtsfresh特徴量を読み込み、sample_name単位
     （rmb版の"file"に相当）でグループ化して各クラスの割合(%)を予測・保存

【前提条件（実行前に必ず済ませておくこと）】
このスクリプトは tsfresh特徴量の「抽出」は行わない（読み込むだけ）。
そのため、学習に使う純粋分子(smns)と、予測したい混合サンプル(test_smns)の
両方について、事前に extract_features_tsfresh.py の `samples` リストに名前を
追加して実行し、data/features/rmc/ 以下に
    <sample>_10k_Sample_ANAL_meta.csv
    <sample>_10k_Sample_ANAL_tsfresh_input.csv
を生成しておく必要がある。
（例: samples = ['Guanine', 'OxoG', 'GO6MeG1-1']）

【スケーリングについて（rmb版からの改良点）】
train_xgboost_tsfresh.py の learn_dataset() は preprocessing.scale(x) をその場で
呼んでおり、呼び出すたびにその場のデータの平均・標準偏差で標準化される。
これは学習単体では問題ないが、「学習データとは別のサンプル（混合サンプル）に
同じ基準で適用する」という今回の用途には使えない
（混合サンプル自身の平均・標準偏差で再標準化されてしまい、学習時に学習した
 判断基準とズレてしまうため）。
そのため本スクリプトでは StandardScaler を学習データのみで fit し、
その基準（mean/std）を保存 → 混合サンプル側は transform のみを適用する。

【pmax（予測確率の最大値）について】
objective を multi:softmax → multi:softprob に変更し、各イベントについて
「一番確率が高かったクラスの、その確率値そのもの」＝pmax を計算できるようにした。
→ 予測結果（比率集計）は常に2種類のCSVを出力する:
   predict_{smn}_all.csv      : 従来通り全イベントで比率計算（+pmaxの統計列を追加しただけ）
   predict_{smn}_highconf.csv : pmax >= PMAX_THRESHOLD のイベントだけに絞って比率を再計算
→ all版の結果は今まで通りで変更なし。highconf版はあくまで追加の参考情報。

【使わなかったモジュール／機能】
train_xgboost_tsfresh.py にある SHAP分析・PCA/UMAP・統計検定・クラス間距離解析などは
「純粋分子同士の解釈」のための機能であり、今回の「混合サンプルの比率推定」には
直接使わないため、本スクリプトには含めていない
（必要であれば train_xgboost_tsfresh.py 側で別途分析してください）。
"""

from __future__ import annotations

import os
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import f1_score
from imblearn.under_sampling import RandomUnderSampler

from tsfresh.feature_extraction import MinimalFCParameters

import common.paths as paths
import common.data_pipeline as dp
from common.eval_viz import conmtx

# =====================
# 設定
# =====================
FEATURE_SET = "rmc"
ALGORITHM = "xgboost"

RANDOM_STATE = 0
TEST_SIZE = 0.2

# 既にモデルがあっても強制的に再学習したい場合は True にする
RETRAIN = False

DATA_ROOT = paths.feature_dir(FEATURE_SET)
CACHE_DIR = paths.cache_dir(FEATURE_SET)

N_JOBS = 4
CHUNKSIZE = 50

# 重要: 学習時と混合サンプル予測時で必ず同じ fc_parameters を使うこと。
# 変えると tsfresh特徴量の列名・列数が変わり、保存済みモデルと整合しなくなる。
FC_PARAMETERS = MinimalFCParameters()

USE_TSFRESH_FEATURE_SELECTION = True

# train_xgboost_tsfresh.py と同じメタ特徴量セット
WAVE_COLUMNS = [f"wave_{i}" for i in range(12)]
META_FEATURE_COLUMNS = ["absolute_signal", "relative_signal", "duration"] + WAVE_COLUMNS

# common/data_pipeline.py 側の設定にも反映
dp.DATA_ROOT = DATA_ROOT
dp.CACHE_DIR = CACHE_DIR
dp.N_JOBS = N_JOBS
dp.CHUNKSIZE = CHUNKSIZE
dp.FC_PARAMETERS = FC_PARAMETERS

XGB_PARAMS = {
    "max_depth": 6,
    "eta": 0.2,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "min_child_weight": 1,
    "lambda": 1.0,
    "alpha": 0.0,
    "eval_metric": "mlogloss",
}
XGB_NUM_ROUND = 300

# high-confidence とみなすpmax（予測確率の最大値）の閾値
# 0.8 = 一般的な目安。厳しめにしたい場合は 0.9 などに変更する。
PMAX_THRESHOLD = 0.8


@dataclass
class TrainArtifacts:
    bst: xgb.Booster
    scaler: StandardScaler
    label_encoder: LabelEncoder
    class_names: List[str]
    feature_columns: List[str]
    X_test: Optional[np.ndarray]
    y_test: Optional[np.ndarray]


# =====================
# モデルの保存・再利用（指紋照合による自動バージョン管理）
# =====================
def model_family_dir(smns: List[str]):
    """smns の組み合わせごとの固定フォルダ（バージョンをまたいだ親フォルダ）を返す。

    results/ とは別ツリーの models/ 配下（common/paths.model_dir）に保存する。
    """
    tag = "-".join(smns)
    return paths.model_dir(FEATURE_SET, ALGORITHM, tag)


def _fp(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    st = path.stat()
    return f"{st.st_size}_{int(st.st_mtime)}"


def _source_fingerprints(smns: List[str]) -> Dict[str, Dict[str, Optional[str]]]:
    """学習に使う各サンプルの meta.csv / tsfresh_input.csv の指紋
    （サイズ_更新日時）を計算する。

    別日に再計測してextract_features_tsfresh.pyを再実行すると、これらのファイルの
    中身・更新日時が変わるため、保存済みモデルの指紋と比較するだけで
    「データが変わったかどうか」を自動判定できる（解析者が手動でバージョンを
    指定・管理する必要がない）。
    """
    fps: Dict[str, Dict[str, Optional[str]]] = {}
    for smn in smns:
        meta_path, tsfresh_path = dp._sample_paths(smn, data_root=DATA_ROOT)
        fps[smn] = {
            "meta": _fp(meta_path),
            "tsfresh_input": _fp(tsfresh_path),
        }
    return fps


def save_artifacts(art: TrainArtifacts, smns: List[str],
                    fingerprints: Dict[str, Dict[str, Optional[str]]]) -> Path:
    """モデル一式を新しいバージョンフォルダに保存し、latest.txtを更新する。

    既存バージョンを上書きしない（常に新規タイムスタンプのフォルダに保存する）ため、
    過去に学習したモデルの履歴がそのまま残る。
    """
    family_dir = model_family_dir(smns)
    version = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = family_dir / version
    d.mkdir(parents=True, exist_ok=True)

    art.bst.save_model(str(d / "bst.json"))
    joblib.dump(art.scaler, d / "scaler.pkl")
    joblib.dump(art.label_encoder, d / "label_encoder.pkl")
    joblib.dump(art.class_names, d / "class_names.pkl")
    # 学習時に実際に使った特徴量の並び（tsfresh特徴量選択の結果を含む）を保存。
    # 混合サンプル側の予測時はこのリストと完全一致する列を使う必要がある。
    joblib.dump(art.feature_columns, d / "feature_columns.pkl")

    manifest = {
        "trained_at": version,
        "smns": smns,
        "source_fingerprints": fingerprints,
        "fc_parameters": type(FC_PARAMETERS).__name__,
        "xgb_params": {**XGB_PARAMS, "objective": "multi:softprob", "num_class": len(art.class_names)},
        "num_boost_round": XGB_NUM_ROUND,
        "pmax_threshold": PMAX_THRESHOLD,
        "n_feature_columns": len(art.feature_columns),
    }
    with open(d / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    (family_dir / "latest.txt").write_text(version, encoding="utf-8")

    print(f"[SAVE] モデルを新規バージョンとして保存しました: {d}")
    return d


def load_artifacts(smns: List[str]) -> Optional[TrainArtifacts]:
    """latest.txtが指すバージョンを読み込み、学習データの指紋が今と一致するか確認する。

    - latest.txtが無い（一度も学習していない） → None（学習が必要）
    - 指紋が一致しない（元データが変わった）    → None（自動的に再学習させる）
    - 指紋が一致する                          → 既存モデルをそのまま再利用
    """
    family_dir = model_family_dir(smns)
    latest_file = family_dir / "latest.txt"
    if not latest_file.exists():
        return None

    version = latest_file.read_text(encoding="utf-8").strip()
    d = family_dir / version
    manifest_path = d / "manifest.json"
    if not d.exists() or not manifest_path.exists():
        print(f"[WARN] latest.txtが指すバージョン {version} が見つかりません。再学習します。")
        return None

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    saved_fps = manifest.get("source_fingerprints", {})
    current_fps = _source_fingerprints(smns)

    if saved_fps != current_fps:
        print("[INFO] 学習データ(meta.csv/tsfresh_input.csv)が保存時から変更されているため、"
              "自動的に再学習します。")
        print(f"       保存時の指紋: {saved_fps}")
        print(f"       現在の指紋　: {current_fps}")
        return None

    bst = xgb.Booster()
    bst.load_model(str(d / "bst.json"))
    scaler = joblib.load(d / "scaler.pkl")
    label_encoder = joblib.load(d / "label_encoder.pkl")
    class_names = joblib.load(d / "class_names.pkl")
    feature_columns = joblib.load(d / "feature_columns.pkl")

    print(f"[LOAD] 既存モデルを再利用します（学習データの変更なしを確認）: {d}")
    return TrainArtifacts(
        bst=bst,
        scaler=scaler,
        label_encoder=label_encoder,
        class_names=class_names,
        feature_columns=feature_columns,
        X_test=None,
        y_test=None,
    )


# =====================
# 学習
# =====================
def train(smns: List[str]) -> TrainArtifacts:
    """純粋分子サンプル(smns)のtsfresh特徴量を読み込み→前処理→学習。"""
    dnf, tsfresh_feature_cols = dp.build_combined_dataset(
        smns, data_root=DATA_ROOT, use_cache=True,
        n_jobs=N_JOBS, chunksize=CHUNKSIZE
    )

    # --- クラス数チェック（tsfreshの特徴量選択はクラスが2種類以上ないと
    #     内部でAssertionErrorになり原因が分かりにくいため、その前に確認する） ---
    y_raw_check = [s.split("_")[0] for s in dnf["sample"]]
    label_counts = Counter(y_raw_check)
    print(f"クラスごとの行数（フィルタ後）: {dict(label_counts)}")
    if len(label_counts) < 2:
        raise RuntimeError(
            "学習データに含まれるクラスが1種類しかありません "
            f"(検出されたクラス: {list(label_counts.keys())}, 指定した smns: {smns})。\n"
            "extract_features_tsfresh.py で両方のサンプルが正しく抽出・保存されているか、"
            f"{DATA_ROOT} 以下に両方の *_meta.csv / *_tsfresh_input.csv が"
            "存在するか確認してください。"
        )

    if USE_TSFRESH_FEATURE_SELECTION:
        tsfresh_feature_cols = dp.apply_tsfresh_feature_selection(
            dnf, tsfresh_feature_cols, n_jobs=N_JOBS, chunksize=CHUNKSIZE
        )

    feature_columns = META_FEATURE_COLUMNS + tsfresh_feature_cols
    print(f"学習に使う特徴量の総数: {len(feature_columns)} "
          f"(メタ特徴量 {len(META_FEATURE_COLUMNS)} + tsfresh特徴量 {len(tsfresh_feature_cols)})")

    y_raw = [s.split("_")[0] for s in dnf["sample"]]
    X_raw = dnf[feature_columns]

    le = LabelEncoder().fit(y_raw)
    y = le.transform(y_raw)
    class_names = list(le.classes_)
    num_class = len(class_names)

    # --- スケーラー：train のみで fit（混合サンプル予測時はこの基準をtransformのみ適用） ---
    scaler = StandardScaler()

    X_train_raw, X_test_raw, y_train, y_test = train_test_split(
        X_raw, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )

    X_train = scaler.fit_transform(X_train_raw)
    X_test = scaler.transform(X_test_raw)

    # --- クラス不均衡対応：Undersampling（train のみ） ---
    class_counts = Counter(y_train)
    min_count = min(class_counts.values())
    rus = RandomUnderSampler(
        random_state=RANDOM_STATE,
        sampling_strategy={k: min_count for k in class_counts.keys()}
    )
    X_train_bal, y_train_bal = rus.fit_resample(X_train, y_train)

    dtrain = xgb.DMatrix(X_train_bal, label=y_train_bal)
    params = dict(XGB_PARAMS)
    # multi:softmax（クラスラベルのみ返す）から multi:softprob（各クラスの
    # 確率を返す）に変更。pmax（予測確率の最大値）を計算するために必須。
    params["objective"] = "multi:softprob"
    params["num_class"] = num_class

    bst = xgb.train(params, dtrain, num_boost_round=XGB_NUM_ROUND)

    return TrainArtifacts(
        bst=bst,
        scaler=scaler,
        label_encoder=le,
        class_names=class_names,
        feature_columns=feature_columns,
        X_test=X_test,
        y_test=y_test,
    )


# =====================
# 評価（common/eval_viz.conmtx を利用）
# =====================
def evaluate(art: TrainArtifacts, run_dir) -> Optional[float]:
    if art.X_test is None or art.y_test is None:
        print("[SKIP] 保存済みモデルをロードしたため評価はスキップします"
              "（再学習した場合のみ X_test/y_test が存在します）")
        return None

    dtest = xgb.DMatrix(art.X_test)
    probs = art.bst.predict(dtest)          # shape: (n_samples, num_class)
    y_pred = probs.argmax(axis=1)
    pmax = probs.max(axis=1)                # 各イベントの「一番確率が高かったクラスの確率値」

    _MX, _N_MX, _report = conmtx(art.y_test, y_pred, art.label_encoder, save_dir=run_dir)

    f1 = f1_score(art.y_test, y_pred, average="micro")
    print(f"micro-F1（全イベント）: {f1:.4f}")

    # --- pmaxによる高信頼度サブセットの参考指標（絞り込みはしない、表示のみ） ---
    high_mask = pmax >= PMAX_THRESHOLD
    high_ratio = high_mask.mean() * 100
    print(f"pmax >= {PMAX_THRESHOLD} のイベント割合: {high_ratio:.1f}%")
    if high_mask.sum() > 0:
        f1_high = f1_score(art.y_test[high_mask], y_pred[high_mask], average="micro")
        print(f"micro-F1（pmax >= {PMAX_THRESHOLD} のイベントのみ）: {f1_high:.4f}")

    return f1


# =====================
# 予測（混合サンプルのsample_name単位の比率集計）
# =====================
def _aggregate_all(event_df: pd.DataFrame, smns: List[str],
                    id2name: Dict[int, str], threshold: float) -> pd.DataFrame:
    """従来通り全イベントで比率計算。pmaxの統計列（平均・高信頼度割合）を追加するだけで、
    絞り込み（除外）は一切行わない。"""
    rows = []
    for name, g in event_df.groupby("sample_name"):
        total = len(g)
        counts = Counter(g["pred_class"])

        row: Dict[str, object] = {"file": name, "count": total}
        for cls in smns:
            row[cls] = 0.0
        for cls_id, cnt in counts.items():
            cls_name = id2name.get(cls_id, str(cls_id))
            if cls_name in row:
                row[cls_name] = round(cnt / total * 100.0, 2)

        row["mean_pmax"] = round(float(g["pmax"].mean()), 4)
        n_high = int((g["pmax"] >= threshold).sum())
        row["high_confidence_pct"] = round(n_high / total * 100.0, 2)
        rows.append(row)

    cols = ["file", "count"] + smns + ["mean_pmax", "high_confidence_pct"]
    return pd.DataFrame(rows, columns=cols)


def _aggregate_highconf(event_df: pd.DataFrame, smns: List[str],
                         id2name: Dict[int, str], threshold: float) -> pd.DataFrame:
    """pmax >= threshold のイベントだけに絞り込んで比率を再計算した参考版。"""
    rows = []
    for name, g_all in event_df.groupby("sample_name"):
        total_original = len(g_all)
        g = g_all[g_all["pmax"] >= threshold]
        n_used = len(g)

        row: Dict[str, object] = {
            "file": name, "count": n_used, "count_excluded": total_original - n_used
        }
        for cls in smns:
            row[cls] = 0.0

        if n_used > 0:
            counts = Counter(g["pred_class"])
            for cls_id, cnt in counts.items():
                cls_name = id2name.get(cls_id, str(cls_id))
                if cls_name in row:
                    row[cls_name] = round(cnt / n_used * 100.0, 2)
            row["mean_pmax"] = round(float(g["pmax"].mean()), 4)
        else:
            row["mean_pmax"] = np.nan

        rows.append(row)

    cols = ["file", "count", "count_excluded"] + smns + ["mean_pmax"]
    return pd.DataFrame(rows, columns=cols)


def predict_grouped(mix_smn: str, art: TrainArtifacts, smns: List[str],
                     threshold: float = PMAX_THRESHOLD):
    """混合サンプル(mix_smn)のtsfresh特徴量を読み込み、sample_name（tdmsファイル単位、
    rmb版の"file"に相当）ごとに予測クラスの割合(%)を集計する。

    事前に extract_features_tsfresh.py で mix_smn を抽出済みであること
    （data/features/rmc/ に mix_smn の meta.csv / tsfresh_input.csv が必要）。

    戻り値: (all_df, highconf_df) のタプル。
      all_df      : 従来通り全イベントで比率計算（+pmax統計列を追加）
      highconf_df : pmax >= threshold のイベントだけに絞った参考版
    """
    # dp.build_combined_dataset は元々複数smnsの結合用だが、要素数1で渡せば
    # 単一サンプル（混合サンプル）のtsfresh特徴量抽出・キャッシュにもそのまま使える
    combined_mix, _ = dp.build_combined_dataset(
        [mix_smn], data_root=DATA_ROOT, use_cache=True,
        n_jobs=N_JOBS, chunksize=CHUNKSIZE
    )

    missing_cols = [c for c in art.feature_columns if c not in combined_mix.columns]
    if missing_cols:
        raise RuntimeError(
            f"[{mix_smn}] 学習時に使った特徴量が見つかりません: {missing_cols}\n"
            "fc_parameters（FC_PARAMETERSの設定）が学習時と一致しているか確認してください。"
        )

    X_raw = combined_mix[art.feature_columns]
    X = art.scaler.transform(X_raw)

    dtest = xgb.DMatrix(X)
    probs = art.bst.predict(dtest)          # shape: (n_events, num_class)
    y_pred = probs.argmax(axis=1)
    pmax = probs.max(axis=1)

    id2name = {i: c for i, c in enumerate(art.class_names)}

    event_df = pd.DataFrame({
        "sample_name": combined_mix["sample_name"].values,
        "pred_class": y_pred,
        "pmax": pmax,
    })

    all_df = _aggregate_all(event_df, smns, id2name, threshold)
    highconf_df = _aggregate_highconf(event_df, smns, id2name, threshold)
    return all_df, highconf_df


def save_group_predictions(test_smns: List[str], art: TrainArtifacts,
                            smns: List[str], run_dir,
                            threshold: float = PMAX_THRESHOLD) -> None:
    for tsmn in test_smns:
        all_df, highconf_df = predict_grouped(tsmn, art, smns, threshold=threshold)

        if not all_df.empty:
            out_path = run_dir / f"predict_{tsmn}_all.csv"
            all_df.to_csv(out_path, index=False)
            print(f"[SAVE] {out_path}")

        if not highconf_df.empty:
            out_path = run_dir / f"predict_{tsmn}_highconf.csv"
            highconf_df.to_csv(out_path, index=False)
            print(f"[SAVE] {out_path} (pmax >= {threshold})")


# =====================
# main
# =====================
if __name__ == "__main__":
    # 学習に使う純粋分子クラス
    smns = ["Guanine", "OMeG"]

    # 比率を推定したい混合サンプル
    # ※事前に extract_features_tsfresh.py の samples リストにこの名前も加えて
    #   実行し、data/features/rmc/ に meta.csv / tsfresh_input.csv を
    #   生成しておくこと
    test_smns = ["GO6MeG1-1"]

    run_dir, run_ts = paths.new_run(FEATURE_SET, ALGORITHM, smns=smns, run_type='mix')

    # --- モデルの取得（学習データの指紋が一致すれば再利用、変わっていれば自動再学習） ---
    artifacts = None if RETRAIN else load_artifacts(smns)
    model_version_dir = None

    if artifacts is None:
        print(f"[TRAIN] モデルを新規学習します: {smns}")
        fingerprints = _source_fingerprints(smns)
        artifacts = train(smns)
        model_version_dir = save_artifacts(artifacts, smns, fingerprints)
        evaluate(artifacts, run_dir)
    else:
        print(f"[SKIP] 既存モデルを再利用します（再学習なし）: {smns}")
        model_version_dir = model_family_dir(smns) / (
            (model_family_dir(smns) / "latest.txt").read_text(encoding="utf-8").strip()
        )

    # --- 今回の実行でどのモデルバージョンを使ったかを記録（トレーサビリティ） ---
    (run_dir / "used_model_version.txt").write_text(str(model_version_dir), encoding="utf-8")

    # --- 予測（混合サンプルのsample_name単位比率集計） ---
    save_group_predictions(test_smns, artifacts, smns, run_dir)

    print("end")
