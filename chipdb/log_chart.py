"""PM_ 50 ms ログのグラフ（Tk Canvas、標準ライブラリだけ）。上段: 電流（対数軸）、下段: 選んだ列（線形）。時間軸は共通。"""
import math
import tkinter as tk

from chipdb.axis import nice_ticks, tick_label
from shared import ui_theme as theme

CURRENT = 'K Current (uA)'
TIME = 'Time [s]'
TRACE = '#5b9dff'
LOWER = '#dfb86b'


def finite_pairs(xs, ys, positive=False):
    return [(x, y) for x, y in zip(xs, ys)
            if math.isfinite(x) and math.isfinite(y) and (y > 0 or not positive)]


def downsample(pairs, buckets):
    """点が多いとき、x を buckets 個に区切り、各区間の最小・最大だけを順に残す（形は保つ）。"""
    if len(pairs) <= buckets * 2 or buckets < 1:
        return list(pairs)
    x0, x1 = pairs[0][0], pairs[-1][0]
    width = (x1 - x0) / buckets or 1.
    out, current, group = [], None, []

    def flush():
        if group:
            low, high = min(group, key=lambda p: p[1]), max(group, key=lambda p: p[1])
            out.extend(sorted({low, high}, key=lambda p: p[0]))
    for pair in pairs:
        index = min(int((pair[0] - x0) / width), buckets - 1)
        if index != current:
            flush()
            current, group = index, []
        group.append(pair)
    flush()
    return out


def decade_ticks(low, high):
    """対数軸の目盛り（10 のべき）。low・high は正の値。"""
    start, end = math.floor(math.log10(low)), math.ceil(math.log10(high))
    if end == start:
        end += 1
    return start, end, list(range(start, end + 1))


class LogChart(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, background=theme.FIELD, highlightthickness=0, **kwargs)
        self.columns, self.title, self.lower = {}, '', ''
        self.mapping = None
        self.bind('<Configure>', lambda e: self.draw())
        self.bind('<Motion>', self.hover)
        self.bind('<Leave>', lambda e: self.delete('hover'))

    def show(self, title, columns, lower):
        self.title, self.columns, self.lower = title, columns, lower
        self.draw()

    def message(self, text):
        self.columns, self.title = {}, ''
        self.mapping = None
        self.delete('all')
        self.create_text(20, 20, anchor='nw', text=text, fill=theme.MUTED)

    def draw(self):
        self.delete('all')
        self.mapping = None
        xs = self.columns.get(TIME)
        if not xs:
            if self.title:
                self.create_text(20, 20, anchor='nw', text=f'{self.title}: 時間の列がありません', fill=theme.MUTED)
            return
        width, height = max(self.winfo_width(), 200), max(self.winfo_height(), 200)
        left, right, top, bottom = 80, width - 20, 46, height - 40
        gap = 30
        split = top + (bottom - top - gap) * 0.62
        boxes = (left, top, right, split), (left, split + gap, right, bottom)
        x_all = [x for x in xs if math.isfinite(x)]
        if not x_all:
            return
        xl, xh, xstep, xticks = nice_ticks(min(x_all), max(x_all), max_intervals=8)
        xf = lambda x: left + (x - xl) / (xh - xl) * (right - left)
        self.create_text(left, 6, anchor='nw', text=self.title, fill=theme.TEXT)
        buckets = max(50, int(right - left))
        upper = self.panel(boxes[0], xs, self.columns.get(CURRENT, []), xf, CURRENT, TRACE, log=True, buckets=buckets)
        lower = self.panel(boxes[1], xs, self.columns.get(self.lower, []), xf, self.lower, LOWER, log=False, buckets=buckets)
        for x in xticks:
            px = xf(x)
            for box in boxes:
                self.create_line(px, box[1], px, box[3], fill=theme.BORDER)
            self.create_text(px, bottom + 5, anchor='n', text=tick_label(x, xstep), fill=theme.MUTED)
        self.create_text(right, bottom + 22, anchor='ne', text=TIME, fill=theme.MUTED)
        for box in boxes:
            self.create_rectangle(*box, outline=theme.MUTED)
        self.mapping = (left, right, xl, xh, boxes, upper, lower)

    def panel(self, box, xs, ys, xf, label, colour, log, buckets):
        left, top, right, bottom = box
        self.create_text(left, top - 4, anchor='sw', text=label + ('（対数）' if log else ''), fill=colour)
        pairs = finite_pairs(xs, ys, positive=log)
        if not pairs:
            self.create_text(left + 10, top + 10, anchor='nw', text='値がありません', fill=theme.MUTED)
            return None
        values = [math.log10(y) if log else y for _, y in pairs]
        if log:
            yl, yh, ticks = decade_ticks(min(y for _, y in pairs), max(y for _, y in pairs))
            labels = [(t, f'1e{t}') for t in ticks]
        else:
            yl, yh, step, ticks = nice_ticks(min(values), max(values), max_intervals=4)
            labels = [(t, tick_label(t, step)) for t in ticks]
        yf = lambda v: bottom - (v - yl) / (yh - yl) * (bottom - top)
        for value, text in labels:
            py = yf(value)
            self.create_line(left, py, right, py, fill=theme.BORDER)
            self.create_text(left - 6, py, anchor='e', text=text, fill=theme.MUTED)
        points = downsample([(x, v) for (x, _), v in zip(pairs, values)], buckets)
        coords = [c for x, v in points for c in (xf(x), yf(v))]
        if len(coords) >= 4:
            self.create_line(*coords, fill=colour, width=1)
        else:
            self.create_oval(coords[0] - 2, coords[1] - 2, coords[0] + 2, coords[1] + 2, fill=colour, outline='')
        return pairs

    def hover(self, event):
        self.delete('hover')
        if not self.mapping:
            return
        left, right, xl, xh, boxes, upper, lower = self.mapping
        if not left <= event.x <= right:
            return
        t = xl + (event.x - left) / (right - left) * (xh - xl)
        self.create_line(event.x, boxes[0][1], event.x, boxes[1][3], fill=theme.MUTED, dash=(2, 3), tags='hover')
        parts = [f't = {t:.2f} s']
        for pairs, name in ((upper, CURRENT), (lower, self.lower)):
            if pairs:
                nearest = min(pairs, key=lambda p: abs(p[0] - t))
                parts.append(f'{name} = {nearest[1]:.6g}')
        anchor = 'ne' if event.x > (left + right) / 2 else 'nw'
        self.create_text(event.x + (-8 if anchor == 'ne' else 8), boxes[0][1] + 6, anchor=anchor,
                         text='\n'.join(parts), fill=theme.TEXT, tags='hover')
