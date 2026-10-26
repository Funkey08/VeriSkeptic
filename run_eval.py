"""Run one experimental condition over VerilogEval v2 (spec-to-RTL).

Examples:
  uv run run_eval.py --mode baseline --samples 5
  uv run run_eval.py --mode self --model gemini-3.5-flash-lite --limit 20   # cheap smoke test
"""

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from vagent.agent import MODES, run
from vagent.dataset import ROOT, load_problems
from vagent.llm import Usage, make_llm

RESULTS_DIR = ROOT / "results"


def _strip_signatures(messages: list[dict]) -> list[dict]:
    """Thinking signatures / raw provider parts are opaque and large; drop them from saved transcripts."""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            content = [{k: v for k, v in b.items() if k != "signature"} for b in content if b["type"] != "provider_raw"]
        out.append({**m, "content": content})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=MODES, required=True)
    ap.add_argument("--model", default="gemini-4-argon", help="gemini-* or claude-*")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--samples", type=int, default=1, help="independent samples per problem (for pass@k)")
    ap.add_argument("--max-attempts", type=int, default=5, help="design writes allowed per agent run")
    ap.add_argument("--limit", type=int, help="only the first N problems")
    ap.add_argument("--spread", action="store_true", help="with --limit, take N evenly spaced problems instead")
    ap.add_argument("--problems", nargs="*", help="problem ids or ProbNNN prefixes")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-cost", type=float, help="stop starting new jobs once this session has spent $X")
    ap.add_argument("--run-name", help="defaults to <model>_<mode>_<effort>")
    args = ap.parse_args()

    run_name = args.run_name or f"{args.model}_{args.mode}_{args.effort}"
    run_dir = RESULTS_DIR / run_name
    (run_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(vars(args), indent=2))
    results_path = run_dir / "results.jsonl"

    done = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            r = json.loads(line)
            done.add((r["problem"], r["sample"]))

    problems = load_problems(ids=args.problems)
    if args.limit:
        # The first problems are all trivial gates; --spread samples the whole difficulty range.
        problems = problems[:: max(1, len(problems) // args.limit)] if args.spread else problems
        problems = problems[: args.limit]
    jobs = [(p, s) for p in problems for s in range(args.samples) if (p.id, s) not in done]
    print(f"{run_name}: {len(problems)} problems x {args.samples} samples, {len(done)} already done, {len(jobs)} to run")

    llm = make_llm(args.model, effort=args.effort)
    lock = threading.Lock()
    total = Usage()
    passed = 0

    def work(problem, sample):
        out = run(problem, llm, args.mode, sample, max_attempts=args.max_attempts)
        transcript = run_dir / "transcripts" / f"{problem.id}_s{sample}.json"
        transcript.parent.mkdir(parents=True, exist_ok=True)  # cloud-sync folders can vanish mid-run
        transcript.write_text(json.dumps({
            "problem": problem.id, "spec": problem.spec, "final_design": out.final_design,
            "messages": _strip_signatures(out.messages),
        }, indent=1))
        return problem, {
            "problem": problem.id, "category": problem.category, "mode": args.mode, "model": args.model,
            "effort": args.effort, "sample": sample, "passed": out.passed, "status": out.status,
            "versions": out.versions, "self_test_final": out.self_test_final, "end_reason": out.end_reason,
            "turns": out.turns, "usage": out.usage.to_dict(),
        }

    with ThreadPoolExecutor(args.workers) as pool, open(results_path, "a") as f:
        futures = [pool.submit(work, p, s) for p, s in jobs]
        bar = tqdm(as_completed(futures), total=len(futures))
        for fut in bar:
            if fut.cancelled():
                continue
            try:
                _, rec = fut.result()
            except Exception as e:  # keep the run going; failed jobs are retried on resume
                tqdm.write(f"ERROR: {type(e).__name__}: {e}")
                continue
            with lock:
                f.write(json.dumps(rec) + "\n")
                f.flush()
                passed += rec["passed"]
                for k, v in rec["usage"].items():
                    setattr(total, k, getattr(total, k) + v)
                bar.set_postfix(passed=passed, cost=f"${total.cost_usd:.2f}")
            if args.max_cost is not None and total.cost_usd >= args.max_cost:
                # In-flight jobs still finish and are saved; the rest resume on the next run.
                cancelled = sum(fu.cancel() for fu in futures)
                tqdm.write(f"Budget ${args.max_cost:.2f} reached; cancelled {cancelled} queued jobs.")
                args.max_cost = None

    print(f"Done. This session: {passed} passed, {total.calls} calls ({total.cached_calls} cached), "
          f"${total.cost_usd:.2f}. Results: {results_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
