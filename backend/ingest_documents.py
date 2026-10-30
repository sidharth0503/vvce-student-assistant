"""Ingest locally supplied public VVCE HTML/PDF files into the RAG knowledge base."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pypdf.errors import PdfReadError

from crawler import DATA_PATH, clean, extract_pdf

DOCUMENTS_DIR = Path(__file__).parent / "documents"
MANIFEST_PATH = DOCUMENTS_DIR / "sources.json"
MANUAL_DATA_PATH = DATA_PATH.parent / "manual_documents.json"


def extract_html(data: bytes) -> tuple[str, str | None]:
    soup = BeautifulSoup(data, "html.parser")
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else None
    for node in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        node.decompose()
    return clean(soup.get_text(" ", strip=True)), title


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(manifest, dict):
        raise ValueError("sources.json must be a JSON object keyed by document filename")
    return manifest


def _safe_source_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("manifest entry must include an original http(s) source URL")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid source URL: {value!r}")
    return value


def read_local_documents(documents_dir: Path = DOCUMENTS_DIR, manifest_path: Path = MANIFEST_PATH) -> list[dict]:
    manifest = load_manifest(manifest_path)
    docs = []
    for path in sorted(documents_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm", ".pdf"}:
            continue
        relative = path.relative_to(documents_dir).as_posix()
        metadata = manifest.get(relative)
        if not isinstance(metadata, dict):
            print(f"Skipping {relative}: add its original URL to sources.json")
            continue
        try:
            url = _safe_source_url(metadata.get("url"))
            if path.suffix.lower() == ".pdf":
                text = extract_pdf(path.read_bytes())
                extracted_title = None
            else:
                text, extracted_title = extract_html(path.read_bytes())
            title = str(metadata.get("title") or extracted_title or path.name).strip()
            date = metadata.get("date")
            if date is not None and not isinstance(date, str):
                raise ValueError("date in sources.json must be a string or null")
            if len(text) < 60:
                print(f"Skipping {relative}: no usable text extracted")
                continue
            docs.append({"url": url, "title": title[:300], "date": date, "text": text[:100_000],
                         "source_type": "manual", "source_file": relative})
        except (OSError, ValueError, PdfReadError) as exc:
            print(f"Skipping {relative}: {exc}")
    return docs


def ingest(documents_dir: Path = DOCUMENTS_DIR, manifest_path: Path = MANIFEST_PATH,
           manual_data_path: Path = MANUAL_DATA_PATH) -> tuple[int, int]:
    """Refresh the manual source set, persist chunks, and refresh BM25 in this process."""
    docs = read_local_documents(documents_dir, manifest_path)
    manual_data_path.parent.mkdir(parents=True, exist_ok=True)
    temp = manual_data_path.with_suffix(".tmp")
    temp.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(manual_data_path)

    # Same chunk format and BM25 index builder as the API.
    from main import make_chunks, rebuild_index
    chunks = make_chunks(docs)
    rebuild_index()
    return len(docs), len(chunks)


if __name__ == "__main__":
    document_count, chunk_count = ingest()
    print(f"Ingested {document_count} local documents ({chunk_count} manual chunks).")
