"""Load VerilogEval v2 spec-to-RTL problems."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Results and the LLM cache can live elsewhere, e.g. outside a cloud-synced folder.
WORK_DIR = Path(os.environ.get("VAGENT_WORK_DIR") or ROOT).expanduser()
DATA_DIR = ROOT / "data" / "verilog-eval" / "dataset_spec-to-rtl"

# Problems whose own reference solution fails the official pass criteria, so no
# design can pass them. Verified by tests/test_harness.py.
EXCLUDED = {
    "Prob082_lfsr32": "stimulus (200k cycles) outlasts the testbench's 1M-ps TIMEOUT watchdog",
    "Prob099_m2014_q6c": "testbench binds ports Y2/Y4 but the reference module declares Y1/Y3",
    "Prob141_count_clock": "stimulus outlasts the testbench's 1M-ps TIMEOUT watchdog",
    "Prob156_review2015_fancytimer": "stimulus outlasts the testbench's 1M-ps TIMEOUT watchdog",
}


@dataclass(frozen=True)
class Problem:
    id: str  # e.g. "Prob003_step_one"
    dir: Path

    @cached_property
    def spec(self) -> str:
        return (self.dir / f"{self.id}_prompt.txt").read_text().strip()

    @property
    def ref_path(self) -> Path:
        return self.dir / f"{self.id}_ref.sv"

    @property
    def test_path(self) -> Path:
        return self.dir / f"{self.id}_test.sv"

    @cached_property
    def category(self) -> str:
        """Coarse heuristic bucket used only for reporting."""
        ref = self.ref_path.read_text()
        name = self.id.lower()
        if not re.search(r"\binput\b[^;]*\bclk\b", ref):
            return "combinational"
        if "fsm" in name or re.search(r"\b(state|next_state|next)\b", ref):
            return "fsm"
        return "sequential"


def load_problems(
    limit: int | None = None, ids: list[str] | None = None, include_excluded: bool = False
) -> list[Problem]:
    if not DATA_DIR.exists():
        raise SystemExit(
            f"Dataset not found at {DATA_DIR}.\n"
            "Run: git clone --depth 1 https://github.com/NVlabs/verilog-eval.git data/verilog-eval"
        )
    names = (DATA_DIR / "problems.txt").read_text().split()
    problems = [Problem(n, DATA_DIR) for n in names if include_excluded or n not in EXCLUDED]
    if ids:
        wanted = set(ids)
        problems = [p for p in problems if p.id in wanted or p.id.split("_", 1)[0] in wanted]
    return problems[:limit] if limit else problems
