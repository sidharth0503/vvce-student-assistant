import httpx
import pytest
from fastapi import HTTPException

import main


@pytest.fixture(autouse=True)
def provider_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")


class FakeClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def groq_response(text="Groq answer."):
    return type("Response", (), {"choices": [type("Choice", (), {
        "message": type("Message", (), {"content": text})()
    })()]})()


def api_error(cls, status, message="test failure"):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body={})


def test_groq_success_uses_expected_model_and_shared_grounded_prompt(monkeypatch):
    client = FakeClient([groq_response()])
    client_config = {}
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client_config.update(kwargs) or client)
    evidence = [{"title": "Calendar", "url": "https://vvce.ac.in/calendar", "date": "2026", "text": "Calendar facts."}]

    assert main.generate_answer("When is the event?", evidence) == "Groq answer."
    call = client.calls[0]
    assert call["model"] == "openai/gpt-oss-20b"
    assert call["max_completion_tokens"] == 700
    assert "max_tokens" not in call
    prompt = call["messages"][0]["content"]
    assert "Calendar facts." in prompt
    assert "https://vvce.ac.in/calendar" in prompt
    assert "Cite factual claims with Markdown links" in prompt
    assert client_config["timeout"] == 90
    assert client_config["max_retries"] == 0
    assert client_config["base_url"] == "https://api.groq.com/openai/v1"


def test_generated_citations_are_limited_to_retrieved_evidence(monkeypatch):
    client = FakeClient([groq_response(
        "Verified [VVCE source](https://vvce.ac.in/calendar/). "
        "Unsupported [other source](https://example.com/fabricated).")])
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client)
    evidence = [{"title": "Calendar", "url": "https://vvce.ac.in/calendar", "text": "Verified facts."}]

    answer = main.generate_answer("When?", evidence)

    assert "[VVCE source](https://vvce.ac.in/calendar/)" in answer
    assert "[other source]" not in answer
    assert "https://example.com/fabricated" not in answer


def test_grounded_prompt_uses_user_context_but_not_prior_assistant_claims():
    prompt = main._build_grounded_prompt("What about its clubs?", [], [
        {"role": "user", "content": "Tell me about ISE."},
        {"role": "assistant", "content": "Unsupported claim from an earlier answer."},
    ])

    assert "Tell me about ISE." in prompt
    assert "Unsupported claim from an earlier answer." not in prompt


def test_groq_timeout_is_configurable_and_exhaustion_uses_gemini(monkeypatch):
    monkeypatch.setenv("GROQ_TIMEOUT_SECONDS", "24")
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    timeout = main.APITimeoutError(request=httpx.Request("POST", "https://api.groq.com"))
    client = FakeClient([timeout for _ in range(main.GEMINI_MAX_RETRIES + 1)])
    config = {}
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: config.update(kwargs) or client)
    monkeypatch.setattr(main, "gemini_answer", lambda *_args: "Gemini fallback.")

    assert main.generate_answer("Question?", []) == "Gemini fallback."
    assert config["timeout"] == 24
    assert len(client.calls) == main.GEMINI_MAX_RETRIES + 1


def test_groq_timeout_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    client = FakeClient([main.APITimeoutError(request=httpx.Request("POST", "https://api.groq.com")), groq_response()])
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client)

    assert main.generate_answer("Question?", []) == "Groq answer."
    assert len(client.calls) == 2


def test_groq_rate_limit_exhaustion_falls_back_to_gemini(monkeypatch, caplog):
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    client = FakeClient([api_error(main.RateLimitError, 429) for _ in range(main.GEMINI_MAX_RETRIES + 1)])
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client)
    fallback = []
    monkeypatch.setattr(main, "gemini_answer", lambda question, evidence, *_history: fallback.append((question, evidence)) or "Gemini answer.")

    answer = main.generate_answer("Question?", [{"title": "Source", "url": "https://vvce.ac.in", "text": "Evidence"}])

    assert answer == "Gemini answer."
    assert len(client.calls) == main.GEMINI_MAX_RETRIES + 1
    assert fallback[0][0] == "Question?"
    assert "fallback provider=gemini" in caplog.text


def test_groq_rate_limit_honors_retry_after_header(monkeypatch):
    error = api_error(main.RateLimitError, 429)
    error.response.headers["retry-after"] = "4"
    client = FakeClient([error, groq_response()])
    sleeps = []
    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client)
    assert main.groq_answer("brief") == "Groq answer."
    assert len(client.calls) == 2
    assert len(sleeps) == 1 and 4 <= sleeps[0] < 4.25


