from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def chart_series_for_run(
    run_dir: Path, rows: list[dict[str, Any]], experiment_id: str
) -> dict[str, Any]:
    summary_path = Path(run_dir) / "summary.json"
    try:
        payload = json.loads(summary_path.read_text())
        series = payload.get("charts")
        if isinstance(series, dict) and series:
            return series
    except (json.JSONDecodeError, OSError):
        pass
    from roast_my_harness.report.charts import chart_series

    return chart_series(rows, experiment_id)


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def render_resolve_rate(path: Path, rates: list[dict[str, Any]]) -> None:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 0.6 + 0.6 * max(len(rates), 1)))
    names = [r["variant"] for r in rates]
    means = [100 * r["rate"] for r in rates]
    lo = [100 * (r["rate"] - r["lo"]) for r in rates]
    hi = [100 * (r["hi"] - r["rate"]) for r in rates]
    y = list(range(len(names)))
    ax.barh(y, means, xerr=[lo, hi], capsize=4)
    ax.set_yticks(y, names)
    ax.set_xlabel("resolve rate % (task-bootstrapped 95% CI)")
    ax.set_xlim(0, 100)
    for i, r in enumerate(rates):
        label = f"{means[i]:.1f}% [{100 * r['lo']:.1f}, {100 * r['hi']:.1f}]"
        ax.text(means[i] + 1, i, label, va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def render_flips(path: Path, flips: list[dict[str, Any]]) -> None:
    plt = _plt()
    rows = [(f"{f['a']} vs {f['b']}", f["a_fail_b_pass"], f["b_fail_a_pass"]) for f in flips]
    if not rows:
        rows = [("no shared tasks", 0, 0)]
    fig, ax = plt.subplots(figsize=(8, 0.8 + 0.7 * len(rows)))
    labels = [r[0] for r in rows]
    y = list(range(len(labels)))
    ax.barh(y, [r[1] for r in rows], label="rescued (a fail, b pass)")
    ax.barh(y, [-r[2] for r in rows], label="broken (a pass, b fail)")
    ax.set_yticks(y, labels)
    ax.set_xlabel("tasks")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def render_near_miss(path: Path, near_miss: dict[str, Any]) -> None:
    plt = _plt()
    arms = near_miss.get("arms", {})
    buckets = ["<0.5", "0.5-0.8", "0.8-1.0"]
    names = sorted(arms)
    fig, ax = plt.subplots(figsize=(8, 0.8 + 0.6 * max(len(names), 1)))
    y = list(range(len(names)))
    left = [0.0] * len(names)
    for bucket in buckets:
        vals = [arms[n].get(bucket, 0) for n in names]
        ax.barh(y, vals, left=left, label=bucket)
        left = [a + b for a, b in zip(left, vals)]
    ax.set_yticks(y, names or ["no data"])
    ax.set_xlabel("unresolved trials by partial credit")
    ax.legend(title="partial", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def render_partial_deltas(path: Path, deltas: list[dict[str, Any]]) -> None:
    plt = _plt()
    top = deltas[:10]
    fig, ax = plt.subplots(figsize=(9, 0.8 + 0.5 * max(len(top), 1)))
    labels = [f"{d['variant']}: {d['task']}" for d in top]
    vals = [d["delta"] for d in top]
    y = list(range(len(labels)))
    ax.barh(y, vals, color=["green" if v >= 0 else "red" for v in vals])
    ax.set_yticks(y, labels, fontsize=8)
    ax.set_xlabel("partial[variant] - partial[control]")
    ax.axvline(0, color="black", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def render_cost(path: Path, cost: list[dict[str, Any]]) -> None:
    plt = _plt()
    names = [c["variant"] for c in cost]
    x = list(range(len(names)))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 1.4 + 0.6 * max(len(names), 1)))
    a1.bar(x, [c["mean_output_tokens"] / 1000 for c in cost])
    a1.set_xticks(x, names, rotation=20, ha="right", fontsize=8)
    a1.set_ylabel("mean output tokens (k)")
    a2.bar(x, [c["mean_wall_sec"] / 60 for c in cost])
    a2.set_xticks(x, names, rotation=20, ha="right", fontsize=8)
    a2.set_ylabel("mean wall time (min)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


CHART_FILES = (
    "resolve-rate.png",
    "flips.png",
    "near-miss.png",
    "partial-delta.png",
    "cost.png",
)


def render_all_charts(run_dir: Path, series: dict[str, Any]) -> list[Path]:
    charts_dir = Path(run_dir) / "charts"
    charts_dir.mkdir(exist_ok=True)
    jobs = [
        (render_resolve_rate, "resolve-rate.png", series.get("resolve_rates", [])),
        (render_flips, "flips.png", series.get("flips", [])),
        (render_near_miss, "near-miss.png", series.get("near_miss", {})),
        (render_partial_deltas, "partial-delta.png", series.get("partial_deltas", [])),
        (render_cost, "cost.png", series.get("cost", [])),
    ]
    written = []
    for fn, name, data in jobs:
        out = charts_dir / name
        fn(out, data)
        written.append(out)
    return written
