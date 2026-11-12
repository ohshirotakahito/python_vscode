"""Total Analysis (Python) - desktop app for the 1st / 2nd step analysis.

Run:  python gui_app.py
"""
import csv
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

ANACONDA = Path.home() / "anaconda3" / "python.exe"
try:
    import numpy, PyQt6, pyqtgraph, nptdms  # noqa: E401,F401
except ImportError:
    # started by another Python (e.g. VS Code's interpreter) -> rerun with Anaconda
    if ANACONDA.exists() and Path(sys.executable).resolve() != ANACONDA.resolve():
        import subprocess
        sys.exit(subprocess.call([str(ANACONDA), str(Path(__file__).resolve()), *sys.argv[1:]]))
    raise

import numpy as np
import pandas as pd
from PyQt6.QtCore import QObject, QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QRadioButton, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout, QWidget,
)
import pyqtgraph as pg
from nptdms import TdmsFile

# Python_VScode 共通の画面テーマ（shared/）。ランチャーの他のアプリと同じ色・ヘッダー・アイコンにする。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from shared import qt_theme, t_stock, ui_theme  # noqa: E402

import first_step_io
import second_step_io
import server_anal
import anal_status
import bnal_status
import conditions
import server_bnal
import viewer_rows
from first_step import DEFAULT_CONDITION as FIRST_DEFAULT, long_path
from second_step import DEFAULT_BASES, DEFAULT_CONDITION as SECOND_DEFAULT, condition_from_bc_table

BASE_COLORS = {"G": "#3b82f6", "A": "#ef4444", "C": "#d946ef", "U": "#2dd4bf", "B": "#9ca3af"}
APP_TITLE = "シグナル解析"
APP_SUBTITLE = "1st Step（ANAL）・2nd Step（BNAL）の解析と結果ビューア"
APP_ICON = ""
ACCENT = ui_theme.ANALYZER
TEXT, MUTED, DIM, FIELD = ui_theme.TEXT, ui_theme.MUTED, ui_theme.DIM, ui_theme.FIELD
SUCCESS, WARNING, DANGER, VIOLET, INFO = (ui_theme.HUES[k] for k in ("success", "warning", "danger", "violet", "info"))

STYLE = qt_theme.stylesheet(ACCENT) + f"""
QListWidget#drop {{ border: 2px dashed {ui_theme.mix(ui_theme.BORDER, ACCENT, 0.3)}; border-radius: 6px; padding: 6px; }}
"""


# ---------------------------------------------------------------------------
# background worker
# ---------------------------------------------------------------------------
class Worker(QObject):
    progress = pyqtSignal(int, str)
    log = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, job):
        super().__init__()
        self.job = job

    def run(self):
        outputs = []
        try:
            outputs = self.job(self.log.emit, self.progress.emit)
        except Exception:
            self.log.emit("エラー:\n" + traceback.format_exc())
        self.finished.emit(outputs)


def expand(paths, pattern):
    """Files of `paths`; folders are expanded with `pattern` (a callable on the file name)."""
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.glob("*.tdms") if pattern(f.name))
        elif p.suffix.lower() == ".tdms":
            files.append(p)
    return files


def is_raw(name):
    return name[1:3] != "@@"


def is_a_file(name):
    return name.startswith("A@@")


# ---------------------------------------------------------------------------
# widgets
# ---------------------------------------------------------------------------
class DropList(QListWidget):
    """File list that accepts dropped files / folders."""

    def __init__(self, hint):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(110)
        self.hint = hint

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:
            from PyQt6.QtGui import QPainter
            p = QPainter(self.viewport())
            p.setPen(QColor(DIM))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.hint)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            self.add_path(url.toLocalFile())

    def add_path(self, path):
        if path and not self.findItems(path, Qt.MatchFlag.MatchExactly):
            self.addItem(path)

    def paths(self):
        return [self.item(i).text() for i in range(self.count())]


def file_box(hint, folder_ok=True):
    """Drop list with add / folder / clear buttons."""
    lst = DropList(hint)
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(lst)
    row = QHBoxLayout()
    b_add, b_dir, b_del, b_clr = (QPushButton(t) for t in ("ファイル追加…", "フォルダ追加…", "選択を削除", "クリア"))
    b_add.clicked.connect(lambda: [lst.add_path(f) for f in QFileDialog.getOpenFileNames(
        w, "ファイルを選択", "", "TDMS (*.tdms)")[0]])
    b_dir.clicked.connect(lambda: lst.add_path(QFileDialog.getExistingDirectory(w, "フォルダを選択")))
    b_del.clicked.connect(lambda: [lst.takeItem(lst.row(i)) for i in lst.selectedItems()])
    b_clr.clicked.connect(lst.clear)
    for b in (b_add, b_dir, b_del, b_clr):
        row.addWidget(b)
    b_dir.setVisible(folder_ok)
    row.addStretch()
    lay.addLayout(row)
    return w, lst


def header(title, subtitle):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    t, s = QLabel(title), QLabel(subtitle)
    t.setObjectName("title")
    s.setObjectName("subtitle")
    lay.addWidget(t)
    lay.addWidget(s)
    return w


def out_dir_row(placeholder):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    btn = QPushButton("参照…")
    btn.clicked.connect(lambda: edit.setText(QFileDialog.getExistingDirectory(w, "出力フォルダ") or edit.text()))
    lay.addWidget(edit)
    lay.addWidget(btn)
    return w, edit


def spin(value, lo, hi, step=1, decimals=None, suffix=""):
    s = QDoubleSpinBox() if decimals is not None else QSpinBox()
    if decimals is not None:
        s.setDecimals(decimals)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    return s


class RunPanel(QWidget):
    """Run button + progress bar + log, running a job in a QThread."""

    def __init__(self, on_finished=None, on_send=None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.button = QPushButton("▶  解析を実行")
        self.button.setObjectName("primary")
        self.bar = QProgressBar()
        self.bar.setFormat("待機中")
        row.addWidget(self.button)
        if on_send:
            send = QPushButton("＋  キューに送る")
            send.setToolTip("今のファイルと設定をジョブとして解析キューに送ります")
            send.clicked.connect(on_send)
            row.addWidget(send)
        self.stop = QPushButton("■  中止")
        self.stop.setToolTip("今のファイルが終わったところで止めます")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self._stop)
        row.addWidget(self.stop)
        row.addWidget(self.bar, 1)
        lay.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        self.log.setMaximumBlockCount(5000)
        lay.addWidget(self.log)
        self.on_finished = on_finished
        self.thread = None
        self.cancel = threading.Event()

    def _stop(self):
        self.cancel.set()
        self.stop.setEnabled(False)
        self.log.appendPlainText("… 中止します（今のファイルが終わるまで待ってください）")

    def start(self, job, done=None):
        """job(log, progress, cancelled) -> list of output paths; cancelled() is checked between files.
        done(outputs) runs in the GUI thread after the panel's own finish."""
        self.button.setEnabled(False)
        self.stop.setEnabled(True)
        self.cancel.clear()
        self.bar.setValue(0)
        self.thread = QThread()
        self.worker = Worker(lambda log, progress: job(log, progress, self.cancel.is_set))
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._progress)
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.finished.connect(self._done)
        if done:
            self.worker.finished.connect(done)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()

    def _progress(self, value, text):
        self.bar.setValue(value)
        self.bar.setFormat(f"{text}  %p%")

    def _done(self, outputs):
        self.button.setEnabled(True)
        self.stop.setEnabled(False)
        self.bar.setFormat(f"{'中止' if self.cancel.is_set() else '完了'} ({len(outputs)} ファイル)")
        if outputs:
            self.bar.setValue(100)
        if self.on_finished:
            self.on_finished(outputs)


def make_progress(emit, i, n, name):
    """progress(done, total) of file i of n -> overall percent."""
    def cb(done, total):
        emit(int(100 * (i + done / max(total, 1)) / n), f"{i + 1}/{n} {name[:40]}")
    return cb


# ---------------------------------------------------------------------------
# 1st step page
# ---------------------------------------------------------------------------
class FirstStepPage(QWidget):
    def __init__(self, open_in_viewer, send_to_queue):
        super().__init__()
        self.open_in_viewer = open_in_viewer
        self.send_to_queue = send_to_queue
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(header("1st Step（ファイル）", "生データ (tdms) から信号とバーストを検出して A@@ ファイルを作成"))

        top = QHBoxLayout()
        g_files = QGroupBox("生データ")
        gl = QVBoxLayout(g_files)
        w, self.files = file_box("ここに生データの tdms ファイル / フォルダをドロップ")
        gl.addWidget(w)
        top.addWidget(g_files, 3)

        g_cond = QGroupBox("解析条件（初期値は ANAL の「現在の条件」）")
        form = QFormLayout(g_cond)
        c = {**FIRST_DEFAULT, **conditions.current("anal")["values"]}
        self.point = spin(c["point"], 1, 100000)
        self.cut = spin(c["cut_region"], 1, 100000)
        self.bl = QComboBox()
        self.bl.addItems(["Div Mode", "All Mode", "Div Mean"])
        self.std = QComboBox()
        self.std.addItems(["Div STD Mode", "All STD", "Div STD Mean"])
        self.d_th = spin(c["d_th_xn"], 0, 100, 0.5, 2)
        self.u_th = spin(c["u_th_xn"], 0, 100, 0.5, 2)
        self.max_d = spin(c["max_d_time_ms"], 0, 1000, 1, 2, " ms")
        self.max_es = spin(c["max_es_time_ms"], 0, 1000, 1, 2, " ms")
        self.rrp = spin(c["read_region_point"], 100, 10000000, 1000)
        self.smooth = spin(c["std_smooth_point"], 0, 10000)
        for label, wdg in [("Filter Point (±)", self.point), ("Cut Region", self.cut),
                           ("BL Selection", self.bl), ("STD Selection", self.std),
                           ("D Th (STD ×)", self.d_th), ("U Th (STD ×)", self.u_th),
                           ("MAX D time", self.max_d), ("MAX E_S time", self.max_es),
                           ("Read Region Point", self.rrp), ("STD smooth Point", self.smooth)]:
            form.addRow(label, wdg)
        top.addWidget(g_cond, 2)
        lay.addLayout(top, 3)

        g_out = QGroupBox("出力")
        ol = QGridLayout(g_out)
        w, self.out_dir = out_dir_row("空欄: 生データと同じフォルダ")
        self.csv = QCheckBox("CSV も出力する")
        self.folder = QCheckBox("結果フォルダ A@日時@<サンプル> も作成（S/R/B Table の CSV・PS/BS 波形 txt・概要 JPG）")
        ol.addWidget(QLabel("出力フォルダ"), 0, 0)
        ol.addWidget(w, 0, 1)
        ol.addWidget(self.csv, 1, 1)
        ol.addWidget(self.folder, 2, 1)
        lay.addWidget(g_out)

        self.run = RunPanel(self.finished, self.send)
        self.run.button.clicked.connect(self.start)
        lay.addWidget(self.run, 2)

    def condition(self):
        return {"point": self.point.value(), "cut_region": self.cut.value(),
                "bl_selection": self.bl.currentText(), "std_selection": self.std.currentText(),
                "d_th_xn": self.d_th.value(), "u_th_xn": self.u_th.value(),
                "max_d_time_ms": self.max_d.value(), "max_es_time_ms": self.max_es.value(),
                "read_region_point": self.rrp.value(), "std_smooth_point": self.smooth.value()}

    def build_job(self):
        """Snapshot of the current files and settings as a queue job (None if incomplete)."""
        files = expand(self.files.paths(), is_raw)
        if not files:
            QMessageBox.information(self, "1st Step", "生データの tdms ファイルを追加してください。")
            return None
        cond, out_dir, csv = self.condition(), self.out_dir.text() or None, self.csv.isChecked()
        folder = self.folder.isChecked()

        def job(log, progress, cancelled=lambda: False):
            outs = []
            for i, f in enumerate(files):
                if cancelled():
                    log("■ 中止しました")
                    break
                log(f"● {f.name}")
                out, tables = first_step_io.process_file(
                    f, out_dir, cond, csv, make_progress(progress, i, len(files), f.name), folder)
                ar = tables["AR Table"].iloc[0]
                log(f"   → {out}\n     信号 {int(ar['Signal#Sum'])}  バースト {int(ar['Burst#Sum'])}")
                outs.append(str(out))
            return outs
        return {"kind": "1st Step", "desc": f"{len(files)} ファイル  ({files[0].parent})",
                "fn": job, "a_files": []}

    def start(self):
        job = self.build_job()
        if job:
            self.run.start(job["fn"])

    def send(self):
        job = self.build_job()
        if job:
            self.send_to_queue(job)
            self.run.log.appendPlainText(f"→ キューに送りました: {job['desc']}")

    def finished(self, outputs):
        if outputs:
            self.open_in_viewer(outputs[-1], outputs)


# ---------------------------------------------------------------------------
# 2nd step page
# ---------------------------------------------------------------------------
SEQ_DIR = server_bnal.SEQ_DIR


def settings():
    return QSettings("TotalAnalysis", "PythonGUI")


def read_seq_csv(path):
    """SEQlist csv (LabVIEW machinedb format): rows of  name, sequence[, length]."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = []
    for row in csv.reader(text.splitlines()):
        if len(row) < 2:
            continue
        name, seq = row[0].strip(), re.sub(r"\s", "", row[1]).upper()
        if name and re.fullmatch(r"[A-Z]+", seq) and name.lower() not in ("name", "sample name"):
            rows.append((name, seq))
    return rows


class SeqList(QWidget):
    """Checkable list of (sample name, sequence) loaded from / saved to a SEQlist csv."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.recipe = QLabel("（未読み込み）")
        self.recipe.setObjectName("subtitle")
        b_load, b_save = QPushButton("CSV 読込…"), QPushButton("CSV 保存…")
        b_load.clicked.connect(self.choose_csv)
        b_save.clicked.connect(self.save_csv)
        top.addWidget(self.recipe, 1)
        top.addWidget(b_load)
        top.addWidget(b_save)
        lay.addLayout(top)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Sample name", "Sample Sequence"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self.update_count)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.count = QLabel("")
        buttons = [("全選択", lambda: self.check_all(True)), ("全解除", lambda: self.check_all(False)),
                   ("行追加", lambda: self.add("", "", True)), ("行削除", self.delete_rows)]
        for text, slot in buttons:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.count)
        lay.addLayout(row)

        last = settings().value("seqlist_csv", "")
        if last and Path(last).exists():
            self.load_csv(last)

    def add(self, name, seq, checked=False):
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QTableWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.table.setItem(r, 0, item)
        s = QTableWidgetItem(seq)
        s.setFont(QFont("Consolas", 10))
        self.table.setItem(r, 1, s)
        self.table.blockSignals(False)
        self.update_count()
        return r

    def add_or_check(self, name, seq):
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == name and self.table.item(r, 1).text() == seq:
                self.table.item(r, 0).setCheckState(Qt.CheckState.Checked)
                return
        self.add(name, seq, True)

    def rows(self):
        out = []
        for r in range(self.table.rowCount()):
            name, seq = self.table.item(r, 0), self.table.item(r, 1)
            out.append((name.text().strip(), re.sub(r"\s", "", seq.text() if seq else "").upper(),
                        name.checkState() == Qt.CheckState.Checked))
        return out

    def checked(self):
        return [(n or s, s) for n, s, on in self.rows() if on and s]

    def check_all(self, on):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def delete_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)
        self.update_count()

    def update_count(self, *_):
        self.count.setText(f"{len(self.checked())} / {self.table.rowCount()} 配列を選択")

    def start_dir(self):
        last = settings().value("seqlist_csv", "")
        if last:
            return str(Path(last).parent)
        return str(SEQ_DIR) if SEQ_DIR.exists() else ""

    def choose_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "配列リスト (SEQlist)", self.start_dir(), "CSV (*.csv)")
        if path:
            self.load_csv(path)

    def load_csv(self, path):
        try:
            rows = read_seq_csv(path)
        except Exception as e:
            QMessageBox.warning(self, "読み込みエラー", f"{path}\n\n{e}")
            return
        self.table.setRowCount(0)
        for name, seq in rows:
            self.add(name, seq)
        self.recipe.setText(Path(path).name)
        self.recipe.setToolTip(str(path))
        settings().setValue("seqlist_csv", str(path))

    def save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "配列リストを保存", self.start_dir(), "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for name, seq, _ in self.rows():
                if seq:
                    w.writerow([name, seq, len(seq)])
        self.recipe.setText(Path(path).name)
        settings().setValue("seqlist_csv", str(path))


class SecondStepPage(QWidget):
    def __init__(self, open_in_viewer, send_to_queue):
        super().__init__()
        self.open_in_viewer = open_in_viewer
        self.send_to_queue = send_to_queue
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(header("2nd Step（ファイル）", "A@@ ファイルのバーストを塩基帰属して B@@ ファイルを作成"
                                         "（チェックした配列ごとに全ファイルを解析）"))

        top = QHBoxLayout()
        g_files = QGroupBox("入力ファイル")
        gl = QVBoxLayout(g_files)
        w, self.files = file_box("ここに A@@ ファイル / フォルダをドロップ")
        gl.addWidget(w)
        self.from_raw = QCheckBox("生データから 1st Step も続けて実行する")
        self.from_raw.toggled.connect(lambda on: setattr(self.files, "hint", (
            "ここに生データの tdms ファイル / フォルダをドロップ" if on else
            "ここに A@@ ファイル / フォルダをドロップ")) or self.files.viewport().update())
        gl.addWidget(self.from_raw)
        top.addWidget(g_files, 3)

        g_seq = QGroupBox("配列リスト (SEQlist)")
        ql = QVBoxLayout(g_seq)
        self.seqlist = SeqList()
        ql.addWidget(self.seqlist)
        top.addWidget(g_seq, 3)

        g_set = QGroupBox("共通条件")
        sl = QVBoxLayout(g_set)
        form = QFormLayout()
        self.rrmin = spin(SECOND_DEFAULT["right_read_min"], 1, 100)
        self.noise = spin(SECOND_DEFAULT["noise_max"], 0, 100)
        form.addRow("Right Read MIN", self.rrmin)
        form.addRow("noise MAX", self.noise)
        sl.addLayout(form)
        self.bases = QTableWidget(0, 2)
        self.bases.setHorizontalHeaderLabels(["塩基", "相対レベル"])
        self.bases.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bases.verticalHeader().setVisible(False)
        self.bases.setMaximumHeight(190)
        self.set_bases(DEFAULT_BASES)
        sl.addWidget(QLabel("Base Table"))
        sl.addWidget(self.bases)
        load = QPushButton("既存の B@@ から設定を読み込む…")
        load.clicked.connect(self.load_settings)
        sl.addWidget(load)
        sl.addStretch()
        top.addWidget(g_set, 2)
        lay.addLayout(top, 3)

        g_out = QGroupBox("出力")
        ol = QGridLayout(g_out)
        w, self.out_dir = out_dir_row("空欄: A@@ と同じ場所。どちらの場合も配列ごとに BNAL@<サンプル名> フォルダを作成")
        self.csv = QCheckBox("CSV も出力する")
        ol.addWidget(QLabel("出力フォルダ"), 0, 0)
        ol.addWidget(w, 0, 1)
        ol.addWidget(self.csv, 1, 1)
        lay.addWidget(g_out)

        self.a_files = []
        self.run = RunPanel(lambda outs: outs and self.open_in_viewer(outs[-1], self.a_files), self.send)
        self.run.button.clicked.connect(self.start)
        lay.addWidget(self.run, 2)

    def set_bases(self, bases):
        self.bases.setRowCount(0)
        for b, v in bases.items():
            r = self.bases.rowCount()
            self.bases.insertRow(r)
            item = QTableWidgetItem(b)
            item.setForeground(QColor(BASE_COLORS.get(b, TEXT)))
            item.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.bases.setItem(r, 0, item)
            self.bases.setItem(r, 1, QTableWidgetItem(f"{v:.3f}"))

    def get_bases(self):
        return {self.bases.item(r, 0).text(): float(self.bases.item(r, 1).text())
                for r in range(self.bases.rowCount())}

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "B@@ ファイル", "", "TDMS (B@@*.tdms *.tdms)")
        if not path:
            return
        try:
            bc = TdmsFile.read(long_path(path))["BC Table"]
            c = condition_from_bc_table(pd.DataFrame({ch.name: ch[:] for ch in bc.channels()}))
        except Exception as e:
            QMessageBox.warning(self, "読み込みエラー", f"BC Table を読めませんでした:\n{e}")
            return
        self.seqlist.add_or_check(c["sample_name"], c["sample_sequence"].upper())
        self.rrmin.setValue(c["right_read_min"])
        self.noise.setValue(c["noise_max"])
        self.set_bases(c["bases"])

    def build_job(self):
        """Snapshot of files x checked sequences as a queue job (None if incomplete)."""
        seqs = self.seqlist.checked()
        if not seqs:
            QMessageBox.information(self, "2nd Step", "配列リストで解析する配列にチェックを入れてください。")
            return None
        from_raw = self.from_raw.isChecked()
        files = expand(self.files.paths(), is_raw if from_raw else is_a_file)
        if not files:
            QMessageBox.information(self, "2nd Step", "入力ファイルを追加してください。")
            return None
        try:
            bases = self.get_bases()
        except (ValueError, AttributeError):
            QMessageBox.warning(self, "2nd Step", "Base Table の相対レベルが数値ではありません。")
            return None
        conds = [{"sample_name": name, "sample_sequence": seq, "right_read_min": self.rrmin.value(),
                  "noise_max": self.noise.value(), "bases": bases} for name, seq in seqs]
        out_dir, csv_out = self.out_dir.text() or None, self.csv.isChecked()
        a_files = []

        def job(log, progress, cancelled=lambda: False):
            outs = []
            per_file = len(conds) + (1 if from_raw else 0)
            n = len(files) * per_file
            for i, f in enumerate(files):
                if cancelled():
                    log("■ 中止しました")
                    break
                log(f"● {f.name}")
                k = i * per_file
                if from_raw:
                    f, _ = first_step_io.process_file(f, csv=csv_out,
                                                      progress=make_progress(progress, k, n, f.name))
                    log(f"   1st → {f}")
                    k += 1
                a_files.append(str(f))
                self.a_files.append(str(f))
                for j, cond in enumerate(conds):
                    folder = Path(out_dir) / f"BNAL@{cond['sample_name']}" if out_dir else None
                    out, res = second_step_io.process_file(
                        f, cond, folder, csv_out,
                        make_progress(progress, k + j, n, f"{cond['sample_name']}  {Path(f).name}"))
                    br = res["BR Table"].iloc[0]
                    log(f"   2nd [{cond['sample_name']}] → {out}\n     信号 {int(br['Signal Number # [n]'])}"
                        f"  長い信号 {int(br['Long Signal Number # [n]'])}"
                        f"  MAX Right Read {int(br['MAX Right Read # [n]'])}")
                    outs.append(str(out))
            return outs
        names = ", ".join(n for n, _ in seqs)
        return {"kind": "1st+2nd Step" if from_raw else "2nd Step",
                "desc": f"{len(files)} ファイル × {len(seqs)} 配列  ({names[:80]})",
                "fn": job, "a_files": a_files}

    def start(self):
        job = self.build_job()
        if job:
            self.run.start(job["fn"])

    def send(self):
        job = self.build_job()
        if job:
            self.send_to_queue(job)
            self.run.log.appendPlainText(f"→ キューに送りました: {job['desc']}")


# ---------------------------------------------------------------------------
# 1st step on the analysis servers (sample list from the experiment file database)
# ---------------------------------------------------------------------------
STATE_TINT = {server_anal.TODO: ui_theme.row("danger"), server_anal.RUNNING: ui_theme.row("warning"),
              server_anal.DONE: ui_theme.row("success"), server_anal.COPY: ui_theme.row("neutral", 0.1),
              server_anal.EMPTY: ui_theme.row("neutral", 0.1), server_anal.EXCESS: ui_theme.row("violet"),
              server_anal.UNKNOWN: ui_theme.row("info")}
STATE_TEXT = {server_anal.TODO: DANGER, server_anal.RUNNING: WARNING, server_anal.DONE: SUCCESS,
              server_anal.EXCESS: VIOLET, server_anal.UNKNOWN: INFO}


def broken_out(t, files, log, report=None):
    """Move unreadable raw files of T to T/broken (journaled, undo from the file manager) and drop them."""
    bad = [(f, server_anal.raw_problem(f)) for f in files]
    bad = [(f, why) for f, why in bad if why]
    if not bad:
        return files
    journal, moved, skipped = server_anal.move_broken(t, [f for f, _ in bad])
    for f, why in bad:
        line = (f"  ⚠ 生データが読めないため T/broken へ移動: {f.name}（{why}）" if f.name in moved
                else f"  ⚠ 生データが読めません（移動しませんでした）: {f.name}（{why}）")
        log(line)
        if report:
            report.write(line)
    if journal and report:
        report.write(f"    移動の記録 {journal}（「整理の履歴…」から元に戻せます）")
    names = {f.name for f, _ in bad}
    return [f for f in files if f.name not in names]


