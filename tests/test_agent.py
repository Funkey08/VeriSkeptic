"""Exercise the agent loop end-to-end with a scripted fake LLM (no API calls)."""

from vagent.agent import extract_verilog, run
from vagent.dataset import load_problems
from vagent.llm import Reply, Usage

PROBLEM = load_problems(ids=["Prob003"])[0]
GOOD = "module TopModule(output one);\n  assign one = 1'b1;\nendmodule\n"
BAD = "module TopModule(output one);\n  assign one = 1'b0;\nendmodule\n"
TB = """module t;
  wire one;
  TopModule dut(.one(one));
  initial begin
    #1 if (one === 1'b1) $display("SELFTEST PASS"); else $display("SELFTEST FAIL: one=%b", one);
    $finish;
  end
endmodule
"""


def tool(name, **inp):
    tool.n = getattr(tool, "n", 0) + 1
    return {"type": "tool_use", "id": f"tu_{tool.n}", "name": name, "input": inp}


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def call(self, *, system, messages, tools=None, sample=0):
        self.seen.append([dict(m) for m in messages])
        return Reply(content=self.script.pop(0), stop_reason="tool_use", usage=Usage(calls=1))


def test_extract_verilog():
    assert extract_verilog(f"Here:\n```verilog\n{GOOD}```") == GOOD
    assert extract_verilog("no code") is None


def test_baseline():
    llm = FakeLLM([[{"type": "text", "text": f"```verilog\n{GOOD}```"}]])
    out = run(PROBLEM, llm, "baseline")
    assert out.passed and out.versions == [{"passed": True, "status": "pass", "mismatches": 0}]


def test_self_mode_fixes_bug_and_tracks_confidence():
    llm = FakeLLM([
        [tool("write_design", code=BAD), tool("write_testbench", code=TB)],
        [tool("run_simulation")],
        [tool("write_design", code=GOOD)],
        [tool("run_simulation")],
        [tool("submit")],
    ])
    out = run(PROBLEM, llm, "self")
    assert out.passed and out.end_reason == "submit"
    assert [v["passed"] for v in out.versions] == [False, True]
    assert out.self_test_final is True
    # The agent must never see hidden-testbench output in self mode.
    feedback = str(llm.seen[-1])
    assert "Mismatches" not in feedback and "Reference testbench" not in feedback
    assert "SELFTEST FAIL" in feedback


def test_self_confidence_requires_sim_on_final_design():
    llm = FakeLLM([
        [tool("write_design", code=GOOD), tool("write_testbench", code=TB)],
        [tool("run_simulation")],
        [tool("write_design", code=BAD)],
        [tool("submit")],
    ])
    out = run(PROBLEM, llm, "self")
    assert not out.passed and out.self_test_final is False


def test_oracle_mode_sees_reference_result():
    llm = FakeLLM([[tool("write_design", code=BAD)], [tool("write_design", code=GOOD)], [tool("submit")]])
    out = run(PROBLEM, llm, "oracle")
    assert out.passed
    assert "Reference testbench: FAIL" in str(llm.seen[1])


def test_attempt_budget_enforced():
    llm = FakeLLM([[tool("write_design", code=BAD)]] * 3 + [[tool("write_design", code=GOOD)], [tool("submit")]])
    out = run(PROBLEM, llm, "compile", max_attempts=3)
    assert len(out.versions) == 3 and not out.passed
