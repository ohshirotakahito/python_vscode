# -*- coding: utf-8 -*-
"""
common/extract_catalog.py

特徴量抽出の「事前確認」と「履歴」を扱うモジュール（UIに依存しない）。

【事前確認（scan_sample）】
計測サーバー上の1サンプルについて、
  //<server>/<share>/<ex>/<sample>/<sample>_10k_Sample/T/           … 測定ファイル(.tdms)
  //<server>/<share>/<ex>/<sample>/<sample>_10k_Sample/T/…ANAL…/    … ANAL済みファイル(.tdms)
を突き合わせ、ファイル（#001 など）ごとに
  - 測定ファイルがあるか / ANAL済みか
  - 既に特徴量を抽出済みか（data/features/<set>/ の保存済みデータに何イベントあるか）
  - ファイル名から読める測定日時・Gap・装置番号・ANAL解析日時
を一覧にする。

ファイル名の例:
  測定: 'BBB_10k_Sample#001 D_20250325_1044 UNAN#2Pex1n2_BBB-Daicel_EXSV#10_20250325_1245_SGM  D0.540nm B100vsI_...tdms'
  ANAL: 'A@@2025_0402_0943_36_BBB_10k_Sample#001.tdms'
どちらも 'BBB_10k_Sample#001'（= 保存済み特徴量の file / sample_name 列の値）で対応付ける。
「抽出済み」の判定は、このキーに加えて実験ID（測定ファイル名の 'D_20250325_1044' と
保存済み特徴量の ex_id 列の先頭 '20250325_1044'）も一致するものだけを数える
（'01' のような同じサンプル名が別の実験にあっても取り違えないため）。

【履歴（record_extraction / load_history）】
抽出を実行するたびに data/features/<set>/ に追記する:
  extraction_runs.csv  : 1行 = 1回の実行×1サンプル（件数・追加数・重複スキップ数）
  extraction_files.csv : 1行 = 1回の実行で読んだ1ファイル（イベント数・読込結果）
"""

import glob
import os
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import common.paths as paths

RUNS_FILE = "extraction_runs.csv"
FILES_FILE = "extraction_files.csv"

# tdms_checker() の戻り値の意味
CHECK_LABELS = {1: "OK", 2: "データ点数不足", 3: "Signal列なし", 4: "S Tableなし", 0: "読込失敗"}

_KEY_RE = re.compile(r"^(?P<key>.+?_Sample#\d+)")
_FILE_NO_RE = re.compile(r"#(?P<no>\d+)$")
_GAP_RE = re.compile(r"\sD(?P<gap>\d+(?:\.\d+)?)nm")
_MEASURED_RE = re.compile(r"_(?P<date>\d{8})_(?P<time>\d{4})_[A-Z]+\s")
_MACHINE_RE = re.compile(r"AN#(?P<no>\d+)")
_EXPERIMENT_RE = re.compile(r"\sD_(?P<id>\d{8}_\d{4})\s")
_EX_ID_RE = re.compile(r"^(?P<id>\d{8}_\d{4})")
_ANAL_RE = re.compile(r"^A@@(?P<y>\d{4})_(?P<md>\d{4})_(?P<hm>\d{4})_(?P<s>\d{2})_(?P<key>.+)\.tdms$")


# =====================
# ファイル名の解析
# =====================
def file_key(name: str) -> str | None:
    """測定ファイル名 → 'BBB_10k_Sample#001' のような対応付け用キー。"""
    match = _KEY_RE.match(name)
    return match.group("key") if match else None


def parse_measurement_name(name: str) -> dict:
    """測定ファイル名から 測定日時・Gap(nm)・装置番号 を読み取る（読めない項目は None）。"""
    info = {"measured_at": None, "gap_nm": None, "machine": None, "experiment_id": None}
    if (m := _EXPERIMENT_RE.search(name)):
        info["experiment_id"] = m.group("id")
    if (m := _MEASURED_RE.search(name)):
        info["measured_at"] = datetime.strptime(m.group("date") + m.group("time"), "%Y%m%d%H%M")
    if (m := _GAP_RE.search(name)):
        info["gap_nm"] = float(m.group("gap"))
    if (m := _MACHINE_RE.search(name)):
        info["machine"] = int(m.group("no"))
    return info


