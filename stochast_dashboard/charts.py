from __future__ import annotations

import io
import threading
from collections.abc import Callable, Sequence

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator, PercentFormatter
from stochast.records import RunRecord
from stochast.stats import analyze_scenario, percentiles, wilson_interval

SURFACE = "#ffffff"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = "#2a78d6"

FONT_SIZE = 9
WIDTH_IN = 4.6
HAIRLINE = 0.75
MAX_BARS = 8
LABEL_LIMIT = 56
CHAR_WIDTH = 0.011
PERCENTILE_LINE_TOP = 0.74
LABEL_BOX = {"facecolor": SURFACE, "edgecolor": "none", "pad": 1.5}

_render_lock = threading.Lock()


# Creates a figure with the dashboard's chart chrome: a recessive hairline
# grid, no top/right spines, and muted tick labels.
def _new_axes(height_in: float) -> tuple[Figure, Axes]:
    figure = Figure(figsize=(WIDTH_IN, height_in), facecolor=SURFACE, layout="constrained")
    axes = figure.add_subplot()
    axes.set_facecolor(SURFACE)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(AXIS)
        axes.spines[side].set_linewidth(HAIRLINE)
    axes.tick_params(colors=INK_MUTED, labelsize=FONT_SIZE, length=0, pad=4)
    axes.grid(color=GRID, linewidth=HAIRLINE)
    axes.set_axisbelow(True)
    return figure, axes


def _axis_label(axes: Axes, *, x: str = "", y: str = "") -> None:
    if x:
        axes.set_xlabel(x, color=INK_MUTED, fontsize=FONT_SIZE)
    if y:
        axes.set_ylabel(y, color=INK_MUTED, fontsize=FONT_SIZE)


def _to_svg(figure: Figure) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="svg", facecolor=SURFACE, metadata={"Date": None})
    return buffer.getvalue()


