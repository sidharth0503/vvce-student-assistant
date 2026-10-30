import builtins

import chat_cli


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.reason = "test response"

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("unexpected status")

    def json(self):
        return self.payload


def test_cli_help_and_exit(monkeypatch, capsys):
    answers = iter(["/help", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    chat_cli.main()

    output = capsys.readouterr().out
    assert "/sources" in output
    assert "Goodbye." in output


def test_cli_chat_and_sources_command(monkeypatch, capsys):
    answers = iter(["When is the calendar?", "/sources", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    monkeypatch.setattr(chat_cli.requests, "post", lambda *args, **kwargs: FakeResponse({
        "answer": "See the [academic calendar](https://vvce.ac.in/calendar).",
        "sources": [{"title": "Academic Calendar", "url": "https://vvce.ac.in/calendar", "source_type": "crawled"}],
    }))

    chat_cli.main()

    output = capsys.readouterr().out
    assert "Academic Calendar: https://vvce.ac.in/calendar" in output
    assert "Sources from the latest answer" in output


def test_cli_health_command_reports_readiness(monkeypatch, capsys):
    answers = iter(["/health", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    monkeypatch.setattr(chat_cli.requests, "get", lambda *args, **kwargs: FakeResponse({
        "status": "ok", "knowledge_base_ready": True, "indexed_chunks": 12,
        "manual_chunks": 4, "crawled_chunks": 8, "gemini_configured": True,
    }))

    chat_cli.main()

    output = capsys.readouterr().out
    assert "Backend: online | Knowledge base: ready" in output
    assert "Indexed chunks: 12" in output


def test_cli_sends_prior_turns_as_follow_up_context(monkeypatch):
    answers = iter(["What is ISE?", "What about its clubs?", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    calls = []
    monkeypatch.setattr(chat_cli.requests, "post", lambda *args, **kwargs: calls.append(kwargs) or FakeResponse({
        "answer": "ISE means Information Science and Engineering.", "sources": []
    }))

    chat_cli.main()

    second_request = calls[1]["json"]
    assert second_request["message"] == "What about its clubs?"
    assert second_request["history"] == [
        {"role": "user", "content": "What is ISE?"},
        {"role": "assistant", "content": "ISE means Information Science and Engineering."},
    ]
