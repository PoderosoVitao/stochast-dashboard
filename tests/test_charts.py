import pytest
from stochast.records import AssertionResult, RunRecord, ToolCall

from stochast_dashboard.charts import CHART_KINDS, render_chart


def make_record(
    run_index: int,
    *,
    passed: bool = True,
    tools: tuple[str, ...] = ("lookup_order",),
    latency_ms: float = 200.0,
    cost_usd: float = 0.0,
    assertions: bool = True,
) -> RunRecord:
    return RunRecord(
        run_index=run_index,
        scenario_name="refund_status_lookup",
        tool_calls=[ToolCall(name=name, arguments={}) for name in tools],
        final_output="ok",
        assertions=[AssertionResult(label="a", passed=passed)] if assertions else [],
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )


@pytest.mark.parametrize("kind", CHART_KINDS)
def test_every_chart_kind_renders_svg(kind: str):
    records = [make_record(i, passed=i % 3 != 0, latency_ms=100.0 + i * 7) for i in range(30)]

    svg = render_chart(kind, records)

    assert b"<svg" in svg


@pytest.mark.parametrize("kind", CHART_KINDS)
def test_every_chart_kind_handles_a_single_run_with_untracked_cost(kind: str):
    svg = render_chart(kind, [make_record(0)])

    assert b"<svg" in svg


def test_tool_paths_fold_the_long_tail_into_other():
    records = [make_record(i, tools=("tool",) * (i + 1)) for i in range(10)]

    svg = render_chart("tool_paths", records)

    assert b"Other (3 paths)" in svg


def test_assertions_chart_says_so_when_nothing_was_asserted():
    svg = render_chart("assertions", [make_record(0, assertions=False)])

    assert b"Nothing recorded yet" in svg


def test_render_chart_rejects_unknown_kinds_and_empty_records():
    with pytest.raises(KeyError):
        render_chart("pie", [make_record(0)])
    with pytest.raises(ValueError):
        render_chart("latency", [])
