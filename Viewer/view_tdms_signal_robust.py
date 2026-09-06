# -*- coding: utf-8 -*-
"""TDMS signal viewer using the extraction tool's robust pickup rules.

This module leaves ``view_tdms_signal.py`` unchanged and reuses its file
navigation and clip-saving UI.  Detection follows ``extract_extdms_data.py``:
a smoothed moving-percentile baseline, iterative MAD noise estimation, and
t1/t2 hysteresis thresholds.
"""

import os
import sys
import time
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection
from matplotlib.patches import Rectangle
from matplotlib.widgets import Button, CheckButtons, TextBox
from scipy.ndimage import find_objects, label, percentile_filter
from scipy.signal import savgol_filter

from view_tdms_signal import TdmsMultiFolderBrowser


def find_direct_tdms_files(folder):
    """Return TDMS files in *folder* without hiding filesystem errors."""
    folder = Path(folder)
    try:
        with os.scandir(folder) as entries:
            return sorted(
                Path(entry.path)
                for entry in entries
                if entry.is_file() and entry.name.lower().endswith(".tdms")
            )
    except PermissionError as exc:
        raise PermissionError(
            f"Access was denied to the TDMS folder: {folder}\n"
            "Open the network share in Windows Explorer and sign in, or ask "
            "the share administrator to grant this Windows account access."
        ) from exc
    except OSError as exc:
        raise OSError(
            f"The TDMS folder could not be accessed: {folder}\n"
            f"Windows reported: {exc}"
        ) from exc


def robust_baseline_and_std(y, n_iter=5, clip_k=4.0):
    """Return median and robust sigma after excluding pulse-like outliers."""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    if len(y) == 0:
        return 0.0, 0.0

    mask = np.ones(len(y), dtype=bool)
    median = float(np.median(y))
    sigma = float(np.std(y))

    for _ in range(n_iter):
        noise_samples = y[mask]
        if len(noise_samples) < 10:
            break
        median = float(np.median(noise_samples))
        mad = float(np.median(np.abs(noise_samples - median)))
        sigma = 1.4826 * mad if mad > 0 else float(np.std(noise_samples))
        if not np.isfinite(sigma) or sigma <= 0:
            sigma = 0.0
            break
        new_mask = np.abs(y - median) < clip_k * sigma
        if np.array_equal(new_mask, mask):
            break
        mask = new_mask

    return median, sigma


def detect_pulses_hysteresis(
    y,
    baseline_curve,
    noise,
    dt=0.0001,
    threshold_k1=1.0,
    threshold_k2=5.0,
    min_width_ms=0.2,
    max_pulses=8000,
):
    """Use t1 for pulse boundaries and require a t2 crossing for acceptance."""
    y = np.asarray(y, dtype=float)
    baseline_curve = np.asarray(baseline_curve, dtype=float)
    if y.shape != baseline_curve.shape:
        raise ValueError("baseline_curve must have the same shape as y")

    t1_curve = baseline_curve + threshold_k1 * noise
    t2_curve = baseline_curve + threshold_k2 * noise
    if len(y) == 0:
        return [], t1_curve, t2_curve

    # A collapsed noise estimate (a flat/saturated stretch, or NaNs) drops t1
    # onto the baseline.  Every wiggle then crosses it, ``label`` finds tens of
    # thousands of "pulses", and the per-pulse loop below plus the matplotlib
    # redraw make the window look permanently frozen.  Bail out early instead.
    if not np.isfinite(noise) or noise <= 0.0:
        print("[WARN] Noise estimate is zero/invalid for this view; "
              "pulse detection skipped.")
        return [], t1_curve, t2_curve

    labeled, n_labels = label(y > t1_curve)
    if n_labels > max_pulses:
        print(f"[WARN] {n_labels} threshold crossings exceed the safety cap "
              f"({max_pulses}); pulse detection skipped for this view. Widen "
              f"the baseline window or raise t1 if pulses are expected here.")
        return [], t1_curve, t2_curve

    pulses = []
    for obj in find_objects(labeled):
        if obj is None:
            continue
        a, b = obj[0].start, obj[0].stop
        start = a - 1 if a > 0 else a
        end = b

        if not np.any(y[a:b] > t2_curve[a:b]):
            continue
        width_ms = (end - start) * dt * 1000.0
        if width_ms < min_width_ms:
            continue

        segment = y[start:end]
        local_baseline = float(baseline_curve[start])
        pulses.append({
            "start_index": int(start),
            "end_index": int(end),
            "start_time_s": float(start * dt),
            "end_time_s": float(end * dt),
            "width_ms": float(width_ms),
            "peak": float(np.max(segment)),
            "mean": float(np.mean(segment)),
            "area": float(np.sum(segment - local_baseline) * dt),
            "baseline": local_baseline,
            "threshold_t1": float(t1_curve[start]),
            "threshold_t2": float(t2_curve[start]),
        })
    return pulses, t1_curve, t2_curve


# Upper bound for the "View window (s)" box.  Anything larger makes the
# per-Next baseline/percentile filtering and the line redraw slow enough that
# the Qt window stops responding.
MAX_VIEW_SEC = 30.0