def _truncate(text: str, limit: int = LABEL_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_ms(value: float) -> str:
    return f"{value:,.0f} ms"


def _format_usd(value: float) -> str:
    return f"${value:,.4f}"


# Plots the cumulative pass rate in completion order with its 95% Wilson
# interval as a band, so you can watch the estimate tighten as runs land.
def _pass_rate_trend(records: Sequence[RunRecord]) -> bytes:
    xs: list[int] = []
    rates: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    passed = 0
    for n, record in enumerate(records, start=1):
        passed += int(record.passed)
        low, high = wilson_interval(passed, n)
        xs.append(n)
        rates.append(passed / n)
        lows.append(low)
        highs.append(high)

    figure, axes = _new_axes(2.6)
    axes.grid(axis="x", visible=False)
    axes.fill_between(xs, lows, highs, color=SERIES, alpha=0.1, linewidth=0)
    axes.plot(xs, rates, color=SERIES, linewidth=1.5, solid_joinstyle="round")
    axes.plot(
        [xs[-1]],
        [rates[-1]],
        marker="o",
        markersize=6,
        color=SERIES,
        markeredgecolor=SURFACE,
        markeredgewidth=1.5,
        clip_on=False,
    )
    if len(xs) >= 10:
        below = rates[-1] > 0.85
        axes.annotate(
            f"{rates[-1]:.0%} ({passed:,}/{len(xs):,})",
            (xs[-1], rates[-1]),
            xytext=(-8, -14 if below else 8),
            textcoords="offset points",
            ha="right",
            color=INK_PRIMARY,
            fontsize=FONT_SIZE,
        )

    axes.set_xlim(0.5, len(xs) + 0.5)
    axes.set_ylim(0, 1.04)
    axes.set_yticks([0, 0.25, 0.5, 0.75, 1])
    axes.yaxis.set_major_formatter(PercentFormatter(1.0))
    axes.xaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    _axis_label(axes, x="Runs completed")
    return _to_svg(figure)


# Plots the distribution of a per-run measure with p50/p95/p99 marked, since
# latency and cost are right-skewed and a mean would hide the tail.
def _distribution(values: list[float], formatter: Callable[[float], str], xlabel: str) -> bytes:
    summary = percentiles(values)
    figure, axes = _new_axes(2.6)
    axes.grid(axis="x", visible=False)
    bins = min(40, max(5, int(len(values) ** 0.5)))
    axes.hist(values, bins=bins, color=SERIES, edgecolor=SURFACE, linewidth=1.5)
    axes.set_ylim(0, axes.get_ylim()[1] * 1.4)

    low, high = axes.get_xlim()
    if low < 0:
        low = 0
        axes.set_xlim(low, high)
    marks = (("p50", summary.p50, 0.98), ("p95", summary.p95, 0.9), ("p99", summary.p99, 0.82))
    for name, value, height in marks:
        axes.axvline(value, ymax=PERCENTILE_LINE_TOP, color=INK_MUTED, linewidth=HAIRLINE)
        text = f"{name} {formatter(value)}"
        position = (value - low) / ((high - low) or 1)
        overflows = position + len(text) * CHAR_WIDTH > 0.99
        axes.text(
            value,
            height,
            text,
            transform=axes.get_xaxis_transform(),
            ha="right" if overflows else "left",
            va="top",
            color=INK_SECONDARY,
            fontsize=FONT_SIZE - 1,
            bbox=LABEL_BOX,
        )

    axes.xaxis.set_major_locator(MaxNLocator(nbins=5))
    axes.xaxis.set_major_formatter(FuncFormatter(lambda v, _: formatter(v)))
    axes.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
    _axis_label(axes, x=xlabel, y="Runs")
    return _to_svg(figure)


def _latency(records: Sequence[RunRecord]) -> bytes:
    return _distribution([r.latency_ms for r in records], _format_ms, "Latency per run")


def _cost(records: Sequence[RunRecord]) -> bytes:
    return _distribution([r.cost_usd for r in records], _format_usd, "Cost per run")


BarRow = tuple[str, float, str, tuple[float, float] | None]


# Draws one horizontal bar per row with its label above the bar (so long
# names get the full width) and its value at the bar's end. Rows are
# (label, value, value_text, interval or None), drawn top to bottom.
def _labelled_bars(
    rows: Sequence[BarRow],
    *,
    x_max: float,
    ticks: list[float],
    tick_formatter: FuncFormatter | PercentFormatter,
    xlabel: str,
) -> bytes:
    figure, axes = _new_axes(0.5 + 0.42 * max(len(rows), 1))
    axes.grid(axis="y", visible=False)
    axes.spines["left"].set_visible(False)
    axes.set_yticks([])
    pad = x_max * 0.015

    if not rows:
        axes.text(
            0.5,
            0.5,
            "Nothing recorded yet",
            transform=axes.transAxes,
            ha="center",
            va="center",
            color=INK_MUTED,
            fontsize=FONT_SIZE,
        )

    for index, (label, value, value_text, interval) in enumerate(rows):
        bar_y = index + 0.14
        axes.text(
            0,
            index - 0.22,
            _truncate(label),
            va="center",
            ha="left",
            color=INK_PRIMARY,
            fontsize=FONT_SIZE,
            bbox=LABEL_BOX,
        )
        axes.barh(bar_y, value, height=0.32, color=SERIES)
        end = value
        if interval is not None:
            axes.plot(interval, (bar_y, bar_y), color=INK_SECONDARY, linewidth=HAIRLINE)
            end = max(value, interval[1])
        axes.text(
            end + pad,
            bar_y,
            value_text,
            va="center",
            ha="left",
            color=INK_SECONDARY,
            fontsize=FONT_SIZE - 1,
            bbox=LABEL_BOX,
        )

    axes.set_ylim(len(rows) - 0.4, -0.6)
    axes.set_xlim(0, x_max * 1.22)
    axes.set_xticks(ticks)
    axes.xaxis.set_major_formatter(tick_formatter)
    _axis_label(axes, x=xlabel)
    return _to_svg(figure)


# Plots each assertion's failure rate with its 95% interval, worst first, so
# the assertion that fails most is the first thing you see.
def _assertions(records: Sequence[RunRecord]) -> bytes:
    stats = analyze_scenario(list(records))
    rows = [
        (
            a.label,
            a.failure_rate,
            f"{a.failure_rate:.0%} ({a.failures:,}/{a.total:,})",
            a.failure_rate_interval,
        )
        for a in stats.assertions[:MAX_BARS]
    ]
    return _labelled_bars(
        rows,
        x_max=1.0,
        ticks=[0, 0.25, 0.5, 0.75, 1],
        tick_formatter=PercentFormatter(1.0),
        xlabel="Failure rate, with 95% interval",
    )


# Plots how many runs took each distinct tool-call path, folding everything
# past the most common few into "Other" rather than growing the chart.
def _tool_paths(records: Sequence[RunRecord]) -> bytes:
    stats = analyze_scenario(list(records))
    total = stats.total_runs
    shown = (
        stats.tool_paths[: MAX_BARS - 1] if len(stats.tool_paths) > MAX_BARS else stats.tool_paths
    )
    rows: list[BarRow] = [
        (" → ".join(p.path) or "(no tool calls)", p.count, f"{p.count:,}/{total:,}", None)
        for p in shown
    ]
    folded = stats.tool_paths[len(shown) :]
    if folded:
        count = sum(p.count for p in folded)
        rows.append((f"Other ({len(folded)} paths)", count, f"{count:,}/{total:,}", None))

    ticks = [t for t in MaxNLocator(nbins=4, integer=True).tick_values(0, total) if 0 <= t <= total]
    return _labelled_bars(
        rows,
        x_max=float(total),
        ticks=ticks,
        tick_formatter=FuncFormatter(lambda v, _: f"{v:,.0f}"),
        xlabel="Runs",
    )


_RENDERERS: dict[str, Callable[[Sequence[RunRecord]], bytes]] = {
    "pass_rate": _pass_rate_trend,
    "latency": _latency,
    "cost": _cost,
    "assertions": _assertions,
    "tool_paths": _tool_paths,
}

CHART_KINDS = tuple(_RENDERERS)


# Renders one chart of a scenario's completed runs as SVG. Raises KeyError for
# an unknown kind and ValueError when no runs have completed yet. Rendering
# is serialized because matplotlib isn't safe to drive from several threads.
def render_chart(kind: str, records: Sequence[RunRecord]) -> bytes:
    renderer = _RENDERERS[kind]
    if not records:
        raise ValueError("no runs have completed yet")
    with _render_lock:
        return renderer(records)
