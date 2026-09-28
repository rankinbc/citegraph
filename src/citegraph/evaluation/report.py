"""Markdown report, README table, SVG charts, and the CI regression gate."""

from __future__ import annotations

import json
from pathlib import Path

from citegraph.evaluation.runner import Level1Result


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _summary_table(result: Level1Result) -> str:
    lines = [
        "| tool | questions | citegraph F1 | grep F1 | citegraph P | citegraph R |",
        "|---|---|---|---|---|---|",
    ]
    for tool, s in result.by_tool.items():
        base = s.baseline.f1 if s.baseline else None
        lines.append(
            f"| {tool} | {s.n} | {_fmt(s.citegraph.f1)} | {_fmt(base)} | "
            f"{_fmt(s.citegraph.precision)} | {_fmt(s.citegraph.recall)} |"
        )
    return "\n".join(lines) + "\n"


def _calibration_table(result: Level1Result) -> str:
    lines = ["| rule | nominal confidence | observed precision | edges |", "|---|---|---|---|"]
    lines += [f"| {b.rule} | {b.nominal:.2f} | {b.observed:.2f} | {b.n} |" for b in result.calibration]
    return "\n".join(lines) + "\n"


def _charts(result: Level1Result, out_dir: Path) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    tools = [t for t, s in result.by_tool.items() if s.baseline is not None]
    fig, ax = plt.subplots(figsize=(6, 3.2))
    xs = range(len(tools))
    ax.bar(  # pyright: ignore[reportUnknownMemberType]
        [x - 0.2 for x in xs], [result.by_tool[t].citegraph.f1 for t in tools], 0.4, label="citegraph"
    )
    baseline_f1 = [result.by_tool[t].baseline.f1 for t in tools]  # pyright: ignore[reportOptionalMemberAccess]
    ax.bar(  # pyright: ignore[reportUnknownMemberType]
        [x + 0.2 for x in xs], baseline_f1, 0.4, label="grep"
    )
    ax.set_xticks(list(xs), tools)  # pyright: ignore[reportUnknownMemberType]
    ax.set_ylim(0, 1)
    ax.set_ylabel("F1")  # pyright: ignore[reportUnknownMemberType]
    ax.legend()  # pyright: ignore[reportUnknownMemberType]
    fig.tight_layout()
    fig.savefig(out_dir / "f1_by_tool.svg")  # pyright: ignore[reportUnknownMemberType]
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.plot([0, 1], [0, 1], linestyle="--", color="grey")  # pyright: ignore[reportUnknownMemberType]
    ax.scatter(  # pyright: ignore[reportUnknownMemberType]
        [b.nominal for b in result.calibration], [b.observed for b in result.calibration]
    )
    for b in result.calibration:
        ax.annotate(b.rule, (b.nominal, b.observed), fontsize=8)  # pyright: ignore[reportUnknownMemberType]
    ax.set_xlabel("nominal confidence")  # pyright: ignore[reportUnknownMemberType]
    ax.set_ylabel("observed precision")  # pyright: ignore[reportUnknownMemberType]
    fig.tight_layout()
    fig.savefig(out_dir / "calibration.svg")  # pyright: ignore[reportUnknownMemberType]
    plt.close(fig)
    return ["f1_by_tool.svg", "calibration.svg"]


def write_report(result: Level1Result, out_dir: Path, charts: bool = True) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    table = _summary_table(result)
    (out_dir / "readme_table.md").write_text(table, encoding="utf-8")
    images = _charts(result, out_dir) if charts else []
    misses = [q for q in result.questions if q.citegraph_score.f1 < 1.0]
    body = [
        "# citegraph Level-1 eval\n",
        f"{len(result.questions)} questions. Tool latency p50 {result.p50_ms:.1f} ms, p95 {result.p95_ms:.1f} ms.\n",
        "## Accuracy\n",
        table,
        "## Calibration\n",
        _calibration_table(result),
        *[f"![{name}]({name})\n" for name in images],
        "## Misses\n",
        *[
            f"- `{q.id}`: missing {sorted(set(q.expected) - set(q.citegraph))}, "
            f"extra {sorted(set(q.citegraph) - set(q.expected))}\n"
            for q in misses
        ],
    ]
    (out_dir / "report.md").write_text("\n".join(body), encoding="utf-8")
    return out_dir


def write_baseline(result: Level1Result, path: Path) -> None:
    data = {tool: round(s.citegraph.f1, 4) for tool, s in result.by_tool.items()}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def check_regression(result: Level1Result, baseline_path: Path, tolerance: float = 0.02) -> list[str]:
    baseline: dict[str, float] = json.loads(baseline_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for tool, expected_f1 in baseline.items():
        summary = result.by_tool.get(tool)
        if summary is not None and summary.citegraph.f1 < expected_f1 - tolerance:
            failures.append(
                f"{tool}: F1 {summary.citegraph.f1:.3f} is below baseline {expected_f1:.3f} - {tolerance}"
            )
    return failures
