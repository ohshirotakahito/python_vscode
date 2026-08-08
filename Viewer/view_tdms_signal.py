# -*- coding: utf-8 -*-

import os
import re
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, SpanSelector, CheckButtons
from scipy.signal import savgol_filter
from nptdms import TdmsFile
from pathlib import Path


# ========= Utility =========

def list_tdms_files(folder: str, recursive: bool = False):
    tdms_paths = []
    if not os.path.isdir(folder):
        return tdms_paths

    if recursive:
        for dirpath, _, filenames in os.walk(folder):
            for fn in filenames:
                if fn.lower().endswith(".tdms"):
                    tdms_paths.append(os.path.join(dirpath, fn))
    else:
        for fn in os.listdir(folder):
            if fn.lower().endswith(".tdms"):
                tdms_paths.append(os.path.join(folder, fn))

    tdms_paths.sort()
    return tdms_paths


def tdms_read_channel(tdms_file_path, target_group="Data", target_channel="Ch1"):
    tdms_file = TdmsFile.read(tdms_file_path)

    group = next((grp for grp in tdms_file.groups() if grp.name == target_group), None)
    if group is None:
        raise ValueError(f"Group '{target_group}' not found in: {tdms_file_path}")

    channel = next((ch for ch in group.channels() if ch.name == target_channel), None)
    if channel is None:
        raise ValueError(
            f"Channel '{target_channel}' not found in Group '{target_group}' in: {tdms_file_path}"
        )

    return np.asarray(channel.data)


def make_safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def detect_pulses(y, dt=0.0001, threshold_k=6.0, min_width_ms=0.2):
    y = np.asarray(y)

    if len(y) == 0:
        return [], 0.0, 0.0

    baseline = np.median(y)
    noise = np.std(y - baseline)
    threshold = baseline + threshold_k * noise

    above = y > threshold

    pulses = []
    in_pulse = False
    start = None

    for i, flag in enumerate(above):
        if flag and not in_pulse:
            start = i
            in_pulse = True

        elif not flag and in_pulse:
            end = i
            width_ms = (end - start) * dt * 1000

            if width_ms >= min_width_ms:
                segment = y[start:end]
                pulses.append({
                    "start_index": start,
                    "end_index": end,
                    "start_time_s": start * dt,
                    "end_time_s": end * dt,
                    "width_ms": width_ms,
                    "peak": float(np.max(segment)),
                    "mean": float(np.mean(segment)),
                    "area": float(np.sum(segment - baseline) * dt),
                    "baseline": float(baseline),
                    "threshold": float(threshold),
                })

            in_pulse = False

    if in_pulse:
        end = len(y)
        width_ms = (end - start) * dt * 1000

        if width_ms >= min_width_ms:
            segment = y[start:end]
            pulses.append({
                "start_index": start,
                "end_index": end,
                "start_time_s": start * dt,
                "end_time_s": end * dt,
                "width_ms": width_ms,
                "peak": float(np.max(segment)),
                "mean": float(np.mean(segment)),
                "area": float(np.sum(segment - baseline) * dt),
                "baseline": float(baseline),
                "threshold": float(threshold),
            })

    return pulses, baseline, threshold


# ========= Core UI =========

