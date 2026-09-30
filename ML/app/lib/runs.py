# -*- coding: utf-8 -*-
"""ML/app/lib/runs.py

results/ 配下の実行結果（run_manifest.json）の一覧・ダウンロード用ユーティリティ。
UI経由で実行した結果だけでなく、従来通りCLIで実行した過去の結果も同じ形式で拾える。
"""

import io
import json
import zipfile
from pathlib import Path

import common.paths as paths


def list_runs() -> list[dict]:
    """results/<feature_set>/<algorithm>/<run>/run_manifest.json を新しい順に列挙する。

    戻り値の各要素: {"run_dir", "manifest", "feature_set", "algorithm"}
    """
    runs = []
    for manifest_path in paths.RESULTS_ROOT.glob("*/*/*/run_manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run_dir = manifest_path.parent
        runs.append({
            "run_dir": run_dir,
            "manifest": manifest,
            "feature_set": run_dir.parent.parent.name,
            "algorithm": run_dir.parent.name,
        })
    runs.sort(key=lambda r: r["manifest"].get("run_timestamp", ""), reverse=True)
    return runs


def run_zip_path(run_dir: Path) -> Path | None:
    """run_dir に対応する既存ZIP（xgboost/lightgbm学習スクリプトが作成するもの）を返す。
    無ければNone。"""
    zip_path = run_dir.parent / f"{run_dir.name}.zip"
    return zip_path if zip_path.exists() else None


def make_zip_bytes(folder: Path) -> bytes:
    """folder をその場でZIP化してバイト列を返す（既存ZIPが無いフォルダのダウンロード用）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(folder.parent))
    buffer.seek(0)
    return buffer.read()
