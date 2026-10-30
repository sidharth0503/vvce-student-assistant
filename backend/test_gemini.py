import logging

import pytest
import requests
from fastapi import HTTPException

import main


class GeminiResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "candidates": [{"content": {"parts": [{"text": "Grounded answer."}]}}]
        }
        self.closed = False

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-api-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")


def test_timeout_configuration_defaults_to_90_and_is_overridable(monkeypatch):
    monkeypatch.delenv("GEMINI_TIMEOUT_SECONDS", raising=False)
    assert main._gemini_timeout_seconds() == 90
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "135")
    assert main._gemini_timeout_seconds() == 135


def test_connection_error_retries_then_succeeds(monkeypatch):
    calls = []
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)

    def post(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.ConnectionError("temporary disconnect")
        return GeminiResponse()

    monkeypatch.setattr(main.requests, "post", post)

    answer = main.gemini_answer("What is VVCE?", [{"title": "VVCE", "url": "https://vvce.ac.in", "text": "VVCE college details."}])

    assert answer == "Grounded answer."
    assert len(calls) == 2
    assert calls[0]["timeout"] == 90


def test_read_timeout_retries_exhaustively_and_returns_sanitized_timeout(monkeypatch, caplog):
    calls = []
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "17")
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)

    def post(*_args, **kwargs):
        calls.append(kwargs)
        raise requests.ReadTimeout("request failed https://example.test/?key=test-secret-api-key")

    monkeypatch.setattr(main.requests, "post", post)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as error:
            main.gemini_answer("Question?", [{"title": "source", "url": "https://vvce.ac.in", "text": "Evidence."}])

    assert len(calls) == main.GEMINI_MAX_RETRIES + 1
    assert all(call["timeout"] == 17 for call in calls)
    assert error.value.status_code == 504
    assert "17 seconds" in error.value.detail
    assert "test-secret-api-key" not in error.value.detail
    assert "test-secret-api-key" not in caplog.text
    assert "model=gemini-test-model" in caplog.text
    assert "elapsed=" in caplog.text


def test_transient_http_retries_then_reports_service_unavailable(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)
    monkeypatch.setattr(main.requests, "post", lambda *_args, **_kwargs: calls.append(True) or GeminiResponse(503, {"error": {"status": "UNAVAILABLE"}}))

    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as error:
            main.gemini_answer("Question?", [{"title": "source", "url": "https://vvce.ac.in", "text": "Evidence."}])

    assert len(calls) == main.GEMINI_MAX_RETRIES + 1
    assert error.value.status_code == 503
    assert error.value.detail == "Gemini is temporarily unavailable. Please try again shortly."
    assert "status=503" in caplog.text


@pytest.mark.parametrize(("status", "payload", "expected_status", "expected_text"), [
    (400, {"error": {"status": "INVALID_ARGUMENT"}}, 502, "Gemini rejected the request (HTTP 400)"),
    (401, {"error": {"status": "UNAUTHENTICATED"}}, 502, "Gemini authentication failed"),
    (404, {"error": {"status": "NOT_FOUND"}}, 502, "Gemini model 'gemini-test-model' was not found"),
])
def test_permanent_http_errors_are_not_retried(monkeypatch, status, payload, expected_status, expected_text):
    calls = []
    monkeypatch.setattr(main.requests, "post", lambda *_args, **_kwargs: calls.append(True) or GeminiResponse(status, payload))

    with pytest.raises(HTTPException) as error:
        main.gemini_answer("Question?", [{"title": "source", "url": "https://vvce.ac.in", "text": "Evidence."}])

    assert len(calls) == 1
    assert error.value.status_code == expected_status
    assert expected_text in error.value.detail


def test_quota_429_retries_then_returns_quota_message(monkeypatch):
    calls = []
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)
    monkeypatch.setattr(main.requests, "post", lambda *_args, **_kwargs: calls.append(True) or GeminiResponse(429, {"error": {"status": "RESOURCE_EXHAUSTED"}}))

    with pytest.raises(HTTPException) as error:
        main.gemini_answer("Question?", [{"title": "source", "url": "https://vvce.ac.in", "text": "Evidence."}])

    assert len(calls) == main.GEMINI_MAX_RETRIES + 1
    assert error.value.status_code == 429
    assert "quota or rate limit" in error.value.detail


@pytest.mark.parametrize("payload", [
    [],
    {"candidates": "invalid"},
    {"candidates": [{"content": {"parts": "invalid"}}]},
])
def test_malformed_success_responses_return_sanitized_api_error(monkeypatch, payload):
    monkeypatch.setattr(main.requests, "post", lambda *_args, **_kwargs: GeminiResponse(200, payload))

    with pytest.raises(HTTPException) as error:
        main.gemini_answer("Question?", [{"title": "source", "url": "https://vvce.ac.in", "text": "Evidence."}])

    assert error.value.status_code == 502
    assert "malformed response" in error.value.detail


def test_blocked_response_returns_a_helpful_answer(monkeypatch):
    monkeypatch.setattr(main.requests, "post", lambda *_args, **_kwargs: GeminiResponse(200, {
        "promptFeedback": {"blockReason": "SAFETY"}, "candidates": []
    }))

    answer = main.gemini_answer("Question?", [{"title": "source", "url": "https://vvce.ac.in", "text": "Evidence."}])

    assert "blocked by a safety filter" in answer