class TdmsMultiFolderBrowser:
    def __init__(
        self,
        anal_folders,
        dt=0.0001,
        chunk_sec=5.0,
        target_group="Data",
        target_channel="Ch1",
        out_root="clips_pick",
        recursive_tdms=False,
        sanitize_filename=False,
        baseline_window=501,
        sg_window=51,
        sg_poly=3,
        threshold_k=6.0,
        pad_for_std=500,
        save_subfolder_per_aa=True,
        min_width_ms=0.2,
    ):
        if not anal_folders:
            raise RuntimeError("ANALフォルダが1つも見つかりませんでした。")

        self.anal_folders = anal_folders
        self.dt = float(dt)
        self.chunk_sec = float(chunk_sec)
        self.chunk_size = int(round(self.chunk_sec / self.dt))

        self.target_group = target_group
        self.target_channel = target_channel
        self.recursive_tdms = recursive_tdms

        self.out_root = out_root
        os.makedirs(self.out_root, exist_ok=True)

        self.sanitize_filename = sanitize_filename
        self.save_subfolder_per_aa = save_subfolder_per_aa

        self.baseline_window = int(baseline_window)
        self.sg_window = int(sg_window)
        self.sg_poly = int(sg_poly)
        self.threshold_k = float(threshold_k)
        self.pad_for_std = int(pad_for_std)
        self.min_width_ms = float(min_width_ms)

        self.folder_idx = 0
        self.tdms_files = []
        self.file_idx = 0
        self.chunk_idx = 0

        self.data = None
        self.n = 0
        self.num_chunks = 1

        self.sel_tmin = None
        self.sel_tmax = None
        self.sel_patch = None

        # ===== 表示ON/OFF設定 =====
        self.show_signal = True
        self.show_baseline = True
        self.show_threshold = True
        self.show_pulse = True
        self.show_peak_value = True

        self.fig, self.ax = plt.subplots(figsize=(14, 7))
        plt.subplots_adjust(left=0.18, bottom=0.26)

        # ==========================
        # CheckButtons
        # ==========================

        ax_check = plt.axes([0.02, 0.52, 0.13, 0.24])

        self.check = CheckButtons(
            ax_check,
            ["Signal", "Baseline", "Threshold", "Pulse", "Peak value"],
            [
                self.show_signal,
                self.show_baseline,
                self.show_threshold,
                self.show_pulse,
                self.show_peak_value,
            ]
        )

        self.check.on_clicked(self.on_check)

        # ==========================
        # ボタン配置
        # ==========================

        ax_prev_file = plt.axes([0.20, 0.05, 0.10, 0.08])
        ax_next_file = plt.axes([0.31, 0.05, 0.10, 0.08])

        ax_prev_chunk = plt.axes([0.43, 0.05, 0.07, 0.08])
        ax_next_chunk = plt.axes([0.51, 0.05, 0.07, 0.08])

        ax_save = plt.axes([0.62, 0.05, 0.08, 0.08])
        ax_clear = plt.axes([0.71, 0.05, 0.08, 0.08])
        ax_stop = plt.axes([0.80, 0.05, 0.10, 0.08])

        # ==========================
        # ボタン生成
        # ==========================

        self.btn_prev_file = Button(ax_prev_file, "Prev File")
        self.btn_next_file = Button(ax_next_file, "Next File")

        self.btn_prev_chunk = Button(ax_prev_chunk, "Prev")
        self.btn_next_chunk = Button(ax_next_chunk, "Next")

        self.btn_save = Button(ax_save, "Save")
        self.btn_clear = Button(ax_clear, "Clear")
        self.btn_stop = Button(ax_stop, "Stop")

        # ==========================
        # イベント登録
        # ==========================

        self.btn_prev_file.on_clicked(self.on_prev_file)
        self.btn_next_file.on_clicked(self.on_next_file)

        self.btn_prev_chunk.on_clicked(self.on_prev_chunk)
        self.btn_next_chunk.on_clicked(self.on_next_chunk)

        self.btn_save.on_clicked(self.on_save)
        self.btn_clear.on_clicked(self.on_clear)
        self.btn_stop.on_clicked(self.on_stop)

        # ==========================
        # SpanSelector
        # ==========================

        self.span = SpanSelector(
            self.ax,
            onselect=self.on_select,
            direction="horizontal",
            useblit=True,
            interactive=True,
            props=dict(alpha=0.25),
        )

        self.load_folder(0)
        self.draw()

    def on_check(self, label):
        if label == "Signal":
            self.show_signal = not self.show_signal
        elif label == "Baseline":
            self.show_baseline = not self.show_baseline
        elif label == "Threshold":
            self.show_threshold = not self.show_threshold
        elif label == "Pulse":
            self.show_pulse = not self.show_pulse
        elif label == "Peak value":
            self.show_peak_value = not self.show_peak_value

        self.draw()

    def current_anal_folder(self):
        return self.anal_folders[self.folder_idx]

    def current_aa_id(self):
        anal = Path(self.current_anal_folder())

        try:
            condition = anal.parent.name
            sample_folder = anal.parent.parent.name
            sample = anal.parent.parent.parent.name
            return f"{sample}_{sample_folder}_{condition}"
        except Exception:
            return anal.parent.name

    def out_dir(self):
        if not self.save_subfolder_per_aa:
            return self.out_root

        aa = self.current_aa_id()
        out = os.path.join(self.out_root, aa)
        os.makedirs(out, exist_ok=True)
        return out

    def current_tdms_path(self):
        return self.tdms_files[self.file_idx]

    def current_base_name(self):
        base = os.path.splitext(os.path.basename(self.current_tdms_path()))[0]
        return make_safe_filename(base) if self.sanitize_filename else base

    def load_folder(self, idx):
        self.folder_idx = idx % len(self.anal_folders)
        anal = self.current_anal_folder()

        self.tdms_files = list_tdms_files(anal, recursive=self.recursive_tdms)
        self.file_idx = 0
        self.chunk_idx = 0
        self.on_clear(None)

        if not self.tdms_files:
            self.data = np.array([])
            self.n = 0
            self.num_chunks = 1
            print(f"[WARN] No tdms in: {anal}")
            return

        self.load_file(0)

    def load_file(self, idx):
        if not self.tdms_files:
            self.data = np.array([])
            self.n = 0
            self.num_chunks = 1
            return

        self.file_idx = idx % len(self.tdms_files)
        tdms_path = self.current_tdms_path()

        print("=" * 80)
        print("[INFO] CURRENT FILE")
        print(tdms_path)

        try:
            self.data = tdms_read_channel(
                tdms_path,
                self.target_group,
                self.target_channel
            )
        except Exception as e:
            self.data = np.array([])
            print(f"[ERROR] 読み込み失敗: {tdms_path}\n  -> {e}")

        self.n = len(self.data)
        self.num_chunks = max(1, int(np.ceil(self.n / self.chunk_size))) if self.n > 0 else 1
        self.chunk_idx = 0
        self.on_clear(None)

    def on_stop(self, event):
        print("=" * 80)
        print("[INFO] Stop button pressed")
        print("[INFO] Closing viewer")

        try:
            plt.close(self.fig)
        except Exception as e:
            print("[ERROR]", e)

    def _compute_baseline_threshold(self, y, global_start):
        if len(y) >= self.sg_window and self.sg_window % 2 == 1:
            y_f = savgol_filter(y, window_length=self.sg_window, polyorder=self.sg_poly)
        else:
            y_f = y.copy()

        w = self.baseline_window

        if w < 3:
            baseline = np.zeros_like(y_f) + (np.mean(y_f) if len(y_f) else 0.0)
        else:
            if w % 2 == 0:
                w += 1

            half = w // 2
            padded = np.pad(y_f, (half, half), mode="edge")
            baseline = np.convolve(padded, np.ones(w) / w, mode="valid")

        if self.n > 0:
            s = max(global_start - self.pad_for_std, 0)
            e = min(global_start + len(y) + self.pad_for_std, self.n)
            std = float(np.std(self.data[s:e])) if e > s else float(np.std(y))
        else:
            std = 0.0

        threshold = baseline + self.threshold_k * std
        return baseline, threshold, std

    def draw(self):
        self.ax.clear()

        anal = self.current_anal_folder()
        aa = self.current_aa_id()

        if not self.tdms_files:
            self.ax.text(
                0.5, 0.5,
                f"No TDMS found in:\n{anal}",
                ha="center",
                va="center",
                transform=self.ax.transAxes
            )
            self.fig.canvas.draw_idle()
            return

        if self.n == 0:
            self.ax.text(
                0.5, 0.5,
                f"Failed to read:\n{self.current_tdms_path()}",
                ha="center",
                va="center",
                transform=self.ax.transAxes
            )
            self.fig.canvas.draw_idle()
            return

        start = self.chunk_idx * self.chunk_size
        end = min(start + self.chunk_size, self.n)

        y = self.data[start:end]
        t = np.arange(start, end) * self.dt

        baseline, threshold, std = self._compute_baseline_threshold(y, start)

        pulses, pulse_baseline, pulse_threshold = detect_pulses(
            y,
            dt=self.dt,
            threshold_k=self.threshold_k,
            min_width_ms=self.min_width_ms
        )

        if self.show_signal:
            self.ax.plot(t, y, label="Signal", linewidth=1.0)

        if self.show_baseline:
            self.ax.plot(t, baseline, label="Baseline", linewidth=2.0)

        if self.show_threshold:
            self.ax.plot(t, threshold, label=f"Threshold (+{self.threshold_k}σ)", linewidth=2.0)

        if self.show_pulse:
            for pulse in pulses:
                x0 = t[0] + pulse["start_time_s"]
                x1 = t[0] + pulse["end_time_s"]
                self.ax.axvspan(x0, x1, alpha=0.25)

                peak_local_idx = pulse["start_index"] + np.argmax(
                    y[pulse["start_index"]:pulse["end_index"]]
                )
                peak_time = t[peak_local_idx]
                peak_value = y[peak_local_idx]

                self.ax.plot(
                    peak_time,
                    peak_value,
                    marker="o",
                    markersize=8,
                    linestyle="None",
                    label="Peak" if pulse == pulses[0] else None
                )

                if self.show_peak_value:
                    self.ax.text(
                        peak_time,
                        peak_value,
                        f"{peak_value:.2f}",
                        fontsize=9,
                        ha="center",
                        va="bottom"
                    )

        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Signal Amplitude")
        self.ax.grid(True, alpha=0.2)

        handles, labels = self.ax.get_legend_handles_labels()
        if handles:
            self.ax.legend(loc="upper right")

        current_file = os.path.basename(self.current_tdms_path())
        current_fullpath = self.current_tdms_path()

        self.ax.set_title(
            f"[{aa}] "
            f"File {self.file_idx+1}/{len(self.tdms_files)} | "
            f"{current_file} | "
            f"Chunk {self.chunk_idx+1}/{self.num_chunks} | "
            f"Pulses: {len(pulses)} | std≈{std:.4g}"
        )

        self.ax.text(
            0.01,
            0.99,
            current_fullpath,
            transform=self.ax.transAxes,
            fontsize=8,
            verticalalignment="top",
            bbox=dict(boxstyle="round", alpha=0.2)
        )

        self.fig.canvas.draw_idle()

    def on_prev_file(self, event):
        self.load_file(self.file_idx - 1)
        self.draw()

    def on_next_file(self, event):
        self.load_file(self.file_idx + 1)
        self.draw()

    def on_prev_chunk(self, event):
        self.chunk_idx = (self.chunk_idx - 1) % self.num_chunks
        self.on_clear(None)
        self.draw()

    def on_next_chunk(self, event):
        self.chunk_idx = (self.chunk_idx + 1) % self.num_chunks
        self.on_clear(None)
        self.draw()

    def on_select(self, xmin, xmax):
        self.sel_tmin = float(min(xmin, xmax))
        self.sel_tmax = float(max(xmin, xmax))

        if self.sel_patch is not None:
            try:
                self.sel_patch.remove()
            except Exception:
                pass

        self.sel_patch = self.ax.axvspan(self.sel_tmin, self.sel_tmax, alpha=0.2)
        self.fig.canvas.draw_idle()

    def on_clear(self, event):
        self.sel_tmin = None
        self.sel_tmax = None

        if self.sel_patch is not None:
            try:
                self.sel_patch.remove()
            except Exception:
                pass

            self.sel_patch = None

        self.fig.canvas.draw_idle()

    def on_save(self, event):
        if self.n == 0:
            print("[INFO] このファイルは読めていないので保存できません。")
            return

        if self.sel_tmin is None or self.sel_tmax is None:
            print("[INFO] 先にドラッグで保存範囲を選択してください。")
            return

        i0 = int(np.floor(self.sel_tmin / self.dt))
        i1 = int(np.ceil(self.sel_tmax / self.dt))

        i0 = max(0, min(i0, self.n - 1))
        i1 = max(0, min(i1, self.n))

        if i1 <= i0 + 1:
            print("[INFO] 選択範囲が短すぎます。")
            return

        clip_y = self.data[i0:i1]
        clip_t = np.arange(i0, i1) * self.dt

        base = self.current_base_name()
        tag = f"chunk{self.chunk_idx+1:04d}_t{self.sel_tmin:.3f}-{self.sel_tmax:.3f}_i{i0}-{i1}"

        outdir = self.out_dir()
        csv_path = os.path.join(outdir, f"{base}_{tag}.csv")
        npy_path = os.path.join(outdir, f"{base}_{tag}.npy")

        arr = np.column_stack([clip_t, clip_y])

        np.savetxt(csv_path, arr, delimiter=",", header="time_s,value", comments="")
        np.save(npy_path, clip_y)

        print(f"[SAVED] {csv_path}")
        print(f"[SAVED] {npy_path}")