def parse_anal_name(name: str) -> tuple[str | None, datetime | None]:
    """ANALファイル名 → (キー, 解析日時)。形式が違う場合は (None, None)。"""
    m = _ANAL_RE.match(name)
    if not m:
        return None, None
    analyzed = datetime.strptime(m.group("y") + m.group("md") + m.group("hm") + m.group("s"),
                                 "%Y%m%d%H%M%S")
    return m.group("key"), analyzed


# =====================
# 保存済み特徴量
# =====================
def _experiment_of(ex_id) -> str:
    m = _EX_ID_RE.match(str(ex_id))
    return m.group("id") if m else ""


def extracted_event_counts(feature_set: str, sample: str) -> dict[tuple[str, str], int]:
    """保存済みの特徴量ファイルから、(ファイルキー, 実験ID) ごとのイベント数を返す（無ければ空）。"""
    data_root = paths.feature_dir(feature_set)
    stem = f"{sample}_10k_Sample_ANAL"
    try:
        if feature_set == "rmc":
            for ext in (".parquet", ".csv"):
                path = data_root / f"{stem}_meta{ext}"
                if path.exists():
                    cols = ["sample_name", "ex_id"]
                    df = (pd.read_parquet(path, columns=cols) if ext == ".parquet"
                          else pd.read_csv(path, usecols=cols, dtype=str))
                    keys = zip(df["sample_name"].astype(str), df["ex_id"].map(_experiment_of))
                    return dict(pd.Series(list(keys)).value_counts())
        else:
            path = data_root / f"{stem}_{feature_set}.npy"
            if path.exists():
                try:
                    array = np.load(path, mmap_mode="r")
                except ValueError:  # object配列はmmapできない
                    array = np.load(path, allow_pickle=True)
                files = np.asarray(array[:, 1]).astype(str)
                experiments = [_experiment_of(v) for v in np.asarray(array[:, 2]).astype(str)]
                return dict(pd.Series(list(zip(files, experiments))).value_counts())
    except (OSError, ValueError, KeyError) as e:
        print(f"[WARN] 保存済み特徴量を読めませんでした ({sample}): {e}")
    return {}


# =====================
# サーバー上のファイル確認
# =====================
def sample_t_dir(server, share, ex, sample) -> str:
    return f"//{server}/{share}/{ex}/{sample}/{sample}_10k_Sample/T"


def scan_sample(server: str, share: str, ex: str, sample: str, feature_set: str) -> pd.DataFrame:
    """1サンプル分の 測定ファイル・ANALファイル・抽出状況 をファイル単位で一覧にする。

    列: sample, file_key, file_no, status, measured_at, gap_nm, machine, analyzed_at,
        extracted_events, last_extracted_at, raw_name, anal_path
    status: '未抽出' / '抽出済み' / 'ANAL未処理'（測定のみ） / '測定ファイルなし'（ANALのみ）
            / 'Tフォルダなし'（そもそも測定データが無い）
            / '未抽出（同名の別実験あり）'（同じファイル名で別の実験IDのデータだけが保存済み）
    """
    t_dir = sample_t_dir(server, share, ex, sample)
    columns = ["sample", "file_key", "file_no", "status", "measured_at", "gap_nm", "machine",
               "experiment_id",
               "analyzed_at", "extracted_events", "last_extracted_at", "raw_name", "anal_path"]
    if not os.path.isdir(t_dir):
        return pd.DataFrame([{"sample": sample, "status": "Tフォルダなし", "raw_name": t_dir}],
                            columns=columns)

    raw = {}
    for name in os.listdir(t_dir):
        if name.endswith(".tdms") and (key := file_key(name)):
            raw[key] = name

    # 抽出スクリプトと同じ探し方（T 以下の '*ANAL*' フォルダ直下の .tdms）
    anal = {}
    for folder in glob.glob(os.path.join(t_dir, "**", "*ANAL*"), recursive=True):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.endswith(".tdms"):
                continue
            key, analyzed = parse_anal_name(name)
            key = key or file_key(name) or name
            path = os.path.join(folder, name)
            # 同じファイルが複数回ANALされていれば新しい方を代表にする（抽出は全て読まれる）
            if key not in anal or (analyzed and anal[key][1] and analyzed > anal[key][1]):
                anal[key] = (path, analyzed)

    extracted = extracted_event_counts(feature_set, sample)
    last_extracted = last_extracted_by_file(feature_set, sample)

    rows = []
    for key in sorted(set(raw) | set(anal)):
        info = parse_measurement_name(raw.get(key, ""))
        anal_path, analyzed = anal.get(key, (None, None))
        n_extracted = int(extracted.get((key, info["experiment_id"] or ""), 0))
        n_same_name = sum(n for (k, _e), n in extracted.items() if k == key)
        if key not in raw:
            status = "測定ファイルなし"
        elif anal_path is None:
            status = "ANAL未処理"
        elif n_extracted > 0:
            status = "抽出済み"
        elif n_same_name > 0:
            status = "未抽出（同名の別実験あり）"
        else:
            status = "未抽出"
        no = _FILE_NO_RE.search(key)
        rows.append({
            "sample": sample, "file_key": key, "file_no": int(no.group("no")) if no else None,
            "status": status, **info, "analyzed_at": analyzed,
            "extracted_events": n_extracted, "last_extracted_at": last_extracted.get(key),
            "raw_name": raw.get(key), "anal_path": anal_path,
        })
    if not rows:
        rows.append({"sample": sample, "status": "Tフォルダなし", "raw_name": f"{t_dir}（tdmsなし）"})
    return pd.DataFrame(rows, columns=columns)


