# -*- coding: utf-8 -*-
"""
predict_mix_xgboost_traditional (common/ 対応版)

【前バージョン（20250903）からの変更点】
1. common/paths.py を使って保存先を一元管理
   results/rmb/xgboost/<timestamp>_<smns>/ 配下に、
   その回の評価結果（混同行列・レポート）と予測CSVをまとめて保存する。
   （学習済みモデル自体は run_dir とは別に、smns の組み合わせごとの
    固定パスに保存する。run_dir は毎回タイムスタンプ付きで変わるが、
    モデルは "OMeG-OxoG" のような組み合わせが同じ限り使い回すため）

2. common/eval_viz.py の conmtx() を使って評価・可視化を統一
   （classification_report の出力・CSV保存も自動で行われるようになった）

3. 学習済みモデル一式（Booster + StandardScaler + LabelEncoder + class_names）を
   保存・再利用できるようにした。
   → 学習に使った.npyファイルの「指紋」（サイズ+更新日時）を保存しておき、
      次回実行時に現在の指紋と自動比較する。一致すれば再学習せず既存モデルを
      再利用し、変わっていれば自動的に新しいバージョンとして再学習する
      （解析者が手動でバージョンを意識・指定する必要はない）。
   → モデルは "models/rmb/xgboost/<smns>/<timestamp>/" に**上書きせず毎回新規保存**され、
      "models/rmb/xgboost/<smns>/latest.txt" が「今使うべき最新版」を指す。
      過去のバージョンもすべてフォルダに残るため、履歴として参照できる。
      （results/ とは別ツリーにして、「毎回の実行結果（消しても再現可能）」と
       「学習済みモデルという資産（残すべきもの）」を分離している）
   → 閾値やXGBoostパラメータを変えて強制的に再学習したい場合は RETRAIN = True にする。

4. pmax（予測確率の最大値）を追加した。
   objective を multi:softmax → multi:softprob に変更し、各イベントについて
   「一番確率が高かったクラスの、その確率値そのもの」＝pmax を計算できるようにした。
   → 予測結果（比率集計）は常に2種類のCSVを出力する:
      predict_{smn}_all.csv      : 従来通り全イベントで比率計算（+pmaxの統計列を追加しただけ）
      predict_{smn}_highconf.csv : pmax >= PMAX_THRESHOLD のイベントだけに絞って比率を再計算
   → all版の結果は今まで通りで変更なし。highconf版はあくまで追加の参考情報。
   → PMAX_THRESHOLD は下の設定セクションで変更可能。

【あえて使わなかったモジュール】
- common/data_pipeline.py: tsfresh特徴量（parquet形式のmeta/tsfresh_input）を
  読み込むためのモジュールで、本スクリプトが読む rmb 形式の .npy
  （f1〜f12の固定長特徴量）とはデータ構造が異なるため、今回は不使用。
- common/tdms_io.py: tdms生波形から .npy を新規生成するモジュールで、
  本スクリプトは既存の .npy を読むだけのため不使用。
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

import common.paths as paths
import common.eval_viz as eval_viz

# =====================
# 設定
# =====================
FEATURE_SET = "rmb"     # common/paths.py の命名規則に合わせる（データ本体の系統名）
ALGORITHM = "xgboost"

RANDOM_STATE = 0
TEST_SIZE = 0.2

# 既にモデルがあっても強制的に再学習したい場合は True にする
RETRAIN = False

# データ本体の場所（旧: "ax_data" 固定 → common/paths.py 経由に統一）
DATA_DIR = paths.feature_dir(FEATURE_SET)

# しきい値（条件選択）
ST_RANGE = (10, 1000)       # signal_time
SB_RANGE = (-300, 300)      # signal_baseline
SI_RANGE = (10, 1000)       # signal_intensity

# XGBoost パラメータ
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

# 入出力カラム
# 注意: common/tdms_io.py の apick() は event_id を先頭に付与するようになったため
#       （旧rmb.pyの出力は22列だったが、統合後は event_id 分が増えて23列になる）、
#       ここでも先頭に "event_id" を追加して実データの列数と合わせている。
COLUMNS = [
    "event_id",
    "file", "Ex_ID", "distance", "sample",
    "signal_position", "signal_intensity", "signal_time",
    "signal_start", "signal_end", "signal_baseline",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"
]

# common/data_pipeline.py（rmc/tsfresh側）と同じ考え方に合わせ、
# signal_baseline を直接特徴量として使うのをやめ、
#   relative_signal = signal_intensity（ベースラインからの相対値）
#   absolute_signal = signal_intensity + signal_baseline（ベースラインを足し戻した絶対値）
# の2つの派生特徴量を使う（load_and_filter()内で計算して追加する）。
FEATURES = [
    "relative_signal", "absolute_signal", "signal_time",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"
]


# =====================
# データ読み込み・フィルタ
# =====================
def load_and_filter(path: str) -> pd.DataFrame:
    """NumPy(np.load) -> DataFrame化し、物理量の範囲でフィルタリング。"""
    datum = np.load(path, allow_pickle=True)
    df = pd.DataFrame(data=datum, columns=COLUMNS)

    df["signal_time"] = df["signal_time"].astype(float)
    df["signal_baseline"] = df["signal_baseline"].astype(float)
    df["signal_intensity"] = df["signal_intensity"].astype(float)

    st_lo, st_hi = ST_RANGE
    sb_lo, sb_hi = SB_RANGE
    si_lo, si_hi = SI_RANGE

    m = (
        (df["signal_time"] > st_lo) & (df["signal_time"] < st_hi) &
        (df["signal_baseline"] > sb_lo) & (df["signal_baseline"] < sb_hi) &
        (df["signal_intensity"] > si_lo) & (df["signal_intensity"] < si_hi)
    )
    df = df.loc[m].reset_index(drop=True)

    # --- 派生特徴量の計算（common/data_pipeline.load_meta_data()と同じ定義） ---
    df["relative_signal"] = df["signal_intensity"]
    df["absolute_signal"] = df["signal_intensity"] + df["signal_baseline"]

    return df


@dataclass
class TrainArtifacts:
    bst: xgb.Booster
    scaler: StandardScaler
    label_encoder: LabelEncoder
    X_test: Optional[np.ndarray]
    y_test: Optional[np.ndarray]
    class_names: List[str]


# =====================
# モデルの保存・再利用（指紋照合による自動バージョン管理）
# =====================
def model_family_dir(smns: List[str]):
    """smns の組み合わせごとの固定フォルダ（バージョンをまたいだ親フォルダ）を返す。

    results/ とは別ツリーの models/ 配下（common/paths.model_dir）に保存する。
    """
    tag = "-".join(smns)
    return paths.model_dir(FEATURE_SET, ALGORITHM, tag)


def _source_fingerprints(smns: List[str]) -> Dict[str, Optional[str]]:
    """学習に使う各サンプルの.npyファイルの指紋（サイズ_更新日時）を計算する。

    別日に再計測してextract_features_traditional.pyを再実行すると.npyの中身・更新日時が
    変わるため、この指紋を保存済みモデルのものと比較するだけで
    「データが変わったかどうか」を自動判定できる（解析者が手動でバージョンを
    指定・管理する必要がない）。
    """
    fps: Dict[str, Optional[str]] = {}
    for smn in smns:
        sam = f"{smn}_10k_Sample_ANAL_rmb"
        npy_path = os.path.join(DATA_DIR, f"{sam}.npy")
        if os.path.exists(npy_path):
            st = os.stat(npy_path)
            fps[smn] = f"{st.st_size}_{int(st.st_mtime)}"
        else:
            fps[smn] = None
    return fps


def save_artifacts(art: TrainArtifacts, smns: List[str],
                    fingerprints: Dict[str, Optional[str]]) -> Path:
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

    manifest = {
        "trained_at": version,
        "smns": smns,
        "source_fingerprints": fingerprints,
        "xgb_params": {**XGB_PARAMS, "objective": "multi:softprob", "num_class": len(art.class_names)},
        "num_boost_round": XGB_NUM_ROUND,
        "pmax_threshold": PMAX_THRESHOLD,
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
        print("[INFO] 学習データ(.npy)が保存時から変更されているため、自動的に再学習します。")
        print(f"       保存時の指紋: {saved_fps}")
        print(f"       現在の指紋　: {current_fps}")
        return None

    bst = xgb.Booster()
    bst.load_model(str(d / "bst.json"))
    scaler = joblib.load(d / "scaler.pkl")
    label_encoder = joblib.load(d / "label_encoder.pkl")
    class_names = joblib.load(d / "class_names.pkl")

    print(f"[LOAD] 既存モデルを再利用します（学習データの変更なしを確認）: {d}")
    return TrainArtifacts(
        bst=bst,
        scaler=scaler,
        label_encoder=label_encoder,
        X_test=None,   # 再学習していないので評価用データは無い
        y_test=None,
        class_names=class_names,
    )


# =====================
# 学習
# =====================
def train(smns: List[str]) -> TrainArtifacts:
    """複数サンプル(smns)を読み込み→前処理→学習。"""
    all_df = pd.DataFrame(columns=COLUMNS)

    for smn in smns:
        sam = f"{smn}_10k_Sample_ANAL_rmb"
        npy_path = os.path.join(DATA_DIR, f"{sam}.npy")
        if not os.path.exists(npy_path):
            print(f"[WARN] missing file: {npy_path} (skip)")
            continue

        df = load_and_filter(npy_path)
        all_df = pd.concat([all_df, df], axis=0, ignore_index=True)
        print(f"{smn}: {len(df)} rows")

    if all_df.empty:
        raise RuntimeError("No data after filtering. Check paths and thresholds.")

    y_raw = [s.split("_")[0] for s in all_df["sample"]]
    X_raw = all_df[FEATURES].copy()

    le = LabelEncoder().fit(y_raw)
    y = le.transform(y_raw)
    class_names = list(le.classes_)
    num_class = len(class_names)

    # --- クラス数チェック（原因特定しやすいエラーメッセージにする） ---
    label_counts = Counter(y_raw)
    print(f"クラスごとの行数（フィルタ後）: {dict(label_counts)}")

    if num_class < 2:
        raise RuntimeError(
            "学習データに含まれるクラスが1種類しかありません "
            f"(検出されたクラス: {class_names}, 指定した smns: {smns})。\n"
            "考えられる原因:\n"
            "  1) smnsのうち片方の.npyファイルが見つからず読み込みをスキップした\n"
            "     （上に出力された [WARN] missing file の行を確認してください）\n"
            "  2) ST_RANGE/SB_RANGE/SI_RANGEのフィルタ条件で片方のデータが\n"
            "     全て除外された（'{smn}: 0 rows' の行がないか確認）\n"
            "  3) 'sample'列の命名規則が smns の値と一致しておらず、\n"
            "     y = sample.split('_')[0] が両方とも同じ文字列になっている\n"
            "     （all_df['sample'].unique() を確認してください）"
        )

    missing_classes = [smn for smn in smns if smn not in class_names]
    if missing_classes:
        print(f"[WARN] 指定した smns のうち検出されなかったクラス: {missing_classes}")

    scaler = StandardScaler()

    X_train_raw, X_test_raw, y_train, y_test = train_test_split(
        X_raw, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )

    X_train = scaler.fit_transform(X_train_raw)
    X_test = scaler.transform(X_test_raw)

    class_counts = Counter(y_train)
    min_count = min(class_counts.values())
    from imblearn.under_sampling import RandomUnderSampler
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
        X_test=X_test,
        y_test=y_test,
        class_names=class_names,
    )


# =====================
# 評価・可視化（common/eval_viz.conmtx を利用）
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

    # common/eval_viz.py の conmtx() に一本化
    # → 混同行列(実数・正規化%)のPNG/CSV保存、classification_reportの
    #   表示・テキスト保存までまとめて面倒を見てくれる
    _MX, _N_MX, _report = eval_viz.conmtx(
        art.y_test, y_pred, art.label_encoder, save_dir=run_dir
    )

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
# 予測（file単位の混合比率集計）
# =====================
def _aggregate_all(event_df: pd.DataFrame, smns: List[str],
                    id2name: Dict[int, str], threshold: float) -> pd.DataFrame:
    """従来通り全イベントで比率計算。pmaxの統計列（平均・高信頼度割合）を追加するだけで、
    絞り込み（除外）は一切行わない。"""
    rows = []
    for name, g in event_df.groupby("file"):
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
    for name, g_all in event_df.groupby("file"):
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


def predict_grouped(test_smn: str, art: TrainArtifacts, smns: List[str],
                     threshold: float = PMAX_THRESHOLD):
    """混合サンプルをfile単位で比率集計する。
    戻り値: (all_df, highconf_df) のタプル。
      all_df      : 従来通り全イベントで比率計算（+pmax統計列を追加）
      highconf_df : pmax >= threshold のイベントだけに絞った参考版
    """
    sam = f"{test_smn}_10k_Sample_ANAL_rmb"
    npy_path = os.path.join(DATA_DIR, f"{sam}.npy")
    if not os.path.exists(npy_path):
        print(f"[WARN] missing file: {npy_path} (skip)")
        return pd.DataFrame(), pd.DataFrame()

    df = load_and_filter(npy_path)
    if df.empty:
        print(f"[WARN] filtered empty: {npy_path}")
        return pd.DataFrame(), pd.DataFrame()

    id2name = {i: c for i, c in enumerate(art.class_names)}

    # --- イベント単位で予測確率・pmaxを計算（fileごとにループせず一括で処理） ---
    X = art.scaler.transform(df[FEATURES])
    dtest = xgb.DMatrix(X)
    probs = art.bst.predict(dtest)          # shape: (n_events, num_class)
    y_pred = probs.argmax(axis=1)
    pmax = probs.max(axis=1)

    event_df = pd.DataFrame({
        "file": df["file"].values,
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
    smns = ["Guanine", "OMeG"]
    test_smns = ["GO6MeG1-1"]

    # この回の実行結果（評価・予測CSV）の保存先
    # common/paths.new_run が results/rmb/xgboost/<timestamp>_mix_<smns>/ を作成する
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

    # --- 予測（混合サンプルのfile単位比率集計） ---
    save_group_predictions(test_smns, artifacts, smns, run_dir)

    print("end")