# ========= main =========

if __name__ == "__main__":

    server = "QDserver"
    keyfolder = "analysis"
    ex = "Lipid"

    samples = ["DSPC"]

    anal_folders = []

    for sample in samples:

        tdms_folder = Path(
            rf"\\{server}\{keyfolder}\{ex}\{sample}\{sample}_10k_Sample\T\ANAL"
        )

        print("=" * 80)
        print("[INFO] TDMS Folder =", tdms_folder)
        print("[INFO] exists? =", tdms_folder.exists())
        print("[INFO] is_dir? =", tdms_folder.is_dir())

        if tdms_folder.exists() and tdms_folder.is_dir():

            tdms_files = sorted(tdms_folder.glob("*.tdms"))

            print("[INFO] TDMS files =", len(tdms_files))

            if tdms_files:
                anal_folders.append(str(tdms_folder))

                for p in tdms_files[:5]:
                    print("   ", p)

            else:
                print(f"[WARN] TDMSファイルが見つかりません: {tdms_folder}")

        else:
            print(f"[WARN] フォルダが存在しません: {tdms_folder}")

    if not anal_folders:
        raise RuntimeError("有効な ANAL フォルダが1つも見つかりませんでした。")

    print("=" * 80)
    print("[INFO] 使用する ANAL フォルダ数 =", len(anal_folders))

    for f in anal_folders:
        print("   ", f)

    browser = TdmsMultiFolderBrowser(
        anal_folders=anal_folders,
        dt=0.0001,
        chunk_sec=5.0,
        target_group="Data",
        target_channel="Ch1",
        out_root="clips_pick",
        recursive_tdms=False,
        sanitize_filename=False,
        save_subfolder_per_aa=True,
        threshold_k=6.0,
        min_width_ms=0.2,
    )

    plt.show()