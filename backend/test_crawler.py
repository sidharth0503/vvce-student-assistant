from datetime import datetime, timezone

import crawler
import main
import pytest
import json
import requests
from fastapi import BackgroundTasks, HTTPException
from urllib.robotparser import RobotFileParser


class FakeResponse:
    def __init__(self, url, text="", headers=None, status_code=200):
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}
        self.status_code = status_code
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    @property
    def is_permanent_redirect(self):
        return self.status_code in (301, 308)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size=64 * 1024):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


def test_crawl_authorization_is_opt_in(monkeypatch):
    monkeypatch.delenv("VVCE_CRAWL_AUTHORIZED", raising=False)
    assert crawler.crawl_authorized() is False
    assert crawler.crawl_authorized("true") is True
    assert crawler.crawl_authorized(" TRUE ") is True
    assert crawler.crawl_authorized("yes") is False


def test_api_crawl_requires_explicit_authorization(monkeypatch):
    monkeypatch.setenv("VVCE_CRAWL_AUTHORIZED", "false")
    with pytest.raises(HTTPException) as error:
        main.start_rebuild(BackgroundTasks())
    assert error.value.status_code == 403


def test_crawler_restricts_domain_and_canonicalizes_duplicates():
    assert crawler.is_vvce_url("https://vvce.ac.in/admissions")
    assert crawler.is_vvce_url("https://www.vvce.ac.in/admissions")
    assert not crawler.is_vvce_url("https://vvce.ac.in.example.com/admissions")
    assert not crawler.is_vvce_url("https://example.com/vvce.ac.in")
    assert crawler.canonical_url("https://vvce.ac.in/admissions/#info") == "https://vvce.ac.in/admissions"
    assert crawler.canonical_url("https://vvce.ac.in/departments/example/") == "https://vvce.ac.in/departments/example"
    assert crawler.request_url("https://vvce.ac.in/departments/example/") == "https://vvce.ac.in/departments/example/"


def _allow_all_robots():
    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Allow: /"])
    return robots


