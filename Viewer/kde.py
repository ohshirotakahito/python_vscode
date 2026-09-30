import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde
import matplotlib as mpl
from pathlib import Path

# ============================================================
# AIP / JCP Figure Style Settings
# ============================================================
ONE_COL_WIDTH = 3.37
TWO_COL_WIDTH = 6.69
MAX_HEIGHT = 8.25
DPI = 300

FONT_SIZE_LABEL = 10
FONT_SIZE_TICK = 8
FONT_SIZE_TITLE = 9

LINE_WIDTH = 1.0
TICK_WIDTH = 0.5
TICK_LENGTH = 3.0

dcolor = "#9ED543"

mpl.rcParams.update({
    "figure.dpi": DPI,
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": FONT_SIZE_LABEL,
    "axes.labelsize": FONT_SIZE_LABEL,
    "axes.titlesize": FONT_SIZE_TITLE,
    "axes.linewidth": 0.8,
    "xtick.labelsize": FONT_SIZE_TICK,
    "ytick.labelsize": FONT_SIZE_TICK,
    "xtick.major.width": TICK_WIDTH,
    "ytick.major.width": TICK_WIDTH,
    "xtick.major.size": TICK_LENGTH,
    "ytick.major.size": TICK_LENGTH,
    "xtick.minor.width": 0.4,
    "ytick.minor.width": 0.4,
    "xtick.minor.size": 1.5,
    "ytick.minor.size": 1.5,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "legend.fontsize": FONT_SIZE_LABEL,
    "legend.frameon": True,
    "legend.framealpha": 1.0,
    "lines.linewidth": LINE_WIDTH,
    "mathtext.fontset": "custom",
    "mathtext.rm": "Arial",
    "mathtext.it": "Arial:italic",
    "mathtext.bf": "Arial:bold",
    "savefig.dpi": DPI,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
})

# ============================================================
# Load data
# ============================================================
script_dir = Path(__file__).resolve().parent
csv_path = script_dir / "csv_data" / "TyrI_sample_data_signal_summary.csv"
df = pd.read_csv(csv_path)

x = df["signal_time"].to_numpy(dtype=float) / 10.0
y = df["signal_intensity"].to_numpy(dtype=float)

# ============================================================
# Summary statistics
# ============================================================
x_mean = np.mean(x)
y_mean = np.mean(y)

# ============================================================
# KDE calculations
# ============================================================
xy = np.vstack([x, y])
kde2d = gaussian_kde(xy)
z = kde2d(xy)


idx = z.argsort()
x_sorted, y_sorted, z_sorted = x[idx], y[idx], z[idx]

x_margin = max((x.max() - x.min()) * 0.08, 0.3)
y_margin = max((y.max() - y.min()) * 0.08, 0.3)

x_grid = np.linspace(x.min() - x_margin, x.max() + x_margin, 400)
y_grid = np.linspace(y.min() - y_margin, y.max() + y_margin, 400)

kde_x = gaussian_kde(x)
kde_y = gaussian_kde(y)

x_density = kde_x(x_grid)
y_density = kde_y(y_grid)

x_mode = x_grid[np.argmax(x_density)]
y_mode = y_grid[np.argmax(y_density)]

xx, yy = np.meshgrid(
    np.linspace(x.min() - x_margin, x.max() + x_margin, 200),
    np.linspace(y.min() - y_margin, y.max() + y_margin, 200)
)
zz = kde2d(np.vstack([xx.ravel(), yy.ravel()])).reshape(xx.shape)

# ============================================================
# KDE density -> enclosed probability (Highest Density Region)
# ============================================================
def kde_to_probability(z_grid, xx, yy, z_points):
    dx = xx[0, 1] - xx[0, 0]
    dy = yy[1, 0] - yy[0, 0]
    z_flat = z_grid.ravel()
    order = np.argsort(z_flat)[::-1]          # 密度の高い順
    cum_mass = np.cumsum(z_flat[order]) * dx * dy
    cum_mass /= cum_mass[-1]                   # 全体を1に正規化

    z_desc = z_flat[order]
    # np.interp は昇順が必要なので反転
    prob = np.interp(z_points, z_desc[::-1], cum_mass[::-1])
    return prob

prob_sorted = kde_to_probability(zz, xx, yy, z_sorted)

