"""Per-application Windows icons matching the existing header symbols."""
import os
from pathlib import Path

from shared import ui_theme

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = {
    'chipdb': ('\uE950', '#dfb86b'),
    'exmanager': ('\uE8AB', ui_theme.EXMANAGER),
    'database': ('\uEE94', ui_theme.DATABASE),
    'analyzer': ('\uE9F5', ui_theme.ANALYZER),
    'viewer': ('\uE9D9', ui_theme.VIEWER),
    'ml': ('\uEBD2', ui_theme.ML),
    'plotting': ('\uE9D2', ui_theme.PLOTTING),
    'scoring': ('\uE9D9', ui_theme.SCORING),
}


def icon_path(app_id):
    if app_id == 'workbench':
        return ROOT / 'assets' / 'quantum-workbench.ico'
    return ROOT / 'assets' / 'app-icons' / f'{app_id}.ico'


def set_taskbar_identity(app_id):
    if os.name == 'nt':
        import ctypes
        identity = {'workbench': 'QuantumWorkbench.Launcher',
                    'ml': 'QuantumWorkbench.MLPanel',
                    'analyzer': 'PythonVScode.Analyzer'}.get(app_id, f'QuantumWorkbench.{app_id}')
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(identity)