class RedirectSession:
    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def get(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.requested.append(url)
        response = self.responses[url].pop(0)
        return response


def test_fetch_follows_301_trailing_slash_redirect():
    no_slash = "https://vvce.ac.in/departments/department-of-information-science-engineering"
    with_slash = no_slash + "/"
    session = RedirectSession({
        no_slash: [FakeResponse(no_slash, headers={"Location": with_slash}, status_code=301)],
        with_slash: [FakeResponse(with_slash, "ok", status_code=200)],
    })

    response = crawler._fetch(session, no_slash, 5, _allow_all_robots(), False)

    assert session.requested == [no_slash, with_slash]
    assert response.status_code == 200
    assert response.url == with_slash
    assert crawler.canonical_url(response.url) == no_slash


def test_fetch_follows_302_redirect():
    source = "https://vvce.ac.in/admissions"
    destination = "https://vvce.ac.in/admissions/current"
    session = RedirectSession({
        source: [FakeResponse(source, headers={"Location": destination}, status_code=302)],
        destination: [FakeResponse(destination, "ok", status_code=200)],
    })

    assert crawler._fetch(session, source, 5, _allow_all_robots(), False).status_code == 200
    assert session.requested == [source, destination]


def test_fetch_detects_redirect_loop_and_logs_chain():
    first = "https://vvce.ac.in/admissions"
    second = "https://vvce.ac.in/admissions/current"
    session = RedirectSession({
        first: [FakeResponse(first, headers={"Location": second}, status_code=302)],
        second: [FakeResponse(second, headers={"Location": first}, status_code=301)],
    })

    with pytest.raises(ValueError, match="redirect loop detected") as error:
        crawler._fetch(session, first, 5, _allow_all_robots(), False)
    assert first in str(error.value) and second in str(error.value)
    assert len(session.requested) == 2


def test_fetch_blocks_redirect_outside_vvce_domain():
    source = "https://vvce.ac.in/admissions"
    session = RedirectSession({source: [FakeResponse(source, headers={"Location": "https://example.com/"}, status_code=301)]})

    with pytest.raises(ValueError, match="non-VVCE host blocked"):
        crawler._fetch(session, source, 5, _allow_all_robots(), True)
    assert session.requested == [source]


def test_fetch_uses_configured_timeout_and_streams_response():
    class TimeoutSession:
        def get(self, _url, **kwargs):
            assert kwargs["timeout"] <= 7
            assert kwargs["stream"] is True
            raise requests.Timeout("test timeout")

    with pytest.raises(requests.Timeout):
        crawler._fetch(TimeoutSession(), "https://vvce.ac.in/", 7, _allow_all_robots(), True)


def test_total_crawl_deadline_stops_before_the_next_page(monkeypatch, capsys):
    clock_values = iter((0.0, 0.0, 2.0, 2.0))
    monkeypatch.setattr(crawler.time, "monotonic", lambda: next(clock_values))
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nAllow: /"))

    class NeverFetchedSession:
        headers = {}

        def get(self, *_args, **_kwargs):
            raise AssertionError("expired crawl should not request a page")

    monkeypatch.setattr(crawler.requests, "Session", NeverFetchedSession)
    assert crawler.crawl(max_pages=10, max_duration=1, authorized=True) == []
    assert "maximum crawl duration" in capsys.readouterr().out


def test_large_response_is_rejected_before_body_is_read(monkeypatch, capsys):
    html = "<html><main>" + "VVCE academic information. " * 20 + "</main></html>"
    response = FakeResponse("https://vvce.ac.in/", html, {
        "Content-Type": "text/html", "Content-Length": "4096"
    })
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nAllow: /"))

    class OnePageSession:
        headers = {}

        def get(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(crawler.requests, "Session", OnePageSession)
    docs = crawler.crawl(max_pages=1, authorized=True, max_html_bytes=1024)
    output = capsys.readouterr().out

    assert docs == []
    assert response.closed is True
    assert "document size limit" in output
    assert "Content-Length" not in output


def test_large_pdf_is_rejected_by_pdf_size_limit(monkeypatch, capsys):
    response = FakeResponse("https://vvce.ac.in/calendar.pdf", "x" * 2048, {
        "Content-Type": "application/pdf", "Content-Length": "2048"
    })
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nAllow: /"))

    class OnePageSession:
        headers = {}

        def get(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(crawler.requests, "Session", OnePageSession)
    docs = crawler.crawl(max_pages=1, authorized=True, max_pdf_bytes=1024)
    assert docs == []
    assert response.closed is True
    assert "response declares 2048 bytes; limit is 1024" in capsys.readouterr().out


def test_streaming_mode_does_not_access_response_content(monkeypatch):
    payload = ("<html><title>VVCE</title><main>" + "Academic information for students. " * 15 + "</main></html>").encode()

    class StreamOnlyResponse:
        status_code = 200
        url = "https://vvce.ac.in/"
        headers = {"Content-Type": "text/html"}
        closed = False
        is_redirect = False
        is_permanent_redirect = False

        @property
        def content(self):
            raise AssertionError("the crawler must consume streamed blocks, not response.content")

        def iter_content(self, chunk_size):
            for start in range(0, len(payload), min(chunk_size, 31)):
                yield payload[start:start + min(chunk_size, 31)]

        def close(self):
            self.closed = True

        def raise_for_status(self):
            pass

    response = StreamOnlyResponse()
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nAllow: /"))

    class OnePageSession:
        headers = {}

        def get(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(crawler.requests, "Session", OnePageSession)
    staged = []
    returned = crawler.crawl(max_pages=1, authorized=True, on_document=staged.append, retain_documents=False)

    assert returned == []
    assert len(staged) == 1
    assert response.closed is True


def test_extracted_text_limit_is_applied(monkeypatch):
    html = "<html><main>" + "VVCE academic information. " * 500 + "</main></html>"
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nAllow: /"))

    class OnePageSession:
        headers = {}

        def get(self, url, **_kwargs):
            return FakeResponse(url, html, {"Content-Type": "text/html"})

    monkeypatch.setattr(crawler.requests, "Session", OnePageSession)
    docs = crawler.crawl(max_pages=1, authorized=True, max_text_chars=1000)
    assert len(docs) == 1
    assert len(docs[0]["text"]) <= 1000


def test_timeout_on_one_url_does_not_stop_later_pages(monkeypatch, capsys):
    links = "".join(f"<a href='/departments/area-{number}'>department</a>" for number in (1, 2))
    home = "<html><body>" + links + "<main>Homepage overview. " + "VVCE student information. " * 10 + "</main></body></html>"
    department = "<html><main>Department overview. " + "Course information for VVCE students. " * 10 + "</main></html>"
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nAllow: /"))

    class Session:
        headers = {}

        def get(self, url, **kwargs):
            assert kwargs["timeout"] <= 5
            if url.endswith("area-1"):
                raise requests.Timeout("deliberate test timeout")
            return FakeResponse(url, home if url == "https://vvce.ac.in/" else department,
                                {"Content-Type": "text/html"})

    monkeypatch.setattr(crawler.requests, "Session", Session)
    monkeypatch.setattr(crawler.time, "sleep", lambda _seconds: None)
    docs = crawler.crawl(max_pages=3, max_depth=1, timeout=5, authorized=True)

    assert len(docs) == 2
    assert any("area-2" in doc["url"] for doc in docs)
    assert "area-1: request timeout" in capsys.readouterr().out


def test_interrupted_staged_crawl_preserves_existing_knowledge_base(tmp_path, monkeypatch):
    destination = tmp_path / "knowledge_base.json"
    original = '[{"url":"https://vvce.ac.in/old","title":"Existing","text":"keep me"}]\n'
    destination.write_text(original, encoding="utf-8")

    def interrupted_crawl(*, on_document, **_kwargs):
        on_document({"url": "https://vvce.ac.in/new", "title": "Partial", "text": "new page"})
        raise KeyboardInterrupt

    monkeypatch.setattr(crawler, "crawl", interrupted_crawl)
    with pytest.raises(KeyboardInterrupt):
        crawler.crawl_to_file(destination=destination)

    assert destination.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [destination]


def test_json_array_reader_handles_large_records_incrementally(tmp_path):
    path = tmp_path / "records.json"
    source = [{"url": "https://vvce.ac.in/large", "text": "x" * 150_000},
              {"url": "https://vvce.ac.in/next", "text": "small"}]
    path.write_text(json.dumps(source), encoding="utf-8")

    records = list(crawler.iter_json_array(path))

    assert records == source


def test_json_array_reader_handles_large_records_incrementally(tmp_path):
    path = tmp_path / "records.json"
    source = [{"url": "https://vvce.ac.in/large", "text": "x" * 150_000},
              {"url": "https://vvce.ac.in/next", "text": "small"}]
    path.write_text(json.dumps(source), encoding="utf-8")

    records = list(crawler.iter_json_array(path))

    assert records == source


def test_html_extraction_preserves_section_headings_for_chunking():
    from bs4 import BeautifulSoup

    page = BeautifulSoup(
        "<main><h2>Course Intake</h2><p>Engineering course intake details.</p>"
        "<h3>Student Clubs</h3><ul><li>Code Club information</li></ul>"
        "<script>not page text</script></main>", "html.parser")
    text = crawler.extract_html_content(page.main, 1000)

    assert "## Course Intake" in text
    assert "### Student Clubs" in text
    assert "Code Club information" in text
    assert "not page text" not in text


def test_department_routes_are_discoverable_even_when_link_label_is_uninformative():
    assert crawler._link_is_relevant(
        "https://vvce.ac.in/departments/department-of-information-science-engineering", "Read more")


def test_crawl_deduplicates_links_and_content(monkeypatch, capsys):
    repeated = "Academic details for VVCE students and staff. " * 10
    home = ("<html><head><title>VVCE</title></head><body><nav>"
            "<a href='/admissions/'>Admissions</a><a href='/admissions/#info'>Admission details</a>"
            "<a href='https://example.com/facilities'>Facilities</a>"
            "<a href='/departments/'>Departments</a></nav><main>Homepage overview. " + repeated + "</main></body></html>")
    same_content = "<html><head><title>Same content</title></head><main>" + repeated + "</main></html>"

    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nDisallow: /"))

    class FakeSession:
        headers = {}

        def __init__(self):
            self.requested = []

        def get(self, url, **kwargs):
            self.requested.append(url)
            body = home if url == "https://vvce.ac.in/" else same_content
            return FakeResponse(url, body, {"Content-Type": "text/html", "Last-Modified": "Thu, 29 Oct 2026 12:00:00 GMT"})

    session = FakeSession()
    monkeypatch.setattr(crawler.requests, "Session", lambda: session)
    monkeypatch.setattr(crawler.time, "sleep", lambda _seconds: None)
    docs = crawler.crawl(max_pages=10, max_depth=1, authorized=True)

    assert len(session.requested) == 3
    assert len(docs) == 2
    assert len({doc["url"] for doc in docs}) == 2
    assert all(doc["source_type"] == "crawled" for doc in docs)
    assert all(doc["last_modified"] == "Thu, 29 Oct 2026 12:00:00 GMT" for doc in docs)
    assert "duplicate content" in capsys.readouterr().out


def test_discovery_queue_is_bounded_by_page_limit(monkeypatch, capsys):
    anchors = "".join(f"<a href='/departments/area-{n}'>department {n}</a>" for n in range(100))
    home = "<html><body>" + anchors + "<main>" + "VVCE academic overview. " * 10 + "</main></body></html>"
    page = "<html><main>" + "Department course information for VVCE students. " * 10 + "</main></html>"
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nAllow: /"))

    class Session:
        headers = {}

        def __init__(self):
            self.requested = []

        def get(self, url, **_kwargs):
            self.requested.append(url)
            return FakeResponse(url, home if url == "https://vvce.ac.in/" else page,
                                {"Content-Type": "text/html"})

    session = Session()
    monkeypatch.setattr(crawler.requests, "Session", lambda: session)
    monkeypatch.setattr(crawler.time, "sleep", lambda _seconds: None)
    crawler.crawl(max_pages=3, max_depth=1, authorized=True)

    assert len(session.requested) == 3
    assert "Discovered=3" in capsys.readouterr().out


def test_discovery_queue_is_bounded_by_page_limit(monkeypatch, capsys):
    anchors = "".join(f"<a href='/departments/area-{n}'>department {n}</a>" for n in range(100))
    home = "<html><body>" + anchors + "<main>" + "VVCE academic overview. " * 10 + "</main></body></html>"
    page = "<html><main>" + "Department course information for VVCE students. " * 10 + "</main></html>"
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nAllow: /"))

    class Session:
        headers = {}

        def __init__(self):
            self.requested = []

        def get(self, url, **_kwargs):
            self.requested.append(url)
            return FakeResponse(url, home if url == "https://vvce.ac.in/" else page,
                                {"Content-Type": "text/html"})

    session = Session()
    monkeypatch.setattr(crawler.requests, "Session", lambda: session)
    monkeypatch.setattr(crawler.time, "sleep", lambda _seconds: None)
    crawler.crawl(max_pages=3, max_depth=1, authorized=True)

    assert len(session.requested) == 3
    assert "Discovered=3" in capsys.readouterr().out


def test_crawl_respects_robots_when_not_authorized(monkeypatch):
    monkeypatch.setattr(crawler.requests, "get", lambda *args, **kwargs: FakeResponse(
        "https://vvce.ac.in/robots.txt", "User-agent: *\nDisallow: /"))

    class NeverFetchedSession:
        headers = {}

        def get(self, *_args, **_kwargs):
            raise AssertionError("robots-disallowed page must not be requested")

    monkeypatch.setattr(crawler.requests, "Session", NeverFetchedSession)
    assert crawler.crawl(max_pages=3, max_depth=1, authorized=False) == []


def test_crawled_metadata_reaches_bm25_chunks():
    docs = [{"url": "https://vvce.ac.in/calendar", "title": "Calendar", "date": "2026-06-01",
             "last_modified": "Thu, 29 Oct 2026 12:00:00 GMT", "fetched_at": datetime.now(timezone.utc).isoformat(),
             "source_type": "crawled", "text": "Academic calendar details for students. " * 12}]
    chunk = main.make_chunks(docs)[0]
    assert chunk["source_type"] == "crawled"
    assert chunk["last_modified"] == docs[0]["last_modified"]
    assert chunk["fetched_at"] == docs[0]["fetched_at"]