# ============================================================
# Figure layout
# ============================================================
fig = plt.figure(figsize=(TWO_COL_WIDTH*0.7, TWO_COL_WIDTH * 0.4))
gs = fig.add_gridspec(
    2, 2,
    width_ratios=(3, 1.2),
    height_ratios=(1.2, 3),
    hspace=0.05,
    wspace=0.05
)

ax_top   = fig.add_subplot(gs[0, 0])   # [0,0]
ax_main  = fig.add_subplot(gs[1, 0])   # [1,0]
ax_right = fig.add_subplot(gs[1, 1])   # [1,1]
ax_box   = fig.add_subplot(gs[0, 1])   # [0,1]

# ============================================================
# Main scatter + contour
# ============================================================
sc = ax_main.scatter(
    x_sorted,
    y_sorted,
    c=1-prob_sorted,
    s=10,
    marker="o",
    linewidths=0,
    rasterized=True,
    cmap="cividis"
)
sc.set_clim(0, 1)

ax_main.set_xlim(0, 54)
ax_main.set_ylim(0, 420)

ax_main.set_xlabel("Signal time / ms")
ax_main.set_ylabel("Signal intensity / pA")

# mean lines (solid)
ax_main.axvline(x_mean, color="black", linestyle="--", linewidth=0.8)
ax_main.axhline(y_mean, color="black", linestyle="--", linewidth=0.8)

ax_main.grid(which='major',color='lightgray',linestyle='--',linewidth=0.8)#Major Grid
ax_main.grid(which='minor',color='lightgray',linestyle='--',linewidth=0.8)#Minor Grid
ax_main.set_axisbelow(True)

# ============================================================
# Top KDE [0,0]
# ============================================================
ax_top.plot(x_grid, x_density, color=dcolor)
ax_top.fill_between(x_grid, x_density, 0, color=dcolor, alpha=0.3)

# mode and mean lines
ax_top.axvline(x_mean, color="black", linestyle="--", linewidth=0.8)
#ax_top.tick_params(labelbottom=False,labelleft=False,labelright=True,labeltop=False)
#ax_top.tick_params(bottom=False,left=False,right=True,top=False)

ax_top.set_xlim(ax_main.get_xlim())
ax_top.set_ylabel("Density")
ax_top.tick_params(axis="x", labelbottom=False)

y_top_max = ax_top.get_ylim()[1]

# mean label
ax_top.text(
    x_mean+7,
    y_top_max * 0.5,
    f"mean: {x_mean:.2f}",
    ha="center", va="top",
    fontsize=8.0,
    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.75)
)

# ============================================================
# Right KDE [1,1]
# ============================================================
ax_right.plot(y_density, y_grid, color=dcolor)
ax_right.fill_betweenx(y_grid, 0, y_density, color=dcolor, alpha=0.3)

# mode and mean lines
#ax_right.axhline(y_mode, color="black", linestyle="--", linewidth=0.8)
ax_right.axhline(y_mean, color="black", linestyle="--", linewidth=0.8)

ax_right.set_ylim(ax_main.get_ylim())
ax_right.set_xlabel("Density")
ax_right.tick_params(axis="y", labelleft=False)

x_right_max = ax_right.get_xlim()[1]

# mean label
ax_right.text(
    x_right_max * 0.8,
    y_mean+40,
    f"mean: {y_mean:.2f}",
    ha="right", va="center",
    fontsize=8.0,
    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.75)
)

# ============================================================
# Compact horizontal colorbar in [0,1]
# ============================================================
ax_box.set_axis_off()
cax = ax_box.inset_axes([0.08, 0.45, 0.84, 0.20])

cbar = fig.colorbar(sc, cax=cax, orientation="horizontal")
cbar.set_label("Enclosed probability", fontsize=8, labelpad=2)
cbar.ax.xaxis.set_ticks_position("bottom")
cbar.ax.xaxis.set_label_position("bottom")
cbar.ax.tick_params(labelsize=7, pad=1, length=2)

# ============================================================
# Clean up
# ============================================================
ax_top.spines["right"].set_visible(False)
ax_top.spines["top"].set_visible(False)
ax_right.spines["right"].set_visible(False)
ax_right.spines["top"].set_visible(False)

# ============================================================
# Save
# ============================================================
output_dir = script_dir / "output_pdf"
output_dir.mkdir(parents=True, exist_ok=True)
output_path = output_dir / "final_kde_TyrI_mode_mean_annotated.pdf"
fig.savefig(output_path)
plt.close(fig)
