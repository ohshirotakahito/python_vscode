"""PyQt6 アプリ用の共通ダークテーマ（ui_theme.py の色・フォント・ヘッダーを Qt で再現する）。

Tkinter アプリと見た目を揃えるため、色はすべて ui_theme の値を使う。PyQt6 は使う側のアプリだけが必要とし、
Tkinter アプリはこのモジュールを import しない。

    app = QApplication(sys.argv)
    qt_theme.apply(app, ui_theme.ANALYZER)
    layout.addWidget(qt_theme.Header('タイトル', '説明', '\\uE9F5', ui_theme.ANALYZER, on_close=window.close))
"""
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from shared.ui_theme import (BG, BORDER, DANGER, DIM, FIELD, HEADER_FROM, HOVER, ICON, MONO, MUTED, PANEL,
                             STRIPE, TEXT, UI, UI_BOLD, mix, row)

BUTTON = '#1d2730'
HEADING = '#1b252d'
SCROLL = '#26323b'
SCROLL_HOVER = '#33424d'
SUBTITLE = '#a9c4b2'
ICONS = (Path(__file__).resolve().parent / 'qt_icons').as_posix()  # チェックボックスの印（SVG）


def stylesheet(accent):
    """ui_theme.apply() の ttk 設定に対応する Qt スタイルシート。"""
    select = mix(accent, '#000000', 0.45)
    tint = lambda t: mix(PANEL, accent, t)  # noqa: E731
    return f"""
QWidget {{ background: {PANEL}; color: {TEXT}; font-family: '{UI}'; font-size: 10pt; }}
QToolTip {{ background: {FIELD}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-family: '{UI_BOLD}'; font-size: 15pt; color: {TEXT}; }}
QLabel#subtitle {{ color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}

QFrame#sidebar {{ background: {BG}; border-right: 1px solid {BORDER}; }}
QFrame#sidebar QLabel {{ color: {MUTED}; }}
QFrame#sidebar QLabel {{ padding-left: 2px; }}
QPushButton#nav {{ background: transparent; border: none; border-left: 3px solid transparent; border-radius: 3px;
    padding: 9px 12px; text-align: left; font-size: 10.5pt; color: {MUTED}; }}
QPushButton#nav:hover {{ background: {HOVER}; color: {TEXT}; border-left: 3px solid {mix(BG, accent, 0.5)}; }}
QPushButton#nav:checked {{ background: {PANEL}; color: {TEXT}; border-left: 3px solid {accent};
    font-family: '{UI_BOLD}'; }}

QGroupBox {{ border: 1px solid {BORDER}; border-radius: 4px; margin-top: 14px; padding: 12px 10px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {accent};
    font-family: '{UI_BOLD}'; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTextBrowser, QListWidget,
QTableWidget, QTableView, QTreeView {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 3px; padding: 3px 5px;
    selection-background-color: {select}; selection-color: #ffffff; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ background: {PANEL}; color: {DIM}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {FIELD}; border: 1px solid {BORDER}; selection-background-color: {select}; }}
QPlainTextEdit#log {{ font-family: '{MONO}'; font-size: 9pt; }}

QPushButton {{ background: {BUTTON}; border: 1px solid {BORDER}; border-radius: 3px; padding: 5px 14px; }}
QPushButton:hover {{ background: {HOVER}; border: 1px solid {accent}; }}
QPushButton:pressed {{ background: {mix(HOVER, accent, 0.25)}; }}
QPushButton:disabled {{ background: {PANEL}; color: {DIM}; border: 1px solid {BORDER}; }}
QPushButton#primary {{ background: {tint(0.32)}; border: 1px solid {tint(0.6)}; font-family: '{UI_BOLD}';
    padding: 8px 22px; }}
QPushButton#primary:hover {{ background: {tint(0.45)}; }}
QPushButton#primary:pressed {{ background: {tint(0.55)}; }}
QPushButton#primary:disabled {{ background: {PANEL}; color: {DIM}; border: 1px solid {BORDER}; }}
QToolButton {{ background: {BUTTON}; border: 1px solid {BORDER}; border-radius: 3px; padding: 3px 8px; }}
QToolButton:hover {{ background: {HOVER}; border: 1px solid {accent}; }}

QCheckBox, QRadioButton {{ background: transparent; spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; background: {FIELD};
    border: 1px solid {mix(BORDER, TEXT, 0.25)}; }}
QCheckBox::indicator {{ border-radius: 2px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border: 1px solid {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; image: url({ICONS}/check.svg); }}
QRadioButton::indicator:checked {{ background: {accent}; border: 1px solid {accent}; image: url({ICONS}/dot.svg); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {DIM}; }}

QProgressBar {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 3px; text-align: center;
    min-height: 16px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}

QHeaderView::section {{ background: {HEADING}; color: {MUTED}; border: none; border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER}; padding: 4px 6px; font-family: '{UI_BOLD}'; }}
QHeaderView::section:hover {{ background: {HOVER}; color: {TEXT}; }}
QTableCornerButton::section {{ background: {HEADING}; border: none; }}
QTableWidget, QTableView {{ gridline-color: {BORDER}; alternate-background-color: {STRIPE}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; top: -1px; }}
QTabBar::tab {{ background: {BG}; color: {MUTED}; padding: 7px 16px; border: 1px solid {BG}; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
    border-top: 2px solid {accent}; border-bottom: none; }}
QTabBar::tab:hover:!selected {{ background: {HOVER}; color: {TEXT}; }}

QSplitter::handle {{ background: {BORDER}; }}
QScrollBar:vertical {{ background: {PANEL}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {PANEL}; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {SCROLL}; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 24px; }}
QScrollBar::handle:horizontal {{ min-width: 24px; }}
QScrollBar::handle:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QMenu {{ background: {FIELD}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {select}; }}
QDialog, QMessageBox {{ background: {PANEL}; }}
"""


