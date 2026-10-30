"""Bounded VVCE public-site crawler with an explicit authorization opt-in."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import time
from collections import Counter, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import BinaryIO, Callable, Iterator
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pypdf import PdfReader
from pypdf.errors import PdfReadError

load_dotenv(Path(__file__).parent / ".env")

BASE = "https://vvce.ac.in/"
DATA_PATH = Path(__file__).parent / "data" / "knowledge_base.json"
HEADERS = {"User-Agent": "VVCEStudentAssistant/0.1 (authorized public-site indexing; contact: college project team)"}
STOP = set("a an and are as at be by for from has have in is it of on or that the to was were will with".split())
RELEVANT_TERMS = {
    "academic", "academics", "calendar", "calendars", "exam", "exams", "examination", "notification",
    "notifications", "notice", "notices", "department", "departments", "branch", "branches", "undergraduate", "curriculum", "syllabus", "faculty", "course", "courses", "program", "programs", "programme", "programmes", "admission",
    "admissions", "placement", "placements", "facility", "facilities", "student", "students", "activity",
    "activities", "contact", "campus", "hostel", "library", "sports", "club", "clubs", "event", "events",
}
MAX_PAGE_CAP = 500
MAX_DEPTH_CAP = 6
DEFAULT_HTML_BYTES = 5 * 1024 * 1024
DEFAULT_PDF_BYTES = 15 * 1024 * 1024
DEFAULT_TEXT_CHARS = 50_000
DEFAULT_PDF_PAGES = 80
DEFAULT_CRAWL_SECONDS = 600
DEFAULT_INDEX_CHUNKS = 6_000
CHUNK_SIZE = 180
CHUNK_OVERLAP = 35
CHUNK_STEP = CHUNK_SIZE - CHUNK_OVERLAP


class CrawlTimeLimit(Exception):
    """Raised when the configured end-to-end crawl deadline has expired."""


class DocumentTooLarge(ValueError):
    """A response exceeded its configured byte limit."""


class CrawlSinkError(RuntimeError):
    """A staged crawl document could not be persisted."""


def crawl_authorized(value: str | None = None) -> bool:
    """Authorization is opt-in. Only the literal value 'true' (case-insensitive) enables it."""
    configured = os.getenv("VVCE_CRAWL_AUTHORIZED", "false") if value is None else value
    return configured.strip().lower() == "true"


def is_vvce_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return (parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password
                and (host == "vvce.ac.in" or host.endswith(".vvce.ac.in")))
    except ValueError:
        return False


def canonical_url(url: str) -> str:
    parsed = urlparse(urldefrag(url)[0])
    host = (parsed.hostname or "").lower()
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc += f":{parsed.port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def request_url(url: str) -> str:
    """Normalize a request URL without changing its path (notably its trailing slash)."""
    parsed = urlparse(urldefrag(url)[0])
    host = (parsed.hostname or "").lower()
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc += f":{parsed.port}"
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path or "/", "", parsed.query, ""))


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_html_content(root: BeautifulSoup, max_chars: int) -> str:
    """Extract semantic blocks and otherwise-unwrapped layout text without duplicates."""
    blocks: list[str] = []
    atomic = {"h1", "h2", "h3", "h4", "p", "li", "tr", "dt", "dd"}
    ignored = {"script", "style", "noscript", "svg", "nav", "footer", "header", "iframe"}

    def visit(node):
        if getattr(node, "name", None) in ignored:
            return
        name = getattr(node, "name", None)
        if name in atomic:
            if name == "p" and node.find_parent("li"):
                return
            value = clean(" | ".join(node.stripped_strings))
            if value:
                if name in {"h1", "h2", "h3", "h4"}:
                    value = f"{'#' * int(name[1])} {value}"
                elif name == "li":
                    value = f"- {value}"
                if not blocks or blocks[-1] != value:
                    blocks.append(value)
            return
        if isinstance(node, str):
            value = clean(str(node))
            if value and (not blocks or blocks[-1] != value):
                blocks.append(value)
            return
        for child in getattr(node, "children", ()):
            visit(child)

    visit(root)
    return "\n".join(blocks)[:max_chars]


def page_date(soup: BeautifulSoup, text: str) -> str | None:
    for selector, attr in (("meta[property='article:published_time']", "content"), ("meta[name='date']", "content"), ("time[datetime]", "datetime")):
        tag = soup.select_one(selector)
        if tag and tag.get(attr):
            return tag.get(attr)[:40]
    match = re.search(r"\b(?:20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]20\d{2})\b", text)
    return match.group(0) if match else None


def extract_pdf(data: bytes | BinaryIO, max_pages: int = DEFAULT_PDF_PAGES,
                max_chars: int = DEFAULT_TEXT_CHARS) -> str:
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    reader = PdfReader(source)
    parts: list[str] = []
    chars = 0
    for page in reader.pages[:max_pages]:
        remaining = max_chars - chars
        if remaining <= 0:
            break
        page_text = page.extract_text() or ""
        part = page_text[:remaining]
        parts.append(part)
        chars += len(part)
    return clean(" ".join(parts))[:max_chars]


def _link_is_relevant(url: str, label: str) -> bool:
    haystack = f"{urlparse(url).path} {label}".lower()
    words = set(re.findall(r"[a-z]+", haystack))
    return bool(words & RELEVANT_TERMS) or url.lower().endswith(".pdf")


def _remaining_timeout(timeout: int, deadline: float | None) -> float:
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CrawlTimeLimit("maximum crawl duration reached")
    return min(timeout, remaining)


def _fetch(session: requests.Session, url: str, timeout: int, robots: RobotFileParser,
           allow_robots_override: bool, deadline: float | None = None) -> requests.Response:
    """Follow same-site redirects, detect loops, and retry one 429 after its Retry-After delay."""
    current = request_url(url)
    redirect_chain = [current]
    seen_redirect_targets = {current}
    for redirect_count in range(4):
        response = session.get(current, timeout=_remaining_timeout(timeout, deadline),
                               allow_redirects=False, stream=True)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "1")
            try:
                delay = min(30, max(1, int(retry_after)))
            except ValueError:
                try:
                    delay = min(30, max(1, int((parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())))
                except (TypeError, ValueError, OverflowError):
                    delay = 2
            response.close()
            if deadline is not None:
                delay = min(delay, max(0, deadline - time.monotonic()))
            if delay:
                time.sleep(delay)
            response = session.get(current, timeout=_remaining_timeout(timeout, deadline),
                                   allow_redirects=False, stream=True)
        if response.is_redirect or response.is_permanent_redirect:
            target = urljoin(current, response.headers.get("Location", ""))
            response.close()
            if not is_vvce_url(target):
                raise ValueError("redirect to a non-VVCE host blocked")
            current = request_url(target)
            if current in seen_redirect_targets:
                chain = " -> ".join(redirect_chain + [current])
                raise ValueError(f"redirect loop detected: {chain}")
            seen_redirect_targets.add(current)
            redirect_chain.append(current)
            if not allow_robots_override and not robots.can_fetch(HEADERS["User-Agent"], current):
                raise ValueError("redirect target disallowed by robots.txt")
            if redirect_count == 3:
                raise ValueError("redirect limit exceeded")
            continue
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise
        response.url = current
        return response
    raise ValueError("redirect limit exceeded")


def _read_limited_body(response: requests.Response, byte_limit: int,
                       deadline: float | None = None) -> tempfile.SpooledTemporaryFile:
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            declared_size = int(declared)
        except ValueError:
            declared_size = 0
        if declared_size > byte_limit:
            response.close()
            raise DocumentTooLarge(f"response declares {declared} bytes; limit is {byte_limit}")
    body = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    total = 0
    try:
        for part in response.iter_content(chunk_size=64 * 1024):
            if deadline is not None and time.monotonic() >= deadline:
                raise CrawlTimeLimit("maximum crawl duration reached while reading response")
            if not part:
                continue
            if total + len(part) > byte_limit:
                raise DocumentTooLarge(f"streamed response exceeded {byte_limit} bytes")
            body.write(part)
            total += len(part)
        body.seek(0)
        return body
    except Exception:
        body.close()
        raise
    finally:
        response.close()


def count_document_chunks(text: str) -> int:
    """Count chunks using the same size/overlap and short-chunk rule as main.make_chunks."""
    words = text.split()
    count = 0
    for start in range(0, len(words), CHUNK_STEP):
        part = words[start:start + CHUNK_SIZE]
        if part and sum(map(len, part)) + len(part) - 1 >= 60:
            count += 1
    return count


def crawl(max_pages: int = 120, max_depth: int = 3, timeout: int = 15,
          authorized: bool | None = None, max_duration: int = DEFAULT_CRAWL_SECONDS,
          max_html_bytes: int = DEFAULT_HTML_BYTES, max_pdf_bytes: int = DEFAULT_PDF_BYTES,
          max_text_chars: int = DEFAULT_TEXT_CHARS, max_pdf_pages: int = DEFAULT_PDF_PAGES,
          on_document: Callable[[dict], None] | None = None,
          retain_documents: bool = True) -> list[dict]:
    """Crawl with request, time, input-size, and extracted-text bounds."""
    max_pages = min(MAX_PAGE_CAP, max(1, max_pages))
    max_depth = min(MAX_DEPTH_CAP, max(0, max_depth))
    timeout = max(1, timeout)
    max_duration = max(1, max_duration)
    max_html_bytes = max(1024, max_html_bytes)
    max_pdf_bytes = max(1024, max_pdf_bytes)
    max_text_chars = max(1000, max_text_chars)
    max_pdf_pages = max(1, max_pdf_pages)
    started_at = time.monotonic()
    deadline = started_at + max_duration
    allow_robots_override = crawl_authorized() if authorized is None else authorized

    robots = RobotFileParser()
    try:
        response = requests.get(urljoin(BASE, "/robots.txt"), headers=HEADERS,
                                timeout=_remaining_timeout(timeout, deadline), allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise requests.RequestException(f"robots.txt returned HTTP {response.status_code}")
        robots.parse(response.text.splitlines())
    except CrawlTimeLimit:
        robots.parse(["User-agent: *", "Disallow: /"])
        print("Crawl time budget expired while requesting robots.txt.", flush=True)
    except requests.RequestException as exc:
        if not allow_robots_override:
            robots.parse(["User-agent: *", "Disallow: /"])
            print(f"robots.txt unavailable; failing closed ({exc})", flush=True)
        else:
            robots.parse([])
            print(f"robots.txt unavailable; proceeding under explicit VVCE_CRAWL_AUTHORIZED=true ({exc})", flush=True)

    session = requests.Session()
    session.headers.update(HEADERS)
    first = canonical_url(BASE)
    queue = deque([(first, 0)])
    discovered = {first}
    visited: set[str] = set()
    processed_urls: set[str] = set()
    seen_content: set[str] = set()
    docs: list[dict] = []
    pages_processed = 0
    pdfs_processed = 0
    docs_indexed = 0
    chunks_created = 0
    failed = 0
    fetch_seconds = 0.0
    extract_seconds = 0.0
    skipped: Counter[str] = Counter()
    time_expired = False

    while queue and len(visited) < max_pages:
        if time.monotonic() >= deadline:
            time_expired = True
            skipped["maximum crawl duration reached"] += len(queue)
            break
        url, depth = queue.popleft()
        if url in visited:
            continue
        if not is_vvce_url(url):
            skipped["non-VVCE URL"] += 1
            continue
        visited.add(url)
        ordinal = len(visited)
        print(f"[{ordinal}/{max_pages}] Fetching: {url}", flush=True)
        if not allow_robots_override and not robots.can_fetch(HEADERS["User-Agent"], url):
            skipped["disallowed by robots.txt"] += 1
            print(f"[{ordinal}/{max_pages}] Failed: disallowed by robots.txt", flush=True)
            continue
        try:
            fetch_started = time.monotonic()
            response = _fetch(session, url, timeout, robots, allow_robots_override, deadline)
            fetch_seconds += time.monotonic() - fetch_started
            final_url = canonical_url(response.url)
            if not is_vvce_url(final_url):
                response.close()
                skipped["non-VVCE redirect"] += 1
                print(f"[{ordinal}/{max_pages}] Failed: redirect left VVCE domain", flush=True)
                continue
            if final_url in processed_urls:
                response.close()
                skipped["duplicate URL after redirect"] += 1
                print(f"[{ordinal}/{max_pages}] Failed: duplicate URL after redirect", flush=True)
                continue
            processed_urls.add(final_url)
            content_type = response.headers.get("Content-Type", "").lower()
            is_pdf = "pdf" in content_type or urlparse(final_url).path.lower().endswith(".pdf")
            is_html = "html" in content_type or not content_type
            if not is_pdf and not is_html:
                response.close()
                skipped["unsupported content type"] += 1
                print(f"[{ordinal}/{max_pages}] Failed: unsupported content type {content_type or '(missing)'}", flush=True)
                continue
            last_modified = response.headers.get("Last-Modified")
            size_limit = max_pdf_bytes if is_pdf else max_html_bytes
            extract_started = time.monotonic()
            body = _read_limited_body(response, size_limit, deadline)
            if is_pdf:
                with body:
                    text = extract_pdf(body, max_pages=max_pdf_pages, max_chars=max_text_chars)
                title = Path(urlparse(final_url).path).name or "VVCE document"
                date = None
                pdfs_processed += 1
            else:
                with body:
                    soup = BeautifulSoup(body, "html.parser")
                title = clean(soup.title.get_text(" ", strip=True)) if soup.title else final_url
                if depth < max_depth:
                    for anchor in soup.find_all("a", href=True):
                        try:
                            target = canonical_url(urljoin(final_url, anchor["href"]))
                        except ValueError:
                            skipped["invalid link URL"] += 1
                            continue
                        if not is_vvce_url(target) or target in discovered or len(discovered) >= max_pages:
                            continue
                        label = clean(anchor.get_text(" ", strip=True))
                        if not _link_is_relevant(target, label):
                            continue
                        discovered.add(target)
                        queue.append((target, depth + 1))
                for node in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
                    node.decompose()
                main_content = soup.find("main") or soup.find("article") or soup.body or soup
                text = extract_html_content(main_content, max_text_chars)
                date = page_date(soup, text)
                pages_processed += 1
                del main_content, soup, body
            if is_pdf:
                del body
            extract_seconds += time.monotonic() - extract_started

            print(f"[{ordinal}/{max_pages}] Extracted: {len(text)} characters", flush=True)
            normalized = clean(text).lower()
            if len(normalized) < 80:
                skipped["insufficient extracted text"] += 1
                print(f"[{ordinal}/{max_pages}] Added: 0 chunks (insufficient extracted text)", flush=True)
                continue
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if digest in seen_content:
                skipped["duplicate content"] += 1
                print(f"[{ordinal}/{max_pages}] Added: 0 chunks (duplicate content)", flush=True)
                continue
            seen_content.add(digest)
            doc = {"url": final_url, "title": title[:300], "date": date,
                   "last_modified": last_modified, "text": text,
                   "fetched_at": datetime.now(timezone.utc).isoformat(), "source_type": "crawled"}
            doc_chunks = count_document_chunks(text)
            if on_document is not None:
                try:
                    on_document(doc)
                except OSError as exc:
                    raise CrawlSinkError(f"could not stage {final_url}: {exc}") from exc
            if retain_documents:
                docs.append(doc)
            docs_indexed += 1
            chunks_created += doc_chunks
            print(f"[{ordinal}/{max_pages}] Added: {doc_chunks} chunks", flush=True)
            time.sleep(min(0.4, max(0, deadline - time.monotonic())))
        except CrawlSinkError:
            raise
        except CrawlTimeLimit:
            time_expired = True
            skipped["maximum crawl duration reached"] += 1
            print(f"[{ordinal}/{max_pages}] Failed: maximum crawl duration reached", flush=True)
            break
        except DocumentTooLarge as exc:
            failed += 1
            skipped["document size limit"] += 1
            print(f"[{ordinal}/{max_pages}] Failed: {url}: {exc}", flush=True)
        except requests.Timeout as exc:
            failed += 1
            skipped["timeout"] += 1
            print(f"[{ordinal}/{max_pages}] Failed: {url}: request timeout ({exc})", flush=True)
        except requests.HTTPError as exc:
            failed += 1
            status = exc.response.status_code if exc.response is not None else "HTTP error"
            skipped[f"HTTP {status}"] += 1
            print(f"[{ordinal}/{max_pages}] Failed: {url}: HTTP {status} ({exc})", flush=True)
        except (requests.RequestException, ValueError, OSError, PdfReadError) as exc:
            failed += 1
            skipped[type(exc).__name__] += 1
            print(f"[{ordinal}/{max_pages}] Failed: {url}: {type(exc).__name__}: {exc}", flush=True)

    if queue and not time_expired:
        skipped["page limit reached before queue drained"] += len(queue)
    duration = time.monotonic() - started_at
    print(f"Crawl summary: Discovered={len(discovered)}, Processed={pages_processed + pdfs_processed}, "
          f"Failed={failed}, PDFs processed={pdfs_processed}, Documents indexed={docs_indexed}, "
          f"Chunks created={chunks_created}, Total duration={duration:.1f}s, "
          f"response wait={fetch_seconds:.1f}s, download+extract={extract_seconds:.1f}s", flush=True)
    for reason, count in sorted(skipped.items()):
        print(f"  skipped {count}: {reason}", flush=True)
    if time_expired:
        print("Crawl stopped at its configured time limit; successfully staged documents are retained for commit.", flush=True)
    if allow_robots_override:
        print("Robots policy override used under VVCE_CRAWL_AUTHORIZED=true.", flush=True)
    return docs


def save(docs: list[dict], path: Path = DATA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def iter_json_array(path: Path) -> Iterator[dict]:
    """Read a JSON array incrementally so a prior crawl need not be loaded as one string."""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as source:
        buffer = source.read(64 * 1024)
        position = 0
        eof = not buffer

        def fill() -> None:
            nonlocal buffer, position, eof
            if eof:
                return
            remaining = buffer[position:]
            more = source.read(64 * 1024)
            buffer = remaining + more
            position = 0
            eof = len(more) == 0

        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position == len(buffer) and not eof:
                fill()
                continue
            if position >= len(buffer) or buffer[position] != "[":
                raise ValueError(f"{path} must contain a JSON array")
            position += 1
            break

        while True:
            while True:
                while position < len(buffer) and (buffer[position].isspace() or buffer[position] == ","):
                    position += 1
                if position < len(buffer) or eof:
                    break
                fill()
            if position < len(buffer) and buffer[position] == "]":
                return
            try:
                item, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                if eof:
                    raise ValueError(f"{path} contains an incomplete or invalid JSON array")
                fill()
                continue
            if not isinstance(item, dict):
                raise ValueError(f"{path} must contain document objects")
            yield item
            position = end
            if position > 64 * 1024:
                fill()


def merge_staged_documents(stage_path: Path, destination: Path = DATA_PATH) -> tuple[int, int, int]:
    """Atomically merge fresh crawled records with old records, replacing matching URLs."""
    fresh_urls: set[str] = set()
    with stage_path.open("r", encoding="utf-8") as staged:
        for line in staged:
            if line.strip():
                item = json.loads(line)
                fresh_urls.add(canonical_url(item["url"]))

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    written = 0
    fresh_written = 0
    fresh_chunks = 0
    seen_urls: set[str] = set()
    seen_content: set[str] = set()
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent,
                                         prefix=".knowledge-base-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write("[\n")

            def emit(item: dict, fresh: bool = False) -> None:
                nonlocal written, fresh_written, fresh_chunks
                url = canonical_url(item["url"])
                if url in seen_urls:
                    return
                content = clean(item.get("text", "")).lower()
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if digest in seen_content:
                    return
                seen_urls.add(url)
                seen_content.add(digest)
                if written:
                    output.write(",\n")
                json.dump(item, output, ensure_ascii=False)
                written += 1
                if fresh:
                    fresh_written += 1
                    fresh_chunks += count_document_chunks(item.get("text", ""))

            if destination.exists():
                for old_item in iter_json_array(destination):
                    if canonical_url(old_item["url"]) not in fresh_urls:
                        emit(old_item)
            with stage_path.open("r", encoding="utf-8") as staged:
                for line in staged:
                    if line.strip():
                        emit(json.loads(line), fresh=True)
            output.write("\n]\n")
            output.flush()
            os.fsync(output.fileno())
        if fresh_written == 0:
            temporary.unlink(missing_ok=True)
            return written, 0, 0
        temporary.replace(destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return written, fresh_written, fresh_chunks


def prospective_index_chunks(stage_path: Path, destination: Path, manual_path: Path,
                             chunk_limit: int) -> int:
    """Preflight the exact merged corpus so a chunk-cap failure cannot replace the KB."""
    fresh_urls: set[str] = set()
    with stage_path.open("r", encoding="utf-8") as staged:
        for line in staged:
            if line.strip():
                fresh_urls.add(canonical_url(json.loads(line)["url"]))

    count = 0
    seen_urls: set[str] = set()
    seen_content: set[str] = set()

    def count_crawled(item: dict) -> None:
        nonlocal count
        url = canonical_url(item["url"])
        if url in seen_urls:
            return
        digest = hashlib.sha256(clean(item.get("text", "")).lower().encode("utf-8")).hexdigest()
        if digest in seen_content:
            return
        seen_urls.add(url)
        seen_content.add(digest)
        count += count_document_chunks(item.get("text", ""))
        if count > chunk_limit:
            raise RuntimeError(f"Prospective index exceeds VVCE_MAX_INDEX_CHUNKS={chunk_limit}; knowledge base was not changed.")

    if destination.exists():
        for item in iter_json_array(destination):
            if canonical_url(item["url"]) not in fresh_urls:
                count_crawled(item)
    with stage_path.open("r", encoding="utf-8") as staged:
        for line in staged:
            if line.strip():
                count_crawled(json.loads(line))
    if manual_path.exists():
        for item in iter_json_array(manual_path):
            count += count_document_chunks(item.get("text", ""))
            if count > chunk_limit:
                raise RuntimeError(f"Prospective index exceeds VVCE_MAX_INDEX_CHUNKS={chunk_limit}; knowledge base was not changed.")
    return count


def crawl_to_file(*, max_pages: int = 120, max_depth: int = 3, timeout: int = 15,
                  authorized: bool | None = None, max_duration: int = DEFAULT_CRAWL_SECONDS,
                  max_html_bytes: int = DEFAULT_HTML_BYTES, max_pdf_bytes: int = DEFAULT_PDF_BYTES,
                  max_text_chars: int = DEFAULT_TEXT_CHARS, max_pdf_pages: int = DEFAULT_PDF_PAGES,
                  max_index_chunks: int = DEFAULT_INDEX_CHUNKS,
                  destination: Path = DATA_PATH) -> tuple[int, int, int]:
    """Stage each extracted document to disk and atomically merge after a safe crawl stop."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".vvce-crawl-", dir=destination.parent) as temporary_dir:
        stage_path = Path(temporary_dir) / "documents.jsonl"
        staged_count = 0
        staged_chunks = 0
        with stage_path.open("w", encoding="utf-8") as stage:
            def persist(document: dict) -> None:
                nonlocal staged_count, staged_chunks
                stage.write(json.dumps(document, ensure_ascii=False) + "\n")
                stage.flush()
                staged_count += 1
                staged_chunks += count_document_chunks(document["text"])

            crawl(max_pages=max_pages, max_depth=max_depth, timeout=timeout,
                  authorized=authorized, max_duration=max_duration,
                  max_html_bytes=max_html_bytes, max_pdf_bytes=max_pdf_bytes,
                  max_text_chars=max_text_chars, max_pdf_pages=max_pdf_pages,
                  on_document=persist, retain_documents=False)
        if staged_count == 0 or staged_chunks == 0:
            print("No new indexable crawl documents; existing knowledge base was left unchanged.", flush=True)
            return staged_count, staged_chunks, 0
        prospective_index_chunks(stage_path, destination, DATA_PATH.parent / "manual_documents.json", max_index_chunks)
        total_documents, fresh_documents, fresh_chunks = merge_staged_documents(stage_path, destination)
        if fresh_documents == 0:
            print("No new unique crawl records were retained; existing knowledge base was left unchanged.", flush=True)
        else:
            print(f"Committed {fresh_documents} new crawl documents; {total_documents} crawled records retained.", flush=True)
        return fresh_documents, fresh_chunks, total_documents


