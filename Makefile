MODEL ?= gemini-4-argon
SMOKE_MODEL ?= gemini-3.5-flash-lite
EFFORT ?= medium
# Agent runs are noisy; 3 samples tightens the confidence intervals (3x cost).
AGENT_SAMPLES ?= 1

.PHONY: setup test smoke eval eval-budget analyze

setup:
	brew list icarus-verilog >/dev/null 2>&1 || brew install icarus-verilog
	[ -d data/verilog-eval ] || git clone --depth 1 https://github.com/NVlabs/verilog-eval.git data/verilog-eval
	uv sync

test:
	uv run pytest -q

# 20 problems spread across the benchmark on a cheap model: checks the pipeline end to end for cents.
smoke:
	for m in baseline compile self oracle; do \
	  uv run run_eval.py --mode $$m --model $(SMOKE_MODEL) --effort $(EFFORT) --limit 20 --spread --run-name smoke_$$m || exit 1; \
	done

# Full benchmark: all four conditions on one model (baseline with 5 samples for pass@5).
eval:
	uv run run_eval.py --mode baseline --model $(MODEL) --effort $(EFFORT) --samples 5
	uv run run_eval.py --mode compile  --model $(MODEL) --effort $(EFFORT) --samples $(AGENT_SAMPLES)
	uv run run_eval.py --mode self     --model $(MODEL) --effort $(EFFORT) --samples $(AGENT_SAMPLES)
	uv run run_eval.py --mode oracle   --model $(MODEL) --effort $(EFFORT) --samples $(AGENT_SAMPLES)
	uv run analyze.py --model $(MODEL)

# The published results: three modes, one sample, cheap model (~$3 of API calls).
BUDGET_MODEL ?= gemini-3.5-flash-lite
eval-budget:
	for m in baseline self oracle; do \
	  uv run run_eval.py --mode $$m --model $(BUDGET_MODEL) --effort $(EFFORT) || exit 1; \
	done
	uv run analyze.py --model $(BUDGET_MODEL)

analyze:
	uv run analyze.py
