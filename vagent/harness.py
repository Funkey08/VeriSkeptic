"""Compile and simulate Verilog with Icarus Verilog.

Two kinds of checks live here, and the separation between them is the core of
the experiment's integrity:

* ``hidden_eval`` runs the benchmark's reference testbench. It is the ground
  truth for scoring every mode, but its output is only ever shown to the agent
  in the explicitly-labelled ``oracle`` mode.
* ``compile_check`` and ``self_sim`` use nothing but the agent's own code, so
  their feedback is fair game for an autonomous agent.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .dataset import Problem

IVERILOG = shutil.which("iverilog") or "iverilog"
VVP = shutil.which("vvp") or "vvp"
# Same flags as the official VerilogEval Makefile.
COMPILE_FLAGS = ["-Wall", "-Winfloop", "-Wno-timescale", "-g2012"]
COMPILE_TIMEOUT_S = 30
SIM_TIMEOUT_S = 30

_MISMATCH_RE = re.compile(r"^Mismatches: (\d+) in (\d+) samples$")
_DUMP_RE = re.compile(r"^\s*\$dump(file|vars)\b.*$", re.MULTILINE)


@dataclass
class SimResult:
    passed: bool
    status: str  # pass | compile_error | sim_fail | timeout | no_result
    log: str
    mismatches: int | None = None
    samples: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, bool]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.returncode, p.stdout + p.stderr, False
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return -1, out, True


def _relativize(log: str, tmp: Path) -> str:
    return log.replace(str(tmp) + "/", "")


def hidden_eval(problem: Problem, design_src: str) -> SimResult:
    """Score a design against the benchmark's reference testbench.

    Pass/fail mirrors scripts/sv-iv-analyze in VerilogEval: any line containing
    "error" or a TIMEOUT is a failure; otherwise a "Mismatches: 0 in N samples"
    line is required.
    """
    with tempfile.TemporaryDirectory(prefix="vhidden_") as d:
        tmp = Path(d)
        (tmp / "design.sv").write_text(design_src)
        # Icarus 13 rejects the forward reference to tb_mismatch in the testbench's
        # $dumpvars (Icarus 12, which VerilogEval targets, accepted it). Waveforms
        # are not needed for scoring, so drop the dump calls.
        (tmp / "test.sv").write_text(_DUMP_RE.sub("", problem.test_path.read_text()))
        shutil.copy(problem.ref_path, tmp / "ref.sv")
        rc, clog, ctimeout = _run(
            [IVERILOG, *COMPILE_FLAGS, "-s", "tb", "-o", "sim", "design.sv", "test.sv", "ref.sv"],
            tmp, COMPILE_TIMEOUT_S,
        )
        clog = _relativize(clog, tmp)
        if ctimeout or rc != 0 or not (tmp / "sim").exists():
            return SimResult(False, "compile_error", clog)
        _, slog, stimeout = _run([VVP, "-n", "sim"], tmp, SIM_TIMEOUT_S)
        log = clog + _relativize(slog, tmp)

    if stimeout or "TIMEOUT" in log:
        return SimResult(False, "timeout", log + ("\nTIMEOUT" if stimeout else ""))
    mismatches = samples = None
    for line in log.splitlines():
        m = _MISMATCH_RE.match(line.strip())
        if m:
            mismatches, samples = int(m.group(1)), int(m.group(2))
    if any("error" in line for line in log.splitlines()):
        return SimResult(False, "compile_error", log, mismatches, samples)
    if mismatches is None:
        return SimResult(False, "no_result", log)
    if mismatches > 0:
        return SimResult(False, "sim_fail", log, mismatches, samples)
    return SimResult(True, "pass", log, mismatches, samples)


def compile_check(design_src: str) -> tuple[bool, str]:
    """Compile the design on its own (no testbench). Returns (ok, log)."""
    with tempfile.TemporaryDirectory(prefix="vcomp_") as d:
        tmp = Path(d)
        (tmp / "design.sv").write_text(design_src)
        rc, log, timeout = _run(
            [IVERILOG, *COMPILE_FLAGS, "-s", "TopModule", "-o", "sim", "design.sv"], tmp, COMPILE_TIMEOUT_S
        )
        log = _relativize(log, tmp)
    ok = not timeout and rc == 0 and "error" not in log.lower()
    return ok, log


def self_sim(design_src: str, tb_src: str, timeout: int = SIM_TIMEOUT_S) -> SimResult:
    """Run the agent's own testbench against its own design.

    The testbench is asked to print ``SELFTEST PASS`` or ``SELFTEST FAIL``.
    """
    with tempfile.TemporaryDirectory(prefix="vself_") as d:
        tmp = Path(d)
        (tmp / "design.sv").write_text(design_src)
        (tmp / "tb.sv").write_text(tb_src)
        rc, clog, ctimeout = _run([IVERILOG, *COMPILE_FLAGS, "-o", "sim", "design.sv", "tb.sv"], tmp, COMPILE_TIMEOUT_S)
        clog = _relativize(clog, tmp)
        if ctimeout or rc != 0 or not (tmp / "sim").exists():
            return SimResult(False, "compile_error", clog)
        _, slog, stimeout = _run([VVP, "-n", "sim"], tmp, timeout)
        log = clog + _relativize(slog, tmp)
    if stimeout:
        return SimResult(False, "timeout", log + f"\n[simulation killed after {timeout}s: missing $finish or infinite loop?]")
    if "SELFTEST FAIL" in log:
        return SimResult(False, "sim_fail", log)
    if "SELFTEST PASS" in log:
        return SimResult(True, "pass", log)
    return SimResult(False, "no_result", log + "\n[testbench printed neither SELFTEST PASS nor SELFTEST FAIL]")


def truncate(log: str, max_chars: int = 4000, max_lines: int = 60) -> str:
    """Keep feedback compact: the first lines carry the useful errors."""
    lines = [l for l in log.splitlines() if l.strip()]
    out = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        out += f"\n... ({len(lines) - max_lines} more lines omitted)"
    if len(out) > max_chars:
        out = out[:max_chars] + "\n... (truncated)"
    return out


def oracle_feedback(result: SimResult) -> str:
    """Summarise the hidden testbench result (shown to the agent only in oracle mode)."""
    if result.passed:
        return "Reference testbench: PASS (0 mismatches)."
    if result.status == "compile_error":
        return "Compilation against the reference testbench FAILED:\n" + truncate(result.log)
    if result.status == "timeout":
        return "Reference simulation TIMED OUT (likely a combinational loop or missing clocking)."
    hints = [l for l in result.log.splitlines() if l.startswith("Hint:") or l.startswith("Mismatches:")]
    return "Reference testbench: FAIL\n" + truncate("\n".join(hints) or result.log)