def palette(accent):
    """Fusion が描く部品（チェックボックスの印など）用の配色。"""
    pal = QPalette()
    roles = QPalette.ColorRole
    for role, color in ((roles.Window, PANEL), (roles.WindowText, TEXT), (roles.Base, FIELD),
                        (roles.AlternateBase, STRIPE), (roles.Text, TEXT), (roles.Button, BUTTON),
                        (roles.ButtonText, TEXT), (roles.BrightText, '#ffffff'), (roles.ToolTipBase, FIELD),
                        (roles.ToolTipText, TEXT), (roles.PlaceholderText, DIM), (roles.Link, accent),
                        (roles.Highlight, mix(accent, '#000000', 0.45)), (roles.HighlightedText, '#ffffff'),
                        (roles.Light, HOVER), (roles.Midlight, BORDER), (roles.Mid, BORDER),
                        (roles.Dark, BG), (roles.Shadow, '#000000')):
        pal.setColor(role, QColor(color))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(DIM))
    return pal


def apply(app, accent):
    """QApplication 全体にテーマを当てる（Fusion スタイル＋配色＋スタイルシート）。"""
    app.setStyle('Fusion')
    app.setPalette(palette(accent))
    app.setFont(QFont(UI, 10))
    app.setStyleSheet(stylesheet(accent))


def row_color(kind, strength=0.24):
    """表の行の背景色（ui_theme.row と同じ: success/info/warning/danger/violet/neutral/teal）。"""
    return QColor(row(kind, strength))


def _font(family, points):
    font = QFont(family)
    font.setPointSizeF(points)
    return font


def window_icon(glyph, accent):
    """ランチャーのカードと同じ「角丸の色付きタイル＋アイコン」をウィンドウ・タスクバーのアイコンにする。"""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(mix(BG, accent, 0.22)))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
        font = QFont(ICON)
        font.setPixelSize(round(size * 0.62))
        p.setFont(font)
        p.setPen(QColor(accent))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, glyph)
        p.end()
        icon.addPixmap(pixmap)
    return icon


class Header(QWidget):
    """ui_theme.header() と同じグラデーションの上部バナー。on_close を渡すと右端に「終了」ボタンを描く。"""

    def __init__(self, title, subtitle, icon, accent, height=64, on_close=None, parent=None):
        super().__init__(parent)
        self.title, self.subtitle, self.icon, self.accent = title, subtitle, icon, accent
        self.on_close, self._hot = on_close, False
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def _close_rect(self):
        h = self.height()
        return QRectF(self.width() - 18 - 96, h / 2 - 16, 96, 32)

    def paintEvent(self, event):
        w, h, a = self.width(), self.height(), self.accent
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 左から右へ、幅の 1/1.6 の位置でアクセント寄りの色に達するグラデーション（Tk 版と同じ）
        gradient = QLinearGradient(0, 0, w, 0)
        gradient.setColorAt(0, QColor(HEADER_FROM))
        gradient.setColorAt(1 / 1.6, QColor(mix(HEADER_FROM, a, 0.22)))
        gradient.setColorAt(1, QColor(mix(HEADER_FROM, a, 0.22)))
        p.fillRect(self.rect(), gradient)
        p.setPen(QColor(mix(BORDER, a, 0.35)))
        p.drawLine(0, h - 1, w, h - 1)

        tile = QRectF(18, 12, h - 24, h - 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(mix(HEADER_FROM, a, 0.22)))
        p.drawRoundedRect(tile, 10, 10)
        p.setFont(_font(ICON, 17))
        p.setPen(QColor(a))
        p.drawText(tile, Qt.AlignmentFlag.AlignCenter, self.icon)

        p.setFont(_font(UI_BOLD, 15))
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(h + 14, 0, w, h / 2 + 4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   self.title)
        p.setFont(_font(UI, 9))
        p.setPen(QColor(SUBTITLE))
        p.drawText(QRectF(h + 15, h / 2 + 4, w, h / 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   self.subtitle)

        if self.on_close is not None:
            r = self._close_rect()
            path = QPainterPath()
            path.addRoundedRect(r, 16, 16)
            p.setPen(QPen(QColor(mix(HEADER_FROM, DANGER, 0.55)), 1))
            p.setBrush(QColor(mix(HEADER_FROM, DANGER, 0.38 if self._hot else 0.18)))
            p.drawPath(path)
            p.setFont(_font(ICON, 11))
            p.setPen(QColor(DANGER))
            p.drawText(QRectF(r.left() + 14, r.top(), 20, r.height()), Qt.AlignmentFlag.AlignCenter, '')
            p.setFont(_font(UI_BOLD, 10))
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(r.left() + 40, r.top(), 44, r.height()), Qt.AlignmentFlag.AlignCenter, '終了')
        p.end()

    def mouseMoveEvent(self, event):
        hot = self.on_close is not None and self._close_rect().contains(event.position())
        if hot != self._hot:
            self._hot = hot
            self.setCursor(Qt.CursorShape.PointingHandCursor if hot else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        if self._hot:
            self._hot = False
            self.unsetCursor()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._hot and event.button() == Qt.MouseButton.LeftButton:
            self.on_close()