# ---------------------------------------------------------------------------
# analysis condition sets (conditions.py): checked right before every server run
# ---------------------------------------------------------------------------
class ConditionDialog(QDialog):
    """Choose / check the condition set before a run (run=True) or manage the sets (run=False).

    Values can be edited here; edited values are saved as a new dated set (it becomes the current one) or used
    for this run only. bc_choices (BNAL): {"kind": list kind, "files": [BC files], "selected": file of the page}."""

    def __init__(self, parent, kind, run=True, summary="", bc_choices=None):
        super().__init__(parent)
        self.kind, self.bc = kind, bc_choices
        self.result_set = None
        self.setWindowTitle(f"解析条件 — {conditions.KINDS[kind]}")
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        if summary:
            s = QLabel(summary)
            s.setWordWrap(True)
            lay.addWidget(s)
        row = QHBoxLayout()
        row.addWidget(QLabel("条件セット"))
        self.box = QComboBox()
        self.box.setMinimumWidth(360)
        row.addWidget(self.box, 1)
        self.b_current = QPushButton("現在の条件にする")
        self.b_current.setToolTip("選んだ条件セットを、これからの解析で使う「現在の条件」にします（日時を記録）")
        self.b_current.clicked.connect(self.make_current)
        row.addWidget(self.b_current)
        lay.addLayout(row)
        self.info = QLabel("")
        self.info.setObjectName("subtitle")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        fields = conditions.FIELDS[kind] + ([("bc_file", f"BC ファイル（{bc_choices['kind']}）", str,
                                              bc_choices["files"])] if bc_choices else [])
        self.table = QTableWidget(len(fields), 3)
        self.table.setHorizontalHeaderLabels(["項目", "この解析で使う値", "既定値"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.editors = {}
        defaults = conditions.DEFAULT_VALUES[kind]
        for i, (key, label, typ, spec) in enumerate(fields):
            self.table.setItem(i, 0, QTableWidgetItem(label))
            if typ is str:
                w = QComboBox()
                w.addItems(list(spec))
                w.currentTextChanged.connect(self.edited)
            else:
                w = spin(0, spec[0], spec[1], 1 if typ is int else 0.5, None if typ is int else 2)
                w.valueChanged.connect(self.edited)
            self.editors[key] = w
            self.table.setCellWidget(i, 1, w)
            d = defaults.get(key, "（ページの選択）" if key == "bc_file" else "")
            self.table.setItem(i, 2, QTableWidgetItem(str(d)))
        lay.addWidget(self.table, 1)
        self.diff = QLabel("")
        self.diff.setWordWrap(True)
        lay.addWidget(self.diff)

        buttons = QHBoxLayout()
        b_save = QPushButton("新しい条件セットとして保存…")
        b_save.setToolTip("表の値を、今日の日付の新しい条件セットとして保存し「現在の条件」にします")
        b_save.clicked.connect(self.save_new)
        buttons.addWidget(b_save)
        buttons.addStretch()
        b_cancel = QPushButton("キャンセル" if run else "閉じる")
        b_cancel.clicked.connect(self.reject)
        buttons.addWidget(b_cancel)
        if run:
            b_run = QPushButton("この条件で実行")
            b_run.setObjectName("primary")
            b_run.setDefault(True)
            b_run.clicked.connect(self.run_now)
            buttons.addWidget(b_run)
        lay.addLayout(buttons)
        self.box.currentIndexChanged.connect(self.show_set)
        self.fill_sets(conditions.current(kind)["name"])

    def fill_sets(self, select):
        cur = conditions.current(self.kind)["name"]
        self.box.blockSignals(True)
        self.box.clear()
        for s in conditions.sets(self.kind):
            self.box.addItem(conditions.describe(self.kind, s) + ("　★現在" if s["name"] == cur else ""), s["name"])
        names = [self.box.itemData(i) for i in range(self.box.count())]
        self.box.setCurrentIndex(names.index(select) if select in names else 0)
        self.box.blockSignals(False)
        self.show_set()

    def chosen(self):
        return conditions.find(self.kind, self.box.currentData()) or conditions.default_set(self.kind)

    def bc_of(self, s):
        return (s["values"].get("bc_files") or {}).get(self.bc["kind"]) or self.bc["selected"] if self.bc else None

    def show_set(self, *_):
        s = self.chosen()
        self.loading = True
        for key, w in self.editors.items():
            v = self.bc_of(s) if key == "bc_file" else s["values"].get(key, conditions.DEFAULT_VALUES[self.kind][key])
            if isinstance(w, QComboBox):
                w.setCurrentText(str(v))
            else:
                w.setValue(v)
        self.loading = False
        cur = conditions.current(self.kind)["name"]
        self.b_current.setEnabled(s["name"] != cur)
        self.info.setText(" / ".join(x for x in (
            "★ 現在の条件" if s["name"] == cur else "（現在の条件ではありません）",
            f"{s['since']} から使用" if s.get("since") else "", f"作成 {s['operator']}" if s.get("operator") else "",
            s.get("note", "")) if x))
        self.edited()

    def values(self):
        out = {}
        for key, _, typ, _ in conditions.FIELDS[self.kind]:
            w = self.editors[key]
            v = w.currentText() if isinstance(w, QComboBox) else w.value()
            out[key] = int(v) if typ is int else v
        if self.bc:
            out["bc_files"] = {**(self.chosen()["values"].get("bc_files") or {}),
                               self.bc["kind"]: self.editors["bc_file"].currentText()}
        return out

    def changed(self):
        """Edited away from the chosen set (the BC file counts when the set names one, or differs from the page)."""
        s, v = self.chosen(), self.values()
        keys = [k for k, *_ in conditions.FIELDS[self.kind]]
        return (any(v[k] != s["values"].get(k) for k in keys)
                or bool(self.bc) and v["bc_files"][self.bc["kind"]] != self.bc_of(s))

    def edited(self, *_):
        if getattr(self, "loading", False):
            return
        v = self.values()
        diffs = conditions.differences(self.kind, v)
        defaults = conditions.DEFAULT_VALUES[self.kind]
        for i, (key, *_) in enumerate(conditions.FIELDS[self.kind]):
            item = self.table.item(i, 0)
            item.setForeground(QColor(WARNING if v[key] != defaults[key] else TEXT))
        labels = {k: label for k, label, *_ in conditions.FIELDS[self.kind]}
        text = ("既定値と違う項目: " + "、".join(f"{labels[k]} = {a}（既定 {b}）" for k, a, b in diffs)
                if diffs else "すべて既定値です。")
        if self.changed():
            text += f"<br><span style='color:{DANGER}'>選んだ条件セットから値を変更しています（未保存）。</span>"
        self.diff.setText(text)

    def make_current(self):
        conditions.set_current(self.kind, self.chosen()["name"])
        self.fill_sets(self.chosen()["name"])

    def save_new(self):
        name, ok = QInputDialog.getText(self, "新しい条件セット", f"名前（使い始める日 {datetime.now():%Y-%m-%d} を記録します）:",
                                        text=f"{datetime.now():%Y-%m-%d} 条件")
        if not ok:
            return False
        note, ok = QInputDialog.getText(self, "新しい条件セット", "メモ（変更の理由など）:")
        if not ok:
            return False
        try:
            s = conditions.add_set(self.kind, name, self.values(), note)
        except ValueError as e:
            QMessageBox.warning(self, "条件セット", str(e))
            return False
        self.fill_sets(s["name"])
        return True

    def run_now(self):
        if self.changed():
            box = QMessageBox(self)
            box.setWindowTitle("解析条件")
            box.setText("選んだ条件セットから値を変更しています。")
            b_save = box.addButton("保存して実行", QMessageBox.ButtonRole.AcceptRole)
            b_once = box.addButton("保存せず今回だけ使う", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("戻る", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is b_save:
                if not self.save_new():
                    return
            elif box.clickedButton() is b_once:
                self.result_set = {"name": f"{self.chosen()['name']}（今回だけ変更）", "values": self.values()}
                self.accept()
                return
            else:
                return
        self.result_set = {"name": self.chosen()["name"], "values": self.values()}
        self.accept()


def ask_condition(parent, kind, summary="", bc_choices=None):
    """The condition set (name + values) for a run, or None when cancelled."""
    d = ConditionDialog(parent, kind, True, summary, bc_choices)
    return d.result_set if d.exec() == QDialog.DialogCode.Accepted else None


# ---------------------------------------------------------------------------
# history of the journaled moves (file_moves): list them and undo one
# ---------------------------------------------------------------------------
JOURNAL_KINDS = {"ANAL": "ANAL の整理", "T_stock": "T（Data）の退避・整理", "BNAL": "BNAL の整理"}
JOURNAL_ACTIONS = {"restore": "T/stocked → T", "archive": "T → T/stocked", "extract": "ANAL/stocked → ANAL",
                   "delete": "削除（.整理履歴 へ移動）", "undo": "元に戻す", "broken": "→ T/broken",
                   "duplicate": "→ duplicate", "results_to_anal": "T の A@@ → ANAL"}


def read_journals(kinds=("ANAL", "T_stock", "BNAL"), limit=400):
    """Newest first: [dict(path, kind, time, action, folder, count, status, undone)] of the move journals."""
    from shared import file_moves
    import json
    rows, undone = [], set()
    for kind in kinds:
        folder = file_moves.journal_dir(kind)
        for p in sorted(folder.glob("*.json"), reverse=True)[:limit] if folder.is_dir() else []:
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("undo_of"):
                undone.add(str(Path(rec["undo_of"]).resolve()))
            ops = rec.get("operations", [])
            rows.append(dict(path=str(p), kind=kind, time=rec.get("created_at", "")[:19].replace("T", " "),
                             action=rec.get("action") or ("restore" if rec.get("restore") else "archive"),
                             folder=rec.get("folder", ""), count=sum(1 for o in ops if o.get("status") == "完了"),
                             total=len(ops), status=rec.get("status", "")))
    for r in rows:
        r["undone"] = str(Path(r["path"]).resolve()) in undone
    return sorted(rows, key=lambda r: r["time"], reverse=True)


class JournalDialog(QDialog):
    """Moves made from here and from the file manager, newest first; a chosen one can be undone."""
    COLS = ["日時", "種類", "操作", "対象フォルダ", "移動", "状態"]

    def __init__(self, parent, kinds=("ANAL", "T_stock", "BNAL")):
        super().__init__(parent)
        self.kinds = kinds
        self.setWindowTitle("整理の履歴（移動・削除の記録）")
        self.resize(1200, 600)
        lay = QVBoxLayout(self)
        note = QLabel("ANAL・BNAL（サーバー）と実験ファイルDB（旧ファイル管理アプリを含む）で行った移動・削除の記録です。行を選んで「元に戻す」と、"
                      "その操作で動かしたファイルを元の場所へ戻します（戻す前に内容が変わっていないか確かめます）。")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        b_open = QPushButton("記録のフォルダを開く")
        b_open.clicked.connect(self.open_folder)
        row.addWidget(b_open)
        row.addStretch()
        self.b_undo = QPushButton("元に戻す…")
        self.b_undo.setObjectName("primary")
        self.b_undo.clicked.connect(self.undo)
        row.addWidget(self.b_undo)
        b_close = QPushButton("閉じる")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        lay.addLayout(row)
        self.fill()

    def done(self, code):
        if getattr(self, "thread", None) is not None and self.thread.isRunning():
            return  # an undo is moving files: the window stays until it has finished
        super().done(code)

    def fill(self):
        self.rows = read_journals(self.kinds)
        self.table.setRowCount(len(self.rows))
        for n, r in enumerate(self.rows):
            state = r["status"] + ("（元に戻し済み）" if r["undone"] else "")
            values = [r["time"], JOURNAL_KINDS.get(r["kind"], r["kind"]), JOURNAL_ACTIONS.get(r["action"], r["action"]),
                      r["folder"], f"{r['count']} / {r['total']}", state]
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if r["undone"] or r["action"] == "undo":
                    item.setForeground(QColor(MUTED))
                elif r["status"] != "完了":
                    item.setForeground(QColor(WARNING))
                item.setToolTip(r["path"])
                self.table.setItem(n, c, item)
        self.table.resizeColumnsToContents()

    def open_folder(self):
        import os as _os
        from shared import file_moves
        folder = file_moves.journal_dir(self.kinds[0])
        n = self.table.currentRow()
        if n >= 0:
            folder = Path(self.rows[n]["path"]).parent
        folder.mkdir(parents=True, exist_ok=True)
        _os.startfile(str(folder))

    def undo(self):
        from shared import file_moves
        n = self.table.currentRow()
        if n < 0:
            QMessageBox.information(self, "元に戻す", "元に戻す操作を選んでください。")
            return
        r = self.rows[n]
        if r["action"] == "undo":
            QMessageBox.information(self, "元に戻す", "「元に戻す」の記録です。戻した操作のほうを選んでください。")
            return
        if r["undone"] and QMessageBox.question(self, "元に戻す", "この操作は元に戻し済みです。もう一度戻しますか？"
                                                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            plan = file_moves.undo_plan(r["path"])
        except Exception as e:
            QMessageBox.warning(self, "元に戻す", f"戻せません:\n{e}")
            return
        sample = "\n".join(f"  {Path(o['source']).name} → {Path(o['target']).parent}" for o in plan["operations"][:15])
        more = f"\n  …ほか {len(plan['operations']) - 15} 件" if len(plan["operations"]) > 15 else ""
        if QMessageBox.question(self, "元に戻す", f"{r['time']}  {JOURNAL_ACTIONS.get(r['action'], r['action'])}\n"
                                f"{r['folder']}\n\n{len(plan['operations'])} ファイルを元の場所へ戻します。\n\n{sample}{more}\n\n"
                                "実行しますか？") != QMessageBox.StandardButton.Yes:
            return
        self.b_undo.setEnabled(False)
        self.b_undo.setText("戻しています…")

        def job(log, progress):
            try:
                file_moves.execute_moves(plan, Path(r["path"]).parent)
                return []
            except Exception as e:
                return [str(e)]

        def done(errors):
            self.b_undo.setEnabled(True)
            self.b_undo.setText("元に戻す…")
            if errors:
                QMessageBox.warning(self, "元に戻す", f"途中で止まりました:\n{errors[0]}")
            self.fill()
        self.thread, self.worker = QThread(), Worker(job)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(done)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()


class ServerAnalPage(QWidget):
    db_changed = pyqtSignal()
    samples_done = pyqtSignal(list)
    auto_changed = pyqtSignal()
    COLS = ["グループ", "実験", "サンプル", "T", "T/stocked", "ANAL", "ANAL/stocked", "進捗", "状態",
            "再開#", "注意", "実フォルダ（確認結果）", "DB 取得日時"]

    def __init__(self, open_in_viewer, send_to_queue):
        super().__init__()
        self.open_in_viewer = open_in_viewer
        self.send_to_queue = send_to_queue
        self.rows, self.shown, self.checked, self.live = [], [], set(), {}
        self.t_notes = {}  # suspicious T contents found by the live check
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(header("ANAL（サーバー）", "実験ファイル データベースの ANAL 進捗から未解析・進行中のサンプルを選び、"
                                               "T 直下の tdms を 1st Step で解析して T\\ANAL に保存"))

        db_row = QHBoxLayout()
        self.db = QLineEdit(settings().value("inventory_db", str(server_anal.DEFAULT_DB)))
        b_db, b_reload = QPushButton("参照…"), QPushButton("DB を再読込")
        b_db.clicked.connect(self.choose_db)
        b_reload.clicked.connect(self.reload)
        db_row.addWidget(QLabel("データベース"))
        db_row.addWidget(self.db, 1)
        db_row.addWidget(b_db)
        db_row.addWidget(b_reload)
        lay.addLayout(db_row)
        self.db_info = QLabel("")
        self.db_info.setObjectName("subtitle")
        lay.addWidget(self.db_info)

        flt = QHBoxLayout()
        self.group = QComboBox()
        self.group.setMinimumWidth(190)
        self.search = QLineEdit()
        self.search.setPlaceholderText("実験名・サンプル名で検索")
        self.show_state = {}
        flt.addWidget(QLabel("グループ"))
        flt.addWidget(self.group)
        flt.addWidget(self.search, 1)
        # 超過・確認不可は異常・情報不足なので、最初から表示して見落とさないようにする
        for state, on in [(server_anal.TODO, True), (server_anal.RUNNING, True), (server_anal.EXCESS, True),
                          (server_anal.UNKNOWN, True), (server_anal.DONE, False), (server_anal.COPY, False),
                          (server_anal.EMPTY, False)]:
            cb = QCheckBox(state)
            cb.setChecked(on)
            cb.toggled.connect(self.fill)
            self.show_state[state] = cb
            flt.addWidget(cb)
        self.group.currentIndexChanged.connect(self.fill)
        self.search.textChanged.connect(self.fill)
        lay.addLayout(flt)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(False)
        self.table.itemChanged.connect(self.item_changed)
        self.table.setToolTip("行をダブルクリックすると、そのサンプルの ANAL を結果ビューアで表示します")
        self.table.cellDoubleClicked.connect(
            lambda i, _: self.open_in_viewer(str(server_anal.t_dir(self.shown[i]) / "ANAL")))
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.build_status())
        split.setSizes([420, 260])
        lay.addWidget(split, 5)

        sel = QHBoxLayout()
        for text, slot, tip in [
                ("全部選択", self.select_all, "表示中の行をすべて選択（接続できないグループは除く）。"
                                           "解析するのは各サンプルの T のうち A@@ の無いファイルだけです"),
                ("全解除", self.clear_selection, ""),
                ("自動選択", self.auto_select, "表示中の未着手・進行中のうち、接続できるグループで、注意欄（欠番・退避・混在・要更新）が"
                                             "空のものを選択（外した理由はログに表示）"),
                ("T/stocked ありを選択", self.select_stocked, "表示中で T/stocked が 0 でないサンプルを選択"
                                                          "（「T/stocked → T に戻す」でまとめて移動できます）"),
                ("ANAL/stocked ありを選択", self.select_anal_stocked, "表示中で ANAL/stocked が 0 でないサンプルを選択"
                                                                    "（「ANAL/stocked → ANAL に戻す」でまとめて移動できます）"),
                ("選択行にチェック", self.check_highlighted, "表で反転表示している行（Ctrl / Shift で複数）にチェック")]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            sel.addWidget(b)
        self.b_check = QPushButton("現状を調べる")
        self.b_check.setToolTip("チェックしたサンプルの T・T/stocked・ANAL・ANAL/stocked をサーバーで読み（変更はしません）、"
                                "未解析の数と、整理が必要なもの（T の A@@・空ファイル・退避・重複・番号の付け直し）を下の一覧に出します")
        self.b_check.clicked.connect(self.check_folders)
        sel.addWidget(self.b_check)
        self.b_restore = QPushButton("T/stocked → T に戻す")
        self.b_restore.setToolTip("チェックしたサンプルの T/stocked のファイルを T 直下へ移動（同名があるものは移動しない）。"
                                  "記録が残り、「整理の履歴…」から元に戻せます")
        self.b_restore.clicked.connect(lambda: self.restore_stocked("T"))
        sel.addWidget(self.b_restore)
        self.b_restore_anal = QPushButton("ANAL/stocked → ANAL に戻す")
        self.b_restore_anal.setToolTip(
            "チェックしたサンプルの ANAL/stocked の A@@ を ANAL 直下へ移動。ANAL に同名・同じ #番号があるものは移動せず、"
            "stocked 内で #番号が重なるものは新しい方だけ戻します。記録が残り、「整理の履歴…」から元に戻せます")
        self.b_restore_anal.clicked.connect(lambda: self.restore_stocked("ANAL"))
        sel.addWidget(self.b_restore_anal)
        sel.addStretch()
        self.count = QLabel("")
        sel.addWidget(self.count)
        lay.addLayout(sel)

        opt = QHBoxLayout()
        self.folder = QCheckBox("結果フォルダ A@日時@<サンプル> も作成（S/R/B Table の CSV・PS/BS 波形 txt・概要 JPG）")
        self.settle = spin(5, 0, 1440, 1, 1, " 分")
        opt.addWidget(self.folder)
        self.update_db = QCheckBox("解析後に DB を更新")
        self.update_db.setChecked(True)
        self.update_db.setToolTip("解析したサンプルだけをサーバーで数え直し、実験ファイル データベースに保存します"
                                  "（データベースアプリの「更新」と同じ内容）")
        opt.addWidget(self.update_db)
        opt.addSpacing(20)
        opt.addWidget(QLabel("更新から"))
        opt.addWidget(self.settle)
        opt.addWidget(QLabel("以内のファイルは測定中（書き込み中）として飛ばす"))
        opt.addStretch()
        self.cond_label = QLabel("")
        self.cond_label.setObjectName("subtitle")
        opt.addWidget(self.cond_label)
        b_cond = QPushButton("解析条件…")
        b_cond.setToolTip("ANAL の条件セットの確認・追加・「現在の条件」の切り替え（解析の実行前にも確認します）")
        b_cond.clicked.connect(self.manage_conditions)
        opt.addWidget(b_cond)
        self.show_condition()
        lay.addLayout(opt)

        auto = QHBoxLayout()
        self.b_auto = QPushButton("▶▶  全自動着手")
        self.b_auto.setObjectName("primary")
        self.b_auto.setToolTip("チェックした未着手・進行中のサンプルを順番に：T/stocked を T へ戻す → 整理できるものを整理"
                               "（同じ #番号の別の測定があるサンプルは飛ばす） → 未解析の T 直下を解析 → DB 更新。内容はレポートに記録し、中止しても再開できます")
        self.b_auto.clicked.connect(self.start_auto)
        auto.addWidget(self.b_auto)
        b_db = QPushButton("DB を更新（チェックしたサンプル）")
        b_db.setToolTip("チェックしたサンプルをサーバーで数え直して実験ファイル データベースに保存します")
        b_db.clicked.connect(self.update_db_now)
        auto.addWidget(b_db)
        self.resume_label = QLabel("")
        self.resume_label.setStyleSheet(f"color: {WARNING}")
        self.resume_label.setWordWrap(True)
        auto.addWidget(self.resume_label, 1)
        self.b_resume = QPushButton("⟳  前回の続きから再開")
        self.b_resume.clicked.connect(self.resume_auto)
        self.b_discard = QPushButton("再開しない")
        self.b_discard.setToolTip("中止した全自動着手の記録を消します（レポートは残ります）")
        self.b_discard.clicked.connect(self.discard_auto)
        auto.addWidget(self.b_resume)
        auto.addWidget(self.b_discard)
        lay.addLayout(auto)

        self.run = RunPanel(self.finished, self.send)
        self.run.button.clicked.connect(self.start)
        lay.addWidget(self.run, 2)
        self.db_changed.connect(self.reload)
        self.samples_done.connect(self.uncheck)
        self.auto_changed.connect(self.show_resume)
        self.reload()
        self.show_resume()

    # -- database -------------------------------------------------------------
    def choose_db(self):
        path, _ = QFileDialog.getOpenFileName(self, "inventory.sqlite3", self.db.text(), "SQLite (*.sqlite3 *.db)")
        if path:
            self.db.setText(path)
            self.reload()

    def reload(self):
        path = self.db.text().strip()
        try:
            self.rows = server_anal.load_samples(path)
        except Exception as e:
            self.rows = []
            self.db_info.setText(f"データベースを読めません: {e}")
        else:
            settings().setValue("inventory_db", path)
            dates = sorted(r["last_success"] for r in self.rows if r["last_success"])
            down = sorted({r["group"] for r in self.rows if not r["reachable"]})
            self.db_info.setText(f"{len(self.rows)} サンプル　DB 取得日時 (UTC) {dates[0][:16] if dates else '-'} 〜 "
                                 f"{dates[-1][:16] if dates else '-'}　※ 一覧は保存済みの DB の値です（サーバーには接続しません）"
                                 + (f"　接続失敗: {', '.join(down)}" if down else ""))
        current = self.group.currentText()
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("すべて")
        self.group.addItems(sorted({r["group"] for r in self.rows}))
        self.group.setCurrentText(current or "すべて")
        self.group.blockSignals(False)
        self.fill()

    # -- table ----------------------------------------------------------------
    @staticmethod
    def key(r):
        return r["root"], r["experiment"], r["sample"]

    def fill(self, *_):
        group, text = self.group.currentText(), self.search.text().strip().lower()
        states = {s for s, cb in self.show_state.items() if cb.isChecked()}
        self.shown = [r for r in self.rows if r["state"] in states
                      and (group in ("", "すべて") or r["group"] == group)
                      and (not text or text in r["experiment"].lower() or text in r["sample"].lower())]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.shown))
        for i, r in enumerate(self.shown):
            prog = "" if r["progress"] is None else f"{r['progress']:.1f}%"
            values = [r["group"], r["experiment"], r["sample"], r["t_tdms"], r["t_stocked_tdms"], r["anal_tdms"],
                      r["anal_stocked_tdms"], prog, r["state"] + ("" if r["reachable"] else "（接続不可）"),
                      "" if r["anal_resume"] is None else f"#{r['anal_resume']:03d}",
                      " ".join(r["flags"] + ([self.t_notes[self.key(r)]] if self.t_notes.get(self.key(r)) else [])),
                      self.live.get(self.key(r), ""), (r["last_success"] or "")[:16]]
            tint = QColor(ui_theme.row("neutral", 0.16) if not r["reachable"] else STATE_TINT.get(r["state"], FIELD))
            for c, v in enumerate(values):
                item = QTableWidgetItem("" if v is None else str(v))
                item.setBackground(tint)
                if c == 8 and r["state"] in STATE_TEXT:
                    item.setForeground(QColor(STATE_TEXT[r["state"]]))
                if c == 10 and (r["flags"] or self.t_notes.get(self.key(r))):
                    item.setForeground(QColor(DANGER if "T番号重複" in v else WARNING))
                    item.setToolTip(v)
                if c == 11 and v:
                    item.setToolTip(v)
                if c == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if self.key(r) in self.checked
                                       else Qt.CheckState.Unchecked)
                self.table.setItem(i, c, item)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.update_count()

    def item_changed(self, item):
        if item.column() != 0:
            return
        k = self.key(self.shown[item.row()])
        if item.checkState() == Qt.CheckState.Checked:
            self.checked.add(k)
        else:
            self.checked.discard(k)
        self.update_count()

    def set_checked(self, keys):
        self.checked = set(keys)
        self.fill()

    def select_all(self):
        rows = [r for r in self.shown if r["reachable"]]
        self.set_checked(self.checked | {self.key(r) for r in rows})
        states = {}
        for r in rows:
            states[r["state"]] = states.get(r["state"], 0) + 1
        skipped = len(self.shown) - len(rows)
        self.run.log.appendPlainText(f"全部選択: 表示中 {len(self.shown)} 件のうち {len(rows)} 件を選択（"
                                     + "、".join(f"{k} {v}" for k, v in states.items()) + "）"
                                     + (f"。接続できないグループの {skipped} 件は除外" if skipped else ""))

    def clear_selection(self):
        self.set_checked(set())

    def uncheck(self, keys):
        self.set_checked(self.checked - set(keys))

    def auto_select(self):
        picked = [r for r in self.shown if server_anal.auto_pick(r)]
        self.set_checked({self.key(r) for r in picked})
        unfinished = [r for r in self.shown if r["state"] in (server_anal.TODO, server_anal.RUNNING)]
        reasons = {}
        for r in unfinished:
            if server_anal.auto_pick(r):
                continue
            for why in (server_anal.blocking(r) if r["reachable"] else ["接続できないグループ"]):
                reasons[why] = reasons.get(why, 0) + 1
        self.run.log.appendPlainText(
            f"自動選択: 表示中の未着手・進行中 {len(unfinished)} 件のうち {len(picked)} 件を選択"
            "（基準：接続できるグループで、注意欄の欠番・退避・混在・要更新がどれも無いもの）"
            + ("。外したもの: " + "、".join(f"{k} {v} 件" for k, v in reasons.items()) if reasons else ""))

    def select_stocked(self):
        rows = [r for r in self.shown if r["t_stocked_tdms"]]
        self.set_checked({self.key(r) for r in rows})
        self.run.log.appendPlainText(f"T/stocked ありを選択: {len(rows)} サンプル（T/stocked 合計 "
                                     f"{sum(r['t_stocked_tdms'] for r in rows)} 件）")

    def select_anal_stocked(self):
        rows = [r for r in self.shown if r["anal_stocked_tdms"]]
        self.set_checked({self.key(r) for r in rows})
        self.run.log.appendPlainText(f"ANAL/stocked ありを選択: {len(rows)} サンプル（ANAL/stocked 合計 "
                                     f"{sum(r['anal_stocked_tdms'] for r in rows)} 件）")

    def check_highlighted(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        self.set_checked(self.checked | {self.key(self.shown[i]) for i in rows})

    def selected(self):
        return [r for r in self.rows if self.key(r) in self.checked]

    def update_count(self):
        sel = self.selected()
        left = sum((r["total"] or 0) - (r["analysed"] or 0) for r in sel)
        self.count.setText(f"{len(sel)} サンプルを選択（DB 上の残り {left} ファイル）　表示 {len(self.shown)} 件")

    # -- live check -----------------------------------------------------------
    # -- T/stocked -> T ------------------------------------------------------------
    def background(self, job, done):
        """Run job(log, progress) in a thread and call done(result) in the GUI thread."""
        thread, worker = QThread(), Worker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.run._progress)
        worker.log.connect(self.run.log.appendPlainText)
        worker.finished.connect(done)
        worker.finished.connect(thread.quit)
        # keep every running thread referenced until it has stopped (a job may start the next one in done)
        self._bg = [(t, w) for t, w in getattr(self, "_bg", []) if t.isRunning()] + [(thread, worker)]
        thread.start()

    # what: "T" (T/stocked -> T) or "ANAL" (ANAL/stocked -> ANAL)
    RESTORE = {"T": ("t_stocked_tdms", "T/stocked → T", server_anal.restore_plan, server_anal.T_STOCK_LOGS),
               "ANAL": ("anal_stocked_tdms", "ANAL/stocked → ANAL", server_anal.anal_restore_plan,
                        server_anal.ANAL_LOGS)}

    def restore_button(self, what):
        return self.b_restore if what == "T" else self.b_restore_anal

    def restore_stocked(self, what="T"):
        column, title, planner, _ = self.RESTORE[what]
        rows = [r for r in self.selected() if r[column]]
        if not rows:
            QMessageBox.information(self, title, f"{what}/stocked にファイルがあるサンプルにチェックを入れてください。")
            return
        self.restore_button(what).setEnabled(False)

        def plan(log, progress):
            out = []
            for i, r in enumerate(rows):
                progress(int(100 * i / len(rows)), f"移動計画 {r['sample']}")
                try:
                    out.append((r, *planner(r)))
                except Exception as e:
                    log(f"✖ {r['experiment']} / {r['sample']}: {e}")
            return out
        self.background(plan, lambda plans: self.confirm_restore(plans, what))

    def confirm_restore(self, plans, what="T"):
        _, title, _, log_dir = self.RESTORE[what]
        button = self.restore_button(what)
        button.setEnabled(True)
        plans = [p for p in plans if p[1]["operations"] or p[2]]
        if not any(p[1]["operations"] for p in plans):
            skipped = [f"{p[0]['sample']}: {name}（{why}）" for p in plans for name, why in p[2]]
            QMessageBox.information(self, title, "移動するファイルはありません。"
                                    + ("\n\n" + "\n".join(skipped[:15]) if skipped else ""))
            return
        n = sum(len({o["source"].rsplit(".", 1)[0] for o in p[1]["operations"]}) for p in plans)
        skipped = [f"{p[0]['sample']}: {name}（{why}）" for p in plans for name, why in p[2]]
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(f"{len(plans)} サンプルの {what}/stocked から {what} 直下へ {n} 件（付属の .tdms_index を含む）を移動します。"
                    + (f"\n移動しないもの {len(skipped)} 件（同名・同じ番号あり など）" if skipped else "")
                    + "\n\nよろしいですか？（「整理の履歴…」から元に戻せます）")
        box.setDetailedText("\n".join([p[3] for p in plans if p[3]] + (["移動しないもの:"] + skipped if skipped else [])))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        button.setEnabled(False)
        db_path = self.db.text().strip() if self.update_db.isChecked() else None

        def move(log, progress):
            for i, (r, plan, _, _) in enumerate(plans):
                progress(int(100 * i / len(plans)), f"移動 {r['sample']}")
                if not plan["operations"]:
                    continue
                try:
                    journal = server_anal.execute_restore(plan, log_dir)
                    log(f"● {r['group']} / {r['experiment']} / {r['sample']}: "
                        f"{len(plan['operations'])} ファイルを {what} へ移動（記録 {journal.name}）")
                except Exception as e:
                    log(f"✖ {r['experiment']} / {r['sample']}: {e}")
                if db_path:
                    try:
                        server_anal.refresh_db(db_path, r)
                    except Exception as e:
                        log(f"  ✖ DB 更新 {r['sample']}: {e}")
            if db_path:
                self.db_changed.emit()
            return []
        self.background(move, lambda _: (button.setEnabled(True), self.run.bar.setFormat(f"{title} 完了")))

    # -- duplicated numbers ----------------------------------------------------------
    # -- current state (anal_status): survey the checked samples, put right what can be ---------
    ISSUE_COLS = ["対処", "グループ", "実験", "サンプル", "内容", "件数", "説明", "手作業・対処"]
    # order in which checked actions are carried out (results and broken files first, then stocked, then duplicates)
    ACTION_ORDER = ["results_in_t", "empty_raw", "restore_t", "raw_copy", "broken_anal", "old_anal", "restore_anal"]
    ACTION_TEXT = {"results_in_t": "ANAL へ移す", "empty_raw": "T/broken へ移す", "restore_t": "T に戻す",
                   "raw_copy": "T/duplicate へ移す", "old_anal": "削除（元に戻せる）",
                   "broken_anal": "削除して解析し直す（元に戻せる）", "restore_anal": "ANAL に戻す"}
    MANUAL_KINDS = ("renumber", "renumber_anal", "results_b", "empty_raw_stocked")

    def build_status(self):
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        self.status_label = QLabel("現状: チェックしたサンプルで「現状を調べる」を押すと、整理が必要なものを一覧にします")
        row.addWidget(self.status_label, 1)
        self.only_todo = QCheckBox("未解析だけのものは隠す")
        self.only_todo.setChecked(False)
        self.only_todo.toggled.connect(self.fill_issues)
        row.addWidget(self.only_todo)
        self.b_fix = QPushButton("チェックした対処を実行…")
        self.b_fix.setObjectName("primary")
        self.b_fix.setToolTip("下の一覧でチェックした対処を、実行前に調べ直してから行います。移動・削除はすべて記録され、"
                              "「整理の履歴…」から元に戻せます（削除は ANAL\\.整理履歴 への移動）")
        self.b_fix.clicked.connect(self.run_actions)
        row.addWidget(self.b_fix)
        b_all = QPushButton("対処をすべてチェック")
        b_all.clicked.connect(lambda: self.check_issues(True))
        row.addWidget(b_all)
        b_redo = QPushButton("ANAL をやり直す…")
        b_redo.setToolTip("番号を付け直したサンプルなど：チェックしたサンプルの ANAL と ANAL/stocked の A@@ をすべて "
                          "ANAL\\.整理履歴 へ移し（元に戻せます）、T の生データをすべて解析し直せる状態にします")
        b_redo.clicked.connect(self.redo_selected)
        row.addWidget(b_redo)
        b_archive = QPushButton("解析済み T → T/stocked…")
        b_archive.setToolTip("チェックしたサンプルの、解析済みの生データを T/stocked へ退避します。"
                             "未解析・欠番は T に残します")
        b_archive.clicked.connect(self.archive_t)
        row.addWidget(b_archive)
        b_hist = QPushButton("整理の履歴…")
        b_hist.setToolTip("ここと実験ファイルDBで行った移動・削除の記録を一覧にし、選んだ操作を元に戻します")
        b_hist.clicked.connect(self.show_journals)
        row.addWidget(b_hist)
        bl.addLayout(row)
        self.issue_table = QTableWidget(0, len(self.ISSUE_COLS))
        self.issue_table.setHorizontalHeaderLabels(self.ISSUE_COLS)
        self.issue_table.verticalHeader().setVisible(False)
        self.issue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.issue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.issue_table.setToolTip("行をダブルクリックすると、そのサンプルの T フォルダをエクスプローラーで開きます")
        self.issue_table.cellDoubleClicked.connect(self.open_issue_folder)
        bl.addWidget(self.issue_table, 1)
        self.issues = []  # [(row, issue)]
        return box

    def show_journals(self):
        JournalDialog(self, ("ANAL", "T_stock", "BNAL")).exec()

    def archive_t(self):
        """Analysed raw files of the checked samples: T -> T/stocked (what the file manager's Data 退避 tab did)."""
        rows = [r for r in self.selected() if r["reachable"]]
        if not rows:
            QMessageBox.information(self, "T → T/stocked", "退避するサンプルにチェックを入れてください。")
            return
        if self.running_now():
            return
        box = QMessageBox(self)
        box.setWindowTitle("T → T/stocked")
        box.setText(f"{len(rows)} サンプルの、解析済み（ANAL に同じ番号の空でない A@@ がある）生データを T/stocked へ移す"
                    "予定を作ります。未解析・欠番の生データは T に残します。")
        keep = QCheckBox("最後の解析番号は T に残す（書きかけ対策）")
        keep.setChecked(True)
        box.setCheckBox(keep)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return
        keep_last = keep.isChecked()

        def plan(log, progress):
            out = []
            for i, r in enumerate(rows):
                progress(int(100 * i / len(rows)), f"退避の予定 {r['sample']}")
                try:
                    p, skipped = t_stock.plan_moves(t_stock.scan_t(server_anal.t_dir(r)), False, keep_last)
                    out.append((r, p, skipped, ""))
                except Exception as e:
                    out.append((r, None, [], str(e)))
            return out

        def confirm(plans):
            todo = [(r, p) for r, p, _, err in plans if p and p["operations"]]
            files = lambda p: len({Path(o["source"]).stem for o in p["operations"]})
            lines = [f"・{r['experiment']} / {r['sample']}: " + (f"エラー {err}" if err else
                     f"{files(p)} 件を退避" + (f"、残す {len(skipped)} 件" if skipped else ""))
                     for r, p, skipped, err in plans]
            if not todo:
                QMessageBox.information(self, "T → T/stocked", "退避するファイルはありません。\n\n" + "\n".join(lines[:30]))
                return
            if QMessageBox.question(self, "T → T/stocked", "\n".join(lines[:30]) + "\n\n実行しますか？（記録が残り、"
                                    "「整理の履歴…」から元に戻せます）") != QMessageBox.StandardButton.Yes:
                return
            db_path = self.db.text().strip() if self.update_db.isChecked() else None

            def job(log, progress, cancelled=lambda: False):
                for r, p in todo:
                    try:
                        journal = server_anal.execute_restore(p)
                        log(f"■ {r['experiment']} / {r['sample']}: T → T/stocked {files(p)} 件（記録 {journal}）")
                        if db_path:
                            server_anal.refresh_db(db_path, r)
                    except Exception as e:
                        log(f"✖ {r['experiment']} / {r['sample']}: {e}")
                return []
            self.run.start(job, lambda _: self.actions_done([r for r, _ in todo]))
        self.background(plan, confirm)

    def check_folders(self):
        """Survey the checked samples on the server (nothing is changed)."""
        rows = [r for r in self.selected() if r["reachable"]]
        if not rows:
            QMessageBox.information(self, "ANAL（サーバー）", "調べるサンプルにチェックを入れてください（接続できるグループのみ）。")
            return
        settle = self.settle.value()
        self.b_check.setEnabled(False)
        self.b_check.setText("調査中…")

        def job(log, progress):
            out = []
            for i, r in enumerate(rows):
                progress(int(100 * i / len(rows)), f"現状を調査 {r['sample']}（{i + 1}/{len(rows)}）")
                try:
                    out.append((r, anal_status.survey(server_anal.t_dir(r), settle), ""))
                except Exception as e:
                    out.append((r, [], str(e)))
            return out
        self.background(job, self.surveyed)

    def surveyed(self, results):
        keys = {self.key(r) for r, _, _ in results}
        self.surveyed_keys = getattr(self, "surveyed_keys", set()) | keys
        self.issues = [(r, i) for r, i in self.issues if self.key(r) not in keys]
        for r, issues, err in results:
            k = self.key(r)
            if err:
                self.live[k], self.t_notes[k] = f"エラー: {err}", ""
                continue
            pending = next((i for i in issues if i["kind"] == "pending"), None)
            fix = [i for i in issues if i["action"]]
            manual = [i for i in issues if i["kind"] in self.MANUAL_KINDS]
            self.live[k] = "　".join(x for x in (
                f"未解析 {pending['count']}" if pending else "未解析 0",
                f"整理 {len(fix)} 種" if fix else "", f"手作業 {len(manual)} 種" if manual else "") if x)
            self.t_notes[k] = "、".join(i["label"] for i in manual)
            self.issues += [(r, i) for i in issues]
        self.b_check.setEnabled(True)
        self.b_check.setText("現状を調べる")
        self.run.bar.setFormat(f"調査完了 ({len(results)} サンプル)")
        self.run.bar.setValue(100)
        self.fill()
        self.fill_issues()

    def fill_issues(self, *_):
        shown = [(r, i) for r, i in self.issues if not (self.only_todo.isChecked() and i["kind"] == "pending")]
        # what needs doing first: hand work, then what can be put right here, then information
        rank = lambda i: 0 if i["kind"] in self.MANUAL_KINDS else 1 if i["action"] else 2
        shown.sort(key=lambda ri: (rank(ri[1]), ri[0]["group"], ri[0]["experiment"], ri[0]["sample"]))
        self.issue_shown = shown
        self.issue_table.setRowCount(len(shown))
        tints = {0: ui_theme.row("danger", 0.22), 1: ui_theme.row("warning", 0.18), 2: FIELD}
        for n, (r, i) in enumerate(shown):
            todo = self.ACTION_TEXT.get(i["action"], "") if i["action"] else i["manual"]
            values = ["", r["group"], r["experiment"], r["sample"], i["label"], i["count"], i["detail"], todo]
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                item.setBackground(QColor(tints[rank(i)]))
                if c == 0 and i["action"]:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                elif c == 0:  # hand work and information: nothing to carry out here
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                if c in (6, 7) or c == 4:
                    files = "\n".join(Path(f).name for f in i["files"][:30]) + ("\n…" if len(i["files"]) > 30 else "")
                    item.setToolTip(i["detail"] + (f"\n\n{files}" if files else ""))
                if c == 4 and rank(i) == 0:
                    item.setForeground(QColor(DANGER))
                self.issue_table.setItem(n, c, item)
        self.issue_table.resizeColumnsToContents()
        n_fix = sum(1 for _, i in shown if i["action"])
        n_manual = sum(1 for _, i in shown if i["kind"] in self.MANUAL_KINDS)
        samples, with_issues = len(getattr(self, "surveyed_keys", ())), len({self.key(r) for r, _ in self.issues})
        self.status_label.setText(f"現状: {samples} サンプルを調査（問題あり {with_issues}）　手作業が必要 {n_manual} 件（赤）・"
                                  f"ここで整理できる {n_fix} 件（黄・チェックして実行）・情報（そのほか）")

    def check_issues(self, on):
        for n, (_, i) in enumerate(self.issue_shown):
            if i["action"]:
                self.issue_table.item(n, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def open_issue_folder(self, n, _):
        import os as _os
        try:
            _os.startfile(str(server_anal.t_dir(self.issue_shown[n][0])))
        except OSError as e:
            QMessageBox.warning(self, "ANAL（サーバー）", f"フォルダを開けません:\n{e}")

    def run_actions(self):
        picked = [(r, i) for n, (r, i) in enumerate(self.issue_shown)
                  if i["action"] and self.issue_table.item(n, 0).checkState() == Qt.CheckState.Checked]
        if not picked:
            QMessageBox.information(self, "現状の整理", "一覧で、実行する対処にチェックを入れてください。")
            return
        if self.running_now():
            return
        text = "\n".join(f"・{r['experiment']} / {r['sample']}: {i['label']} {i['count']} 件 → {self.ACTION_TEXT[i['action']]}"
                         for r, i in picked[:25]) + (f"\n…ほか {len(picked) - 25} 件" if len(picked) > 25 else "")
        if QMessageBox.question(self, "現状の整理", f"次の対処を行います（実行の直前にサーバーで調べ直します）。\n\n{text}\n\n"
                                "移動・削除はすべて記録され、「整理の履歴…」から元に戻せます。実行しますか？"
                                ) != QMessageBox.StandardButton.Yes:
            return
        by_sample = {}
        for r, i in picked:
            by_sample.setdefault(self.key(r), (r, set()))[1].add(i["action"])
        settle = self.settle.value()
        db_path = self.db.text().strip() if self.update_db.isChecked() else None

        def job(log, progress, cancelled=lambda: False):
            done = []
            for n, (r, kinds) in enumerate(by_sample.values()):
                log(f"■ {r['group']} / {r['experiment']} / {r['sample']}")
                t = server_anal.t_dir(r)
                for kind in [k for k in self.ACTION_ORDER if k in kinds]:
                    progress(int(100 * n / len(by_sample)), f"整理 {r['sample']}: {self.ACTION_TEXT[kind]}")
                    try:
                        for line in anal_status.apply(t, kind, settle):
                            log("  " + line)
                    except Exception as e:
                        log(f"  ✖ {self.ACTION_TEXT[kind]}: {e}")
                if db_path:
                    try:
                        new = server_anal.refresh_db(db_path, r)
                        log(f"  DB 更新: T {new['t_tdms']}  ANAL {new['anal_tdms']}")
                    except Exception as e:
                        log(f"  ✖ DB 更新: {e}")
                done.append(r)
            return []
        done = []
        self.run.start(job, lambda _: self.actions_done(done))

    def actions_done(self, rows):
        if self.update_db.isChecked():
            self.db_changed.emit()
        if rows:  # show the state after the changes
            self.set_checked(self.checked | {self.key(r) for r in rows})
            self.check_folders()

    def redo_selected(self):
        rows = [r for r in self.selected() if r["reachable"]]
        if not rows:
            QMessageBox.information(self, "ANAL をやり直す", "やり直すサンプルにチェックを入れてください。")
            return
        if self.running_now():
            return

        def count(log, progress):
            return [(r, len(anal_status.redo_files(server_anal.t_dir(r)))) for r in rows]

        def confirm(counts):
            text = "\n".join(f"・{r['experiment']} / {r['sample']}: A@@ {n} 件" for r, n in counts[:25])
            if QMessageBox.warning(
                    self, "ANAL をやり直す",
                    f"次のサンプルの ANAL と ANAL/stocked の A@@ をすべて ANAL\\.整理履歴 へ移します。\n\n{text}\n\n"
                    "移したあとは T の生データがすべて未解析になります（「解析を実行」で解析し直してください）。"
                    "移した A@@ は「整理の履歴…」から元に戻せます。\n"
                    "※ この A@@ から作った BNAL（B@@）も、解析し直したあとで作り直しが必要です。\n\n実行しますか？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            db_path = self.db.text().strip() if self.update_db.isChecked() else None

            def job(log, progress, cancelled=lambda: False):
                for r, _ in counts:
                    log(f"■ {r['experiment']} / {r['sample']}")
                    try:
                        for line in anal_status.redo(server_anal.t_dir(r)):
                            log("  " + line)
                        if db_path:
                            server_anal.refresh_db(db_path, r)
                    except Exception as e:
                        log(f"  ✖ {e}")
                return []
            self.run.start(job, lambda _: self.actions_done([r for r, _ in counts]))
        self.background(count, confirm)

    # -- run ------------------------------------------------------------------
    def build_job(self):
        rows = [dict(r) for r in self.selected()]
        if not rows:
            QMessageBox.information(self, "ANAL（サーバー）", "解析するサンプルにチェックを入れてください。")
            return None
        down = [r for r in rows if not r["reachable"]]
        if down and QMessageBox.question(
                self, "ANAL（サーバー）", f"DB で接続失敗になっているグループのサンプルが {len(down)} 件あります。"
                                         "このまま試しますか？") != QMessageBox.StandardButton.Yes:
            return None
        chosen = self.ask_condition(f"{len(rows)} サンプルの T 直下にある未解析のファイルを、この条件で解析します。")
        if not chosen:
            return None
        cond, folder, settle = chosen, self.folder.isChecked(), self.settle.value()
        db_path = self.db.text().strip() if self.update_db.isChecked() else None

        def refresh(log):
            """Re-count the selected samples and save them to the inventory (database app's refresh_sample)."""
            if not db_path:
                return
            done = 0
            for r in rows:
                try:
                    new = server_anal.refresh_db(db_path, r)
                    done += 1
                    log(f"  DB 更新 {r['experiment']} / {r['sample']}: T {new['t_tdms']}  ANAL {new['anal_tdms']}")
                except Exception as e:
                    log(f"  ✖ DB 更新 {r['experiment']} / {r['sample']}: {e}")
            if done:
                self.db_changed.emit()

        def finished_samples(log):
            """Samples with nothing left to analyse (checked on the server) are unchecked."""
            keys = []
            for r in rows:
                try:
                    todo, busy = server_anal.pending_files(server_anal.t_dir(r), settle)
                except Exception:
                    continue
                if not todo and not busy:
                    keys.append(self.key(r))
            if keys:
                log(f"  解析が終わった {len(keys)} サンプルのチェックを外しました")
                self.samples_done.emit(keys)

        def job(log, progress, cancelled=lambda: False):
            plan = []
            for i, r in enumerate(rows):
                progress(0, f"フォルダ確認 {i + 1}/{len(rows)}")
                t = server_anal.t_dir(r)
                try:
                    todo, busy = server_anal.pending_files(t, settle)
                except Exception as e:
                    log(f"✖ {r['group']} / {r['experiment']} / {r['sample']}: {e}")
                    continue
                todo = broken_out(t, todo, log)
                log(f"□ {r['group']} / {r['experiment']} / {r['sample']}  未解析 {len(todo)}"
                    + (f"（書き込み中のため保留 {len(busy)}）" if busy else ""))
                plan += [(r, t, f) for f in todo]
            if not plan:
                log("解析するファイルはありません。")
                refresh(log)
                finished_samples(log)
                return []
            outs, failed = [], []
            for k, (r, t, f) in enumerate(plan):
                if cancelled():
                    log("■ 中止しました")
                    break
                if server_anal.original_key(f.name) in server_anal.analysed_keys(t):
                    log(f"  - {f.name[:60]}  （ほかで解析済みのため飛ばします）")
                    continue
                t0 = time.perf_counter()
                try:
                    out, tables = first_step_io.process_file(
                        f, t / "ANAL", cond, False, make_progress(progress, k, len(plan), f.name), folder)
                except Exception as e:
                    failed.append(f)
                    log(f"  ✖ {f.name}\n    {e}")
                    server_anal.log_run(group=r["group"], experiment=r["experiment"], sample=r["sample"],
                                        raw=str(f), out="", seconds=f"{time.perf_counter() - t0:.1f}",
                                        status="error", error=str(e), condition_set=cond["condition_set"])
                    continue
                ar = tables["AR Table"].iloc[0]
                log(f"  ● {out.name}  信号 {int(ar['Signal#Sum'])}  バースト {int(ar['Burst#Sum'])}")
                server_anal.log_run(group=r["group"], experiment=r["experiment"], sample=r["sample"],
                                    raw=str(f), out=str(out), seconds=f"{time.perf_counter() - t0:.1f}",
                                    status="ok", error="", condition_set=cond["condition_set"])
                outs.append(str(out))
            log(f"完了 {len(outs)} / {len(plan)} ファイル" + (f"　失敗 {len(failed)}" if failed else "")
                + f"　記録: {server_anal.LOG_CSV}")
            refresh(log)
            finished_samples(log)
            return outs
        names = ", ".join(f"{r['experiment']}/{r['sample']}" for r in rows)
        return {"kind": "ANAL（サーバー）", "desc": f"{len(rows)} サンプル  ({names[:90]})",
                "fn": job, "a_files": []}

    def start(self):
        job = self.build_job()
        if job:
            self.run.start(job["fn"])

    def send(self):
        job = self.build_job()
        if job:
            self.send_to_queue(job)
            self.run.log.appendPlainText(f"→ キューに送りました: {job['desc']}")

    # -- automatic start (全自動着手) ----------------------------------------------------
    def show_resume(self):
        state = server_anal.load_state()
        stopped = bool(state) and state.get("status") != "完了"
        if stopped:
            left = [s for s in state["samples"] if s["status"] != "完了"]
            cur = state.get("current") or {}
            self.resume_label.setText(
                f"⚠ 前回の全自動着手（{state['started']}）は途中で止まっています。残り {len(left)} サンプル"
                + (f"（最後に解析したファイル: {cur.get('sample', '')} {cur.get('file', '')[:40]}）" if cur else "")
                + "。「前回の続きから再開」で続けられます。")
        else:
            self.resume_label.setText("")
        self.b_resume.setVisible(stopped)
        self.b_discard.setVisible(stopped)

    def start_auto(self):
        if self.running_now():
            return
        rows = [r for r in self.selected() if r["state"] in (server_anal.TODO, server_anal.RUNNING)]
        other = len(self.selected()) - len(rows)
        if not rows:
            QMessageBox.information(self, "全自動着手", "未着手・進行中のサンプルにチェックを入れてください（「自動選択」も使えます）。")
            return
        old = server_anal.load_state()
        if old and old.get("status") != "完了" and QMessageBox.question(
                self, "全自動着手", "途中で止まった前回の全自動着手があります。その記録を破棄して新しく始めますか？\n"
                                  "（続ける場合は「前回の続きから再開」を使ってください）") != QMessageBox.StandardButton.Yes:
            return
        stocked = sum(r["t_stocked_tdms"] or 0 for r in rows)
        text = (f"{len(rows)} サンプルを順番に処理します。\n"
                f"  1. T/stocked のファイルを T 直下へ戻す（DB 上 {stocked} 件、同名があるものは戻さない）\n"
                "  2. 整理できるものを整理（元に戻せます）：T の A@@ → ANAL、空の生データ → T/broken、"
                "同じ内容の生データのコピー → T/duplicate、同じ生データの古い ANAL を削除\n"
                "     同じ #番号の別の測定があるサンプルは、番号の付け直しが必要なので解析しません（レポートに記録）\n"
                "  3. ANAL に同じ #番号がある T 直下のファイルは解析しない。残りを解析\n"
                + ("  4. DB を更新\n" if self.update_db.isChecked() else "")
                + (f"\n（完了のサンプル {other} 件は対象外）" if other else "")
                + "\n\n中止するとファイルの区切りで止まり、あとから再開できます。始めますか？")
        if QMessageBox.question(self, "全自動着手", text) != QMessageBox.StandardButton.Yes:
            return
        cond = self.ask_condition(f"全自動着手の {len(rows)} サンプルを、この条件で解析します。")
        if not cond:
            return
        state = {"started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "status": "実行中", "current": None,
                 "report": str(server_anal.Report().path),
                 "options": {"cond": cond, "folder": self.folder.isChecked(),
                             "settle": self.settle.value(),
                             "db_path": self.db.text().strip() if self.update_db.isChecked() else None},
                 "samples": [{"root": r["root"], "experiment": r["experiment"], "sample": r["sample"],
                              "group": r["group"], "status": "待機"} for r in rows]}
        server_anal.save_state(state)
        self.run.start(self.auto_job(state))
        self.show_resume()

    def resume_auto(self):
        state = server_anal.load_state()
        if not state or self.running_now():
            return
        self.run.start(self.auto_job(state))

    def discard_auto(self):
        if QMessageBox.question(self, "全自動着手", "中止した全自動着手の記録を消します（レポートは残ります）。"
                                "よろしいですか？") == QMessageBox.StandardButton.Yes:
            server_anal.clear_state()
            self.show_resume()

    def running_now(self):
        if self.run.thread is not None and self.run.thread.isRunning():
            QMessageBox.information(self, "全自動着手", "実行中の処理が終わってから操作してください。")
            return True
        return False

    def auto_job(self, state):
        opt = state["options"]
        cond, folder, settle, db_path = opt["cond"], opt["folder"], opt["settle"], opt["db_path"]

        def job(log, progress, cancelled=lambda: False):
            report = server_anal.Report(state["report"])
            resumed = state["status"] != "実行中" or any(s["status"] != "待機" for s in state["samples"])
            state["status"] = "実行中"
            server_anal.save_state(state)
            if resumed:
                report.write(f"\n===== 再開 {datetime.now():%Y-%m-%d %H:%M:%S} =====")
            else:
                report.write(f"全自動着手レポート  開始 {state['started']}")
                report.write(f"対象 {len(state['samples'])} サンプル　解析条件 {cond}　結果フォルダ {'作成' if folder else 'なし'}"
                             f"　書き込み中とみなす {settle} 分　DB 更新 {'あり' if db_path else 'なし'}")
            log(f"レポート: {report.path}")
            outs = []
            todo_samples = [s for s in state["samples"] if s["status"] != "完了"]
            stopped = False
            for i, s in enumerate(todo_samples):
                if cancelled():
                    stopped = True
                    break
                where = f"{s['group']} / {s['experiment']} / {s['sample']}"
                report.write(f"\n■ {where}")
                log(f"■ {where}")
                t = server_anal.t_dir(s)
                try:
                    # 1. T/stocked -> T
                    if server_anal._tdms(t / "stocked"):
                        plan, skipped, _ = server_anal.restore_plan(s)
                        moved = len({o["source"].rsplit(".", 1)[0] for o in plan["operations"]})
                        if plan["operations"]:
                            journal = server_anal.execute_restore(plan)
                            report.write(f"  T/stocked → T: {moved} 件を移動（記録 {journal}）")
                        for name, why in skipped:
                            report.write(f"  T/stocked に残したもの: {name}（{why}）")
                        log(f"  T/stocked → T {moved} 件" + (f"、残した {len(skipped)} 件" if skipped else ""))
                    # 2. tidy what can be (A@@ in T, empty raw files, identical copies, older analyses of one raw
                    #    file). One number used by two measurements needs new numbers first: the sample waits for that.
                    issues = anal_status.survey(t, settle)
                    block = anal_status.blocking(issues)
                    if block:
                        s["status"] = "要確認"
                        for i in block:
                            report.write(f"  ⚠ {i['label']}: {i['detail']} → {i['manual']}（このサンプルは解析しません）")
                            log(f"  ⚠ {i['label']}（解析しません）")
                        server_anal.save_state(state)
                        continue
                    for kind in ("results_in_t", "empty_raw", "raw_copy", "broken_anal", "old_anal"):
                        if any(i["action"] == kind for i in issues):
                            for line in anal_status.apply(t, kind, settle):
                                report.write("  " + line)
                                log("  " + line)
                    # 3. analyse T files without ANAL
                    todo, busy = server_anal.pending_files(t, settle)
                    todo = broken_out(t, todo, log, report)
                    total = len(server_anal._tdms(t))
                    report.write(f"  T 直下 {total} 件：ANAL あり（解析しない） {total - len(todo) - len(busy)} 件、"
                                 f"解析する {len(todo)} 件" + (f"、書き込み中のため保留 {len(busy)} 件" if busy else ""))
                except Exception as e:
                    s["status"] = "エラー"
                    report.write(f"  ✖ エラー: {e}")
                    log(f"  ✖ {e}")
                    server_anal.save_state(state)
                    continue
                s["status"] = "解析中"
                failed = 0
                for k, f in enumerate(todo):
                    if cancelled():
                        stopped = True
                        break
                    if server_anal.original_key(f.name) in server_anal.analysed_keys(t):
                        report.write(f"  - {f.name}（ほかで解析済み）")
                        continue
                    t0 = time.perf_counter()
                    try:
                        out, tables = first_step_io.process_file(
                            f, t / "ANAL", cond, False,
                            make_progress(progress, k, max(len(todo), 1), f"[{i + 1}/{len(todo_samples)}] {f.name}"),
                            folder)
                        ar = tables["AR Table"].iloc[0]
                        report.write(f"  ● {out.name}  信号 {int(ar['Signal#Sum'])}  バースト {int(ar['Burst#Sum'])}")
                        log(f"  ● {out.name}")
                        outs.append(str(out))
                        status, err = "ok", ""
                    except Exception as e:
                        failed += 1
                        report.write(f"  ✖ {f.name}: {e}")
                        log(f"  ✖ {f.name}: {e}")
                        status, err = "error", str(e)
                    server_anal.log_run(group=s["group"], experiment=s["experiment"], sample=s["sample"], raw=str(f),
                                        out=str(out) if status == "ok" else "",
                                        seconds=f"{time.perf_counter() - t0:.1f}", status=status, error=err,
                                        condition_set=cond.get("condition_set", "（記録なし）"))
                    state["current"] = {"sample": f"{s['experiment']} / {s['sample']}", "file": f.name}
                    server_anal.save_state(state)
                if db_path:
                    try:
                        new = server_anal.refresh_db(db_path, s)
                        report.write(f"  DB 更新: T {new['t_tdms']}  ANAL {new['anal_tdms']}")
                    except Exception as e:
                        report.write(f"  ✖ DB 更新: {e}")
                s["status"] = "中止" if stopped else ("完了" if not failed else "エラー")
                report.write(f"  → {s['status']}" + (f"（失敗 {failed} 件）" if failed else ""))
                server_anal.save_state(state)
                if stopped:
                    break
            left = [s for s in state["samples"] if s["status"] != "完了"]
            if stopped:
                state["status"] = "中止"
                msg = (f"中止しました（{datetime.now():%H:%M:%S}）。完了 {len(state['samples']) - len(left)} / "
                       f"{len(state['samples'])} サンプル。残り {len(left)} サンプルは「前回の続きから再開」で再開できます。")
            else:
                state["status"] = "完了" if not left else "エラーあり"
                msg = (f"終了（{datetime.now():%H:%M:%S}）。完了 {len(state['samples']) - len(left)} / "
                       f"{len(state['samples'])} サンプル" + (f"、エラー {len(left)} サンプル（再開で再試行できます）"
                                                           if left else ""))
            report.write(f"\n{msg}")
            log(msg + f"\nレポート: {report.path}")
            if state["status"] == "完了":
                server_anal.clear_state()
            else:
                server_anal.save_state(state)
            if db_path:
                self.db_changed.emit()
            done = [(s["root"], s["experiment"], s["sample"]) for s in state["samples"] if s["status"] == "完了"]
            if done:
                self.samples_done.emit(done)
            self.auto_changed.emit()
            return outs
        return job

    # -- analysis conditions ----------------------------------------------------
    def show_condition(self):
        self.cond_label.setText("解析条件: " + conditions.describe("anal", conditions.current("anal")))

    def manage_conditions(self):
        ConditionDialog(self, "anal", run=False).exec()
        self.show_condition()

    def ask_condition(self, summary):
        """Values of the chosen set with its name (cond["condition_set"]), or None when cancelled."""
        chosen = ask_condition(self, "anal", summary)
        self.show_condition()
        return None if chosen is None else {**chosen["values"], "condition_set": chosen["name"]}

    def update_db_now(self):
        rows = self.selected()
        if not rows:
            QMessageBox.information(self, "DB を更新", "更新するサンプルにチェックを入れてください。")
            return
        path = self.db.text().strip()

        def job(log, progress):
            for i, r in enumerate(rows):
                progress(int(100 * i / len(rows)), f"DB 更新 {r['sample']}")
                try:
                    new = server_anal.refresh_db(path, r)
                    log(f"DB 更新 {r['experiment']} / {r['sample']}: T {new['t_tdms']}  T/stocked {new['t_stocked_tdms']}"
                        f"  ANAL {new['anal_tdms']}")
                except Exception as e:
                    log(f"✖ DB 更新 {r['experiment']} / {r['sample']}: {e}")
            return []
        self.background(job, lambda _: (self.reload(), self.run.bar.setFormat("DB 更新 完了")))

    def finished(self, outputs):
        if not self.update_db.isChecked():
            self.run.log.appendPlainText("※ DB の件数は、実験ファイル データベースでそのグループ / 実験を更新すると反映されます。")


# ---------------------------------------------------------------------------
# 2nd step on the analysis servers (BNAL@<target> for a list of sample x target combinations)
# ---------------------------------------------------------------------------
BNAL_LOG = Path(__file__).resolve().parent.parent / "logs" / "bnal_runs.csv"
BNAL_TINT = {"未確認": ui_theme.row("info"), "未計数": ui_theme.row("info"), "ANALなし": ui_theme.row("violet"),
             "未着手": ui_theme.row("danger"), "進行中": ui_theme.row("warning"), "完了": ui_theme.row("success"),
             "超過": ui_theme.row("violet"), "対象なし": ui_theme.row("neutral", 0.1)}
BNAL_TEXT = {"未着手": DANGER, "進行中": WARNING, "完了": SUCCESS, "超過": VIOLET, "ANALなし": VIOLET,
             "未確認": INFO, "未計数": INFO}


def check_table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    return t


def keep_widths(t, key, defaults):
    """Column widths: the defaults, or the ones last dragged by the user (saved under key)."""
    saved = settings().value(key)
    widths = [int(v) for v in saved] if saved and len(saved) == len(defaults) else defaults
    for c, w in enumerate(widths):
        t.setColumnWidth(c, w)
    t.horizontalHeader().sectionResized.connect(
        lambda *_: settings().setValue(key, [t.columnWidth(c) for c in range(t.columnCount())]))


def buttons(row, items):
    out = []
    for text, slot in items:
        b = QPushButton(text)
        b.clicked.connect(slot)
        row.addWidget(b)
        out.append(b)
    return out


def colored_seq(seq, colors, size=10):
    """Sequence as HTML, each letter in the colour of its species."""
    return (f"<span style='font-family:Consolas;font-size:{size}pt'>"
            + "".join(f"<span style='color:{colors.get(ch, (TEXT,))[0]}'>{ch}</span>" for ch in seq)
            + "</span>")


class TargetDialog(QDialog):
    """Create targets: type name + sequence, generate modified variants, save to a SEQlist csv."""

    def __init__(self, parent, bc, seq_dir, current_csv, base=None):
        super().__init__(parent)
        self.setWindowTitle("target の作成")
        self.resize(900, 620)
        self.bc, self.seq_dir, self.current_csv = bc, Path(seq_dir), current_csv
        self.colors = server_bnal.species(bc)
        self.saved = None
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(base[0] if base else "")
        self.seq = QLineEdit(base[1] if base else "")
        self.seq.setFont(QFont("Consolas", 11))
        self.preview = QLabel("")
        self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.seq.textChanged.connect(self.update_preview)
        form.addRow("target 名", self.name)
        form.addRow("配列", self.seq)
        form.addRow("", self.preview)
        lay.addLayout(form)
        row = QHBoxLayout()
        b_add = QPushButton("↓ 一覧に追加")
        b_add.clicked.connect(lambda: self.add(self.name.text().strip(), self.clean(self.seq.text())))
        row.addWidget(b_add)
        row.addSpacing(20)
        row.addWidget(QLabel("修飾体を作る：文字"))
        self.mod_letter = QComboBox()
        self.mod_letter.addItems([f"{b}  {n}" for b, n, *_ in bc if b != "B"])
        row.addWidget(self.mod_letter)
        row.addWidget(QLabel("位置（1始まり、カンマ区切り。空欄は置き換え元の文字の全位置）"))
        self.positions = QLineEdit()
        self.positions.setPlaceholderText("例  8, 22")
        row.addWidget(self.positions, 1)
        self.replace_from = QLineEdit()
        self.replace_from.setPlaceholderText("置き換え元 例 C")
        self.replace_from.setMaximumWidth(110)
        row.addWidget(self.replace_from)
        b_var = QPushButton("修飾体を生成")
        b_var.clicked.connect(self.make_variants)
        row.addWidget(b_var)
        lay.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["target 名", "配列", "長さ"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        buttons(row, [("選択行を削除", self.delete_rows)])
        row.addStretch()
        self.msg = QLabel("")
        self.msg.setObjectName("subtitle")
        row.addWidget(self.msg)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addStretch()
        buttons(row, [(f"{current_csv} に追加…" if current_csv else "（CSV 未選択）", self.append_csv),
                      ("新しい CSV として保存…", self.save_new), ("閉じる", self.reject)])
        lay.addLayout(row)
        self.update_preview()

    @staticmethod
    def clean(text):
        return re.sub(r"\s", "", text).upper()

    def update_preview(self):
        seq = self.clean(self.seq.text())
        unknown = sorted(set(seq) - set(self.colors))
        self.preview.setText(colored_seq(seq, self.colors, 12) + f"　{len(seq)} 文字"
                             + (f"　<span style='color:{DANGER}'>BC に無い文字: {''.join(unknown)}</span>"
                                if unknown else ""))

    def add(self, name, seq):
        if not name or not seq:
            self.msg.setText("target 名と配列を入力してください")
            return
        names = [self.table.item(r, 0).text() for r in range(self.table.rowCount())]
        if name in names:
            self.msg.setText(f"{name} はすでに一覧にあります")
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name))
        self.table.setItem(r, 1, QTableWidgetItem(seq))
        self.table.item(r, 1).setFont(QFont("Consolas", 10))
        self.table.setItem(r, 2, QTableWidgetItem(str(len(seq))))
        self.msg.setText(f"{self.table.rowCount()} 件")

    def make_variants(self):
        name, seq = self.name.text().strip(), self.clean(self.seq.text())
        if not name or not seq:
            self.msg.setText("先に元の target 名と配列を入力してください")
            return
        letter = self.mod_letter.currentText().split()[0]
        text = self.positions.text().strip()
        if text:
            pos = [int(p) for p in re.findall(r"\d+", text)]
        else:
            src = self.clean(self.replace_from.text())[:1]
            if not src:
                self.msg.setText("位置か置き換え元の文字を入力してください")
                return
            pos = [i + 1 for i, ch in enumerate(seq) if ch == src]
        new = server_bnal.variants(name, seq, letter, pos)
        if name not in [self.table.item(r, 0).text() for r in range(self.table.rowCount())]:
            self.add(name, seq)
        for n, s in new:
            self.add(n, s)

    def delete_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def rows(self):
        return [(self.table.item(r, 0).text().strip(), self.clean(self.table.item(r, 1).text()))
                for r in range(self.table.rowCount()) if self.table.item(r, 0).text().strip()]

    def append_csv(self):
        rows = self.rows()
        if not rows or not self.current_csv:
            return
        path = self.seq_dir / self.current_csv
        existing = {n for n, _ in server_bnal.read_targets(path)}
        dup = [n for n, _ in rows if n in existing]
        if dup:
            QMessageBox.warning(self, "target の作成", "すでに CSV にある名前です:\n" + "\n".join(dup[:10]))
            return
        if QMessageBox.question(self, "target の作成", f"{path}\nに {len(rows)} 件を追加します。"
                                "（共有フォルダのファイルです）よろしいですか？") != QMessageBox.StandardButton.Yes:
            return
        server_bnal.write_targets(path, rows, append=True)
        self.saved = self.current_csv
        self.accept()

    def save_new(self):
        rows = self.rows()
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "新しい target CSV", str(self.seq_dir), "CSV (*.csv)")
        if path:
            server_bnal.write_targets(path, rows)
            self.saved = Path(path).name if Path(path).parent == self.seq_dir else None
            self.accept()


class HistoryDialog(QDialog):
    """Past BNAL runs (conditions, combinations, results); load their conditions back."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("解析条件の履歴")
        self.resize(1100, 520)
        self.entries = server_bnal.read_history()
        self.choice = None
        lay = QVBoxLayout(self)
        self.table = check_table(["日時", "組み合わせ", "BC", "RR MIN", "noise MAX", "Filter P", "変更したレベル",
                                  "モード", "結果"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setRowCount(len(self.entries))
        for i, e in enumerate(self.entries):
            c = e.get("conditions", {})
            vals = [e.get("time", ""), f"{len(e.get('combinations', []))} 組", c.get("bc_file", ""),
                    c.get("right_read_min", ""), c.get("noise_max", ""), c.get("filter_p", ""),
                    ", ".join(f"{k}={v:g}" for k, v in (c.get("overrides") or {}).items()),
                    e.get("mode", ""), e.get("result", "")]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))
        self.table.resizeColumnsToContents()
        self.table.itemSelectionChanged.connect(self.show_detail)
        lay.addWidget(self.table, 2)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Consolas", 9))
        lay.addWidget(self.detail, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel(f"保存場所: {server_bnal.HISTORY}"))
        row.addStretch()
        buttons(row, [("条件を読み込む", lambda: self.pick("conditions")),
                      ("条件と組み合わせを読み込む", lambda: self.pick("all")), ("閉じる", self.reject)])
        lay.addLayout(row)

    def show_detail(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            e = self.entries[rows[0].row()]
            lines = [f"{c['group']} / {c['experiment']} / {c['sample']} / {c['target']}  {c['sequence']}"
                     for c in e.get("combinations", [])]
            bases = e.get("conditions", {}).get("bases", {})
            lines += ["", "Base Table:"] + [f"  {t}: " + "  ".join(f"{b}={v:g}" for b, v in bt.items())
                                            for t, bt in bases.items()]
            self.detail.setPlainText("\n".join(lines))

    def pick(self, what):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.choice = (what, self.entries[rows[0].row()])
            self.accept()


class ServerBnalPage(QWidget):
    db_changed = pyqtSignal()
    combos_done = pyqtSignal(list)
    SAMPLE_COLS = ["グループ", "実験", "サンプル", "ANAL", "ANAL/stocked", "BNAL@"]
    TARGET_COLS = ["target", "配列", "長さ", "種別", "基本配列"]
    GROUP_FILTERS = {"対象＋未分類": {"target", None}, "BNAL 対象": {"target"}, "非対象": {"excluded"},
                     "未分類": {None}, "すべて": {"target", "excluded", None}}
    STATUS_COLS = ["グループ", "実験", "サンプル", "target", "種別", "配列", "ANAL", "ANAL/stocked", "BNAL TDMS", "進捗",
                   "状態", "注意", "実フォルダ（確認結果）"]

    def __init__(self, open_in_viewer, send_to_queue):
        super().__init__()
        self.open_in_viewer = open_in_viewer
        self.send_to_queue = send_to_queue
        self.samples, self.shown, self.checked = [], [], set()
        self.targets, self.checked_targets, self.bnals, self.live = [], set(), {}, {}
        self.combos = []          # [(sample row, target, sequence)] to analyse
        self.overrides = {}       # edited relative levels {letter: level}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(header("BNAL（サーバー）", "グループ・サンプルと target を選んで「組み合わせに追加」を繰り返し、"
                                               "組み合わせごとに ANAL のうち B@@ の無いものを解析して T\\BNAL@<target> に保存"))
        db_row = QHBoxLayout()
        self.db = QLineEdit(settings().value("inventory_db", str(server_anal.DEFAULT_DB)))
        db_row.addWidget(QLabel("データベース"))
        db_row.addWidget(self.db, 1)
        buttons(db_row, [("DB を再読込", self.reload)])
        lay.addLayout(db_row)

        top = QSplitter(Qt.Orientation.Horizontal)
        # (1) samples
        g1 = QGroupBox("① グループ → サンプル（ANAL のあるもの）")
        l1 = QVBoxLayout(g1)
        f1 = QHBoxLayout()
        self.group = QComboBox()
        self.search = QLineEdit()
        self.search.setPlaceholderText("実験名・サンプル名で検索")
        self.groups = server_bnal.read_groups()  # {sample id: 'target' | 'excluded'}
        self.bnal_filter = QComboBox()
        self.bnal_filter.addItems(list(self.GROUP_FILTERS))
        self.bnal_filter.setCurrentText(settings().value("bnal_filter", "対象＋未分類"))
        self.bnal_filter.setToolTip("BNAL 対象 / 非対象 の分類で絞り込みます（分類は下のボタンで設定）")
        f1.addWidget(self.group)
        f1.addWidget(self.bnal_filter)
        f1.addWidget(self.search, 1)
        l1.addLayout(f1)
        self.sample_table = check_table(self.SAMPLE_COLS)
        keep_widths(self.sample_table, "bnal_sample_widths", [132, 148, 117, 45, 85, 57])
        self.sample_table.itemChanged.connect(self.sample_checked)
        l1.addWidget(self.sample_table, 1)
        r1 = QHBoxLayout()
        buttons(r1, [("表示中を全選択", lambda: self.set_samples(self.checked | {self.key(r) for r in self.shown})),
                     ("全解除", lambda: self.set_samples(set())),
                     ("選択行にチェック", lambda: self.set_samples(self.checked | {
                         self.key(self.shown[i.row()]) for i in self.sample_table.selectedIndexes()}))])
        r1.addStretch()
        for text, value in (("→ 対象", "target"), ("→ 非対象", "excluded"), ("分類解除", None)):
            b = QPushButton(text)
            b.setToolTip("チェックしたサンプル（無ければ選択行）を BNAL " +
                         {"target": "対象にします", "excluded": "非対象にします", None: "未分類に戻します"}[value])
            b.clicked.connect(lambda _, v=value: self.classify(v))
            r1.addWidget(b)
        l1.addLayout(r1)
        self.bnal_filter.currentTextChanged.connect(
            lambda t: (settings().setValue("bnal_filter", t), self.fill_samples()))
        self.group.currentIndexChanged.connect(self.fill_samples)
        self.search.textChanged.connect(self.fill_samples)
        top.addWidget(g1)

        # (2) targets
        g2 = QGroupBox("② target リスト（SEQlist の CSV）→ target を選ぶ")
        l2 = QVBoxLayout(g2)
        f2 = QHBoxLayout()
        s = settings()
        if s.value("seq_kind") is None and s.value("bnal_csv"):  # settings from before the kinds: carry them over
            old = s.value("bnal_csv")
            kind = next((k for k in server_bnal.SEQ_KINDS if old in server_bnal.seq_files(k)), "Nucleotide")
            s.setValue("seq_kind", kind)
            s.setValue(f"bnal_csv/{kind}", old)
            if s.value("bc_file") in server_bnal.bc_files(kind):
                s.setValue(f"bc_file/{kind}", s.value("bc_file"))
        self.kind_box = QComboBox()
        self.kind_box.addItems(list(server_bnal.SEQ_KINDS))
        self.kind_box.setCurrentText(settings().value("seq_kind", "Nucleotide"))
        self.kind_box.setToolTip("Nucleotide → BC は Base、Peptide → BC は Amino acid のリストから選びます")
        self.kind_box.currentTextChanged.connect(
            lambda k: (settings().setValue("seq_kind", k), self.overrides.clear(), self.fill_dirs()))
        f2.addWidget(self.kind_box)
        self.seq_dir = QLineEdit(settings().value("seqlist_folder", str(server_bnal.SEQ_DIR)))
        self.seq_dir.setToolTip("SEQlist のフォルダ（machinedb の SEQlist。核酸・ペプチドは CSV の中身で見分けます）")
        f2.addWidget(self.seq_dir, 1)
        buttons(f2, [("参照…", self.choose_seq_dir)])
        l2.addLayout(f2)
        f3 = QHBoxLayout()
        self.csv_box = QComboBox()
        self.csv_box.setMinimumWidth(200)
        self.csv_box.currentTextChanged.connect(self.load_targets)
        f3.addWidget(self.csv_box, 1)
        buttons(f3, [("＋ target を作成…", self.create_targets)])
        l2.addLayout(f3)
        self.target_table = check_table(self.TARGET_COLS)
        keep_widths(self.target_table, "bnal_target_widths", [75, 82, 35, 62, 63])
        self.target_table.itemChanged.connect(self.target_checked)
        self.target_table.currentCellChanged.connect(lambda *_: self.show_bases())
        l2.addWidget(self.target_table, 1)
        r2 = QHBoxLayout()
        buttons(r2, [("全選択", lambda: self.set_targets({n for n, _ in self.targets})),
                     ("全解除", lambda: self.set_targets(set())),
                     ("基本配列のみ", lambda: self.set_targets(
                         {n for n, _ in self.targets if server_bnal.parent_target(n) == n})),
                     ("選択行にチェック", lambda: self.set_targets(self.checked_targets | {
                         self.targets[i.row()][0] for i in self.target_table.selectedIndexes()}))])
        r2.addStretch()
        l2.addLayout(r2)
        top.addWidget(g2)

        # (3) conditions
        g3 = QGroupBox("③ 解析条件")
        l3 = QVBoxLayout(g3)
        form = QFormLayout()
        self.bc_box = QComboBox()
        self.bc_box.currentTextChanged.connect(self.bc_changed)
        self.rrmin = spin(SECOND_DEFAULT["right_read_min"], 1, 100)
        self.noise = spin(SECOND_DEFAULT["noise_max"], 0, 100)
        self.filter_p = spin(SECOND_DEFAULT["filter_p"], 0, 100)
        form.addRow("塩基・アミノ酸の電流値 (BC)", self.bc_box)
        form.addRow("Right Read MIN", self.rrmin)
        form.addRow("noise MAX", self.noise)
        form.addRow("Filter P", self.filter_p)
        l3.addLayout(form)
        cr = QHBoxLayout()
        self.cond_label = QLabel("")
        self.cond_label.setObjectName("subtitle")
        cr.addWidget(self.cond_label, 1)
        b_cond = QPushButton("解析条件…")
        b_cond.setToolTip("BNAL の条件セット（BC・Right Read MIN・noise MAX・Filter P）の確認・追加・"
                          "「現在の条件」の切り替え（解析の実行前にも確認します）")
        b_cond.clicked.connect(self.manage_conditions)
        cr.addWidget(b_cond)
        l3.addLayout(cr)
        self.base_label = QLabel("Base Table（選択中の target）")
        l3.addWidget(self.base_label)
        # Base Table at about half the panel width, the run options beside it
        tr = QHBoxLayout()
        self.base_view = QTableWidget(0, 4)
        self.base_view.setHorizontalHeaderLabels(["文字", "名前", "種類", "相対レベル"])
        self.base_view.horizontalHeaderItem(3).setToolTip("ダブルクリックで編集できます")
        self.base_view.verticalHeader().setVisible(False)
        self.base_view.setMinimumHeight(170)
        self.base_view.itemChanged.connect(self.level_edited)
        tr.addWidget(self.base_view, 1)
        opts = QVBoxLayout()
        self.mode_resume = QRadioButton("未解析のみ")
        self.mode_resume.setToolTip("B@@ の無い ANAL だけを解析します（#番号で照合）")
        self.mode_redo = QRadioButton("再解析")
        self.mode_redo.setToolTip("既存の B@@ を BNAL@target\\old へ移動して、すべての ANAL を解析します")
        self.mode_resume.setChecked(True)
        modes = QButtonGroup(self)
        modes.addButton(self.mode_resume)
        modes.addButton(self.mode_redo)
        self.update_db = QCheckBox("DB更新")
        self.update_db.setToolTip("解析後に、解析したサンプルだけ実験ファイル データベースを更新します")
        self.update_db.setChecked(True)
        for w in (self.mode_resume, self.mode_redo):
            opts.addWidget(w)
        opts.addSpacing(10)
        opts.addWidget(self.update_db)
        opts.addStretch()
        tr.addLayout(opts, 0)
        l3.addLayout(tr, 1)
        r3 = QHBoxLayout()
        buttons(r3, [("BC の値に戻す", self.reset_levels), ("BC ファイルとして保存…", self.save_bc),
                     ("履歴…", self.open_history)])
        r3.addStretch()
        l3.addLayout(r3)
        top.addWidget(g3)
        # default widths of the three panels (user's layout); a dragged layout is remembered
        saved = settings().value("bnal_splitter")
        top.setSizes([int(v) for v in saved] if saved else [655, 511, 458])
        top.splitterMoved.connect(lambda *_: settings().setValue("bnal_splitter", top.sizes()))
        lay.addWidget(top, 3)

        # (4) combinations to analyse, and the current state of the checked samples
        self.bottom = QTabWidget()
        combo_box = QWidget()
        cl = QVBoxLayout(combo_box)
        cl.setContentsMargins(0, 4, 0, 0)
        st = QHBoxLayout()
        add = QPushButton("↓  チェックしたサンプル × target を組み合わせに追加")
        add.setObjectName("primary")
        add.clicked.connect(self.add_combos)
        st.addWidget(add)
        self.status_label = QLabel("④ 組み合わせ（0 組）")
        st.addWidget(self.status_label, 1)
        buttons(st, [("選択行を削除", self.remove_combos), ("全削除", self.clear_combos)])
        self.b_check = QPushButton("実フォルダを確認")
        self.b_check.clicked.connect(self.check_folders)
        st.addWidget(self.b_check)
        cl.addLayout(st)
        self.status = check_table(self.STATUS_COLS)
        cl.addWidget(self.status, 1)
        self.bottom.addTab(combo_box, "④ 組み合わせ（解析）")
        self.bottom.addTab(self.build_state(), "現状（整理）")
        lay.addWidget(self.bottom, 3)

        self.run = RunPanel(self.finished, self.send)
        self.run.button.setText("▶  組み合わせを解析")
        self.run.button.clicked.connect(self.start)
        lay.addWidget(self.run, 2)
        self.db_changed.connect(self.reload)
        self.combos_done.connect(self.drop_combos)
        self.fill_dirs()
        self.apply_condition(conditions.current("bnal")["values"])
        self.show_condition()
        self.reload()

    # -- data -------------------------------------------------------------------
    @staticmethod
    def key(r):
        return r["root"], r["experiment"], r["sample"]

    def reload(self):
        path = self.db.text().strip()
        try:
            rows = server_anal.load_samples(path)
            self.bnals = server_bnal.load_bnals(path)
        except Exception as e:
            QMessageBox.warning(self, "BNAL（サーバー）", f"データベースを読めません:\n{e}")
            return
        settings().setValue("inventory_db", path)
        self.samples = [r for r in rows if (r["anal_tdms"] or 0) + (r["anal_stocked_tdms"] or 0) > 0]
        fresh = {self.key(r): r for r in self.samples}
        self.combos = [(fresh.get(self.key(r), r), n, s) for r, n, s in self.combos]
        current = self.group.currentText()
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("すべて")
        self.group.addItems(sorted({r["group"] for r in self.samples}))
        self.group.setCurrentText(current or "すべて")
        self.group.blockSignals(False)
        self.fill_samples()
        self.fill_status()

    def seq_folder(self):
        return server_bnal.seq_dir(root=self.seq_dir.text())

    def bc_folder(self):
        return server_bnal.bc_dir()

    def fill_dirs(self, select=None):
        kind = self.kind_box.currentText()
        folder, bc_folder = self.seq_folder(), self.bc_folder()
        current = select or settings().value(f"bnal_csv/{kind}", "")
        self.csv_box.blockSignals(True)
        self.csv_box.clear()
        if folder.is_dir():
            self.csv_box.addItems(server_bnal.seq_files(kind, folder))
        self.csv_box.setCurrentText(current)
        self.csv_box.blockSignals(False)
        self.bc_box.blockSignals(True)
        self.bc_box.clear()
        if bc_folder.is_dir():
            self.bc_box.addItems(server_bnal.bc_files(kind, bc_folder))
        self.bc_box.setCurrentText(settings().value(f"bc_file/{kind}", ""))
        self.bc_box.blockSignals(False)
        self.load_targets(self.csv_box.currentText())
        self.show_bases()

    def choose_seq_dir(self):
        d = QFileDialog.getExistingDirectory(self, "SEQlist フォルダ", self.seq_dir.text())
        if d:
            self.seq_dir.setText(d)
            settings().setValue("seqlist_folder", d)
            self.fill_dirs()

    def load_targets(self, name):
        self.targets, self.checked_targets = [], set()
        if name:
            try:
                self.targets = server_bnal.read_targets(self.seq_folder() / name)
                settings().setValue(f"bnal_csv/{self.kind_box.currentText()}", name)
            except Exception as e:
                QMessageBox.warning(self, "BNAL（サーバー）", f"{name} を読めません:\n{e}")
        self.fill_targets()

    def bc(self):
        try:
            return server_bnal.read_bc(self.bc_folder() / self.bc_box.currentText())
        except Exception:
            return []

    def colors(self):
        return server_bnal.species(self.bc())

    def bc_changed(self, *_):
        settings().setValue(f"bc_file/{self.kind_box.currentText()}", self.bc_box.currentText())
        self.overrides = {}
        self.fill_targets()
        self.show_bases()

    # -- samples / targets --------------------------------------------------------
    def fill_samples(self, *_):
        group, text = self.group.currentText(), self.search.text().strip().lower()
        allowed = self.GROUP_FILTERS.get(self.bnal_filter.currentText(), {"target", "excluded", None})
        self.shown = [r for r in self.samples if (group in ("", "すべて") or r["group"] == group)
                      and self.groups.get(server_bnal.sample_id(r)) in allowed
                      and (not text or text in r["experiment"].lower() or text in r["sample"].lower())]
        t = self.sample_table
        t.blockSignals(True)
        t.setRowCount(len(self.shown))
        for i, r in enumerate(self.shown):
            n_bnal = len(self.bnals.get((server_anal.identity(r["root"]), r["experiment"], r["sample"]), {}))
            vals = [r["group"], r["experiment"], r["sample"], r["anal_tdms"], r["anal_stocked_tdms"] or 0, n_bnal]
            cls = self.groups.get(server_bnal.sample_id(r))
            for c, v in enumerate(vals):
                item = QTableWidgetItem("" if v is None else str(v))
                if c == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if self.key(r) in self.checked else Qt.CheckState.Unchecked)
                if c == 4 and v:
                    item.setForeground(QColor(WARNING))
                if cls == "excluded":
                    item.setForeground(QColor(DIM))
                    item.setToolTip("BNAL 非対象")
                elif cls == "target" and c == 2:
                    item.setForeground(QColor(SUCCESS))
                    item.setToolTip("BNAL 対象")
                t.setItem(i, c, item)
        t.blockSignals(False)

    def classify(self, value):
        """Mark the checked samples (or the selected rows) as BNAL target / excluded / unclassified."""
        rows = [r for r in self.samples if self.key(r) in self.checked] or \
               [self.shown[i] for i in sorted({i.row() for i in self.sample_table.selectedIndexes()})]
        if not rows:
            QMessageBox.information(self, "BNAL（サーバー）", "サンプルにチェックを付けるか、行を選択してください。")
            return
        try:
            # only these samples are written to the shared ledger (machinedb), keeping other PCs' changes
            self.groups = server_bnal.set_groups({server_bnal.sample_id(r): value or None for r in rows})
        except OSError as e:
            QMessageBox.warning(self, "BNAL（サーバー）", f"サンプル台帳に保存できませんでした:\n{e}")
            return
        self.checked -= {self.key(r) for r in rows}
        self.fill_samples()

    def fill_targets(self):
        t = self.target_table
        colors = self.colors()
        t.blockSignals(True)
        t.setRowCount(0)  # drops the sequence labels of the previous list
        t.setRowCount(len(self.targets))
        for i, (name, seq) in enumerate(self.targets):
            parent = server_bnal.parent_target(name)
            vals = [name, "", len(seq), "修飾配列" if parent != name else "基本配列", parent if parent != name else ""]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if name in self.checked_targets else Qt.CheckState.Unchecked)
                t.setItem(i, c, item)
            label = QLabel(colored_seq(seq, colors))
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setStyleSheet("background: transparent; padding-left: 4px")
            t.setCellWidget(i, 1, label)
        t.blockSignals(False)

    def sample_checked(self, item):
        if item.column() == 0:
            k = self.key(self.shown[item.row()])
            (self.checked.add if item.checkState() == Qt.CheckState.Checked else self.checked.discard)(k)

    def target_checked(self, item):
        if item.column() == 0:
            n = self.targets[item.row()][0]
            (self.checked_targets.add if item.checkState() == Qt.CheckState.Checked
             else self.checked_targets.discard)(n)

    def set_samples(self, keys):
        self.checked = set(keys)
        self.fill_samples()

    def set_targets(self, names):
        self.checked_targets = set(names)
        self.fill_targets()

    def create_targets(self):
        row = self.target_table.currentRow()
        base = self.targets[row] if 0 <= row < len(self.targets) else None
        dlg = TargetDialog(self, self.bc(), self.seq_folder(), self.csv_box.currentText(), base)
        if dlg.exec() and dlg.saved:
            self.fill_dirs(select=dlg.saved)

    # -- base table -----------------------------------------------------------------
    def bases_for(self, seq, bc=None):
        bases, stds, missing = server_bnal.base_table(seq, bc if bc is not None else self.bc())
        return {b: self.overrides.get(b, v) for b, v in bases.items()}, stds, missing

    def show_bases(self):
        row = self.target_table.currentRow()
        self.base_view.blockSignals(True)
        self.base_view.setRowCount(0)
        if 0 <= row < len(self.targets):
            name, seq = self.targets[row]
            bc = self.bc()
            bases, _, missing = self.bases_for(seq, bc)
            names, colors = {b: n for b, n, *_ in bc}, server_bnal.species(bc)
            self.base_label.setText(f"Base Table（{name}）" + (f"　⚠ BC に無い文字: {''.join(missing)}" if missing else ""))
            for b, v in bases.items():
                r = self.base_view.rowCount()
                self.base_view.insertRow(r)
                color, cls = colors.get(b, (TEXT, ""))
                cells = [b, names.get(b, ""), cls, f"{v:.3f}"]
                for c, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if c < 3:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if c in (0, 2):
                        item.setForeground(QColor(color))
                    if c == 0:
                        item.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
                    if c == 3 and b in self.overrides:
                        item.setForeground(QColor(WARNING))
                        item.setToolTip("BC の値から変更しています")
                    self.base_view.setItem(r, c, item)
        self.base_view.blockSignals(False)
        self.base_view.resizeColumnsToContents()

    def level_edited(self, item):
        if item.column() != 3:
            return
        letter = self.base_view.item(item.row(), 0).text()
        try:
            value = float(item.text())
        except ValueError:
            self.show_bases()
            return
        original = {b: v for b, _, v, _ in self.bc()}.get(letter)
        if original is not None and abs(value - original) < 1e-12:
            self.overrides.pop(letter, None)
        else:
            self.overrides[letter] = value
        self.show_bases()

    def reset_levels(self):
        self.overrides = {}
        self.show_bases()

    def save_bc(self):
        src = self.bc_folder() / self.bc_box.currentText()
        path, _ = QFileDialog.getSaveFileName(self, "BC ファイルとして保存", str(self.bc_folder()), "CSV (*.csv)")
        if not path:
            return
        if Path(path).resolve() == src.resolve() and QMessageBox.question(
                self, "BC", f"{src.name} を上書きします。よろしいですか？"
        ) != QMessageBox.StandardButton.Yes:
            return
        server_bnal.write_bc(path, src, self.overrides)
        if Path(path).parent.resolve() == self.bc_folder().resolve():
            self.fill_dirs()
            self.bc_box.setCurrentText(Path(path).name)

    # -- history --------------------------------------------------------------------
    def open_history(self):
        dlg = HistoryDialog(self)
        if not dlg.exec() or not dlg.choice:
            return
        what, e = dlg.choice
        c = e.get("conditions", {})
        kind = c.get("list_kind") or next((k for k in server_bnal.SEQ_KINDS
                                           if c.get("bc_file", "") in server_bnal.bc_files(k)), None)
        if kind:
            self.kind_box.setCurrentText(kind)
        if c.get("bc_file"):
            self.bc_box.setCurrentText(c["bc_file"])
        self.rrmin.setValue(int(c.get("right_read_min", self.rrmin.value())))
        self.noise.setValue(int(c.get("noise_max", self.noise.value())))
        self.filter_p.setValue(int(c.get("filter_p", self.filter_p.value())))
        self.overrides = {k: float(v) for k, v in (c.get("overrides") or {}).items()}
        self.show_bases()
        if what == "all":
            by_key = {(r["root"], r["experiment"], r["sample"]): r for r in self.samples}
            missing = 0
            for cmb in e.get("combinations", []):
                r = by_key.get((cmb["root"], cmb["experiment"], cmb["sample"]))
                if r is None:
                    missing += 1
                    continue
                self.add_combo(r, cmb["target"], cmb["sequence"])
            self.fill_status()
            if missing:
                QMessageBox.information(self, "履歴", f"DB に見つからないサンプルの組み合わせ {missing} 件は読み込みませんでした。")

    # -- combinations -----------------------------------------------------------------
    def add_combo(self, r, name, seq):
        if (self.key(r), name) not in {(self.key(x), n) for x, n, _ in self.combos}:
            self.combos.append((r, name, seq))

    def add_combos(self):
        samples = [r for r in self.samples if self.key(r) in self.checked]
        targets = [(n, s) for n, s in self.targets if n in self.checked_targets]
        if not samples or not targets:
            QMessageBox.information(self, "BNAL（サーバー）", "① のサンプルと ② の target にチェックを入れてください。")
            return
        for r in samples:
            for n, s in targets:
                self.add_combo(r, n, s)
        self.fill_status()
        # added: clear the checks for the next sample x target choice
        self.set_samples(set())
        self.set_targets(set())

    def remove_combos(self):
        for i in sorted({i.row() for i in self.status.selectedIndexes()}, reverse=True):
            del self.combos[i]
        self.fill_status()

    def clear_combos(self):
        self.combos = []
        self.fill_status()

    def drop_combos(self, done):
        done = {tuple(d) for d in done}
        self.combos = [c for c in self.combos if (self.key(c[0]), c[1]) not in done]
        self.fill_status()

    def fill_status(self):
        t = self.status
        t.setRowCount(0)
        t.setRowCount(len(self.combos))
        colors = self.colors()
        totals = [0, 0]
        for i, (r, name, seq) in enumerate(self.combos):
            counts = self.bnals.get((server_anal.identity(r["root"]), r["experiment"], r["sample"]), {})
            bnal = counts.get(name)
            anal, stocked = r["anal_tdms"] or 0, r["anal_stocked_tdms"] or 0
            goal = anal + stocked
            state = server_bnal.combo_state(r, name, counts)
            notes = []
            if stocked:
                notes.append(f"退避 {stocked}（解析しない）")
            if r["anal_distinct"] is not None and anal > r["anal_distinct"]:
                notes.append("ANAL 混在")
            if state == server_bnal.UNCONFIRMED:
                notes.append("BNAL@ 名が DB に未取得（実験ファイルDBで更新）")
            parent = server_bnal.parent_target(name)
            if parent != name and counts.get(parent) not in (None, bnal):
                notes.append(f"基本配列 {counts.get(parent)} 件と差")
            totals[0] += bnal or 0
            totals[1] += goal
            vals = [r["group"], r["experiment"], r["sample"], name, "修飾" if parent != name else "基本", "", anal,
                    stocked, "" if bnal is None and name not in counts else (bnal if bnal is not None else "?"),
                    f"{100 * (bnal or 0) / goal:.1f}%" if goal else "", state, " / ".join(notes),
                    self.live.get((self.key(r), name), "")]
            tint = QColor(BNAL_TINT.get(state, FIELD))
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setBackground(tint)
                if c == 10 and state in BNAL_TEXT:
                    item.setForeground(QColor(BNAL_TEXT[state]))
                if c == 11 and notes:
                    item.setForeground(QColor(WARNING))
                t.setItem(i, c, item)
            label = QLabel(colored_seq(seq, colors, 9))
            label.setStyleSheet(f"background: {tint.name()}; padding-left: 4px")
            t.setCellWidget(i, 5, label)
        t.resizeColumnsToContents()
        n_samples = len({self.key(r) for r, *_ in self.combos})
        self.status_label.setText(f"④ 組み合わせ（{n_samples} サンプル・{len(self.combos)} 組）"
                                  f"　DB 上 BNAL {totals[0]} / {totals[1]} TDMS")

    # -- live check -------------------------------------------------------------
    # -- current state (bnal_status): survey the samples checked in (1), put right what can be ------------
    B_ISSUE_COLS = ["✓", "グループ", "実験", "サンプル", "target", "内容", "件数", "説明", "手作業・対処"]
    B_ACTION_ORDER = ["restore_anal", "empty_b", "broken_b", "old_b", "orphan_b"]
    B_ACTION_TEXT = {"restore_anal": "ANAL に戻す", "empty_b": "削除（元に戻せる）", "old_b": "削除（元に戻せる）",
                     "broken_b": "削除して解析し直す（元に戻せる）", "orphan_b": "old へ移す"}
    B_MANUAL = ("renumber", "renumber_anal")

    def build_state(self):
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        self.b_survey = QPushButton("現状を調べる")
        self.b_survey.setToolTip("① でチェックしたサンプルの ANAL と、T にあるすべての BNAL@ フォルダをサーバーで読みます"
                                 "（変更はしません）")
        self.b_survey.clicked.connect(self.survey_samples)
        row.addWidget(self.b_survey)
        self.state_label = QLabel("① でサンプルにチェックを入れて「現状を調べる」を押すと、target ごとの整理が必要なものと"
                                  "未解析を一覧にします")
        row.addWidget(self.state_label, 1)
        b_all = QPushButton("対処をすべてチェック")
        b_all.clicked.connect(lambda: self.check_state(lambda i: bool(i["action"])))
        row.addWidget(b_all)
        b_fix = QPushButton("チェックした対処を実行…")
        b_fix.setObjectName("primary")
        b_fix.setToolTip("チェックした対処を、実行の直前に調べ直してから行います。移動・削除は記録され、"
                         "「整理の履歴…」から元に戻せます")
        b_fix.clicked.connect(self.run_state_actions)
        row.addWidget(b_fix)
        b_pending = QPushButton("チェックした未解析を ④ に追加")
        b_pending.setToolTip("チェックした「未解析の A@@」の行（サンプル × target）を ④ の組み合わせに追加します。"
                             "配列は ② のリスト、無ければその target の B@@ の BC Table から取ります")
        b_pending.clicked.connect(self.add_pending)
        row.addWidget(b_pending)
        b_hist = QPushButton("整理の履歴…")
        b_hist.clicked.connect(lambda: JournalDialog(self, ("BNAL", "ANAL", "T_stock")).exec())
        row.addWidget(b_hist)
        bl.addLayout(row)
        self.state_table = QTableWidget(0, len(self.B_ISSUE_COLS))
        self.state_table.setHorizontalHeaderLabels(self.B_ISSUE_COLS)
        self.state_table.verticalHeader().setVisible(False)
        self.state_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.state_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.state_table.setToolTip("行をダブルクリックすると、その BNAL@ フォルダ（target の無い行は T）を開きます")
        self.state_table.cellDoubleClicked.connect(self.open_state_folder)
        bl.addWidget(self.state_table, 1)
        self.state = []  # [(row, issue)]
        return box

    def survey_samples(self):
        rows = [r for r in self.samples if self.key(r) in self.checked and r["reachable"]]
        if not rows:
            QMessageBox.information(self, "BNAL（サーバー）", "① で調べるサンプルにチェックを入れてください。")
            return
        self.b_survey.setEnabled(False)
        self.b_survey.setText("調査中…")

        def job(log, progress):
            out = []
            for i, r in enumerate(rows):
                progress(int(100 * i / len(rows)), f"現状を調査 {r['sample']}（{i + 1}/{len(rows)}）")
                try:
                    out.append((r, bnal_status.survey(server_anal.t_dir(r)), ""))
                except Exception as e:
                    out.append((r, [], str(e)))
            return out

        def done(results):
            keys = {self.key(r) for r, _, _ in results}
            self.state = [(r, i) for r, i in self.state if self.key(r) not in keys]
            for r, issues, err in results:
                if err:
                    issues = [dict(kind="error", label="調べられません", target=None, count=0, files=[], detail=err,
                                   action=None, manual="")]
                self.state += [(r, i) for i in issues]
            self.surveyed_b = getattr(self, "surveyed_b", set()) | keys
            self.b_survey.setEnabled(True)
            self.b_survey.setText("現状を調べる")
            self.fill_state()
            self.bottom.setCurrentIndex(1)
        self.background(job, done)

    def background(self, job, done):
        thread, worker = QThread(), Worker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.run._progress)
        worker.log.connect(self.run.log.appendPlainText)
        worker.finished.connect(done)
        worker.finished.connect(thread.quit)
        self._bg = [(t, w) for t, w in getattr(self, "_bg", []) if t.isRunning()] + [(thread, worker)]
        thread.start()

    def fill_state(self):
        rank = lambda i: 0 if i["kind"] in self.B_MANUAL or i["kind"] == "error" else 1 if i["action"] else \
            2 if i["kind"] == "pending" else 3
        shown = sorted(self.state, key=lambda ri: (rank(ri[1]), ri[0]["group"], ri[0]["experiment"], ri[0]["sample"],
                                                   ri[1]["target"] or ""))
        self.state_shown = shown
        tints = {0: ui_theme.row("danger", 0.22), 1: ui_theme.row("warning", 0.18), 2: ui_theme.row("info", 0.16), 3: FIELD}
        t = self.state_table
        t.setRowCount(len(shown))
        for n, (r, i) in enumerate(shown):
            todo = self.B_ACTION_TEXT.get(i["action"], "") if i["action"] else i["manual"]
            values = ["", r["group"], r["experiment"], r["sample"], i["target"] or "（サンプル）", i["label"], i["count"],
                      i["detail"], todo]
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                item.setBackground(QColor(tints[rank(i)]))
                if c == 0:
                    if i["action"] or i["kind"] == "pending":
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState.Unchecked)
                    else:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                if c in (5, 7):
                    files = "\n".join(Path(f).name for f in i["files"][:30]) + ("\n…" if len(i["files"]) > 30 else "")
                    item.setToolTip(i["detail"] + (f"\n\n{files}" if files else ""))
                if c == 5 and rank(i) == 0:
                    item.setForeground(QColor(DANGER))
                t.setItem(n, c, item)
        t.resizeColumnsToContents()
        count = lambda f: sum(1 for _, i in shown if f(i))
        self.state_label.setText(
            f"{len(getattr(self, 'surveyed_b', ()))} サンプルを調査　手作業 {count(lambda i: rank(i) == 0)} 件（赤）・"
            f"整理できる {count(lambda i: bool(i['action']))} 件（黄）・未解析の組 {count(lambda i: i['kind'] == 'pending')} 件（青）")

    def check_state(self, want):
        for n, (_, i) in enumerate(self.state_shown):
            if i["action"] or i["kind"] == "pending":
                self.state_table.item(n, 0).setCheckState(Qt.CheckState.Checked if want(i) else Qt.CheckState.Unchecked)

    def checked_state(self, want):
        return [(r, i) for n, (r, i) in enumerate(getattr(self, "state_shown", []))
                if want(i) and self.state_table.item(n, 0).checkState() == Qt.CheckState.Checked]

    def open_state_folder(self, n, _):
        import os as _os
        r, i = self.state_shown[n]
        folder = server_anal.t_dir(r) / (f"BNAL@{i['target']}" if i["target"] else "")
        try:
            _os.startfile(str(folder))
        except OSError as e:
            QMessageBox.warning(self, "BNAL（サーバー）", f"フォルダを開けません:\n{e}")

    def run_state_actions(self):
        picked = self.checked_state(lambda i: bool(i["action"]))
        if not picked:
            QMessageBox.information(self, "現状の整理", "一覧で、実行する対処（黄の行）にチェックを入れてください。")
            return
        if self.run.thread is not None and self.run.thread.isRunning():
            QMessageBox.information(self, "現状の整理", "実行中の処理が終わってから操作してください。")
            return
        text = "\n".join(f"・{r['sample']} / {i['target'] or 'サンプル'}: {i['label']} {i['count']} 件 → "
                         f"{self.B_ACTION_TEXT[i['action']]}" for r, i in picked[:25]) + \
            (f"\n…ほか {len(picked) - 25} 件" if len(picked) > 25 else "")
        if QMessageBox.question(self, "現状の整理", f"次の対処を行います（実行の直前に調べ直します）。\n\n{text}\n\n"
                                "移動・削除は記録され、「整理の履歴…」から元に戻せます。実行しますか？"
                                ) != QMessageBox.StandardButton.Yes:
            return
        order = {k: n for n, k in enumerate(self.B_ACTION_ORDER)}
        picked.sort(key=lambda ri: (self.key(ri[0]), order[ri[1]["action"]], ri[1]["target"] or ""))
        db_path = self.db.text().strip() if self.update_db.isChecked() else None
        rows = list({self.key(r): r for r, _ in picked}.values())

        def job(log, progress, cancelled=lambda: False):
            for n, (r, i) in enumerate(picked):
                progress(int(100 * n / len(picked)), f"整理 {r['sample']} {i['target'] or ''}")
                try:
                    for line in bnal_status.apply(server_anal.t_dir(r), i["target"], i["action"]):
                        log(f"{r['experiment']} / {r['sample']}: {line}")
                except Exception as e:
                    log(f"✖ {r['experiment']} / {r['sample']} {i['target'] or ''}: {e}")
            for r in rows if db_path else []:
                try:
                    server_anal.refresh_db(db_path, r)
                except Exception as e:
                    log(f"✖ DB 更新 {r['sample']}: {e}")
            return []

        def done(_):
            if db_path:
                self.db_changed.emit()
            self.set_samples(self.checked | {self.key(r) for r in rows})
            self.survey_samples()
        self.run.start(job, done)

    def add_pending(self):
        picked = self.checked_state(lambda i: i["kind"] == "pending")
        if not picked:
            QMessageBox.information(self, "BNAL（サーバー）", "一覧で「未解析の A@@」の行（青）にチェックを入れてください。")
            return
        known = dict(self.targets)

        def job(log, progress):
            out = []
            for n, (r, i) in enumerate(picked):
                progress(int(100 * n / len(picked)), f"配列を確認 {i['target']}")
                seq = known.get(i["target"]) or bnal_status.sequence_of(server_anal.t_dir(r), i["target"])
                out.append((r, i["target"], seq))
            return out

        def done(found):
            missing = [f"{r['sample']} / {name}" for r, name, seq in found if not seq]
            for r, name, seq in found:
                if seq:
                    self.add_combo(next((x for x in self.samples if self.key(x) == self.key(r)), r), name, seq)
            self.fill_status()
            self.bottom.setCurrentIndex(0)
            if missing:
                QMessageBox.warning(self, "BNAL（サーバー）", "配列が分からないため追加しなかった組（② で target リストを"
                                    "読み込んでから追加してください）:\n" + "\n".join(missing[:20]))
        self.background(job, done)

    def check_folders(self):
        combos = list(self.combos)
        if not combos:
            QMessageBox.information(self, "BNAL（サーバー）", "組み合わせを追加してください。")
            return
        self.b_check.setEnabled(False)
        self.b_check.setText("確認中…")

        def job(log, progress):
            out = []
            for i, (r, name, _) in enumerate(combos):
                progress(int(100 * i / len(combos)), f"確認 {r['sample']} / {name}")
                try:
                    p = server_bnal.pending(server_anal.t_dir(r), name)
                    out.append(((self.key(r), name), f"未解析 {len(p['todo'])} / ANAL {p['anal']}"
                                + (f"　退避 {p['stocked']}" if p["stocked"] else "")
                                + (f"　同じ#の重複 {p['duplicates']}" if p["duplicates"] else "")))
                except Exception as e:
                    out.append(((self.key(r), name), f"エラー: {e}"))
            return out
        self.check_thread = QThread()
        self.check_worker = Worker(job)
        self.check_worker.moveToThread(self.check_thread)
        self.check_thread.started.connect(self.check_worker.run)
        self.check_worker.progress.connect(self.run._progress)
        self.check_worker.finished.connect(self.checked_folders)
        self.check_worker.finished.connect(self.check_thread.quit)
        self.check_thread.start()

    def checked_folders(self, results):
        self.live.update(dict(results))
        self.b_check.setEnabled(True)
        self.b_check.setText("実フォルダを確認")
        self.run.bar.setFormat(f"確認完了 ({len(results)} 組)")
        self.run.bar.setValue(100)
        self.fill_status()

    # -- run --------------------------------------------------------------------
    def build_job(self):
        pairs = list(self.combos)
        if not pairs:
            QMessageBox.information(self, "BNAL（サーバー）", "組み合わせを追加してください。")
            return None
        chosen = self.ask_condition(f"{len(pairs)} 組（サンプル × target）を、この条件で解析します。"
                                    "target ごとに Base Table で変えたレベルはそのまま使います。")
        if not chosen:
            return None
        bc = self.bc()
        if not bc:
            QMessageBox.warning(self, "BNAL（サーバー）", "BC（電流値）ファイルを読めません。")
            return None
        conds, bad = {}, []
        for name, seq in {(n, s) for _, n, s in pairs}:
            bases, stds, missing = self.bases_for(seq, bc)
            if missing:
                bad.append(f"{name}: {''.join(missing)}")
                continue
            conds[name] = {"sample_name": name, "sample_sequence": seq, "right_read_min": self.rrmin.value(),
                           "noise_max": self.noise.value(), "filter_p": self.filter_p.value(),
                           "bases": bases, "base_stds": stds, "condition_set": chosen}
        if bad:
            QMessageBox.warning(self, "BNAL（サーバー）", f"{self.bc_box.currentText()} に無い文字を含む target は"
                                "解析しません:\n" + "\n".join(bad[:10]))
        pairs = [p for p in pairs if p[1] in conds]
        if not pairs:
            return None
        redo = self.mode_redo.isChecked()
        if redo and QMessageBox.question(
                self, "BNAL（サーバー）", f"{len(pairs)} 組の BNAL@target にある B@@ を old フォルダへ移動して、"
                                         "すべての ANAL を再解析します。よろしいですか？") != QMessageBox.StandardButton.Yes:
            return None
        db_path = self.db.text().strip() if self.update_db.isChecked() else None
        history = {
            "conditions": {"condition_set": chosen,
                           "list_kind": self.kind_box.currentText(), "target_csv": self.csv_box.currentText(),
                           "bc_file": self.bc_box.currentText(), "right_read_min": self.rrmin.value(),
                           "noise_max": self.noise.value(), "filter_p": self.filter_p.value(),
                           "overrides": dict(self.overrides),
                           "bases": {n: c["bases"] for n, c in sorted(conds.items())}},
            "mode": "再解析" if redo else "未解析のみ",
            "combinations": [{"group": r["group"], "root": r["root"], "experiment": r["experiment"],
                              "sample": r["sample"], "target": n, "sequence": s} for r, n, s in pairs],
            "program": second_step_io.BNAL_VERSION,
        }

        def job(log, progress, cancelled=lambda: False):
            started = datetime.now()
            plan = []
            for i, (r, name, _) in enumerate(pairs):
                progress(0, f"フォルダ確認 {i + 1}/{len(pairs)}")
                t = server_anal.t_dir(r)
                where = f"{r['group']} / {r['experiment']} / {r['sample']} / {name}"
                try:
                    if redo:
                        moved, skipped = server_bnal.move_to_old(t, name)
                        log(f"  old へ移動 {moved} 件" + (f"（同名があり移動しない {skipped} 件）" if skipped else "")
                            + f"  {where}")
                    p = server_bnal.pending(t, name)
                except Exception as e:
                    log(f"✖ {where}: {e}")
                    continue
                log(f"□ {where}  未解析 {len(p['todo'])} / ANAL {p['anal']}"
                    + (f"　⚠ ANAL/stocked に退避中 {p['stocked']}（解析しません）" if p["stocked"] else ""))
                plan += [(r, t, name, a) for a in p["todo"]]
            outs, failed = [], 0
            for k, (r, t, name, a) in enumerate(plan):
                if cancelled():
                    log("■ 中止しました")
                    break
                folder = t / f"BNAL@{name}"
                if server_anal.anal_key(a.name) in {server_anal.anal_key(e.name) for e in server_bnal._tdms(folder)
                                                    if e.name.startswith("B@@")}:
                    log(f"  - {a.name}  （ほかで解析済みのため飛ばします）")
                    continue
                t0 = time.perf_counter()
                row = dict(group=r["group"], experiment=r["experiment"], sample=r["sample"], target=name, anal=str(a),
                           condition_set=chosen)
                try:
                    out, res = second_step_io.process_file(
                        a, conds[name], folder, False, make_progress(progress, k, len(plan), f"{name}  {a.name}"))
                except Exception as e:
                    failed += 1
                    log(f"  ✖ {name}  {a.name}\n    {e}")
                    server_anal.log_run(BNAL_LOG, **row, out="", seconds=f"{time.perf_counter() - t0:.1f}",
                                        status="error", error=str(e))
                    continue
                br = res["BR Table"].iloc[0]
                log(f"  ● {name}  {out.name}  長い信号 {int(br['Long Signal Number # [n]'])}"
                    f"  MAX Right Read {int(br['MAX Right Read # [n]'])}")
                server_anal.log_run(BNAL_LOG, **row, out=str(out), seconds=f"{time.perf_counter() - t0:.1f}",
                                    status="ok", error="")
                outs.append(str(out))
            result = f"{len(outs)} / {len(plan)} ファイル" + (f"　失敗 {failed}" if failed else "") \
                + ("　中止" if cancelled() else "")
            log(f"完了 {result}　記録: {BNAL_LOG}")
            server_bnal.add_history({"time": started.strftime("%Y-%m-%d %H:%M:%S"), **history, "result": result})
            log(f"  条件を履歴に保存しました: {server_bnal.HISTORY}")
            if db_path:
                for r in {self.key(r): r for r, *_ in pairs}.values():
                    try:
                        server_anal.refresh_db(db_path, r)
                        log(f"  DB 更新 {r['experiment']} / {r['sample']}")
                    except Exception as e:
                        log(f"  ✖ DB 更新 {r['experiment']} / {r['sample']}: {e}")
                self.db_changed.emit()
            # combinations with nothing left to analyse leave the list
            done = []
            for r, name, _ in pairs:
                try:
                    if not server_bnal.pending(server_anal.t_dir(r), name)["todo"]:
                        done.append([self.key(r), name])
                except Exception:
                    pass
            if done:
                log(f"  解析が終わった {len(done)} 組を組み合わせから外しました")
                self.combos_done.emit(done)
            return outs
        names = sorted(conds)
        return {"kind": "BNAL（サーバー）", "desc": f"{len({self.key(r) for r, *_ in pairs})} サンプル・{len(pairs)} 組"
                f"  ({', '.join(names)[:80]})", "fn": job, "a_files": []}

    def start(self):
        job = self.build_job()
        if job:
            self.run.start(job["fn"])

    def send(self):
        job = self.build_job()
        if job:
            self.send_to_queue(job)
            self.run.log.appendPlainText(f"→ キューに送りました: {job['desc']}")

    def finished(self, outputs):
        pass

    # -- analysis conditions ----------------------------------------------------
    def bc_choices(self):
        return {"kind": self.kind_box.currentText(), "selected": self.bc_box.currentText(),
                "files": [self.bc_box.itemText(i) for i in range(self.bc_box.count())]}

    def apply_condition(self, values):
        """Show the values of a condition set in (3) (the BC file of the current list kind when the set has one)."""
        self.rrmin.setValue(values["right_read_min"])
        self.noise.setValue(values["noise_max"])
        self.filter_p.setValue(values["filter_p"])
        bc = (values.get("bc_files") or {}).get(self.kind_box.currentText())
        if bc and self.bc_box.findText(bc) >= 0:
            self.bc_box.setCurrentText(bc)

    def show_condition(self):
        self.cond_label.setText("解析条件: " + conditions.describe("bnal", conditions.current("bnal")))

    def manage_conditions(self):
        ConditionDialog(self, "bnal", run=False, bc_choices=self.bc_choices()).exec()
        self.apply_condition(conditions.current("bnal")["values"])
        self.show_condition()

    def ask_condition(self, summary):
        """Name of the chosen set after its values are put into (3), or None when cancelled."""
        chosen = ask_condition(self, "bnal", summary, self.bc_choices())
        self.show_condition()
        if chosen is None:
            return None
        self.apply_condition(chosen["values"])
        return chosen["name"]

# ---------------------------------------------------------------------------
# queue page
# ---------------------------------------------------------------------------
class QueuePage(QWidget):
    """Jobs sent from the 1st / 2nd step pages, run one after another."""
    status = pyqtSignal(int, str)
    STATE_COLORS = {"待機": MUTED, "実行中": ACCENT, "完了": SUCCESS, "エラー": DANGER, "中止": WARNING}

    def __init__(self, open_in_viewer, on_change):
        super().__init__()
        self.open_in_viewer = open_in_viewer
        self.on_change = on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(header("解析キュー", "各ページの「キューに送る」で送ったジョブを上から順にまとめて実行"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["状態", "種類", "内容"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 2)
        row = QHBoxLayout()
        for text, slot in [("▲ 上へ", lambda: self.move(-1)), ("▼ 下へ", lambda: self.move(1)),
                           ("選択を削除", self.delete_selected), ("完了を消去", self.clear_done)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.run = RunPanel(self.finished)
        self.run.button.setText("▶  キューを実行")
        self.run.button.clicked.connect(self.start)
        lay.addWidget(self.run, 2)
        self.jobs = []
        self.next_id = 0
        self.status.connect(self.set_status)

    def running(self):
        return self.run.thread is not None and self.run.thread.isRunning()

    def add(self, job):
        job.update(id=self.next_id, state="待機")
        self.next_id += 1
        self.jobs.append(job)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(len(self.jobs))
        for r, j in enumerate(self.jobs):
            st = QTableWidgetItem(j["state"])
            st.setForeground(QColor(self.STATE_COLORS[j["state"]]))
            self.table.setItem(r, 0, st)
            self.table.setItem(r, 1, QTableWidgetItem(j["kind"]))
            self.table.setItem(r, 2, QTableWidgetItem(j["desc"]))
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        self.on_change(sum(j["state"] != "完了" for j in self.jobs))

    def set_status(self, job_id, state):
        for j in self.jobs:
            if j["id"] == job_id:
                j["state"] = state
        self.refresh()

    def selected_rows(self):
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def move(self, step):
        rows = self.selected_rows()
        if self.running() or len(rows) != 1 or not 0 <= rows[0] + step < len(self.jobs):
            return
        r = rows[0]
        self.jobs[r], self.jobs[r + step] = self.jobs[r + step], self.jobs[r]
        self.refresh()
        self.table.selectRow(r + step)

    def delete_selected(self):
        if self.running():
            return
        for r in reversed(self.selected_rows()):
            del self.jobs[r]
        self.refresh()

    def clear_done(self):
        if not self.running():
            self.jobs = [j for j in self.jobs if j["state"] != "完了"]
            self.refresh()

    def start(self):
        pending = [j for j in self.jobs if j["state"] != "完了"]
        if not pending:
            QMessageBox.information(self, "解析キュー", "実行するジョブがありません。\n"
                                    "各ページの「キューに送る」でジョブを追加してください。")
            return
        n = len(pending)

        def job(log, progress, cancelled=lambda: False):
            outs = []
            for k, j in enumerate(pending):
                if cancelled():
                    break
                self.status.emit(j["id"], "実行中")
                log(f"■ ジョブ {k + 1}/{n}  {j['kind']}  {j['desc']}")
                try:
                    outs += j["fn"](log, lambda v, t, k=k: progress(int((k + v / 100) * 100 / n),
                                                                    f"[{k + 1}/{n}] {t}"), cancelled)
                    self.status.emit(j["id"], "中止" if cancelled() else "完了")
                except Exception:
                    log("エラー:\n" + traceback.format_exc())
                    self.status.emit(j["id"], "エラー")
            return outs
        self.pending = pending
        self.run.start(job)

    def finished(self, outputs):
        if outputs:
            a_files = [a for j in self.pending for a in j["a_files"]] + outputs
            self.open_in_viewer(outputs[-1], a_files)


# ---------------------------------------------------------------------------
# viewer
# ---------------------------------------------------------------------------
def read_tables(t, names):
    return {n: pd.DataFrame({c.name: c[:] for c in t[n].channels()}) for n in names if n in t}


def anal_folder(path):
    """The folder holding the A@@ files: the folder itself, or ANAL / T\\ANAL below a sample folder
    (of several, e.g. _10k_Sample and _100k_Sample, the one with the most A@@ files)."""
    p = Path(path)
    if any(p.glob("A@@*.tdms")):
        return p
    found = [c for c in [p / "ANAL", p / "T" / "ANAL", *p.glob("*_Sample/T/ANAL"), *p.glob("*/*_Sample/T/ANAL")]
             if c.is_dir()]
    return max(found, key=lambda c: sum(1 for _ in c.glob("A@@*.tdms")), default=p)


FOLDER_COLS, B_FOLDER_COLS = viewer_rows.A_COLS, viewer_rows.B_COLS


def read_rows(folder, prefix, progress):
    """One row per <prefix>*.tdms of a folder (viewer_rows: worker processes and a cache)."""
    cols = viewer_rows.READERS[prefix][1]
    rows = []
    for path, values, err in viewer_rows.read_rows(folder, prefix, progress):
        row = {"#": server_anal.anal_key(path.name), "ファイル": path.name, "path": str(path), "エラー": err}
        row.update(values or {col: float("nan") for col in cols})
        rows.append(row)
    # of one '#NNN' only the newest analysis counts (names start with the analysis time)
    newest = {r["#"]: r["ファイル"] for r in sorted(rows, key=lambda r: r["ファイル"])}
    for r in rows:
        r["重複"] = "" if newest[r["#"]] == r["ファイル"] else "古い解析（合計に含めない）"
    return rows


def read_folder(folder, progress=lambda *a: None):
    """One row per A@@ file of a folder from its AR Table only (the waveform is not read)."""
    return read_rows(folder, "A@@", progress)


def read_bfolder(folder, progress=lambda *a: None):
    """One row per B@@ file of a BNAL@<target> folder (BR / BC / AR tables and the positions covered)."""
    return read_rows(folder, "B@@", progress)


def source_raw(b_path, a_name, known=None):
    """Waveform of a B@@ file: its own Data channel (LabVIEW copies it), else the A@@ it was made from
    (T\\ANAL, T\\ANAL\\stocked or next to it). Returns (raw [pA] or None, A@@ path or None)."""
    b = Path(b_path)
    cands = ([Path(known[a_name])] if known and a_name in known else []) + [
        b.parent.parent / "ANAL" / a_name, b.parent.parent / "ANAL" / "stocked" / a_name,
        b.parent / a_name, b.parent.parent / a_name]
    for cand in cands if a_name else []:
        if os.path.exists(long_path(cand)):
            with TdmsFile.open(long_path(cand)) as t:
                return t["Data"]["Ch1"][:] * 1000, cand
    return None, None


def hms(seconds):
    s = int(round(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


class ViewerPage(QWidget):
    """kind "A": ANAL results (A@@) of a sample chosen by server / experiment / sample; kind "B": B@@ files."""

    def __init__(self, kind="A"):
        super().__init__()
        self.kind = kind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        row = QHBoxLayout()
        if kind == "A":
            row.addWidget(header("ANAL 結果ビューア", "サーバー・実験・サンプルを選ぶと、その T\\ANAL の A@@ をまとめて表示"
                                                     "（信号数・バースト数・時間の合計、ファイルごとの波形）"), 1)
            dir_tip = "一覧にないフォルダ（サンプルフォルダまたは T\\ANAL）を直接指定します"
        else:
            row.addWidget(header("BNAL 結果ビューア", "サーバー・実験・サンプル・target を選ぶと、その T\\BNAL@target の "
                                                     "B@@ をまとめて表示（長い信号・カバレッジの合計、ファイルごとの帰属）"), 1)
            dir_tip = "一覧にない BNAL@target フォルダを直接指定します"
        self.folder_key = "viewer_folder" if kind == "A" else "bviewer_folder"
        b_dir = QPushButton("フォルダを指定…")
        b_dir.setToolTip(dir_tip)
        b_dir.clicked.connect(lambda: self.open(QFileDialog.getExistingDirectory(
            self, "フォルダ", settings().value(self.folder_key, ""))))
        row.addWidget(b_dir)
        b = QPushButton("ファイルを開く…")
        b.clicked.connect(lambda: self.open(QFileDialog.getOpenFileName(
            self, "結果ファイル", "", "TDMS (A@@*.tdms)" if kind == "A" else "TDMS (B@@*.tdms)")[0]))
        row.addWidget(b)
        lay.addLayout(row)
        lay.addLayout(self.build_picker())
        info = QHBoxLayout()
        self.file_label = QLabel("")
        self.file_label.setObjectName("subtitle")
        info.addWidget(self.file_label, 1)
        self.busy = QProgressBar()
        self.busy.setFixedWidth(320)
        self.busy.setTextVisible(True)
        self.busy.setVisible(False)
        info.addWidget(self.busy)
        self.pick_raw = QPushButton("元の A@@ を指定…")
        self.pick_raw.setVisible(False)
        self.pick_raw.clicked.connect(self.choose_raw)
        info.addWidget(self.pick_raw)
        lay.addLayout(info)
        self.known_a = {}  # A@@ file name -> path (files analysed in this session)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.summary = QTableWidget(0, 2)
        self.summary.setHorizontalHeaderLabels(["項目", "値"])
        self.summary.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.summary.verticalHeader().setVisible(False)
        self.summary.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ll.addWidget(QLabel("サマリー"))
        ll.addWidget(self.summary, 2)
        # folder mode: one row per A@@ file
        self.files_label = QLabel("")
        ll.addWidget(self.files_label)
        self.files = QTableWidget(0, 0)
        self.files.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files.verticalHeader().setVisible(False)
        self.files.setSortingEnabled(True)
        self.files.itemSelectionChanged.connect(self.file_selected)
        ll.addWidget(self.files, 3)
        self.files_label.setVisible(False)
        self.files.setVisible(False)
        list_row = QHBoxLayout()
        self.list_label = QLabel("")
        list_row.addWidget(self.list_label, 1)
        self.list_kind = QComboBox()
        self.list_kind.addItems(["バースト", "信号"])
        self.list_kind.setVisible(False)
        self.list_kind.currentIndexChanged.connect(self.fill_a_items)
        list_row.addWidget(self.list_kind)
        ll.addLayout(list_row)
        self.items = QTableWidget(0, 0)
        self.items.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.items.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.items.verticalHeader().setVisible(False)
        self.items.itemSelectionChanged.connect(self.item_selected)
        ll.addWidget(self.items, 3)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        pg.setConfigOptions(antialias=True, background=FIELD, foreground=MUTED)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("left", "Current", units="pA")
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.addLegend(offset=(10, 10))
        # y follows the data in view (also after zoom / pan)
        self.plot.getViewBox().setAutoVisible(y=True)
        # whole-file overview (min/max envelope + bursts); drag the band to move the view above
        self.overview = pg.PlotWidget()
        self.overview.setMaximumHeight(120)
        self.overview.setMouseEnabled(x=False, y=False)
        self.overview.hideButtons()
        self.overview.setLabel("bottom", "全体 (s)")
        self.overview.getAxis("left").setWidth(self.plot.getAxis("left").width())
        self.view_band = pg.LinearRegionItem((0, 10), brush=pg.mkBrush(122, 162, 255, 50))
        self.view_band.setZValue(10)
        self.view_band.sigRegionChanged.connect(self.band_moved)
        self.plot.sigXRangeChanged.connect(self.view_moved)
        self.overview.setVisible(False)
        rl.addWidget(self.overview, 1)
        rl.addWidget(self.plot, 4)
        rl.addLayout(self.build_y_controls())
        self.seq_view = QTextBrowser()
        self.seq_view.setMaximumHeight(120)
        rl.addWidget(self.seq_view, 1)
        split.addWidget(right)
        split.setSizes([420, 900])
        lay.addWidget(split, 1)
        self.mode = None
        self.folder_rows, self.folder_info = [], {}
        self.load_db()

    # -- server / experiment / sample (/ target) from the experiment file database ------------
    def build_picker(self):
        row = QHBoxLayout()
        self.p_group, self.p_exp, self.p_sample = QComboBox(), QComboBox(), QComboBox()
        self.p_group.setMinimumWidth(190)
        self.p_exp.setMinimumWidth(220)
        self.p_sample.setMinimumWidth(220)
        boxes = [("サーバー", self.p_group), ("実験", self.p_exp), ("サンプル", self.p_sample)]
        if self.kind == "B":
            # a sample can hold hundreds of targets: type part of the name to find one
            self.p_target = QComboBox()
            self.p_target.setEditable(True)
            self.p_target.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.p_target.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            self.p_target.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.p_target.setMinimumWidth(300)
            self.p_target.setToolTip("名前の一部を入力して絞り込めます。サンプルを変えても同じ target を開きます")
            boxes.append(("target", self.p_target))
        for label, box in boxes:
            box.setMaxVisibleItems(25)
            row.addWidget(QLabel(label))
            row.addWidget(box)
        what = "target（最後まで行くと次のサンプル）" if self.kind == "B" else "サンプル"
        for text, tip, step in (("◀", "前の", -1), ("▶", "次の", 1)):
            b = QPushButton(text)
            b.setToolTip(f"{tip}{what}を開きます")
            b.clicked.connect(lambda _, s=step: self.step_pick(s))
            row.addWidget(b)
        b_db = QPushButton("一覧を再読込")
        b_db.setToolTip("実験ファイル データベース（ANAL（サーバー）ページと同じ DB）からサンプル一覧を読み直します")
        b_db.clicked.connect(self.load_db)
        row.addWidget(b_db)
        row.addStretch()
        self.p_group.currentIndexChanged.connect(self.fill_exps)
        self.p_exp.currentIndexChanged.connect(self.fill_samples)
        if self.kind == "A":
            self.p_sample.activated.connect(self.open_picked)
        else:
            self.p_sample.activated.connect(lambda *_: self.fill_targets())
            self.p_target.activated.connect(self.open_picked)
            self.targets_listed.connect(self.targets_found)
        return row

    def picked_row(self):
        i = self.p_sample.currentData()
        return None if i is None else self.db_rows[i]

    def pick_key(self):
        return "viewer_pick" if self.kind == "A" else "bviewer_pick"

    def load_db(self):
        db = settings().value("inventory_db", str(server_anal.DEFAULT_DB))
        try:
            rows = server_anal.load_samples(db)
            bnals = server_bnal.load_bnals(db) if self.kind == "B" else {}
        except Exception as e:
            self.file_label.setText(f"データベースを読めません: {e}")
            rows, bnals = [], {}
        if self.kind == "A":
            self.db_rows = [r for r in rows if (r["anal_tdms"] or 0) > 0]
        else:
            # targets whose BNAL@ folder holds B@@ (or is not counted yet)
            for r in rows:
                counts = bnals.get((server_anal.identity(r["root"]), r["experiment"], r["sample"]), {})
                r["targets"] = {n: c for n, c in counts.items() if c is None or c > 0}
            self.db_rows = [r for r in rows if r["targets"]]
        last = list(settings().value(self.pick_key()) or []) + ["", "", "", ""]
        self.p_group.blockSignals(True)
        self.p_group.clear()
        self.p_group.addItems(sorted({r["group"] for r in self.db_rows}))
        self.p_group.setCurrentText(last[0])
        self.p_group.blockSignals(False)
        self.fill_exps(select=last[1], sample=last[2], target=last[3])

    def fill_exps(self, *_, select=None, sample=None, target=None):
        g = self.p_group.currentText()
        self.p_exp.blockSignals(True)
        self.p_exp.clear()
        self.p_exp.addItems(sorted({r["experiment"] for r in self.db_rows if r["group"] == g}, key=str.lower))
        if select:
            self.p_exp.setCurrentText(select)
        self.p_exp.blockSignals(False)
        self.fill_samples(sample=sample, target=target)

    def fill_samples(self, *_, sample=None, target=None):
        """List the samples of the experiment and show one at once (the given one, else the top one)."""
        g, e = self.p_group.currentText(), self.p_exp.currentText()
        self.p_sample.blockSignals(True)
        self.p_sample.clear()
        # the item data is the index into db_rows (the row itself would be copied by Qt)
        for i in sorted((i for i, r in enumerate(self.db_rows) if r["group"] == g and r["experiment"] == e),
                        key=lambda i: self.db_rows[i]["sample"].lower()):
            r = self.db_rows[i]
            label = f"ANAL {r['anal_tdms']}" if self.kind == "A" else f"target {len(r['targets'])}"
            self.p_sample.addItem(f"{r['sample']}　（{label}）", i)
        names = [self.db_rows[self.p_sample.itemData(i)]["sample"] for i in range(self.p_sample.count())]
        self.p_sample.setCurrentIndex(names.index(sample) if sample in names else 0 if names else -1)
        self.p_sample.blockSignals(False)
        if self.kind == "B":
            self.fill_targets(target)
        # only while the page is shown: no server access at start-up or from sync_picker
        elif self.isVisible() and not getattr(self, "quiet", False):
            self.open_picked()

    def fill_targets(self, target=None):
        """Targets of the sample; the target shown before stays chosen when this sample has it too."""
        r = self.picked_row()
        target = target or self.p_target.currentData()
        self.p_target.blockSignals(True)
        self.p_target.clear()
        for name in sorted((r or {}).get("targets", {}), key=lambda n: (server_bnal.parent_target(n).lower(), n)):
            n = r["targets"][name]
            self.p_target.addItem(f"{name}　（B@@ {'?' if n is None else n}）", name)
        names = [self.p_target.itemData(i) for i in range(self.p_target.count())]
        self.p_target.setCurrentIndex(names.index(target) if target in names else 0 if names else -1)
        self.p_target.blockSignals(False)
        if self.isVisible() and not getattr(self, "quiet", False):
            self.open_picked()

    targets_listed = pyqtSignal(object, dict)

    def list_targets(self, r):
        """(thread) B@@ counts of the BNAL@ folders in T now: the database may know only some of them."""
        t = server_anal.t_dir(r)
        try:
            with os.scandir(long_path(t)) as it:
                names = [e.name[len("BNAL@"):] for e in it if e.is_dir() and e.name.startswith("BNAL@")]
        except OSError:
            return
        counts = {}
        for n in names:
            try:
                with os.scandir(long_path(t / f"BNAL@{n}")) as it:
                    counts[n] = sum(1 for e in it if e.name.startswith("B@@") and e.name.lower().endswith(".tdms"))
            except OSError:
                pass
        self.targets_listed.emit(r, counts)

    def targets_found(self, r, counts):
        """The live counts replace the saved ones; empty BNAL@ folders are left out."""
        for n, c in counts.items():
            if c:
                r["targets"][n] = c
            else:
                r["targets"].pop(n, None)
        if self.picked_row() is r:
            self.quiet = True
            self.fill_targets(self.p_target.currentData())
            self.quiet = False
            self.p_sample.setItemText(self.p_sample.currentIndex(), f"{r['sample']}　（target {len(r['targets'])}）")

    def step_pick(self, step):
        """◀ / ▶: the previous / next target (BNAL) or sample (ANAL); past the last target, the next sample."""
        if self.kind == "B":
            i = self.p_target.currentIndex() + step
            if 0 <= i < self.p_target.count():
                self.p_target.setCurrentIndex(i)
                self.open_picked()
                return
        i = self.p_sample.currentIndex() + step
        if not 0 <= i < self.p_sample.count():
            return
        self.p_sample.setCurrentIndex(i)
        if self.kind == "B":
            # the same target in the next sample when it has one, else its first / last target
            keep = self.p_target.currentData()
            self.quiet = True
            self.fill_targets()
            self.quiet = False
            if self.p_target.currentData() != keep:
                self.p_target.setCurrentIndex(0 if step > 0 else self.p_target.count() - 1)
        self.open_picked()

    def showEvent(self, event):
        super().showEvent(event)
        if self.mode is None and not self.folder_rows and not self.busy.isVisible():
            self.open_picked()

    def picked_folder(self):
        r = self.picked_row()
        if not r:
            return None
        if self.kind == "A":
            settings().setValue(self.pick_key(), [r["group"], r["experiment"], r["sample"]])
            return server_anal.t_dir(r) / "ANAL"
        target = self.p_target.currentData()
        if not target:
            return None
        settings().setValue(self.pick_key(), [r["group"], r["experiment"], r["sample"], target])
        return server_anal.t_dir(r) / f"BNAL@{target}"

    def open_picked(self, *_):
        folder = self.picked_folder()
        if folder:
            self.open_folder(folder)
        r = self.picked_row()
        if self.kind == "B" and r and not r.get("listed"):
            r["listed"] = True
            threading.Thread(target=self.list_targets, args=(r,), daemon=True).start()

    def sync_picker(self, folder):
        """Show the server / experiment / sample (/ target) of a folder opened from elsewhere."""
        folder = Path(folder)
        target = None
        if self.kind == "A":
            key = server_anal.identity(folder)
            r = next((r for r in getattr(self, "db_rows", [])
                      if server_anal.identity(server_anal.t_dir(r) / "ANAL") == key), None)
        else:
            if not folder.name.startswith("BNAL@"):
                return
            key, target = server_anal.identity(folder.parent), folder.name[len("BNAL@"):]
            r = next((r for r in getattr(self, "db_rows", [])
                      if server_anal.identity(server_anal.t_dir(r)) == key), None)
            if r and target not in r["targets"]:
                r["targets"][target] = None  # made after the database was updated
        if not r:
            return
        self.quiet = True
        self.p_group.blockSignals(True)
        self.p_group.setCurrentText(r["group"])
        self.p_group.blockSignals(False)
        self.fill_exps(select=r["experiment"], sample=r["sample"], target=target)
        self.quiet = False

    # -- y axis: automatic, or set by hand --------------------------------------------
    def build_y_controls(self):
        row = QHBoxLayout()
        self.y_auto = QCheckBox("縦軸 自動")
        self.y_auto.setChecked(True)
        self.y_auto.setToolTip("見えている範囲の波形に合わせて縦軸を決めます。外すと下限・上限を手で決められます")
        self.y_auto.toggled.connect(self.y_auto_toggled)
        row.addWidget(self.y_auto)
        self.y_lo, self.y_hi = QDoubleSpinBox(), QDoubleSpinBox()
        for label, box in (("下限", self.y_lo), ("上限", self.y_hi)):
            box.setRange(-100000, 100000)
            box.setDecimals(1)
            box.setSingleStep(1)
            box.setSuffix(" pA")
            box.setKeyboardTracking(False)
            box.valueChanged.connect(self.y_typed)
            row.addWidget(QLabel(label))
            row.addWidget(box)
        for text, tip, f in (("縦に拡大", "縦の範囲を 0.8 倍", lambda: self.y_scale(0.8)),
                             ("縦に縮小", "縦の範囲を 1.25 倍", lambda: self.y_scale(1.25)),
                             ("▲", "表示を上へ（範囲の 10%）", lambda: self.y_shift(0.1)),
                             ("▼", "表示を下へ（範囲の 10%）", lambda: self.y_shift(-0.1))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(f)
            row.addWidget(b)
        for text, tip, f in (("横に拡大", "時間の範囲を 0.5 倍", lambda: self.x_scale(0.5)),
                             ("横に縮小", "時間の範囲を 2 倍", lambda: self.x_scale(2)),
                             ("◀", "表示を前へ（範囲の 50%）", lambda: self.x_shift(-0.5)),
                             ("▶", "表示を後へ（範囲の 50%）", lambda: self.x_shift(0.5))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(f)
            row.addWidget(b)
        self.show_bl = QCheckBox("ベースライン")
        self.show_bl.setChecked(True)
        self.show_bl.setToolTip("R Table の区間ごとの Region BL を階段状の線で表示します")
        self.show_bl.toggled.connect(lambda on: self.bl_curve is not None and self.bl_curve.setVisible(on))
        row.addWidget(self.show_bl)
        self.bl_curve = None
        row.addStretch()
        self.plot.sigYRangeChanged.connect(self.y_changed)
        return row

    def auto_y(self):
        if self.y_auto.isChecked():
            self.plot.enableAutoRange(axis="y")
            self.plot.getViewBox().updateAutoRange()  # now, not at the next paint

    def y_auto_toggled(self, on):
        if on:
            self.auto_y()
        else:
            self.plot.disableAutoRange(axis="y")

    def y_changed(self, _, rng):
        for box, v in ((self.y_lo, rng[0]), (self.y_hi, rng[1])):
            box.blockSignals(True)
            box.setValue(v)
            box.blockSignals(False)
        # a mouse zoom on the y axis turns the automatic range off
        auto = bool(self.plot.getViewBox().autoRangeEnabled()[1])
        if auto != self.y_auto.isChecked():
            self.y_auto.blockSignals(True)
            self.y_auto.setChecked(auto)
            self.y_auto.blockSignals(False)

    def set_y(self, lo, hi):
        self.y_auto.setChecked(False)
        if hi > lo:
            self.plot.setYRange(lo, hi, padding=0)

    def y_typed(self, *_):
        self.set_y(self.y_lo.value(), self.y_hi.value())

    def y_scale(self, f):
        lo, hi = self.plot.viewRange()[1]
        mid, half = (lo + hi) / 2, (hi - lo) / 2 * f
        self.set_y(mid - half, mid + half)

    def y_shift(self, f):
        lo, hi = self.plot.viewRange()[1]
        d = (hi - lo) * f
        self.set_y(lo + d, hi + d)

    def x_set(self, lo, hi):
        """Show lo..hi, kept inside the trace of an A@@ file."""
        raw = getattr(self, "raw", None)
        if self.mode == "A" and raw is not None and len(raw):
            end = len(raw) / self.fs
            w = min(hi - lo, end)
            lo = min(max(lo, 0), end - w)
            hi = lo + w
        self.plot.setXRange(lo, hi, padding=0)
        self.auto_y()

    def x_scale(self, f):
        lo, hi = self.plot.viewRange()[0]
        mid, half = (lo + hi) / 2, max((hi - lo) / 2 * f, 0.0005)
        self.x_set(mid - half, mid + half)

    def x_shift(self, f):
        lo, hi = self.plot.viewRange()[0]
        d = (hi - lo) * f
        self.x_set(lo + d, hi + d)

    # -- loading ------------------------------------------------------------
    def open(self, path):
        if not path:
            return
        path = Path(path)
        if path.is_dir():
            self.sync_picker(anal_folder(path) if self.kind == "A" else path)
            self.open_folder(path)
            return
        if self.kind == "B" and path.parent.name.startswith("BNAL@"):
            # a B@@ of a BNAL@target folder: the whole folder with this file chosen
            self.sync_picker(path.parent)
            self.open_folder(path.parent, select=path.name)
            return
        self.leave_folder()
        self.open_file(path)

    def leave_folder(self):
        self.folder_rows, self.folder_info = [], {}
        self.files_label.setVisible(False)
        self.files.setVisible(False)

    def load(self, what, job, done):
        """Run job(log, progress) in a thread with the busy bar shown; done(result) runs only if no newer
        load was started meanwhile (a later selection wins)."""
        self.load_id = getattr(self, "load_id", 0) + 1
        me = self.load_id
        self.busy.setRange(0, 0)  # moving bar until the first progress value
        self.busy.setFormat("")
        self.busy.setVisible(True)
        self.file_label.setText(f"⏳ 読み込み中: {what}")
        self.file_label.setStyleSheet(f"color:{WARNING}")
        self.setCursor(Qt.CursorShape.BusyCursor)

        def progress(v, text):
            if me == self.load_id:
                self.busy.setRange(0, 100)
                self.busy.setValue(v)
                self.busy.setFormat(text)

        def finished(out):
            if me != self.load_id:
                return
            self.busy.setVisible(False)
            self.file_label.setStyleSheet("")
            self.unsetCursor()
            done(out)
        thread, worker = QThread(), Worker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(progress)
        worker.finished.connect(finished)
        worker.finished.connect(thread.quit)
        self._bg = [(t, w) for t, w in getattr(self, "_bg", []) if t.isRunning()] + [(thread, worker)]
        thread.start()

    def open_folder(self, path, select=None):
        folder = anal_folder(path) if self.kind == "A" else Path(path)
        settings().setValue(self.folder_key, str(path))
        self.leave_folder()
        self.mode = None
        self.summary.setRowCount(0)
        self.items.setRowCount(0)
        self.list_label.setText("")
        self.seq_view.clear()
        self.plot.clear()
        self.overview.setVisible(False)
        self.pick_raw.setVisible(False)
        prefix, read = ("A@@", read_folder) if self.kind == "A" else ("B@@", read_bfolder)
        self.load(f"{folder}（{prefix} の一覧と合計）", lambda log, progress: read(folder, progress),
                  lambda rows: self.show_folder(folder, rows, select))

    def a_totals(self, folder, rows, ok, old_bad):
        total = lambda col: float(np.nansum([r[col] for r in ok]))
        time_s = total("測定時間 [s]")
        return {
            "フォルダ": str(folder),
            "A@@ ファイル数": f"{len(rows)}{old_bad}",
            "合計した測定数": len(ok),
            "信号数 合計": f"{total('信号数'):,.0f}",
            "バースト数 合計": f"{total('バースト数'):,.0f}",
            "測定時間 合計": f"{time_s:,.1f} s（{hms(time_s)}）",
            "バースト時間 合計": f"{total('バースト時間 [s]'):,.2f} s",
            "信号頻度（合計÷時間）": f"{total('信号数') / time_s:.4f} /s" if time_s else "-",
        }

    def b_totals(self, folder, rows, ok, old_bad):
        total = lambda col: float(np.nansum([r[col] for r in ok]))
        time_s, sig, long_ = total("測定時間 [s]"), total("信号数"), total("長い信号数")
        bc = next((r["bc"] for r in ok if r.get("bc")), {})
        cp = next((r["Total CP"] for r in ok if r.get("Total CP", 0) > 0), 0)
        covered = set().union(*(r.get("covered", set()) for r in ok))
        mx = np.nanmax([r["MAX Right Read"] for r in ok]) if ok else float("nan")
        return {
            "フォルダ": str(folder),
            "target": bc.get("Sample name", ""),
            "Sample Sequence": bc.get("Sample Sequence", ""),
            "Right Read MIN": bc.get("Right Read MIN [n]", ""),
            "B@@ ファイル数": f"{len(rows)}{old_bad}",
            "合計した測定数": len(ok),
            "信号数 合計": f"{sig:,.0f}",
            "長い信号数 合計": f"{long_:,.0f}" + (f"（{long_ / sig * 100:.2f} %）" if sig else ""),
            "MAX Right Read（最大）": "-" if mx != mx else f"{mx:.0f}",
            "Coverage（全ファイル）": f"{len(covered) / cp * 100:.1f} %（{len(covered)} / {cp:.0f}）" if cp else "-",
            "Long Read 合計": f"{total('Long Read [bp]'):,.0f} bp",
            "Long Right Read 合計": f"{total('Long Right Read [bp]'):,.0f} bp",
            "測定時間 合計": f"{time_s:,.1f} s（{hms(time_s)}）",
            "長い信号の頻度（合計÷時間）": f"{long_ / time_s:.4f} /s" if time_s else "-",
        }

    def show_folder(self, folder, rows, select=None):
        self.file_label.setText(str(folder))
        prefix = "A@@" if self.kind == "A" else "B@@"
        if not rows:  # no dialog: ◀ ▶ go on through the targets / samples
            self.file_label.setText(f"✖ {prefix} ファイルがありません（またはフォルダを開けません）: {folder}")
            self.file_label.setStyleSheet(f"color:{DANGER}")
            return
        ok = [r for r in rows if not r["エラー"] and not r["重複"]]
        bad, old = sum(bool(r["エラー"]) for r in rows), sum(bool(r["重複"]) for r in rows)
        old_bad = (f"（同じ#番号の古い解析 {old} は合計に含めない）" if old else "") + (f"（読めない {bad}）" if bad else "")
        self.folder_rows = rows
        self.folder_info = (self.a_totals if self.kind == "A" else self.b_totals)(folder, rows, ok, old_bad)
        self.fill_summary(self.folder_info)
        value_cols = [c for c, _ in FOLDER_COLS] if self.kind == "A" else [c for c, _ in B_FOLDER_COLS] + ["測定時間 [s]"]
        cols = ["#"] + value_cols + ["ファイル", "重複", "エラー"]
        self.files.blockSignals(True)
        self.files.setSortingEnabled(False)
        self.files.clear()
        self.files.setColumnCount(len(cols))
        self.files.setHorizontalHeaderLabels(cols)
        self.files.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for c, col in enumerate(cols):
                v = r[col]
                item = QTableWidgetItem()
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    item.setData(Qt.ItemDataRole.DisplayRole, "" if v != v else (int(v) if float(v).is_integer() else v))
                else:
                    item.setText(str(v))
                item.setData(Qt.ItemDataRole.UserRole, i)
                self.files.setItem(i, c, item)
        self.files.resizeColumnsToContents()
        self.files.setSortingEnabled(True)
        self.files.blockSignals(False)
        what = "波形と信号・バースト" if self.kind == "A" else "長い信号と塩基の帰属"
        self.files_label.setText(f"ファイル一覧 ({len(rows)})　行を選ぶと{what}を表示（↑↓ で移動）")
        self.files_label.setVisible(True)
        self.files.setVisible(True)
        names = [r["ファイル"] for r in rows]
        self.files.selectRow(names.index(select) if select in names else 0)

    def file_selected(self):
        rows = self.files.selectionModel().selectedRows()
        if not rows:
            return
        r = self.folder_rows[self.files.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)]
        self.open_file(r["path"])

    A_TABLES = ["S Table", "R Table", "B Table", "AR Table"]
    B_TABLES = ["LSP Table", "LTP Table", "BR Table", "BC Table"]

    def open_file(self, path):
        known = dict(self.known_a)

        def read(log, progress):
            # only the tables shown are read (a B@@ also holds large per-base tables)
            try:
                with viewer_rows.tdms(path) as t:
                    kind = "B" if "BR Table" in t or Path(path).name.upper().startswith("B@@") else "A"
                    tab = read_tables(t, self.B_TABLES if kind == "B" else self.A_TABLES)
                    data = t["Data"]["Ch1"][:] * 1000 if "Data" in t and "Ch1" in t["Data"] else np.array([])
                return [(kind, tab, data)]
            except Exception as e:
                return [e]

        def show(out):
            res = out[0] if out else RuntimeError("読み込めませんでした")
            try:
                if isinstance(res, Exception):
                    raise res
                kind, tab, data = res
                if kind == "B":
                    self.show_b(path, tab, data)
                else:
                    self.show_a(path, tab, data)
                self.file_label.setText(str(path))
                if kind == "B" and self.raw is None and self.a_name:
                    self.fetch_raw(path, known)
            except Exception as e:
                self.file_label.setText(f"✖ 読み込みエラー: {path}")
                QMessageBox.warning(self, "読み込みエラー", f"{path}\n\n{e}")
        what = "波形と信号・バースト" if self.kind == "A" else "長い信号と波形"
        self.load(f"{Path(path).name}（{what}）", read, show)

    def fill_summary(self, row):
        self.summary.setRowCount(0)
        for k, v in row.items():
            r = self.summary.rowCount()
            self.summary.insertRow(r)
            self.summary.setItem(r, 0, QTableWidgetItem(str(k)))
            self.summary.setItem(r, 1, QTableWidgetItem(str(v)))

    def fill_items(self, df, cols):
        self.items.blockSignals(True)
        self.items.clear()
        self.items.setColumnCount(len(cols))
        self.items.setHorizontalHeaderLabels(cols)
        self.items.setRowCount(len(df))
        for r, (_, row) in enumerate(df[cols].iterrows()):
            for c, v in enumerate(row):
                self.items.setItem(r, c, QTableWidgetItem(str(v)))
        self.items.resizeColumnsToContents()
        self.items.blockSignals(False)

    def show_a(self, path, tab, raw):
        self.mode = "A"
        self.raw = raw
        self.fs = 10000
        ar = tab["AR Table"].iloc[0]
        keys = ["Signal#Sum", "Burst#Sum", "Signal Freq [/s]", "B Duty Cycle [%]", "Region BL Mode [pA]",
                "Region STD Mode [pA]", "Total Time [s]", "Anal Version"]
        file_info = {k: ar[k] for k in keys if k in ar}
        if self.folder_info:  # folder totals stay on top, the selected file below
            file_info = {**self.folder_info, "── 選択ファイル": Path(path).name, **file_info}
        self.fill_summary(file_info)
        self.btab, self.stab = tab["B Table"], tab["S Table"]
        self.list_kind.setVisible(True)
        self.fill_a_items()
        self.plot.clear()
        self.plot.addLegend(offset=(10, 10))
        tt = np.arange(len(self.raw)) / self.fs
        curve = self.plot.plot(tt, self.raw, pen=pg.mkPen("#7aa2ff", width=1), name="Raw")
        curve.setDownsampling(auto=True, method="peak")
        curve.setClipToView(True)
        s = tab["S Table"]
        if len(s):
            pk = s["S Peak Position [s]"].astype(float).to_numpy()
            idx = np.clip(np.rint(pk * self.fs).astype(int), 0, len(self.raw) - 1)
            self.plot.plot(pk, self.raw[idx], pen=None, symbol="o", symbolSize=5,
                           symbolBrush="#fbbf24", symbolPen=None, name="Signal peak")
        self.bl_curve = self.draw_baseline(tab.get("R Table"), s)
        for _, b in self.btab.iterrows():
            reg = pg.LinearRegionItem((float(b["B Start [s]"]), float(b["B End [s]"])), movable=False,
                                      brush=pg.mkBrush(239, 68, 68, 22), pen=pg.mkPen(None))
            self.plot.addItem(reg)
        self.draw_overview(ar)
        self.plot.setXRange(0, min(10, tt[-1] if len(tt) else 1), padding=0)
        self.auto_y()
        self.seq_view.setHtml("<span style='color:#8b9a92'>赤い帯 = バースト、黄色の点 = 信号のピーク。"
                              "一覧のバースト・信号を選ぶと拡大します（右上で切り替え）。"
                              "上の全体図の青い帯をドラッグすると表示位置が動きます（緑 = バースト）。"
                              "白い階段線 = 区間ごとのベースライン（Region BL）。"
                              "軸の上でホイール・ドラッグしても拡大・縮小できます。</span>")

    def draw_baseline(self, r, s):
        """Region BL of each region (R Table) as a step line; a region is "Read Region Point" samples."""
        if r is None or "Region BL [pA]" not in r or not len(r):
            return None
        bl = r["Region BL [pA]"].astype(float).to_numpy()
        col = next((c for c in s.columns if c.startswith("Read Region Point")), None) if len(s) else None
        try:
            rrp = int(float(s[col].iloc[0]))
        except (TypeError, ValueError, KeyError):
            rrp = len(self.raw) // len(bl)
        edges = np.arange(len(bl) + 1) * rrp / self.fs
        curve = self.plot.plot(edges, bl, stepMode="center", name="Baseline (Region BL)",
                               pen=pg.mkPen("#f1f5f9", width=2))
        curve.setZValue(5)
        curve.setVisible(self.show_bl.isChecked())
        return curve

    def draw_overview(self, ar):
        """Min/max envelope of the whole trace with the bursts as green ticks."""
        ov = self.overview
        ov.clear()
        n = len(self.raw)
        if not n:
            ov.setVisible(False)
            return
        bins = min(3000, n)
        k = n // bins
        env = self.raw[:bins * k].reshape(bins, k)
        x = (np.arange(bins) * k + k / 2) / self.fs
        lo, hi = env.min(axis=1), env.max(axis=1)
        top, bottom = ov.plot(x, hi, pen=pg.mkPen("#94a3b8")), ov.plot(x, lo, pen=pg.mkPen("#94a3b8"))
        ov.addItem(pg.FillBetweenItem(top, bottom, brush=pg.mkBrush(148, 163, 184, 150)))
        # a few huge spikes would flatten the strip, so the range ignores the extreme 0.5 %
        y0, y1 = float(np.percentile(lo, 0.5)), float(np.percentile(hi, 99.5))
        if len(self.btab):
            bs = self.btab["B Start [s]"].astype(float).to_numpy()
            ov.plot(np.repeat(bs, 2), np.tile([y0, y1], len(bs)), connect="pairs", pen=pg.mkPen("#22c55e", width=1))
        try:
            ov.addItem(pg.InfiniteLine(float(ar["Region BL Mode [pA]"]), angle=0,
                                       pen=pg.mkPen("#f87171", style=Qt.PenStyle.DashLine)))
        except (KeyError, ValueError):
            pass
        ov.addItem(self.view_band)
        ov.setXRange(0, n / self.fs, padding=0)
        ov.setYRange(y0, y1)
        ov.setVisible(True)

    def band_moved(self):
        lo, hi = self.view_band.getRegion()
        self.plot.blockSignals(True)
        self.plot.setXRange(lo, hi, padding=0)
        self.plot.blockSignals(False)

    def view_moved(self, _, rng):
        self.view_band.blockSignals(True)
        self.view_band.setRegion(rng)
        self.view_band.blockSignals(False)

    S_COLS = ["S # [n]", "S Peak Position [s]", "Signal [pA]", "S TL [ms]", "Region BL [pA]", "Threshold [pA]"]
    B_COLS = ["B # [n]", "B Start [s]", "B time length [ms]", "B Signal Count [n]", "B BL [pA]", "B Signal MAX [pA]"]

    def fill_a_items(self, *_):
        if self.mode != "A":
            return
        if self.list_kind.currentText() == "信号":
            self.list_label.setText(f"信号一覧 ({len(self.stab)})")
            self.fill_items(self.stab, [c for c in self.S_COLS if c in self.stab])
        else:
            self.list_label.setText(f"バースト一覧 ({len(self.btab)})")
            self.fill_items(self.btab, self.B_COLS)

    def show_b(self, path, tab, raw):
        self.mode = "B"
        self.list_kind.setVisible(False)
        self.overview.setVisible(False)
        br = tab["BR Table"].iloc[0]
        bc = tab["BC Table"].iloc[0]
        # without long signals LabVIEW writes no LSP / LTP table
        for n in ("LSP Table", "LTP Table"):
            tab.setdefault(n, pd.DataFrame(columns=["L#", "File name"]))
        self.sample, self.b_path = bc["Sample Sequence"], str(path)
        info = {"Sample name": bc["Sample name"], "Sample Sequence": self.sample}
        info.update({k: br[k] for k in br.index if "BNAL" not in k})
        if self.folder_info:  # folder totals stay on top, the selected file below
            info = {**self.folder_info, "── 選択ファイル": Path(path).name, **info}
        self.fill_summary(info)
        self.lsp, self.ltp = tab["LSP Table"], tab["LTP Table"]
        self.list_label.setText(f"長い信号 (Right Read ≥ {bc['Right Read MIN [n]']}) {len(self.lsp)} 件")
        self.fill_items(self.lsp, [c for c in ["L#", "Signal Start [s]", "Base Number", "Mx Right Read Seq",
                                               "Mx Right No", "M_MxRight_No", "Direction"] if c in self.lsp])
        # the waveform: the B@@ Data channel, else the A@@ named in the tables (fetch_raw, after this)
        self.raw = raw if raw is not None and len(raw) else None
        self.a_name = str(self.lsp["File name"].iloc[0]) if len(self.lsp) and "File name" in self.lsp else ""
        self.pick_raw.setVisible(False)
        self.plot.clear()
        self.seq_view.setHtml("<span style='color:#8b9a92'>" + ("一覧の長い信号を選ぶと、生データと塩基の帰属を表示します。"
                              if len(self.lsp) else "このファイルに長い信号はありません。") + "</span>")
        if len(self.lsp):
            self.items.selectRow(0)

    def fetch_raw(self, path, known):
        """The tables are shown already; the waveform of the A@@ the B@@ was made from follows."""
        a_name = self.a_name

        def read(log, progress):
            return [source_raw(path, a_name, known)]

        def done(out):
            raw, a_path = out[0] if out else (None, None)
            self.file_label.setText(str(path))
            if raw is None:
                self.pick_raw.setVisible(True)
                self.seq_view.append(f"<span style='color:{WARNING}'>※ 元の A@@ ({a_name}) が見つからないため"
                                     "帰属点のみ表示します。「元の A@@ を指定…」で生データも表示できます。</span>")
                return
            self.raw = raw
            self.known_a[Path(a_path).name] = str(a_path)
            self.item_selected()
        self.load(f"{a_name}（元の A@@ の波形）", read, done)

    def choose_raw(self):
        path, _ = QFileDialog.getOpenFileName(self, f"元の A@@ ({self.a_name})", "", "TDMS (A@@*.tdms)")
        if not path:
            return

        def read(log, progress):
            with TdmsFile.open(long_path(path)) as t:
                return [t["Data"]["Ch1"][:] * 1000]

        def done(out):
            if not out:
                QMessageBox.warning(self, "読み込みエラー", f"波形を読めません:\n{path}")
                return
            self.raw = out[0]
            self.known_a[Path(path).name] = path
            self.pick_raw.setVisible(False)
            self.file_label.setText(f"{self.b_path}　（波形: {path}）")
            self.item_selected()
        self.load(f"{Path(path).name}（波形）", read, done)

    # -- selection ----------------------------------------------------------
    def item_selected(self):
        rows = self.items.selectionModel().selectedRows()
        if not rows:
            return
        r = rows[0].row()
        if self.mode == "A":
            if self.list_kind.currentText() == "信号":
                sig = self.stab.iloc[r]
                s, e = float(sig["Signal S [s]"]), float(sig["Signal E (s)"])
                pad = max((e - s) * 3, 0.005)
            else:
                b = self.btab.iloc[r]
                s, e = float(b["B Start [s]"]), float(b["B End [s]"])
                pad = (e - s) * 0.5
            self.plot.setXRange(s - pad, e + pad)
            self.auto_y()
        elif self.mode == "B":
            self.show_long_signal(r)

    def show_long_signal(self, r):
        sig = self.lsp.iloc[r]
        pts = self.ltp[self.ltp["L#"] == sig["L#"]]
        self.plot.clear()
        self.plot.addLegend(offset=(10, 10))
        t0, t1 = float(sig["Signal Start [s]"]), float(sig["Signal End [s]"])
        pad = max((t1 - t0) * 0.6, 0.002)
        if self.raw is not None:
            i0, i1 = max(int((t0 - pad) * 10000), 0), min(int((t1 + pad) * 10000), len(self.raw))
            tt = np.arange(i0, i1) / 10000
            self.plot.plot(tt, self.raw[i0:i1], pen=pg.mkPen("#4b5263", width=1.2),
                           symbol="o", symbolSize=3, symbolBrush="#4b5263", symbolPen=None, name="Raw")
        for base, color in BASE_COLORS.items():
            col = f"{base}_AL" if f"{base}_AL" in sig else ("BL_AL" if base == "B" else None)
            if col and col in sig:
                lvl = float(sig[col])
                line = pg.InfiniteLine(lvl, angle=0, pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine),
                                       label=base, labelOpts={"color": color, "position": 0.02})
                self.plot.addItem(line)
            sel = pts[pts["Assinged Base"] == base]
            if len(sel):
                self.plot.plot(sel["Time [s]"].astype(float).to_numpy(),
                               sel["Raw data [pA]"].astype(float).to_numpy() * 1000,
                               pen=None, symbol="o", symbolSize=8, symbolBrush=color, symbolPen=None,
                               name=f"{base}")
        self.plot.setXRange(t0 - pad, t1 + pad)
        self.auto_y()
        self.seq_view.setHtml(self.sequence_html(sig))

    def sequence_html(self, sig):
        def colored(seq, hl=None):
            out = []
            for i, ch in enumerate(seq):
                style = f"color:{BASE_COLORS.get(ch, TEXT)}"
                if hl and hl[0] <= i < hl[1]:
                    style += f";background:{ui_theme.mix(FIELD, ACCENT, 0.3)};font-weight:bold"
                out.append(f"<span style='{style}'>{ch}</span>")
            return "".join(out)
        seq, mx = sig["Assinged Sequence"], sig["Mx Right Read Seq"]
        rev = not str(sig["Direction"]).startswith("5")
        target = mx[::-1] if rev else mx
        p = seq.find(target)
        mono = "font-family:Consolas;font-size:12pt"
        return (f"<div style='{mono}'>"
                f"<span style='color:#8b9a92'>Read&nbsp;&nbsp;&nbsp;</span>{colored(seq, (p, p + len(mx)) if p >= 0 else None)}<br>"
                f"<span style='color:#8b9a92'>Sample&nbsp;</span>{colored(self.sample)}<br>"
                f"<span style='color:#8b9a92'>Right read: {mx} ({sig['Mx Right No']} / 全長換算 "
                f"{sig['M_MxRight_No']})  {sig['Direction']}</span></div>")


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} — Total Analysis (Python)")
        self.setWindowIcon(qt_theme.window_icon(APP_ICON, ACCENT))
        self.resize(1400, 880)
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(qt_theme.Header(APP_TITLE, APP_SUBTITLE, APP_ICON, ACCENT, on_close=self.close))
        body = QWidget()
        lay = QHBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        outer.addWidget(body, 1)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(240)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 10, 8, 16)
        self.stack = QStackedWidget()
        self.viewer, self.bviewer = ViewerPage("A"), ViewerPage("B")
        self.queue = QueuePage(self.show_result, self.queue_changed)
        first = FirstStepPage(self.show_result, self.queue.add)
        pages = [("①  ANAL（サーバー）", ServerAnalPage(self.show_result, self.queue.add)),
                 ("①  1st Step（ファイル）", first),
                 ("②  BNAL（サーバー）", ServerBnalPage(self.show_result, self.queue.add)),
                 ("②  2nd Step（ファイル）", SecondStepPage(self.show_result, self.queue.add)),
                 ("③  解析キュー", self.queue),
                 ("◎  ANAL 結果ビューア", self.viewer),
                 ("◎  BNAL 結果ビューア", self.bviewer)]
        group = QButtonGroup(self)
        self.nav = []
        for i, (text, page) in enumerate(pages):
            self.stack.addWidget(page)
            b = QPushButton(text)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _, i=i: self.stack.setCurrentIndex(i))
            group.addButton(b)
            sl.addWidget(b)
            self.nav.append(b)
        self.nav[0].setChecked(True)
        sl.addStretch()
        sl.addWidget(QLabel("解析者"))
        self.operator = QLineEdit(settings().value("operator", first_step_io.OPERATOR))
        self.operator.setToolTip("ANAL / BNAL のファイル（A@@・B@@ のプロパティ）と実行記録に書き込む名前")
        self.operator.textChanged.connect(self.set_operator)
        sl.addWidget(self.operator)
        self.set_operator(self.operator.text())
        sl.addSpacing(10)
        ver = QLabel("LabVIEW 20180816 /\n20180801 の Python 移植")
        ver.setObjectName("subtitle")
        sl.addWidget(ver)
        lay.addWidget(side)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)

    def set_operator(self, name):
        first_step_io.OPERATOR = name.strip() or "（未入力）"
        settings().setValue("operator", name.strip())

    def queue_changed(self, pending):
        i = self.stack.indexOf(self.queue)
        self.nav[i].setText(f"③  解析キュー ({pending})" if pending else "③  解析キュー")

    def show_result(self, path, a_files=()):
        viewer = self.bviewer if Path(path).name.upper().startswith("B@@") else self.viewer
        for a in a_files:
            self.bviewer.known_a[Path(a).name] = str(a)
        viewer.open(path)
        i = self.stack.indexOf(viewer)
        self.nav[i].setChecked(True)
        self.stack.setCurrentIndex(i)


def main():
    if sys.platform == "win32":
        # タスクバーに pythonw ではなくこのアプリのアイコンを出す
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PythonVScode.Analyzer")
    app = QApplication(sys.argv)
    qt_theme.apply(app, ACCENT)
    app.setStyleSheet(STYLE)
    app.setWindowIcon(qt_theme.window_icon(APP_ICON, ACCENT))
    w = MainWindow()
    w.show()
    viewer_rows.warm_up()  # the result viewers read folders in worker processes
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
