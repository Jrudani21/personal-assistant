"""Offline tests for assistant/local_llm.py.

Deliberately no network: these cover the two bugs that actually bit during the
Ollama -> LM Studio migration, both of which are pure logic.

1. A module-level `list` shadowed the builtin inside the module, so
   `list(embedding)` inside embeddings() raised
   "TypeError: list() takes 0 positional arguments but 1 was given".
2. The app builds tool history in Ollama's shape, which the OpenAI endpoint
   rejects on the SECOND round of a tool loop ("Invalid 'messages' in payload").
"""
import json

from assistant import local_llm


def test_module_does_not_shadow_the_list_builtin():
    """A bare `list` at module level breaks every list(...) call in the file."""
    assert not hasattr(local_llm, "list"), (
        "local_llm must not define `list` — it shadows the builtin for the whole "
        "module (regression: embeddings() raised TypeError)"
    )
    assert callable(local_llm.list_models)


def test_openai_messages_translate_ollama_tool_history():
    """Ollama shape -> OpenAI shape: id added, arguments stringified,
    tool result keyed by tool_call_id instead of name."""
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "what is 17*23?"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "calculator",
                                      "arguments": {"expression": "17 * 23"}}}]},
        {"role": "tool", "content": "391", "name": "calculator"},
    ]
    out = local_llm._to_openai_messages(history)

    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assistant = out[2]
    call = assistant["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "calculator"
    # arguments must be a JSON STRING on the OpenAI path, not a dict
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {"expression": "17 * 23"}
    # the tool result must carry the id of the call it answers
    assert out[3]["tool_call_id"] == call["id"]
    assert "name" not in out[3]


def test_openai_messages_match_tool_results_by_name_not_position():
    """Results can return in any order. A positional match attaches a result to the
    WRONG call, and the server accepts that silently (measured: reverse-order and
    cross-round mis-pairs both answered HTTP 200)."""
    history = [
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"function": {"name": "a", "arguments": {"x": 1}}},
             {"function": {"name": "b", "arguments": {"y": 2}}},
         ]},
        {"role": "tool", "content": "RESULT_B", "name": "b"},   # reversed order
        {"role": "tool", "content": "RESULT_A", "name": "a"},
    ]
    out = local_llm._to_openai_messages(history)
    ids = {c["function"]["name"]: c["id"] for c in out[0]["tool_calls"]}
    assert out[1]["tool_call_id"] == ids["b"] and out[1]["content"] == "RESULT_B"
    assert out[2]["tool_call_id"] == ids["a"] and out[2]["content"] == "RESULT_A"


def test_openai_messages_clear_unanswered_ids_on_a_new_tool_round():
    """A second tool-call turn invalidates ids the first round never answered, so a
    stale id can't be paired with the new round's result."""
    history = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "a", "arguments": {}}},
                        {"function": {"name": "b", "arguments": {}}}]},
        {"role": "tool", "content": "A", "name": "a"},           # b never answered
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "c", "arguments": {}}}]},
        {"role": "tool", "content": "C", "name": "c"},
    ]
    out = local_llm._to_openai_messages(history)
    round2_id = out[2]["tool_calls"][0]["id"]
    assert out[3]["tool_call_id"] == round2_id
    assert out[3]["content"] == "C"


def test_openai_messages_keeps_plain_turns_untouched():
    history = [{"role": "user", "content": "hello"},
               {"role": "assistant", "content": "hi"}]
    assert local_llm._to_openai_messages(history) == history


def test_openai_messages_survives_a_tool_result_with_no_pending_call():
    """Defensive: a stray tool message must not raise (KeyError/IndexError)."""
    out = local_llm._to_openai_messages([{"role": "tool", "content": "orphan"}])
    assert out[0]["role"] == "tool"
    assert out[0]["tool_call_id"]


def test_lmstudio_base_always_has_the_v1_suffix(monkeypatch):
    """The OpenAI client appends only the route, not /v1, so the base must carry it."""
    monkeypatch.setattr(local_llm._config, "get",
                        lambda key, default=None: "http://127.0.0.1:1234"
                        if key == "lmstudio_base_url" else default)
    assert local_llm.lmstudio_base() == "http://127.0.0.1:1234/v1"