def env_limit(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


if __name__ == "__main__":
    if not crawl_authorized():
        raise SystemExit("Crawling is disabled. Set VVCE_CRAWL_AUTHORIZED=true in backend/.env after confirming project authorization.")
    new_documents, new_chunks, retained_documents = crawl_to_file(
        max_pages=env_limit("VVCE_MAX_PAGES", 120, 1, MAX_PAGE_CAP),
        max_depth=env_limit("VVCE_MAX_DEPTH", 3, 0, MAX_DEPTH_CAP),
        timeout=env_limit("VVCE_REQUEST_TIMEOUT", 15, 1, 120),
        max_duration=env_limit("VVCE_MAX_CRAWL_SECONDS", DEFAULT_CRAWL_SECONDS, 1, 3600),
        max_html_bytes=env_limit("VVCE_MAX_HTML_BYTES", DEFAULT_HTML_BYTES, 1024, 50 * 1024 * 1024),
        max_pdf_bytes=env_limit("VVCE_MAX_PDF_BYTES", DEFAULT_PDF_BYTES, 1024, 100 * 1024 * 1024),
        max_text_chars=env_limit("VVCE_MAX_EXTRACTED_CHARS", DEFAULT_TEXT_CHARS, 1000, 500_000),
        max_pdf_pages=env_limit("VVCE_MAX_PDF_PAGES", DEFAULT_PDF_PAGES, 1, 1000),
        max_index_chunks=env_limit("VVCE_MAX_INDEX_CHUNKS", DEFAULT_INDEX_CHUNKS, 1, 20_000))
    if not new_documents or not new_chunks:
        raise SystemExit("No new indexable documents were found; existing knowledge base was left unchanged.")
    from main import rebuild_index
    total_chunks = rebuild_index()
    print(f"Final index: documents retained={retained_documents}, total BM25 chunks={total_chunks}, "
          f"new crawl documents={new_documents}, new chunks={new_chunks}; manual documents preserved.", flush=True)
