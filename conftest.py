"""pytest をどのフォルダから実行しても shared/ を import できるようにする。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
