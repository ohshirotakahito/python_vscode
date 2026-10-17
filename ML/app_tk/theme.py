"""Project-wide dark theme (ui_theme.py at the repository root) for the ML panel."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ui_theme  # noqa: E402

BACKGROUND = ui_theme.PANEL
SURFACE = ui_theme.FIELD
TEXT = ui_theme.TEXT
MUTED = ui_theme.MUTED
DANGER = ui_theme.DANGER
ACCENT = ui_theme.ML
SELECTED = ui_theme.mix(ui_theme.FIELD, ACCENT, 0.28)
BORDER = ui_theme.BORDER
# Status colors that stay readable on the dark background.
INFO = '#7fb2ff'
SUCCESS = '#6fd08a'
WARNING = '#e8b45e'


def apply_theme(root, font, on_close=None):
    style = ui_theme.apply(root, ACCENT, font)
    ui_theme.header(root, 'ML解析パネル', '特徴量抽出・統計・学習・混合予測・クラスタリング',
                    '\uEBD2', ACCENT, on_close=on_close).pack(fill='x')
    return style
