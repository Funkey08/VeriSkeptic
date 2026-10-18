"""Summarise all runs in results/ into results/summary.md and results/pass_vs_attempts.png.

  uv run analyze.py                     # all runs
  uv run analyze.py --model claude-sonnet-5-5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from math import comb
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from vagent.dataset import ROOT  # noqa: E402

RESULTS_DIR = ROOT / "results"
MODE_ORDER = ["baseline", "compile", "self", "oracle"]
MODE_LABEL = {
    "baseline": "A. Single call",
    "compile": "B. Compiler feedback",
    "self": "C. Self-verified (own testbench)",
    "oracle": "D. Oracle feedback (upper bound)",
}
# Categorical slots 1-4 of the validated reference palette (light mode), fixed order by mode.
MODE_COLOR = {"baseline": "#2a78d6", "compile": "#eb6834", "self": "#1baf7a", "oracle": "#eda100"}
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al., 2021)."""
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def load_runs(model: str | None) -> list[dict]:
    runs = []
    for path in sorted(RESULTS_DIR.glob("*/results.jsonl")):
        recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        if not recs or (model and recs[0]["model"] != model):
            continue
        runs.append({"name": path.parent.name, "mode": recs[0]["mode"], "model": recs[0]["model"],
                     "effort": recs[0]["effort"], "recs": recs})
    runs.sort(key=lambda r: (r["model"], MODE_ORDER.index(r["mode"]), r["name"]))
    return runs


def by_problem(recs: list[dict]) -> dict[str, list[dict]]:
    d = defaultdict(list)
    for r in recs:
        d[r["problem"]].append(r)
    return d


def pass_at(recs: list[dict], k: int) -> float | None:
    probs = by_problem(recs)
    if min(len(v) for v in probs.values()) < k:
        return None
    return sum(pass_at_k(len(v), sum(r["passed"] for r in v), k) for v in probs.values()) / len(probs)


