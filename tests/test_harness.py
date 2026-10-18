"""The harness must score every reference solution as passing, and catch obvious failures."""

import pytest

from vagent.dataset import EXCLUDED, load_problems
from vagent.harness import compile_check, hidden_eval, self_sim

PROBLEMS = load_problems()
BROKEN = [p for p in load_problems(include_excluded=True) if p.id in EXCLUDED]


def as_top(problem) -> str:
    return problem.ref_path.read_text().replace("module RefModule", "module TopModule")


def test_dataset_size():
    assert len(PROBLEMS) == 156 - len(EXCLUDED) == 152


@pytest.mark.parametrize("problem", PROBLEMS, ids=lambda p: p.id)
def test_reference_solution_passes(problem):
    result = hidden_eval(problem, as_top(problem))
    assert result.passed, result.log[-2000:]


@pytest.mark.parametrize("problem", BROKEN, ids=lambda p: p.id)
def test_excluded_problems_are_really_broken(problem):
    assert not hidden_eval(problem, as_top(problem)).passed


def test_wrong_design_fails():
    p = next(p for p in PROBLEMS if p.id == "Prob003_step_one")
    r = hidden_eval(p, "module TopModule(output one);\n  assign one = 1'b0;\nendmodule\n")
    assert not r.passed and r.status == "sim_fail" and r.mismatches > 0


def test_syntax_error_detected():
    p = next(p for p in PROBLEMS if p.id == "Prob003_step_one")
    bad = "module TopModule(output one)\n  assign one = 1;\nendmodule\n"
    assert hidden_eval(p, bad).status == "compile_error"
    ok, log = compile_check(bad)
    assert not ok and "error" in log.lower()


def test_self_sim_roundtrip():
    design = "module TopModule(input a, output y);\n  assign y = ~a;\nendmodule\n"
    tb = """module t;
  reg a; wire y;
  TopModule dut(.a(a), .y(y));
  initial begin
    a = 0; #1; if (y !== 1) begin $display("SELFTEST FAIL: y=%b", y); $finish; end
    a = 1; #1; if (y !== 0) begin $display("SELFTEST FAIL: y=%b", y); $finish; end
    $display("SELFTEST PASS"); $finish;
  end
endmodule
"""
    assert self_sim(design, tb).passed
    assert not self_sim(design.replace("~a", "a"), tb).passed
