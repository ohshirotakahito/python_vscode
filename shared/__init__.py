"""アプリ間で共有する部品（画面テーマ・サーバー選択・フォルダ構成・SEQ履歴・実験ファイルDB）。

各アプリはリポジトリ直下を sys.path に入れてから `from shared import ...` で使う。
アプリ同士は直接 import しない。取り決めは shared/README.md を参照。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