def test_groq_rate_limit_uses_reset_header_and_defers_long_wait_to_fallback(monkeypatch):
    error = api_error(main.RateLimitError, 429)
    error.response.headers["x-ratelimit-reset-tokens"] = "8.5s"
    monkeypatch.setenv("GROQ_MAX_RETRY_WAIT_SECONDS", "2")
    client = FakeClient([error])
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(main, "gemini_answer", lambda *_args: "Gemini fallback.")
    assert main.generate_answer("Question?", []) == "Gemini fallback."
    assert len(client.calls) == 1


def test_generation_context_bounds_and_deduplicates_evidence_and_history(monkeypatch):
    monkeypatch.setenv("GROQ_EVIDENCE_TOKEN_BUDGET", "250")
    monkeypatch.setenv("GROQ_HISTORY_TOKEN_BUDGET", "50")
    source = {"title": "Primary", "url": "https://vvce.ac.in/a", "date": "2026",
              "text": ("VVCE college source facts and details. " * 40)}
    duplicate = dict(source)
    second = {"title": "Secondary", "url": "https://vvce.ac.in/b", "text": "Other evidence. " * 30}
    evidence, history, prompt = main.prepare_generation_context(
        "Question", [source, duplicate, second],
        [{"role": "user", "content": "Earlier context " * 40}, {"role": "assistant", "content": "Short follow-up"}])
    assert len(evidence) <= 2
    assert all(item.get("url") and item.get("title") for item in evidence)
    assert main._estimated_tokens("\n".join(item["text"] for item in evidence)) <= 250
    assert len(history) <= 1
    assert main._estimated_tokens(prompt) <= 3000


@pytest.mark.parametrize(("error_type", "status", "expected"), [
    (main.AuthenticationError, 401, "Groq authentication failed"),
    (main.NotFoundError, 404, "Groq model 'openai/gpt-oss-20b' was not found"),
])
def test_permanent_groq_errors_are_reported_without_gemini_fallback(monkeypatch, error_type, status, expected):
    client = FakeClient([api_error(error_type, status)])
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(main, "gemini_answer", lambda *_args: pytest.fail("permanent errors must not fall back"))

    with pytest.raises(HTTPException) as error:
        main.generate_answer("Question?", [])
    assert expected in error.value.detail
    assert len(client.calls) == 1


def test_malformed_groq_response_is_reported_without_fallback(monkeypatch):
    client = FakeClient([type("Response", (), {"choices": []})()])
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(main, "gemini_answer", lambda *_args: pytest.fail("malformed response is not a transient provider failure"))

    with pytest.raises(HTTPException) as error:
        main.generate_answer("Question?", [])
    assert error.value.status_code == 502
    assert "malformed response" in error.value.detail


def test_groq_400_logs_sanitized_provider_diagnostics_without_retry(monkeypatch, caplog):
    body = {"error": {"type": "invalid_request_error", "code": "bad_parameter",
                      "message": "max_tokens is deprecated; use max_completion_tokens"}}
    client = FakeClient([api_error(main.APIStatusError, 400)])
    # Replace the helper-created empty body with a representative provider response body.
    client.outputs[0].body = body
    monkeypatch.setattr(main, "OpenAI", lambda **kwargs: client)

    with pytest.raises(HTTPException) as error:
        main.groq_answer("PRIVATE USER QUESTION AND EVIDENCE")

    assert len(client.calls) == 1
    assert "HTTP 400" in error.value.detail
    assert "bad_parameter" in error.value.detail
    assert "max_completion_tokens" in error.value.detail
    assert "PRIVATE USER QUESTION" not in caplog.text
    assert "test-groq-key" not in caplog.text
    assert "provider_type=invalid_request_error" in caplog.text


def test_gemini_remains_available_as_direct_compatible_provider(monkeypatch):
    monkeypatch.setattr(main.requests, "post", lambda *_args, **_kwargs: type("Response", (), {
        "status_code": 200, "json": lambda _self: {"candidates": [{"content": {"parts": [{"text": "Gemini answer."}]}}]},
        "close": lambda _self: None,
    })())
    assert main.gemini_answer("Question?", [{"title": "Source", "url": "https://vvce.ac.in", "text": "Evidence"}]) == "Gemini answer."
