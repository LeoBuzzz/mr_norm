from __future__ import annotations

from unittest.mock import patch

from mr_norm.runtime.contracts import RuntimeRequest
from mr_norm.runtime.dialog_memory import DialogContext, DialogTurn
from mr_norm.runtime.llm_providers import build_final_answer_llm_provider


def test_final_answer_provider_includes_dialog_turns() -> None:
    captured: dict[str, object] = {}

    def fake_chat_json_with_model_fallback(*args, **kwargs):
        captured["user_payload"] = kwargs["user_payload"]
        return {"answer": "ok", "citations": []}

    provider = build_final_answer_llm_provider(
        "polza",
        ["test-model"],
        temperature=0.0,
        max_tokens=100,
    )
    context = DialogContext(
        session_key="1:2",
        turns=(DialogTurn(user_text="первый", answer="ответ"),),
    )
    request = RuntimeRequest(
        query="первый как часто?",
        user_query="как часто?",
        dialog_context=context,
    )

    with patch("mr_norm.runtime.llm_providers.chat_json_with_model_fallback", fake_chat_json_with_model_fallback):
        provider(request, [], {"prompt": "p", "output_contract": {}})

    payload = captured["user_payload"]
    assert payload["query"] == "как часто?"
    assert payload["retrieval_query"] == "первый как часто?"
    assert payload["dialog_turns"][0]["user"] == "первый"