def pass_after_attempt(recs: list[dict], k: int) -> float:
    """Pass rate if every run had been stopped after its k-th design version."""
    hits = 0
    for r in recs:
        v = r["versions"]
        hits += bool(v) and v[min(k, len(v)) - 1]["passed"]
    return hits / len(recs)


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def summarize(runs: list[dict]) -> str:
    out = ["# Results", ""]
    out += ["| Run | Mode | Problems | Samples | pass@1 | pass@5 | Avg LLM calls | Avg design versions | $ / problem | Total $ |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        recs = run["recs"]
        n_prob = len(by_problem(recs))
        calls = sum(r["usage"]["calls"] for r in recs) / len(recs)
        versions = sum(len(r["versions"]) for r in recs) / len(recs)
        cost = sum(r["usage"]["cost_usd"] for r in recs)
        out.append(
            f"| {run['name']} | {MODE_LABEL[run['mode']]} | {n_prob} | {len(recs) // n_prob} | "
            f"**{pct(pass_at(recs, 1))}** | {pct(pass_at(recs, 5))} | {calls:.1f} | {versions:.1f} | "
            f"${cost / len(recs):.3f} | ${cost:.2f} |"
        )

    self_runs = [r for r in runs if r["mode"] == "self"]
    if self_runs:
        out += ["", "## Self-verification reliability", "",
                "How far can the agent trust its own testbench? *False confidence* = its own testbench "
                "passed on the submitted design, but the hidden reference testbench failed.", "",
                "| Run | Self-test passed | ...and hidden passed | False confidence | Hidden pass when self-test did NOT pass |",
                "|---|---|---|---|---|"]
        for run in self_runs:
            recs = run["recs"]
            conf = [r for r in recs if r["self_test_final"]]
            unconf = [r for r in recs if not r["self_test_final"]]
            fc = sum(not r["passed"] for r in conf)
            out.append(
                f"| {run['name']} | {len(conf)}/{len(recs)} | {len(conf) - fc} | "
                f"{fc}/{len(conf)} ({pct(fc / len(conf) if conf else None)}) | "
                f"{sum(r['passed'] for r in unconf)}/{len(unconf)} |"
            )

    cats = sorted({r["category"] for run in runs for r in run["recs"]})
    out += ["", "## pass@1 by problem category", "", "| Run | " + " | ".join(cats) + " |",
            "|---|" + "---|" * len(cats)]
    for run in runs:
        cells = []
        for c in cats:
            rs = [r for r in run["recs"] if r["category"] == c]
            cells.append(f"{pct(sum(r['passed'] for r in rs) / len(rs))} (n={len(rs)})" if rs else "-")
        out.append(f"| {run['name']} | " + " | ".join(cells) + " |")

    statuses = ["pass", "compile_error", "sim_fail", "timeout", "no_result", "no_design"]
    out += ["", "## Final status breakdown", "", "| Run | " + " | ".join(statuses) + " | end reasons |",
            "|---|" + "---|" * (len(statuses) + 1)]
    for run in runs:
        c = Counter(r["status"] for r in run["recs"])
        ends = Counter(r["end_reason"] for r in run["recs"])
        out.append(f"| {run['name']} | " + " | ".join(str(c.get(s, 0)) for s in statuses)
                   + " | " + ", ".join(f"{k}: {v}" for k, v in ends.most_common()) + " |")

    out += ["", "## Problems fixed by iteration", "",
            "Runs where the first design failed the hidden tests but the submitted one passed.", ""]
    for run in runs:
        if run["mode"] == "baseline":
            continue
        fixed = sorted(r["problem"] for r in run["recs"] if r["versions"] and not r["versions"][0]["passed"] and r["passed"])
        broke = sorted(r["problem"] for r in run["recs"] if r["versions"] and r["versions"][0]["passed"] and not r["passed"])
        out.append(f"- **{run['name']}**: fixed {len(fixed)}, regressed {len(broke)}"
                   + (f" (regressed: {', '.join(broke[:8])})" if broke else ""))
        if fixed:
            out.append(f"  - e.g. {', '.join(fixed[:10])}")
    return "\n".join(out) + "\n"


def plot(runs: list[dict], path: Path, max_k: int = 5) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ks = list(range(1, max_k + 1))
    for run in runs:
        mode = run["mode"]
        if mode == "baseline":
            ys = [pass_at(run["recs"], k) for k in ks]
            pts = [(k, y) for k, y in zip(ks, ys) if y is not None]
            label = f"{MODE_LABEL[mode]}: pass@k (any of k samples; needs an oracle to pick)"
            style = dict(linestyle=(0, (4, 3)))
        else:
            pts = [(k, pass_after_attempt(run["recs"], k)) for k in ks]
            label = MODE_LABEL[mode]
            style = {}
        if not pts:
            continue
        xs, ys = zip(*pts)
        ys = [100 * y for y in ys]
        ax.plot(xs, ys, color=MODE_COLOR[mode], linewidth=2, marker="o", markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=2, label=label, **style)
        ax.annotate(f"{ys[-1]:.1f}%", (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=9, color=INK)
    ax.set_xticks(ks)
    ax.set_xlim(0.8, max_k + 0.6)
    ax.set_xlabel("Attempts (design versions for agents; independent samples for the baseline)", color=INK_2, fontsize=9)
    ax.set_ylabel("Hidden-testbench pass rate (%)", color=INK_2, fontsize=9)
    model = ", ".join(sorted({r["model"] for r in runs}))
    ax.set_title(f"VerilogEval v2 spec-to-RTL: pass rate vs. attempts ({model})", color=INK, fontsize=11, loc="left")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=INK_2, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.legend(frameon=False, fontsize=8.5, loc="lower right", labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="only include runs for this model")
    args = ap.parse_args()
    runs = load_runs(args.model)
    if not runs:
        raise SystemExit("No runs found in results/. Run run_eval.py first.")
    md = summarize(runs)
    (RESULTS_DIR / "summary.md").write_text(md)
    plot(runs, RESULTS_DIR / "pass_vs_attempts.png")
    print(md)
    print("Wrote results/summary.md and results/pass_vs_attempts.png")


if __name__ == "__main__":
    main()
