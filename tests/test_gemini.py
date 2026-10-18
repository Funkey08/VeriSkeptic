"""Offline tests for the Gemini <-> agent message translation (no API calls)."""

from google.genai import types

from vagent.agent import DESIGN_TOOL, SUBMIT_TOOL
from vagent.llm import GeminiLLM

SIG = b"\x01\x02opaque-signature"


def fake_response(parts, finish="STOP"):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1000, cached_content_token_count=600, candidates_token_count=50, thoughts_token_count=200
        ),
    )


def test_response_to_blocks_and_usage():
    resp = fake_response([
        types.Part(text="thinking...", thought=True),
        types.Part(text="Writing the design."),
        types.Part(function_call=types.FunctionCall(name="write_design", args={"code": "module TopModule; endmodule"}),
                   thought_signature=SIG),
    ])
    data = GeminiLLM.from_response(resp, turn_index=0)
    kinds = [b["type"] for b in data["content"]]
    assert kinds == ["text", "tool_use", "provider_raw"]  # thought text is not surfaced as answer text
    assert data["content"][0]["text"] == "Writing the design."
    assert data["content"][1]["name"] == "write_design" and data["content"][1]["id"] == "call_0_2"
    assert data["stop_reason"] == "tool_use"
    assert data["usage"] == {"input_tokens": 400, "output_tokens": 250, "cache_read_tokens": 600, "cache_write_tokens": 0}


def test_history_roundtrip_preserves_signature_and_matches_names():
    data = GeminiLLM.from_response(fake_response([
        types.Part(function_call=types.FunctionCall(name="write_design", args={"code": "x"}), thought_signature=SIG),
    ]), turn_index=0)
    call_id = data["content"][0]["id"]
    messages = [
        {"role": "user", "content": "spec"},
        {"role": "assistant", "content": data["content"]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "compiled", "is_error": False}]},
    ]
    contents = GeminiLLM.to_contents(messages)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].thought_signature == SIG
    fr = contents[2].parts[0].function_response
    assert fr.name == "write_design" and fr.response == {"output": "compiled"}


def test_error_results_and_refusals():
    data = GeminiLLM.from_response(fake_response([types.Part(function_call=types.FunctionCall(name="submit", args={}))]), 0)
    msgs = [{"role": "user", "content": "s"}, {"role": "assistant", "content": data["content"]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": data["content"][0]["id"],
                                          "content": "bad", "is_error": True}]}]
    assert GeminiLLM.to_contents(msgs)[2].parts[0].function_response.response == {"error": "bad"}
    blocked = GeminiLLM.from_response(fake_response([], finish="SAFETY"), 0)
    assert blocked["stop_reason"] == "refusal" and blocked["content"] == []


def test_tool_declarations():
    decls = GeminiLLM.to_declarations([DESIGN_TOOL, SUBMIT_TOOL])
    assert [d.name for d in decls] == ["write_design", "submit"]
    assert "additionalProperties" not in decls[0].parameters_json_schema
    assert decls[0].parameters_json_schema["required"] == ["code"]
