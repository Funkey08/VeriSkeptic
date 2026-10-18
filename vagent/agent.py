"""The four experimental conditions.

A. baseline  one LLM call, extract the code.
B. compile   agent loop; write_design returns standalone compiler output.
C. self      agent loop; adds write_testbench + run_simulation so the agent
             can verify against its OWN testbench. Never sees the hidden one.
D. oracle    agent loop; write_design returns the HIDDEN reference testbench
             result. An upper bound, not an autonomous result.

B, C and D share one loop and one system prompt and differ only in the tools'
feedback, so differences between them isolate the value of the feedback signal.
Every design version is also scored against the hidden testbench for logging
(pass rate vs. attempt), but that score is only returned to the model in D.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dataset import Problem
from .harness import compile_check, hidden_eval, oracle_feedback, self_sim, truncate
from .llm import LLM, Usage

MODES = ("baseline", "compile", "self", "oracle")

SYSTEM = """You are an expert digital design engineer writing synthesizable SystemVerilog.
Your code is compiled with Icarus Verilog: iverilog -Wall -Winfloop -Wno-timescale -g2012.
The design must define a module named TopModule whose ports match the specification exactly
(same names, directions and widths). Do not include a testbench in the design source."""

BASELINE_INSTRUCTIONS = """Write the complete SystemVerilog implementation of TopModule.
Respond with a single ```verilog code block containing the full module and nothing else."""

DESIGN_TOOL = {
    "name": "write_design",
    "description": (
        "Save the complete SystemVerilog source for TopModule (plus any helper modules), replacing "
        "the previous version. This is what gets graded. Do not include a testbench here."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Complete SystemVerilog source."}},
        "required": ["code"],
        "additionalProperties": False,
    },
}
TESTBENCH_TOOL = {
    "name": "write_testbench",
    "description": (
        "Save a self-checking SystemVerilog testbench for TopModule, replacing the previous one. "
        "It must instantiate TopModule, check outputs against expected values derived from the "
        "specification, print exactly 'SELFTEST PASS' if all checks pass or 'SELFTEST FAIL: <details>' "
        "otherwise, and then call $finish. Its top module must not be named TopModule."
    ),
    "input_schema": DESIGN_TOOL["input_schema"],
}
SIM_TOOL = {
    "name": "run_simulation",
    "description": "Compile the current design with the current testbench and run it. Returns the simulation output.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}
SUBMIT_TOOL = {
    "name": "submit",
    "description": "Finish. The most recently saved design is submitted for grading.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

FEEDBACK_DESCRIPTION = {
    "compile": "write_design compiles your design on its own and returns the compiler output.",
    "self": (
        "write_design compiles your design on its own and returns the compiler output. "
        "You can also write your own self-checking testbench (write_testbench) and run it "
        "(run_simulation). Hidden tests will grade the design; you cannot see them, so verify "
        "the behaviour yourself. Derive expected values independently from the specification "
        "(e.g. a behavioural model or hand-computed vectors), not by copying your design's logic, "
        "and cover reset, edge cases and multi-cycle sequences for clocked designs."
    ),
    "oracle": "write_design runs your design against the grader's reference testbench and returns the result.",
}


def agent_instructions(mode: str, max_attempts: int) -> str:
    return (
        f"Implement TopModule using the tools. {FEEDBACK_DESCRIPTION[mode]}\n"
        f"You may call write_design at most {max_attempts} times. When you are confident the design "
        "is correct, call submit."
    )


@dataclass
class Outcome:
    problem: str
    mode: str
    sample: int
    passed: bool = False
    status: str = "no_design"
    final_design: str | None = None
    versions: list[dict] = field(default_factory=list)  # hidden result per design version
    self_test_final: bool | None = None  # did the agent's own testbench pass on the submitted design?
    end_reason: str = ""
    turns: int = 0
    usage: Usage = field(default_factory=Usage)
    messages: list[dict] = field(default_factory=list)


_CODE_RE = re.compile(r"```(?:verilog|systemverilog|sv)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_verilog(text: str) -> str | None:
    blocks = _CODE_RE.findall(text)
    for b in reversed(blocks):
        if "module TopModule" in b:
            return b.strip() + "\n"
    if blocks:
        return blocks[-1].strip() + "\n"
    if "module" in text and "endmodule" in text:
        return text[text.index("module") : text.rindex("endmodule") + len("endmodule")] + "\n"
    return None


def _record_version(out: Outcome, problem: Problem, code: str) -> dict:
    hidden = hidden_eval(problem, code)
    out.versions.append({"passed": hidden.passed, "status": hidden.status, "mismatches": hidden.mismatches})
    return hidden


