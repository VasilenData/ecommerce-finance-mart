"""README charts, drawn from the marts in the project palette."""
from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

PAPER, SURFACE, INK, MUTED, RULE, MINT, BLUE, AMBER = "#111315", "#181B1E", "#ECEFF1", "#A3ACB3", "#2B3035", "#7EE0B8", "#8FB4FF", "#F0B86E"


def _style(ax):
    ax.set_facecolor(PAPER)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(RULE)
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(axis="y", color=RULE, linewidth=0.8)
    ax.set_axisbelow(True)


def _fig(w=11, h=4.2):
    fig, ax = plt.subplots(figsize=(w, h), dpi=150)
    fig.patch.set_facecolor(PAPER)
    _style(ax)
    return fig, ax


def draw_all(con: duckdb.DuckDBPyConnection, out_dir: str | Path) -> list[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    m = con.execute("select month, net_product_revenue, return_rate, return_rate_ex_reversals from mart_monthly order by month").fetchall()
    months = [r[0] for r in m]
    net = [float(r[1]) for r in m]

    # 1. revenue by month (last month is partial)
    fig, ax = _fig()
    ax.plot(months[:-1], [v / 1e6 for v in net[:-1]], color=MINT, linewidth=2.4, marker="o", markersize=4)
    ax.plot(months[-2:], [v / 1e6 for v in net[-2:]], color=AMBER, linewidth=2.2, linestyle="--", marker="o", markersize=4)
    ax.set_ylabel("Net product revenue, GBP millions", color=MUTED, fontsize=10)
    ax.set_title("Net revenue by month. Dec 2011 (dashed) has only 9 days.", color=INK, fontsize=12, loc="left", pad=12)
    fig.tight_layout()
    fig.savefig(out / "revenue.png", facecolor=PAPER)
    plt.close(fig)

    # 2. return rate as reported vs excluding same-hour reversals
    fig, ax = _fig()
    ax.plot(months, [float(r[2]) * 100 for r in m], color=AMBER, linewidth=2.2, label="As reported")
    ax.plot(months, [float(r[3]) * 100 for r in m], color=MINT, linewidth=2.4, label="Excluding same-hour reversals")
    ax.set_ylabel("Returns as % of gross sales", color=MUTED, fontsize=10)
    ax.set_title("Two order-entry reversals made returns look like 14% and 28% of sales", color=INK, fontsize=12, loc="left", pad=12)
    leg = ax.legend(frameon=False, labelcolor=INK, fontsize=10, loc="upper left")
    leg.get_frame().set_facecolor(PAPER)
    fig.tight_layout()
    fig.savefig(out / "return_rate.png", facecolor=PAPER)
    plt.close(fig)

    # 3. cohort retention heatmap
    rows = con.execute("select cohort_month, month_index, retention from mart_cohort where cohort_month > '2009-12-01' order by 1, 2").fetchall()
    cohorts = sorted({r[0] for r in rows})
    width = max(r[1] for r in rows) + 1
    grid = [[float("nan")] * width for _ in cohorts]
    for c, i, ret in rows:
        grid[cohorts.index(c)][i] = ret * 100
    cmap = LinearSegmentedColormap.from_list("mint", [SURFACE, "#2E6B58", MINT])
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=150)
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)
    im = ax.imshow(grid, aspect="auto", cmap=cmap, vmin=0, vmax=40)
    ax.set_yticks(range(len(cohorts)))
    ax.set_yticklabels([c.strftime("%b %Y") for c in cohorts], fontsize=8, color=MUTED)
    ax.set_xticks(range(0, width, 2))
    ax.set_xticklabels(range(0, width, 2), color=MUTED, fontsize=9)
    ax.set_xlabel("Months since first order", color=MUTED, fontsize=10)
    ax.set_title("Share of each monthly cohort still buying (Dec 2009 omitted: it mixes old and new customers)", color=INK, fontsize=12, loc="left", pad=12)
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, pad=0.01)
    cb.set_label("% of cohort active", color=MUTED)
    cb.ax.tick_params(colors=MUTED)
    fig.tight_layout()
    fig.savefig(out / "cohorts.png", facecolor=PAPER)
    plt.close(fig)
    return ["revenue.png", "return_rate.png", "cohorts.png"]
