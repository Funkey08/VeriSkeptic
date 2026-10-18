MODEL ?= claude-sonnet-5-5
SMOKE_MODEL ?= claude-haiku-5-5
EFFORT ?= medium

.PHONY: setup test smoke eval analyze

setup:
	brew list icarus-verilog >/dev/null 2>&1 || brew install icarus-verilog
	[ -d data/verilog-eval ] || git clone --depth 1 https://github.com/NVlabs/verilog-eval.git data/verilog-eval
	uv sync

test:
	uv run pytest -q

# ~20 problems on a cheap model: checks the pipeline end to end for cents.
smoke:
	for m in baseline compile self oracle; do \
	  uv run run_eval.py --mode $$m --model $(SMOKE_MODEL) --effort $(EFFORT) --limit 20 --run-name smoke_$$m || exit 1; \
	done

# Full benchmark: all four conditions on one model (baseline with 5 samples for pass@5).
eval:
	uv run run_eval.py --mode baseline --model $(MODEL) --effort $(EFFORT) --samples 5
	uv run run_eval.py --mode compile  --model $(MODEL) --effort $(EFFORT)
	uv run run_eval.py --mode self     --model $(MODEL) --effort $(EFFORT)
	uv run run_eval.py --mode oracle   --model $(MODEL) --effort $(EFFORT)
	uv run analyze.py --model $(MODEL)

analyze:
	uv run analyze.py