def _finalize(out: Outcome, problem: Problem) -> Outcome:
    if out.final_design is not None:
        last = out.versions[-1]
        out.passed, out.status = last["passed"], last["status"]
    return out


def run_baseline(problem: Problem, llm: LLM, sample: int = 0) -> Outcome:
    out = Outcome(problem.id, "baseline", sample)
    messages = [{"role": "user", "content": f"{problem.spec}\n\n{BASELINE_INSTRUCTIONS}"}]
    reply = llm.call(system=SYSTEM, messages=messages, sample=sample)
    out.usage.add(reply.usage)
    out.turns = 1
    out.messages = messages + [{"role": "assistant", "content": reply.content}]
    out.end_reason = reply.stop_reason
    code = extract_verilog(reply.text)
    if code:
        out.final_design = code
        _record_version(out, problem, code)
    return _finalize(out, problem)


def run_agent(
    problem: Problem, llm: LLM, mode: str, sample: int = 0, max_attempts: int = 5, max_turns: int = 20
) -> Outcome:
    assert mode in ("compile", "self", "oracle")
    tools = [DESIGN_TOOL] + ([TESTBENCH_TOOL, SIM_TOOL] if mode == "self" else []) + [SUBMIT_TOOL]
    out = Outcome(problem.id, mode, sample)
    messages: list[dict] = [
        {"role": "user", "content": f"{problem.spec}\n\n{agent_instructions(mode, max_attempts)}"}
    ]
    testbench: str | None = None
    last_sim: tuple[int, int, bool] | None = None  # (design_version, tb_version, passed)
    tb_version = 0
    nudged = False

    for turn in range(max_turns):
        reply = llm.call(system=SYSTEM, messages=messages, tools=tools, sample=sample)
        out.usage.add(reply.usage)
        out.turns = turn + 1
        messages.append({"role": "assistant", "content": reply.content})

        if reply.stop_reason == "refusal":
            out.end_reason = "refusal"
            break
        if not reply.tool_uses:
            if out.final_design is not None or nudged:
                out.end_reason = "end_turn"
                break
            nudged = True
            messages.append({"role": "user", "content": "Use the write_design tool to save your design, then call submit."})
            continue

        results: list[dict] = []
        submitted = False
        for tu in reply.tool_uses:
            name, args = tu["name"], tu.get("input") or {}
            is_error = False
            if name == "submit":
                submitted = True
                text = "Submitted."
            elif name in ("write_design", "write_testbench") and not isinstance(args.get("code"), str):
                text, is_error = "Missing required string argument 'code'.", True
            elif name == "write_design":
                if len(out.versions) >= max_attempts:
                    text, is_error = f"Attempt budget exhausted ({max_attempts} designs). Call submit.", True
                else:
                    code = args["code"]
                    out.final_design = code
                    hidden = _record_version(out, problem, code)
                    v = len(out.versions)
                    if mode == "oracle":
                        text = f"Design v{v} saved.\n{oracle_feedback(hidden)}"
                    else:
                        ok, log = compile_check(code)
                        status = "compiled cleanly" if ok and not log.strip() else ("compiled with warnings" if ok else "FAILED to compile")
                        text = f"Design v{v} saved; {status}." + (f"\n{truncate(log)}" if log.strip() else "")
                    text += f" ({max_attempts - v} design writes left.)"
            elif name == "write_testbench":
                testbench = args["code"]
                tb_version += 1
                text = f"Testbench v{tb_version} saved. Use run_simulation to run it."
            elif name == "run_simulation":
                if out.final_design is None or testbench is None:
                    text, is_error = "Save both a design and a testbench first.", True
                else:
                    sim = self_sim(out.final_design, testbench)
                    last_sim = (len(out.versions), tb_version, sim.passed)
                    text = f"[{sim.status}]\n{truncate(sim.log)}"
            else:
                text, is_error = f"Unknown tool {name!r}.", True
            result = {"type": "tool_result", "tool_use_id": tu["id"], "content": text}
            if is_error:
                result["is_error"] = True
            results.append(result)

        if submitted:
            out.end_reason = "submit"
            break
        messages.append({"role": "user", "content": results})
    else:
        out.end_reason = "max_turns"

    if mode == "self":
        # Self-confidence only counts if the latest testbench passed on the exact design submitted.
        out.self_test_final = bool(
            last_sim and last_sim[0] == len(out.versions) and last_sim[1] == tb_version and last_sim[2]
        )
    out.messages = messages
    return _finalize(out, problem)


def run(problem: Problem, llm: LLM, mode: str, sample: int = 0, max_attempts: int = 5) -> Outcome:
    if mode == "baseline":
        return run_baseline(problem, llm, sample)
    return run_agent(problem, llm, mode, sample, max_attempts=max_attempts)
