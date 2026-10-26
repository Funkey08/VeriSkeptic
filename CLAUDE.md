# CLAUDE.md

LLM agent that writes Verilog from a spec, evaluated on VerilogEval v2 (spec-to-RTL) in four modes:
baseline, compile feedback, self-verified (own testbench) and oracle (hidden testbench). See README.md
for the experiment design.

## Commands

```bash
make setup     # icarus-verilog (brew), clone VerilogEval into data/, uv sync
make test      # pytest; ~10s, no API calls (includes all 152 reference solutions)
make smoke     # 20 problems spread across the benchmark x 4 modes on a cheap model (cents)
make eval      # full run on MODEL (default gemini-4-argon); AGENT_SAMPLES=3 for tighter CIs
make analyze   # results/summary.md + results/pass_vs_attempts.png
uv run pytest tests/test_agent.py -q                                # single test file
uv run run_eval.py --mode self --problems Prob003 --run-name dbg    # one problem
```

The repo sits in OneDrive, whose sync can block writes mid-run. For paid runs set
`VAGENT_WORK_DIR=~/vagent-work` so `results/` and `.cache/` live outside it, then copy final runs back.

API keys live in `.env` (`GEMINI_API_KEY`, optional `ANTHROPIC_API_KEY`). Full evals spend real money;
ask before running anything beyond `make test` or a small smoke run.

## Layout

- `vagent/dataset.py` problem loading; `EXCLUDED` lists the 4 problems whose own reference fails
- `vagent/harness.py` Icarus compile/sim: `hidden_eval` (ground truth) vs. `compile_check` / `self_sim` (agent-visible)
- `vagent/llm.py` provider-neutral client (Anthropic-style content blocks), Gemini + Claude backends, disk cache, cost
- `vagent/agent.py` the four modes; B/C/D share one loop and prompt and differ only in tool feedback
- `run_eval.py` parallel, resumable runner -> `results/<run>/results.jsonl` + transcripts
- `analyze.py` tables (pass@k, bootstrap CIs, paired gain vs. baseline) and the chart

## Invariants (don't break these)

- **No hidden-test leakage.** `hidden_eval` output reaches the model only in `oracle` mode. Every design
  version is scored hidden for logging, never returned in other modes. `tests/test_agent.py` checks this.
- **Scoring mirrors the official scorer** `data/verilog-eval/scripts/sv-iv-analyze`, including its
  warning-based failures. If you change `hidden_eval`, all 152 reference solutions must still pass.
- **Modes stay comparable.** Changing the system prompt, tools or budget for one agent mode changes the
  experiment; apply it to all of B/C/D or call it out.
- **LLM cache key** is a hash of the full request plus `sample`. Changing prompts or tool schemas
  invalidates cached responses (`.cache/llm/`), so re-runs will cost money again.
- `data/` and `.cache/` are gitignored. `results/` is tracked because the README links to the summary and
  chart; commit only final runs there, not smoke or debug runs.

## Git

- Conventional Commits: `type(scope): imperative summary`, types `feat`, `fix`, `refactor`, `test`,
  `docs`, `chore`, `perf`, `build`. Scopes in use: `harness`, `agent`, `llm`, `analysis`, `smoke`.
  Optional body explains *why*, wrapped at ~72 chars.
- No `Co-Authored-By` or other AI attribution lines in commits or PRs.
- Commit after each self-contained change rather than in one large batch.
- The repo is in OneDrive, which flips file permission bits, so every file looks modified. Stage
  content only with `git -c core.fileMode=false add <files>`, and never commit `.DS_Store` or `.vscode/`.