# =====================
# 抽出履歴
# =====================
def _history_paths(feature_set):
    root = paths.feature_dir(feature_set)
    return root / RUNS_FILE, root / FILES_FILE


def _append_csv(df: pd.DataFrame, path: Path):
    df.to_csv(path, mode="a", header=not path.exists(), index=False, encoding="utf-8-sig")


def record_extraction(feature_set, server, share, ex, sample, file_log,
                      n_added=None, n_skipped=None, n_total=None, output_path=None):
    """1サンプル分の抽出結果を履歴CSVに追記する。

    file_log: [(tdmsのパス, tdms_checkerの結果, 抽出したイベント数), ...]
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    extracted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    runs_path, files_path = _history_paths(feature_set)
    common = {"run_id": run_id, "extracted_at": extracted_at, "feature_set": feature_set,
              "server": server, "share": share, "ex": ex, "sample": sample}
    files = pd.DataFrame([
        {**common, "file_key": (parse_anal_name(os.path.basename(p))[0]
                                or file_key(os.path.basename(p)) or os.path.basename(p)),
         "check": CHECK_LABELS.get(check, str(check)), "events": int(n), "anal_path": p}
        for p, check, n in file_log
    ], columns=list(common) + ["file_key", "check", "events", "anal_path"])
    runs = pd.DataFrame([{
        **common, "n_files": len(file_log),
        "n_files_ok": sum(1 for _p, check, _n in file_log if check == 1),
        "events_read": int(sum(n for _p, _c, n in file_log)),
        "added": n_added, "skipped_duplicates": n_skipped, "total_after": n_total,
        "output": str(output_path) if output_path else "",
    }])
    try:
        _append_csv(runs, runs_path)
        _append_csv(files, files_path)
    except OSError as e:  # 履歴の書き込み失敗で抽出自体を失敗扱いにはしない
        print(f"[WARN] 抽出履歴を書き込めませんでした: {e}")


def load_history(feature_set=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(実行履歴, ファイル履歴) を返す。feature_set=None なら rmb と rmc を結合。"""
    sets = [feature_set] if feature_set else ["rmb", "rmc"]
    runs, files = [], []
    for fs in sets:
        runs_path, files_path = _history_paths(fs)
        if runs_path.exists():
            runs.append(pd.read_csv(runs_path))
        if files_path.exists():
            files.append(pd.read_csv(files_path))
    runs_df = pd.concat(runs, ignore_index=True) if runs else pd.DataFrame()
    files_df = pd.concat(files, ignore_index=True) if files else pd.DataFrame()
    if not runs_df.empty:
        runs_df = runs_df.sort_values("run_id", ascending=False, ignore_index=True)
    return runs_df, files_df


def last_extracted_by_file(feature_set, sample) -> dict[str, str]:
    """履歴から、ファイルキーごとの最終抽出日時を返す。

    最後に読んだときに読み込めなかった場合は '2026-09-30 11:49:13（Signal列なし）' のように
    理由を添える（ANALはあるのに抽出されないファイルの原因がわかるように）。
    """
    _runs, files = load_history(feature_set)
    if files.empty:
        return {}
    files = files[files["sample"].astype(str) == str(sample)].sort_values("extracted_at")
    latest = files.groupby("file_key").tail(1)
    return {r.file_key: r.extracted_at + ("" if r.check == "OK" else f"（{r.check}）")
            for r in latest.itertuples()}