def _downsample_indices(y, max_points=12000):
    """Return sorted sample indices that preserve local minima and maxima.

    Large views (an enlarged "View window (s)", or a long selection) would
    otherwise hand hundreds of thousands of vertices to the renderer on every
    redraw.  Keeping two points (segment min and max) per screen bucket bounds
    the vertex count while every spike still survives.  A default 1 s / 10 k
    chunk is below the cap and passes through untouched.
    """
    n = len(y)
    if n <= max_points:
        return np.arange(n)

    n_buckets = max(1, max_points // 2)
    bucket = n // n_buckets
    m = bucket * n_buckets
    block = np.asarray(y[:m]).reshape(n_buckets, bucket)
    base = np.arange(n_buckets) * bucket
    lo = base + block.argmin(axis=1)
    hi = base + block.argmax(axis=1)
    return np.unique(np.concatenate([lo, hi, (n - 1,)]))


class RobustTdmsMultiFolderBrowser(TdmsMultiFolderBrowser):
    """Viewer compatible with the baseline/noise rules of extract_extdms_data."""

    def __init__(
        self,
        *args,
        baseline_percentile=10.0,
        threshold_k1=1.0,
        threshold_k2=5.0,
        overview_buckets=800,
        overview_candidates=12,
        overview_activity_k=4.0,
        overview_min_activity=0.002,
        **kwargs,
    ):
        self.baseline_percentile = float(baseline_percentile)
        self.threshold_k1 = float(threshold_k1)
        self.threshold_k2 = float(threshold_k2)
        self._view_start_override = None
        # Persistent plot artists (built once, then updated in place). Redrawing
        # via ax.clear()+replot costs ~0.5 s per Next and is why the window
        # feels frozen.
        self._artists_ready = False
        self._sel_info = None
        self._legend_sig = None
        self._preview_fig = None

        # Manual axis ranges. ``None`` for an entry means "auto-fit that bound".
        # X bounds are absolute seconds and are cleared whenever the view moves
        # (Next/Prev, jump, window size); Y bounds persist so chunks can be
        # compared on a fixed scale. Set from the boxes or a toolbar zoom.
        self._xlim_user = [None, None]
        self._ylim_user = [None, None]
        self._setting_limits = False    # True while draw() sets limits itself
        self._suspend_axis_cb = False   # reserved re-entrancy guard
        # Values last written into the 4 range boxes (y0, y1, x0, x1).  The
        # boxes always show the *current* axis limits; a box left untouched
        # (its value still equals what we displayed) keeps its auto/pinned
        # mode, an edited box gets pinned to the new number.
        self._box_shown = [None, None, None, None]

        # Whole-file overview strip + "good region" candidate finder.
        self.overview_buckets = int(overview_buckets)
        self.overview_candidates = int(overview_candidates)
        self.overview_activity_k = float(overview_activity_k)
        self.overview_min_activity = float(overview_min_activity)
        self.ov_ax = None
        self._cand_starts = np.array([], dtype=np.int64)   # sorted by time
        self._cand_scores = np.array([], dtype=float)
        self._cand_activity = np.array([], dtype=float)
        self._cand_drift = np.array([], dtype=float)
        # The parent uses one threshold only to construct its existing UI.
        kwargs["threshold_k"] = self.threshold_k2
        kwargs.setdefault("chunk_sec", 1.0)
        super().__init__(*args, **kwargs)

        self.fig.text(0.02, 0.462, "View window (s)", ha="left", va="center")
        window_box_ax = plt.axes([0.02, 0.40, 0.13, 0.055])
        self.window_box = TextBox(
            window_box_ax,
            "",
            initial=f"{self.chunk_sec:g}",
        )
        self.window_box.on_submit(self.on_window_size_submit)

        self.fig.text(0.02, 0.372, "Selection start (s)", ha="left", va="center")
        selection_start_ax = plt.axes([0.02, 0.31, 0.13, 0.055])
        self.selection_start_box = TextBox(
            selection_start_ax,
            "",
            initial="0",
        )
        self.fig.text(0.02, 0.282, "Selection length (s)", ha="left", va="center")
        selection_duration_ax = plt.axes([0.02, 0.22, 0.13, 0.055])
        self.selection_duration_box = TextBox(
            selection_duration_ax,
            "",
            initial="1",
        )
        selection_apply_ax = plt.axes([0.02, 0.13, 0.13, 0.055])
        # Text editing itself is intentionally cheap.  Signal analysis and
        # redraw happen only when this button is pressed.
        self.selection_apply_button = Button(selection_apply_ax, "Update selection")
        self.selection_apply_button.on_clicked(self.on_timed_selection)

        # Navigation used to run from a GUI timer callback.  Some Tk/Windows
        # Matplotlib combinations can deadlock when a synchronous canvas draw
        # is started from that callback, leaving every widget unresponsive.
        self._navigation_busy = False

        self._status_artist = self.fig.text(
            0.985,
            0.965,
            "Ready",
            ha="right",
            va="top",
            fontsize=11,
            fontweight="bold",
            color="#1b5e20",
            bbox=dict(boxstyle="round", facecolor="#e8f5e9", alpha=0.95),
        )

        self._build_axis_controls()
        self._build_overview()
        self._compute_overview()
        self.draw()
        self.fig.canvas.draw_idle()

    # ------------------------------------------------------------------
    # Manual Y / X axis range controls
    # ------------------------------------------------------------------
    def _build_axis_controls(self):
        """Top-row boxes showing the plot's Y and X range, plus an Auto reset.

        Each box always displays the range actually in use (the auto-fit value
        while that bound is automatic), so a typed value can be compared with
        it.  Edit a box + Enter to pin that bound; "Auto axes" returns all
        four to automatic.  A toolbar pan/zoom is captured into the boxes too.
        """
        self.fig.text(0.185, 0.955, "Y min / max  (edit to pin)",
                      fontsize=8, va="center")
        self.ymin_box = TextBox(plt.axes([0.185, 0.925, 0.055, 0.028]), "",
                                initial="")
        self.ymax_box = TextBox(plt.axes([0.245, 0.925, 0.055, 0.028]), "",
                                initial="")
        self.fig.text(0.315, 0.955, "X min / max (s)", fontsize=8, va="center")
        self.xmin_box = TextBox(plt.axes([0.315, 0.925, 0.06, 0.028]), "",
                                initial="")
        self.xmax_box = TextBox(plt.axes([0.38, 0.925, 0.06, 0.028]), "",
                                initial="")
        for box in (self.ymin_box, self.ymax_box, self.xmin_box, self.xmax_box):
            box.on_submit(self.on_axis_range_submit)

        self.auto_axes_button = Button(
            plt.axes([0.45, 0.925, 0.07, 0.028]), "Auto axes"
        )
        self.auto_axes_button.on_clicked(self.on_auto_axes)

        self.ax.callbacks.connect("xlim_changed", self._on_ax_lim_changed)
        self.ax.callbacks.connect("ylim_changed", self._on_ax_lim_changed)

    @property
    def _axis_boxes(self):
        return (self.ymin_box, self.ymax_box, self.xmin_box, self.xmax_box)

    @staticmethod
    def _parse_axis_box(text):
        text = (text or "").strip()
        if text == "":
            return None
        try:
            value = float(text)
        except ValueError:
            return "bad"
        return value if np.isfinite(value) else "bad"

    def on_axis_range_submit(self, _text=None):
        parsed = [self._parse_axis_box(b.text) for b in self._axis_boxes]
        if "bad" in parsed:
            print("[WARN] Axis range values must be numbers.")
            self._refresh_axis_boxes()
            return

        # A box still showing the value we displayed keeps its current mode
        # (auto stays auto); a changed box is pinned; a cleared box goes auto.
        modes = list(self._ylim_user) + list(self._xlim_user)
        for i, (value, shown, mode) in enumerate(
            zip(parsed, self._box_shown, modes)
        ):
            if value is None:
                modes[i] = None
            elif shown is not None and abs(value - shown) <= 1e-9 + 1e-6 * abs(shown):
                pass  # unchanged -> keep auto/pinned as it was
            else:
                modes[i] = float(value)

        ylo, yhi, xlo, xhi = modes
        if ylo is not None and yhi is not None and yhi <= ylo:
            print("[WARN] Y max must be greater than Y min.")
            self._refresh_axis_boxes()
            return
        if xlo is not None and xhi is not None and xhi <= xlo:
            print("[WARN] X max must be greater than X min.")
            self._refresh_axis_boxes()
            return
        self._ylim_user = [ylo, yhi]
        self._xlim_user = [xlo, xhi]
        self.draw()

    def on_auto_axes(self, event):
        self._xlim_user = [None, None]
        self._ylim_user = [None, None]
        self.draw()

    def _reset_x_axis_user(self):
        """Called when the view moves - absolute X bounds no longer apply."""
        self._xlim_user = [None, None]

    def _refresh_axis_boxes(self):
        """Show the range currently in use in all four boxes.

        Uses ``text_disp.set_text`` (not ``TextBox.set_val``, which forces a
        nested ``canvas.draw()`` that is unsafe from an ``xlim_changed``
        callback).
        """
        if not hasattr(self, "ymin_box") or self.ax is None:
            return
        y0, y1 = self.ax.get_ylim()
        x0, x1 = self.ax.get_xlim()
        values = [float(y0), float(y1), float(x0), float(x1)]
        for box, value in zip(self._axis_boxes, values):
            try:
                box.text_disp.set_text(f"{value:.6g}")
            except Exception:
                pass
        self._box_shown = values
        try:
            self.fig.canvas.draw_idle()
        except Exception:
            pass

    def _on_ax_lim_changed(self, ax):
        # Fired by our own draw() (guarded) or by a toolbar pan/zoom (captured).
        if self._setting_limits or not self._artists_ready:
            return
        self._xlim_user = [float(ax.get_xlim()[0]), float(ax.get_xlim()[1])]
        self._ylim_user = [float(ax.get_ylim()[0]), float(ax.get_ylim()[1])]
        self._refresh_axis_boxes()

    # ------------------------------------------------------------------
    # Whole-file overview strip + candidate "good region" finder
    # ------------------------------------------------------------------
    def _build_overview(self):
        """Thin always-visible strip under the main plot: the whole file at a
        glance, the current view window, and green ticks on candidate regions
        (enough signal, little baseline drift).  Click it to jump there."""
        # Lift the main plot to free a band for the overview strip.
        self.fig.subplots_adjust(left=0.18, bottom=0.33)

        self.ov_ax = self.fig.add_axes([0.18, 0.18, 0.72, 0.07])
        self.ov_ax.set_yticks([])
        self.ov_ax.tick_params(labelsize=8)
        self.ov_ax.margins(x=0)
        self.ov_ax.set_title(
            "File overview - click to jump   (green = candidate: signal present "
            "& flat baseline    purple = current view)",
            fontsize=7.5, pad=3,
        )
        self._ov_here = self.ov_ax.plot(
            [], [], marker="v", markersize=9, color="#8e24aa",
            linestyle="None", clip_on=False,
            transform=self.ov_ax.get_xaxis_transform(),
        )[0]
        # A single filled silhouette (no outline lines) keeps this strip cheap
        # to re-rasterise on every main-plot redraw.
        self._ov_fill = None
        self._ov_thr = self.ov_ax.axhline(
            0.0, color="#ef6c00", linewidth=0.8, linestyle="--", alpha=0.7
        )
        self._ov_thr.set_visible(False)
        # Own Rectangle (not axvspan) so it moves the same way on every
        # matplotlib version: 3.8 axvspan -> Polygon, 3.10+ -> Rectangle.
        self._ov_window = self.ov_ax.add_patch(Rectangle(
            (0.0, 0.0), 0.0, 1.0,
            transform=self.ov_ax.get_xaxis_transform(),
            facecolor="#8e24aa", edgecolor="none", alpha=0.25, zorder=5,
        ))
        (self._ov_cand,) = self.ov_ax.plot(
            [], [], linestyle="None", marker="|", markersize=16,
            markeredgewidth=1.6, color="#2e7d32",
            transform=self.ov_ax.get_xaxis_transform(),
        )
        self.fig.canvas.mpl_connect("button_press_event", self._on_overview_click)

        prev_cand_ax = plt.axes([0.02, 0.055, 0.062, 0.05])
        next_cand_ax = plt.axes([0.088, 0.055, 0.062, 0.05])
        self.btn_prev_cand = Button(prev_cand_ax, "Cand -")
        self.btn_next_cand = Button(next_cand_ax, "Cand +")
        self.btn_prev_cand.on_clicked(self.on_prev_candidate)
        self.btn_next_cand.on_clicked(self.on_next_candidate)

    def _compute_overview(self):
        """Recompute the file silhouette and candidate windows (once per file,
        and whenever the view-window length changes)."""
        if self.ov_ax is None:
            return
        self._cand_starts = np.array([], dtype=np.int64)
        self._cand_scores = np.array([], dtype=float)
        self._cand_activity = np.array([], dtype=float)
        self._cand_drift = np.array([], dtype=float)

        if self.n == 0:
            if self._ov_fill is not None:
                try:
                    self._ov_fill.remove()
                except (ValueError, AttributeError):
                    pass
                self._ov_fill = None
            self._ov_cand.set_data([], [])
            self._ov_thr.set_visible(False)
            return

        data = np.asarray(self.data, dtype=float)
        total_s = self.n * self.dt

        # --- file silhouette (min/max envelope) ---
        n_buckets = int(min(self.overview_buckets, self.n))
        starts = np.linspace(0, self.n, n_buckets + 1, dtype=np.int64)[:-1]
        lo = np.minimum.reduceat(data, starts)
        hi = np.maximum.reduceat(data, starts)
        tc = (starts + np.diff(
            np.linspace(0, self.n, n_buckets + 1, dtype=np.int64)) / 2.0) * self.dt
        if self._ov_fill is not None:
            try:
                self._ov_fill.remove()
            except (ValueError, AttributeError):
                pass
        self._ov_fill = self.ov_ax.fill_between(
            tc, lo, hi, color="#90a4ae", linewidth=0
        )
        pad = 0.05 * (hi.max() - lo.min() or 1.0)
        self.ov_ax.set_xlim(0.0, total_s)
        self.ov_ax.set_ylim(lo.min() - pad, hi.max() + pad)

        # --- global robust stats -> "hot sample" threshold ---
        step = max(1, self.n // 200000)
        gmed, gsig = robust_baseline_and_std(data[::step])
        if not np.isfinite(gsig) or gsig <= 0:
            gsig = float(np.std(data)) or 1.0
        hot_thr = gmed + self.overview_activity_k * gsig
        self._ov_thr.set_ydata([hot_thr, hot_thr])
        self._ov_thr.set_visible(True)

        # --- per-window scores (window == current view length) ---
        win = max(1, int(self.chunk_size))
        n_win = self.n // win
        if n_win >= 1:
            w = data[: n_win * win].reshape(n_win, win)
            activity = (w > hot_thr).mean(axis=1)

            sub = 10
            wl = win // sub
            if wl >= 4:
                # 10th-percentile per sub-block tracks the baseline; decimate
                # the input first - drift is low-frequency, and this keeps the
                # per-file/per-resize recompute well under ~0.5 s.
                wb = w[:, : sub * wl].reshape(n_win, sub, wl)[:, :, ::4]
                p10 = np.percentile(wb, 10, axis=2)
                drift = p10.max(axis=1) - p10.min(axis=1)
            else:
                drift = w.max(axis=1) - w.min(axis=1)

            drift_ref = 6.0 * gsig
            drift_norm = drift / drift_ref if drift_ref > 0 else drift
            act = np.clip(activity, 0.0, 0.2) / 0.2
            score = np.where(
                activity >= self.overview_min_activity,
                act / (1.0 + drift_norm ** 2),
                0.0,
            )
            order = np.argsort(score)[::-1]
            order = order[score[order] > 0][: self.overview_candidates]
            if len(order):
                order_by_time = order[np.argsort(order)]
                self._cand_starts = (order_by_time * win).astype(np.int64)
                self._cand_scores = score[order_by_time]
                self._cand_activity = activity[order_by_time]
                self._cand_drift = drift[order_by_time]

        if len(self._cand_starts):
            centres = (self._cand_starts + win / 2.0) * self.dt
            self._ov_cand.set_data(centres, np.full(len(centres), 0.5))
            self._print_candidate_table(win)
        else:
            self._ov_cand.set_data([], [])
            print("[INFO] No candidate windows met the signal/flatness test "
                  "for this file (try lowering overview_activity_k).")

    def _print_candidate_table(self, win):
        rank = np.argsort(-self._cand_scores)
        print("=" * 72)
        print(f"[INFO] Candidate windows ({win * self.dt:g} s each), best first:")
        for r, k in enumerate(rank, 1):
            s = int(self._cand_starts[k])
            print(f"   #{r:2d}  t = {s * self.dt:8.2f} - "
                  f"{(s + win) * self.dt:8.2f} s   "
                  f"activity={self._cand_activity[k]:.3f}  "
                  f"baseline_drift={self._cand_drift[k]:.4g}  "
                  f"score={self._cand_scores[k]:.3f}")
        print("   Use 'Cand -' / 'Cand +' or click a green tick to jump.")
        print("=" * 72)

    def _update_overview_window(self):
        """Slide the purple current-view marker on the overview strip."""
        if self.ov_ax is None or self.n == 0:
            return
        start = (
            self._view_start_override
            if self._view_start_override is not None
            else self.chunk_idx * self.chunk_size
        )
        end = min(start + self.chunk_size, self.n)
        x0, x1 = start * self.dt, end * self.dt
        # axvspan() returns a Rectangle (matplotlib >= 3.x); move it with
        # set_bounds(x, y, w, h) - y/h are axes fraction via its transform.
        self._ov_window.set_bounds(x0, 0.0, max(x1 - x0, self.dt), 1.0)
        self._ov_here.set_data([0.5 * (x0 + x1)], [1.0])

    def _jump_to_sample(self, sample):
        """Move the main view so *sample* is in it, then redraw once."""
        if self._navigation_busy or self.n == 0:
            return
        self._navigation_busy = True
        try:
            self._view_start_override = None
            self.chunk_idx = int(
                min(max(sample, 0) // self.chunk_size, self.num_chunks - 1)
            )
            self.sel_tmin = None
            self.sel_tmax = None
            if self.sel_patch is not None:
                try:
                    self.sel_patch.remove()
                except (ValueError, AttributeError):
                    pass
                self.sel_patch = None
            if self._sel_info is not None:
                self._sel_info.set_visible(False)
            self._reset_x_axis_user()
            self.draw()
        finally:
            self._navigation_busy = False

    def _on_overview_click(self, event):
        if event.inaxes is not self.ov_ax or event.xdata is None:
            return
        self._jump_to_sample(int(round(event.xdata / self.dt)))
        print(f"[INFO] Jumped to t={event.xdata:.2f} s "
              f"(chunk {self.chunk_idx + 1}/{self.num_chunks}).")

    def on_prev_candidate(self, event):
        self._goto_candidate(-1)

    def on_next_candidate(self, event):
        self._goto_candidate(1)

    def _goto_candidate(self, direction):
        if len(self._cand_starts) == 0:
            print("[INFO] No candidate windows for this file.")
            return
        cur = self.chunk_idx * self.chunk_size
        starts = self._cand_starts
        if direction > 0:
            later = starts[starts > cur + 1]
            target = int(later[0]) if len(later) else int(starts[0])
        else:
            earlier = starts[starts < cur - 1]
            target = int(earlier[-1]) if len(earlier) else int(starts[-1])
        self._jump_to_sample(target)
        k = int(np.argmin(np.abs(self._cand_starts - target)))
        print(f"[INFO] Candidate at t={target * self.dt:.2f} s  "
              f"activity={self._cand_activity[k]:.3f}  "
              f"baseline_drift={self._cand_drift[k]:.4g}")

    def _set_processing_status(self, processing):
        """Show calculation state and render it before heavy processing."""
        if processing:
            self._status_artist.set_text("Calculating...")
            self._status_artist.set_color("#8a4b08")
            self._status_artist.set_bbox(
                dict(boxstyle="round", facecolor="#fff3cd", alpha=0.95)
            )
        else:
            self._status_artist.set_text("Ready")
            self._status_artist.set_color("#1b5e20")
            self._status_artist.set_bbox(
                dict(boxstyle="round", facecolor="#e8f5e9", alpha=0.95)
            )

        # Never force a synchronous draw from inside a widget callback.
        self.fig.canvas.draw_idle()

    def on_window_size_submit(self, text):
        """Apply a positive display-window length entered in seconds."""
        try:
            window_sec = float(text)
            if not np.isfinite(window_sec) or window_sec <= 0:
                raise ValueError
        except (TypeError, ValueError):
            print("[WARN] Window size must be a positive number (seconds).")
            self.window_box.set_val(f"{self.chunk_sec:g}")
            return

        max_window = MAX_VIEW_SEC
        if self.n > 0:
            max_window = min(max_window, self.n * self.dt)
        if window_sec > max_window:
            print(f"[WARN] Window capped at {max_window:g} s to keep the UI "
                  f"responsive (requested {window_sec:g} s).")
            window_sec = max_window

        current_start = (
            self._view_start_override
            if self._view_start_override is not None
            else self.chunk_idx * self.chunk_size
        )
        self.chunk_sec = window_sec
        self.chunk_size = max(1, int(round(window_sec / self.dt)))
        self.num_chunks = (
            max(1, int(np.ceil(self.n / self.chunk_size))) if self.n > 0 else 1
        )
        self.chunk_idx = min(current_start // self.chunk_size, self.num_chunks - 1)
        if self._view_start_override is not None:
            self._view_start_override = min(current_start, max(0, self.n - 1))
        self.on_clear(None)
        self._reset_x_axis_user()
        # Candidate windows are scored at the current view length.
        self._compute_overview()
        self.draw()

    def on_timed_selection(self, event):
        """Select a range from an absolute start time and a duration."""
        if self.n == 0:
            print("[WARN] No signal is loaded.")
            return

        try:
            start_sec = float(self.selection_start_box.text)
            duration_sec = float(self.selection_duration_box.text)
            if (
                not np.isfinite(start_sec)
                or not np.isfinite(duration_sec)
                or start_sec < 0
                or duration_sec <= 0
            ):
                raise ValueError
        except (TypeError, ValueError):
            print("[WARN] Start must be 0 or greater and duration must be positive.")
            return

        signal_end = self.n * self.dt
        if start_sec >= signal_end:
            print(
                f"[WARN] Start {start_sec:g} s is outside the signal "
                f"(end={signal_end:g} s)."
            )
            return

        end_sec = min(start_sec + duration_sec, signal_end)
        if end_sec < start_sec + duration_sec:
            print(f"[INFO] Selection was clipped at signal end ({signal_end:g} s).")

        # Keep the current view unchanged and update only the selection patch.
        # This also avoids recalculating the baseline and pulse detection.
        self.on_select(start_sec, end_sec)
        print(
            f"[INFO] Selection updated: start={start_sec:.9g} s, "
            f"duration={end_sec - start_sec:.9g} s"
        )

    def _build_axes_artists(self):
        """Create every persistent artist on ``self.ax`` exactly once.

        ``draw()`` then only pushes new data into these artists, which keeps a
        Next/Prev redraw at tens of milliseconds instead of ~0.5 s.
        """
        ax = self.ax
        (self._l_signal,) = ax.plot([], [], linewidth=1.0, label="Signal")
        (self._l_baseline,) = ax.plot(
            [], [], linewidth=2.0, label="Baseline (percentile)"
        )
        (self._l_t1,) = ax.plot(
            [], [], "--", linewidth=1.3,
            label=f"t1 (+{self.threshold_k1:g} sigma)",
        )
        (self._l_t2,) = ax.plot(
            [], [], linewidth=1.8, label=f"t2 (+{self.threshold_k2:g} sigma)"
        )

        self._pulse_polys = PolyCollection(
            [], facecolors="#f39c12", edgecolors="#d35400", alpha=0.18,
            linewidths=0.8, transform=ax.get_xaxis_transform(),
            label="Detected pulse",
        )
        ax.add_collection(self._pulse_polys)

        (self._peak_pts,) = ax.plot(
            [], [], "o", markersize=7, linestyle="None", zorder=4, label="Peak"
        )

        self._peak_texts = [
            ax.text(0, 0, "", fontsize=9, ha="center", va="bottom")
            for _ in range(30)
        ]
        for txt in self._peak_texts:
            txt.set_visible(False)
        self._peak_more = ax.text(
            0.01, 0.02, "", transform=ax.transAxes, fontsize=9,
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )
        self._peak_more.set_visible(False)

        self._center_msg = ax.text(
            0.5, 0.5, "", transform=ax.transAxes, ha="center", va="center",
        )
        self._center_msg.set_visible(False)
        self._path_text = ax.text(
            0.01, 0.99, "", transform=ax.transAxes, fontsize=8,
            verticalalignment="top", bbox=dict(boxstyle="round", alpha=0.2),
        )
        self._sel_info = ax.text(
            0.99, 0.02, "", transform=ax.transAxes, ha="right", va="bottom",
            fontsize=10, bbox=dict(boxstyle="round", facecolor="#fff3cd", alpha=0.9),
            zorder=10,
        )
        self._sel_info.set_visible(False)

        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Signal Amplitude")
        ax.grid(True, alpha=0.2)
        self._legend = ax.legend(loc="upper right")
        self._artists_ready = True

    def _refresh_legend(self, have_pulses, have_peaks):
        """Rebuild the legend only when the set of visible series changes."""
        sig = (
            self.show_signal, self.show_baseline, self.show_threshold,
            have_pulses, have_peaks,
        )
        if sig == self._legend_sig:
            return
        self._legend_sig = sig
        handles = [
            a for a in (
                self._l_signal, self._l_baseline, self._l_t1, self._l_t2,
                self._pulse_polys, self._peak_pts,
            )
            if a.get_visible()
        ]
        if handles:
            self._legend = self.ax.legend(handles=handles, loc="upper right")
        elif self._legend is not None:
            self._legend.set_visible(False)

    def _draw_selection_info(self):
        """Show the selected start, end, and duration inside the graph."""
        if self._sel_info is None:
            return
        if self.sel_tmin is None or self.sel_tmax is None:
            self._sel_info.set_visible(False)
            return

        duration = self.sel_tmax - self.sel_tmin
        self._sel_info.set_text(
            f"Selected: {duration:.6g} s\n"
            f"{self.sel_tmin:.6g} - {self.sel_tmax:.6g} s"
        )
        self._sel_info.set_visible(True)

    def on_select(self, xmin, xmax):
        super().on_select(xmin, xmax)

        # Manual selection: purple with a clear outline, distinct from the
        # orange pulse-detection regions drawn in draw().
        if self.sel_patch is not None:
            self.sel_patch.set_facecolor("#8e44ad")
            self.sel_patch.set_edgecolor("#5b2c6f")
            self.sel_patch.set_alpha(0.30)
            self.sel_patch.set_linewidth(2.0)
            self.sel_patch.set_hatch("//")

        # Keep cursor selection and numeric selection in sync.  The cursor's
        # left edge becomes the fixed start; only Duration needs editing when
        # the dragged range is not the desired exact length.
        duration = self.sel_tmax - self.sel_tmin
        self.selection_start_box.set_val(f"{self.sel_tmin:.9g}")
        if duration > 0:
            self.selection_duration_box.set_val(f"{duration:.9g}")

        self._draw_selection_info()
        self.fig.canvas.draw_idle()

    def on_clear(self, event):
        super().on_clear(event)
        if getattr(self, "_sel_info", None) is not None:
            self._sel_info.set_visible(False)
        self.fig.canvas.draw_idle()

    def on_save(self, event):
        """Open a preview of the export figure; nothing is written until the
        user presses *Save* in that preview window."""
        if self.n == 0 or self.sel_tmin is None or self.sel_tmax is None:
            # No selection -> keep the parent's "select a range first" message.
            super().on_save(event)
            return

        i0 = max(0, min(int(np.floor(self.sel_tmin / self.dt)), self.n - 1))
        i1 = max(0, min(int(np.ceil(self.sel_tmax / self.dt)), self.n))
        if i1 <= i0 + 1:
            super().on_save(event)
            return

        self._open_save_preview(i0, i1)

    # ------------------------------------------------------------------
    # Save preview (confirm before writing) + peak-value toggle
    # ------------------------------------------------------------------
    def _open_save_preview(self, i0, i1):
        """Second window showing exactly what the PNG will look like, with a
        'Show peak values' checkbox and Save / Cancel buttons."""
        if getattr(self, "_preview_fig", None) is not None:
            try:
                plt.close(self._preview_fig)
            except Exception:
                pass

        self._preview_i0 = int(i0)
        self._preview_i1 = int(i1)
        self._preview_sel = (self.sel_tmin, self.sel_tmax, self.chunk_idx)
        self._preview_show_pv = bool(self.show_peak_value)

        self._preview_fig = plt.figure(figsize=(12, 6.6))
        try:
            self._preview_fig.canvas.manager.set_window_title("Save preview")
        except Exception:
            pass
        self._preview_ax = self._preview_fig.add_axes([0.08, 0.28, 0.88, 0.62])

        pv_ax = self._preview_fig.add_axes([0.08, 0.05, 0.20, 0.13])
        self._preview_pv_check = CheckButtons(
            pv_ax, ["Show peak values"], [self._preview_show_pv]
        )
        self._preview_pv_check.on_clicked(self._on_preview_toggle)

        save_ax = self._preview_fig.add_axes([0.55, 0.06, 0.16, 0.10])
        cancel_ax = self._preview_fig.add_axes([0.74, 0.06, 0.16, 0.10])
        self._preview_save_btn = Button(save_ax, "Save\nCSV / NPY / PNG")
        self._preview_cancel_btn = Button(cancel_ax, "Cancel")
        self._preview_save_btn.on_clicked(self._on_preview_confirm)
        self._preview_cancel_btn.on_clicked(self._on_preview_cancel)

        self._preview_render()
        try:
            self._preview_fig.show()
        except Exception:
            pass
        self._preview_fig.canvas.draw_idle()

    def _on_preview_toggle(self, label):
        self._preview_show_pv = bool(self._preview_pv_check.get_status()[0])
        self._preview_render()
        self._preview_fig.canvas.draw_idle()

    def _on_preview_cancel(self, event):
        print("[INFO] Save cancelled - nothing was written.")
        try:
            plt.close(self._preview_fig)
        finally:
            self._preview_fig = None

    def _on_preview_confirm(self, event):
        i0, i1 = self._preview_i0, self._preview_i1
        # Remember the checkbox choice for the next preview / the live plot.
        self.show_peak_value = self._preview_show_pv
        # CSV + NPY (unchanged parent behaviour).
        super().on_save(event)
        # PNG with the chosen peak-value setting.
        self._write_selection_png(i0, i1, self._preview_show_pv)
        try:
            plt.close(self._preview_fig)
        finally:
            self._preview_fig = None

    def _preview_render(self):
        with plt.rc_context({
            "font.family": "Arial",
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.labelsize": 12,
        }):
            n_pulses = self._render_export(
                self._preview_ax, self._preview_i0, self._preview_i1,
                self._preview_show_pv,
            )
        self._preview_fig.suptitle(
            f"PREVIEW - not saved yet   |   peaks: {n_pulses}   |   "
            f"peak values: {'ON' if self._preview_show_pv else 'OFF'}",
            fontsize=12, fontweight="bold", color="#b26a00",
        )

    def _write_selection_png(self, i0, i1, show_peak_values):
        """Render the export figure to a PNG file."""
        base = self.current_base_name()
        tag = (
            f"chunk{self.chunk_idx + 1:04d}_"
            f"t{self.sel_tmin:.3f}-{self.sel_tmax:.3f}_i{i0}-{i1}"
        )
        png_path = os.path.join(self.out_dir(), f"{base}_{tag}.png")
        with plt.rc_context({
            "font.family": "Arial",
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.labelsize": 12,
        }):
            export_fig, export_ax = plt.subplots(figsize=(12, 5.5))
            self._render_export(export_ax, i0, i1, show_peak_values)
            export_fig.tight_layout()
            export_fig.savefig(png_path, dpi=200, bbox_inches="tight")
            plt.close(export_fig)
        print(f"[SAVED] {png_path}")

    def _render_export(self, ax, i0, i1, show_peak_values):
        """Draw the publication-style figure for [i0, i1) onto *ax*.

        Returns the number of detected pulses.  Peak-value annotations are
        drawn only when *show_peak_values* is true; peak position markers are
        always drawn.
        """
        ax.clear()
        y = np.asarray(self.data[i0:i1], dtype=float)
        t = np.arange(i0, i1) * self.dt
        baseline, _, _, noise = self._compute_baseline_threshold(y, i0)
        pulses, t1, t2 = detect_pulses_hysteresis(
            y, baseline, noise, dt=self.dt,
            threshold_k1=self.threshold_k1,
            threshold_k2=self.threshold_k2,
            min_width_ms=self.min_width_ms,
        )

        ax.plot(t, y, color="#1565c0", linewidth=1.0, label="Signal")
        ax.plot(t, baseline, color="#2e7d32", linewidth=1.5, label="Baseline")
        ax.plot(t, t1, color="#ef6c00", linestyle="--", linewidth=1.0,
                label=f"Threshold t1 (+{self.threshold_k1:g} sigma)")
        ax.plot(t, t2, color="#c62828", linestyle="--", linewidth=1.0,
                label=f"Threshold t2 (+{self.threshold_k2:g} sigma)")

        for pulse_number, pulse in enumerate(pulses, start=1):
            start_index = pulse["start_index"]
            end_index = pulse["end_index"]
            segment = y[start_index:end_index]
            if len(segment) == 0:
                continue
            peak_index = start_index + int(np.argmax(segment))
            peak_time = float(t[peak_index])
            peak_value = float(y[peak_index])

            ax.axvspan(
                t[start_index], t[min(end_index - 1, len(t) - 1)],
                color="#ffb300", alpha=0.13,
                label="Detected pulse" if pulse_number == 1 else None,
            )
            ax.axvline(peak_time, color="#d32f2f", linewidth=0.8, alpha=0.65)
            ax.plot(
                peak_time, peak_value, marker="X", color="#d32f2f",
                markersize=9, label="Peak" if pulse_number == 1 else None,
            )
            if show_peak_values:
                ax.annotate(
                    f"Peak {pulse_number}\nt={peak_time:.6g} s\n"
                    f"y={peak_value:.6g}",
                    xy=(peak_time, peak_value),
                    xytext=(6, 12 + 18 * ((pulse_number - 1) % 2)),
                    textcoords="offset points",
                    fontsize=9,
                    color="#8e0000",
                    arrowprops=dict(arrowstyle="->", color="#d32f2f", lw=0.8),
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.8),
                )

        ax.set_title(f"Selected TDMS signal | {self.current_base_name()} | "
                     f"Peaks: {len(pulses)}")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Signal amplitude")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="best")

        # Carry the main plot's manual axis range into the exported figure so
        # "what you see is what you save".
        if self._xlim_user[0] is not None or self._xlim_user[1] is not None:
            x0 = self._xlim_user[0] if self._xlim_user[0] is not None else float(t[0])
            x1 = self._xlim_user[1] if self._xlim_user[1] is not None else float(t[-1])
            if x1 > x0:
                ax.set_xlim(x0, x1)
        if self._ylim_user[0] is not None or self._ylim_user[1] is not None:
            cur = ax.get_ylim()
            y0 = self._ylim_user[0] if self._ylim_user[0] is not None else cur[0]
            y1 = self._ylim_user[1] if self._ylim_user[1] is not None else cur[1]
            if y1 > y0:
                ax.set_ylim(y0, y1)
        return len(pulses)

    def _compute_baseline_threshold(self, y, global_start):
        """Calculate with surrounding samples, then crop back to the view."""
        y = np.asarray(y, dtype=float)
        if len(y) == 0:
            empty = np.asarray([], dtype=float)
            return empty, empty, empty, 0.0

        window = max(3, int(self.baseline_window))
        if window % 2 == 0:
            window += 1
        context = max(window // 2 + max(self.sg_window, 1), self.pad_for_std)
        ext_start = max(0, global_start - context)
        ext_end = min(self.n, global_start + len(y) + context)
        extended = np.asarray(self.data[ext_start:ext_end], dtype=float)

        sg_window = int(self.sg_window)
        if sg_window % 2 == 0:
            sg_window += 1
        if sg_window >= 3 and len(extended) >= sg_window and self.sg_poly < sg_window:
            filtered = savgol_filter(
                extended, window_length=sg_window,
                polyorder=self.sg_poly, mode="nearest",
            )
        else:
            filtered = extended.copy()

        if len(filtered) >= window:
            baseline_ext = percentile_filter(
                filtered,
                percentile=self.baseline_percentile,
                size=window,
                mode="reflect",
            )
        else:
            baseline_ext = np.full_like(
                filtered, np.percentile(filtered, self.baseline_percentile)
            )

        offset = global_start - ext_start
        baseline = baseline_ext[offset:offset + len(y)]
        _, noise = robust_baseline_and_std(extended)
        t1 = baseline + self.threshold_k1 * noise
        t2 = baseline + self.threshold_k2 * noise
        return baseline, t1, t2, noise

    def draw(self):
        if not self._artists_ready:
            self._build_axes_artists()
        ax = self.ax

        if not self.tdms_files or self.n == 0:
            message = (
                f"No TDMS found in:\n{self.current_anal_folder()}"
                if not self.tdms_files
                else f"Failed to read:\n{self.current_tdms_path()}"
            )
            for line in (self._l_signal, self._l_baseline, self._l_t1,
                         self._l_t2, self._peak_pts):
                line.set_data([], [])
                line.set_visible(False)
            self._pulse_polys.set_verts([])
            self._pulse_polys.set_visible(False)
            for txt in self._peak_texts:
                txt.set_visible(False)
            self._peak_more.set_visible(False)
            self._sel_info.set_visible(False)
            self._center_msg.set_text(message)
            self._center_msg.set_visible(True)
            ax.set_title("")
            self.fig.canvas.draw_idle()
            return

        self._center_msg.set_visible(False)

        start = (
            self._view_start_override
            if self._view_start_override is not None
            else self.chunk_idx * self.chunk_size
        )
        end = min(start + self.chunk_size, self.n)
        y = np.asarray(self.data[start:end], dtype=float)
        t = np.arange(start, end) * self.dt
        baseline, t1, t2, noise = self._compute_baseline_threshold(y, start)
        pulses, t1, t2 = detect_pulses_hysteresis(
            y, baseline, noise, dt=self.dt,
            threshold_k1=self.threshold_k1,
            threshold_k2=self.threshold_k2,
            min_width_ms=self.min_width_ms,
        )

        pidx = _downsample_indices(y)
        if len(y) > 50000 and len(pidx) < len(y):
            print(f"[INFO] View has {len(y)} samples; plotting a "
                  f"{len(pidx)}-point envelope to keep the UI responsive.")
        tp = t[pidx]
        yp = y[pidx]

        self._l_signal.set_data(tp, yp)
        self._l_signal.set_visible(self.show_signal)
        self._l_baseline.set_data(tp, baseline[pidx])
        self._l_baseline.set_visible(self.show_baseline)
        self._l_t1.set_data(tp, t1[pidx])
        self._l_t1.set_visible(self.show_threshold)
        self._l_t2.set_data(tp, t2[pidx])
        self._l_t2.set_visible(self.show_threshold)

        # Detected pulses: one batched PolyCollection + one marker line.
        verts = []
        peak_indices = []
        if self.show_pulse and pulses:
            for pulse in pulses:
                a = pulse["start_index"]
                b = pulse["end_index"]
                if b <= a:
                    continue
                x0 = t[a]
                x1 = t[min(b - 1, len(t) - 1)]
                verts.append([(x0, 0.0), (x0, 1.0), (x1, 1.0), (x1, 0.0)])
                segment = y[a:b]
                if len(segment):
                    peak_indices.append(a + int(np.argmax(segment)))
        self._pulse_polys.set_verts(verts)
        self._pulse_polys.set_visible(bool(verts))

        peak_indices = np.asarray(peak_indices, dtype=int)
        if len(peak_indices):
            self._peak_pts.set_data(t[peak_indices], y[peak_indices])
            self._peak_pts.set_visible(True)
        else:
            self._peak_pts.set_data([], [])
            self._peak_pts.set_visible(False)

        shown = 0
        if self.show_pulse and self.show_peak_value and len(peak_indices):
            relative_peaks = y[peak_indices] - baseline[peak_indices]
            order = np.argsort(relative_peaks)[::-1][:len(self._peak_texts)]
            for slot, pos in enumerate(order):
                peak_index = int(peak_indices[pos])
                txt = self._peak_texts[slot]
                txt.set_position((t[peak_index], y[peak_index]))
                txt.set_text(
                    f"{y[peak_index]:.2f}\n+{relative_peaks[pos]:.2f}"
                )
                txt.set_visible(True)
                shown += 1
            if len(peak_indices) > len(self._peak_texts):
                self._peak_more.set_text(
                    f"Peak labels: strongest {len(self._peak_texts)}"
                    f"/{len(peak_indices)}"
                )
                self._peak_more.set_visible(True)
            else:
                self._peak_more.set_visible(False)
        else:
            self._peak_more.set_visible(False)
        for txt in self._peak_texts[shown:]:
            txt.set_visible(False)

        # ----- Axis limits: manual boxes win, blank bounds auto-fit -----
        auto_x0 = float(tp[0]) if len(tp) else 0.0
        auto_x1 = float(tp[-1]) if len(tp) else 1.0
        x0 = self._xlim_user[0] if self._xlim_user[0] is not None else auto_x0
        x1 = self._xlim_user[1] if self._xlim_user[1] is not None else auto_x1

        lo = float(np.nanmin(yp))
        hi = float(np.nanmax(yp))
        if self.show_baseline:
            lo = min(lo, float(np.nanmin(baseline[pidx])))
        if self.show_threshold:
            hi = max(hi, float(np.nanmax(t2[pidx])))
        if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
            lo, hi = 0.0, 1.0
        pad = 0.05 * (hi - lo)
        auto_y0, auto_y1 = lo - pad, hi + pad
        y_manual = (self._ylim_user[0] is not None
                    or self._ylim_user[1] is not None)
        y0 = self._ylim_user[0] if self._ylim_user[0] is not None else auto_y0
        y1 = self._ylim_user[1] if self._ylim_user[1] is not None else auto_y1

        self._setting_limits = True
        try:
            if x1 > x0:
                ax.set_xlim(x0, x1)
            if y_manual:
                if y1 > y0:
                    ax.set_ylim(y0, y1)
            else:
                # Fully-auto Y: only move the axis when the data would clip or
                # the view is far too zoomed out.  A stable axis skips a tick
                # recompute on most Next presses and eases chunk comparison.
                cur_lo, cur_hi = ax.get_ylim()
                span_now, span_need = cur_hi - cur_lo, y1 - y0
                if not (cur_lo <= y0 and cur_hi >= y1
                        and span_now < 2.5 * span_need):
                    ax.set_ylim(y0, y1)
        finally:
            self._setting_limits = False

        self._refresh_legend(bool(verts), bool(len(peak_indices)))

        path = self.current_tdms_path()
        count = self.tdms_counts.get(path)
        count_label = f"count={count} | " if count is not None else ""
        ax.set_title(
            f"[{self.current_aa_id()}] "
            f"File {self.file_idx + 1}/{len(self.tdms_files)} | "
            f"{os.path.basename(path)} | {count_label}"
            f"Chunk {self.chunk_idx + 1}/{self.num_chunks} | "
            f"Pulses: {len(pulses)} | robust sigma={noise:.4g}"
        )
        if self._path_text.get_text() != path:
            self._path_text.set_text(path)

        self._draw_selection_info()
        self._update_overview_window()
        self._refresh_axis_boxes()
        self.fig.canvas.draw_idle()

    def load_file(self, idx):
        self._view_start_override = None
        self._reset_x_axis_user()
        super().load_file(idx)
        # Rebuild the overview + candidate list for the file just loaded
        # (skipped during the very first load from the parent constructor,
        # when the overview axes do not exist yet).
        if self.ov_ax is not None:
            self._compute_overview()

    def on_prev_chunk(self, event):
        self._navigate_chunks(-1)

    def on_next_chunk(self, event):
        self._navigate_chunks(1)

    def _navigate_chunks(self, steps):
        """Move between chunks with one non-reentrant GUI redraw."""
        if self._navigation_busy or steps == 0 or self.num_chunks <= 0:
            return

        # Re-entrancy guard: a Next/Prev click that arrives mid-redraw is
        # dropped here instead of queueing another full redraw behind this one.
        self._navigation_busy = True

        started = time.perf_counter()
        failed = False
        try:
            if self._view_start_override is not None:
                start = self._view_start_override + steps * self.chunk_size
                self._view_start_override = start % max(self.n, 1)
                self.chunk_idx = min(
                    self._view_start_override // self.chunk_size,
                    self.num_chunks - 1,
                )
            else:
                self.chunk_idx = (self.chunk_idx + steps) % self.num_chunks

            # Clear the selection without scheduling the two intermediate
            # redraws performed by on_clear(); draw() below paints final state.
            self.sel_tmin = None
            self.sel_tmax = None
            if self.sel_patch is not None:
                try:
                    self.sel_patch.remove()
                except (ValueError, AttributeError):
                    pass
                self.sel_patch = None
            if self._sel_info is not None:
                self._sel_info.set_visible(False)
            self._reset_x_axis_user()
            self.draw()
        except Exception:
            failed = True
            traceback.print_exc()
        finally:
            self._navigation_busy = False
            elapsed = time.perf_counter() - started
            if elapsed > 0.5:
                print(f"[INFO] Next/Prev redraw took {elapsed:.2f} s "
                      f"(chunk {self.chunk_idx + 1}/{self.num_chunks}, "
                      f"{self.chunk_size} samples).")
            if failed:
                self._status_artist.set_text("Error - see console")
                self._status_artist.set_color("#b71c1c")
                self._status_artist.set_bbox(
                    dict(boxstyle="round", facecolor="#ffcdd2", alpha=0.95)
                )
                self.fig.canvas.draw_idle()
            else:
                self._set_processing_status(False)


def _run_local(anal_dirs):
    """Open the viewer straight against local ANAL folders (no network share).

    Set ``TDMS_ANAL_DIRS`` to one or more folders (``;`` separated) that each
    hold ``.tdms`` files directly.  Count-based sorting is skipped, so no
    ``count_data`` CSV or ``view_analtdms_count`` run is needed.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folders = []
    for raw in anal_dirs:
        folder = Path(raw.strip().strip('"'))
        if not folder.is_dir():
            print(f"[WARN] Not a folder, skipped: {folder}")
            continue
        if not find_direct_tdms_files(folder):
            print(f"[WARN] No .tdms directly in: {folder}")
            continue
        folders.append(str(folder))
        print(f"[INFO] Local ANAL folder: {folder}")

    if not folders:
        raise RuntimeError(
            "TDMS_ANAL_DIRS was set but no listed folder contained .tdms files."
        )

    RobustTdmsMultiFolderBrowser(
        anal_folders=folders,
        anal_folder_samples=None,
        script_dir=script_dir,
        count_folder_limit=1,
        dt=0.0001,
        chunk_sec=1.0,
        target_group="Data",
        target_channel="Ch1",
        out_root=os.path.join(script_dir, "clips_pick"),
        recursive_tdms=False,
        sanitize_filename=False,
        save_subfolder_per_aa=True,
        baseline_window=501,
        baseline_percentile=10.0,
        sg_window=51,
        sg_poly=3,
        threshold_k1=1.0,
        threshold_k2=5.0,
        pad_for_std=500,
        min_width_ms=0.2,
    )
    plt.show()


def main():
    local_dirs = os.environ.get("TDMS_ANAL_DIRS", "").strip()
    if local_dirs:
        _run_local(local_dirs.split(";"))
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    server = "Rackstation"
    keyfolder = "analysis"
    ex = "Sakano_02"
    samples = ["vasopressin"]

    anal_folders = []
    anal_folder_samples = []
    for sample in samples:
        folder = Path(
            rf"\\{server}\{keyfolder}\{ex}\{sample}\{sample}_10k_Sample\T\ANAL"
        )
        print("=" * 80)
        print("[INFO] TDMS Folder =", folder)
        try:
            tdms_files = find_direct_tdms_files(folder)
        except (PermissionError, OSError) as exc:
            print(f"[ERROR] {exc}")
            continue

        if tdms_files:
            anal_folders.append(str(folder))
            anal_folder_samples.append(sample)
        else:
            print(f"[WARN] No files ending in .tdms were found directly in: {folder}")

    if not anal_folders:
        raise RuntimeError(
            "No accessible ANAL folder containing TDMS files was found. "
            "Review the [ERROR]/[WARN] message above."
        )

    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import view_analtdms_count as count_module
    for sample in anal_folder_samples:
        csv_path = os.path.join(script_dir, "count_data", f"{sample}_sc.csv")
        if not os.path.isfile(csv_path):
            count_module.run_count_for_sample(
                server, keyfolder, ex, sample,
                script_dir=script_dir, folder_limit=1,
            )

    RobustTdmsMultiFolderBrowser(
        anal_folders=anal_folders,
        anal_folder_samples=anal_folder_samples,
        script_dir=script_dir,
        server=server,
        keyfolder=keyfolder,
        ex=ex,
        count_folder_limit=1,
        dt=0.0001,
        chunk_sec=1.0,
        target_group="Data",
        target_channel="Ch1",
        out_root=os.path.join(script_dir, "clips_pick"),
        recursive_tdms=False,
        sanitize_filename=False,
        save_subfolder_per_aa=True,
        baseline_window=501,
        baseline_percentile=10.0,
        sg_window=51,
        sg_poly=3,
        threshold_k1=1.0,
        threshold_k2=5.0,
        pad_for_std=500,
        min_width_ms=0.2,
    )
    plt.show()


if __name__ == "__main__":
    main()
