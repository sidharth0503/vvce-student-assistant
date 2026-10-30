from crawler import BASE
from main import health, make_chunks, tokenize
import ingest_documents
import json
import main


def test_seed_urls_are_same_site_and_deduplicated():
    assert BASE == "https://vvce.ac.in/"


def test_chunk_metadata_and_overlap():
    docs = [{"url": "https://vvce.ac.in/x", "title": "Notice", "date": "2025-01-02", "text": " ".join(["word"] * 250)}]
    chunks = make_chunks(docs, size=100, overlap=20)
    assert len(chunks) >= 3
    assert all(c["url"] == docs[0]["url"] and c["title"] == "Notice" and c["date"] for c in chunks)


def test_tokenization_removes_common_words():
    assert tokenize("The exam calendar for VVCE") == ["exam", "calendar", "vvce"]


def test_health_endpoint_reports_index_state():
    response = health()
    assert response["status"] == "ok"
    assert "documents" in response


def test_local_html_ingestion_preserves_manifest_metadata_and_chunks(tmp_path):
    document_dir = tmp_path / "documents"
    document_dir.mkdir()
    (document_dir / "notice.html").write_text(
        "<html><head><title>Exam Notice</title></head><body><nav>Ignore navigation</nav>"
        "<main>Exam timetable details and student instructions. " + "VVCE examination schedule. " * 12 +
        "</main><script>ignore script</script></body></html>", encoding="utf-8")
    manifest = tmp_path / "sources.json"
    manifest.write_text('{"notice.html":{"url":"https://vvce.ac.in/exam-notice.pdf",'
                        '"title":"Exam timetable","date":"2026-10-20"}}', encoding="utf-8")

    docs = ingest_documents.read_local_documents(document_dir, manifest)
    chunks = make_chunks(docs)

    assert len(docs) == 1
    assert docs[0]["source_type"] == "manual"
    assert docs[0]["url"] == "https://vvce.ac.in/exam-notice.pdf"
    assert docs[0]["title"] == "Exam timetable"
    assert docs[0]["date"] == "2026-10-20"
    assert "Ignore navigation" not in docs[0]["text"]
    assert "ignore script" not in docs[0]["text"]
    assert chunks and chunks[0]["source_type"] == "manual"


def test_local_pdf_ingestion_uses_pdf_extractor(tmp_path, monkeypatch):
    document_dir = tmp_path / "documents"
    document_dir.mkdir()
    (document_dir / "calendar.pdf").write_bytes(b"local PDF placeholder")
    manifest = tmp_path / "sources.json"
    manifest.write_text('{"calendar.pdf":{"url":"https://vvce.ac.in/calendar.pdf",'
                        '"title":"Calendar","date":"2026-06-01"}}', encoding="utf-8")
    monkeypatch.setattr(ingest_documents, "extract_pdf", lambda _data: "Academic calendar details. " * 10)

    docs = ingest_documents.read_local_documents(document_dir, manifest)

    assert len(docs) == 1
    assert docs[0]["source_type"] == "manual"
    assert docs[0]["url"] == "https://vvce.ac.in/calendar.pdf"
    assert docs[0]["title"] == "Calendar"


def test_local_documents_without_original_url_are_skipped(tmp_path):
    document_dir = tmp_path / "documents"
    document_dir.mkdir()
    (document_dir / "unlisted.html").write_text("<p>Some public document content. " + "VVCE content. " * 10 + "</p>", encoding="utf-8")
    manifest = tmp_path / "sources.json"
    manifest.write_text("{}", encoding="utf-8")

    assert ingest_documents.read_local_documents(document_dir, manifest) == []


def test_ingest_persists_manual_chunks_alongside_crawled_chunks(tmp_path, monkeypatch):
    document_dir = tmp_path / "documents"
    document_dir.mkdir()
    (document_dir / "notice.html").write_text(
        "<title>Local notice</title><p>" + "Exam registration opens for students. " * 12 + "</p>", encoding="utf-8")
    manifest = tmp_path / "sources.json"
    manifest.write_text('{"notice.html":{"url":"https://vvce.ac.in/notice.html"}}', encoding="utf-8")
    kb_path = tmp_path / "knowledge_base.json"
    kb_path.write_text(json.dumps([{"url": "https://vvce.ac.in/crawled", "title": "Crawled",
                                    "text": "".join(["Academic information for VVCE students. "] * 12)}]), encoding="utf-8")
    chunks_path = tmp_path / "chunks.json"
    manual_path = tmp_path / "manual_documents.json"
    monkeypatch.setattr(main, "DATA_PATH", kb_path)
    monkeypatch.setattr(main, "MANUAL_DATA_PATH", manual_path)
    monkeypatch.setattr(main, "CHUNKS_PATH", chunks_path)

    document_count, chunk_count = ingest_documents.ingest(document_dir, manifest, manual_path)
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))

    assert document_count == 1 and chunk_count > 0
    assert {chunk["source_type"] for chunk in chunks} == {"manual", "crawled"}


def test_rebuild_index_feeds_bm25_a_lazy_token_stream(tmp_path, monkeypatch):
    crawl_path = tmp_path / "knowledge_base.json"
    crawl_path.write_text(json.dumps([{"url": "https://vvce.ac.in/academic-calendar", "title": "Calendar",
                                      "text": "Academic calendar dates for students. " * 20}]), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_PATH", crawl_path)
    monkeypatch.setattr(main, "MANUAL_DATA_PATH", tmp_path / "missing_manual.json")
    monkeypatch.setattr(main, "CHUNKS_PATH", tmp_path / "chunks.json")
    monkeypatch.setattr(main, "_chunks", main._chunks)
    monkeypatch.setattr(main, "_index", main._index)

    class CapturingIndex:
        def __init__(self, corpus):
            assert not isinstance(corpus, list)
            assert iter(corpus) is corpus
            self.corpus = list(corpus)

    monkeypatch.setattr(main, "BM25Okapi", CapturingIndex)
    chunk_count = main.rebuild_index()

    assert chunk_count > 0
    assert len(main._index.corpus) == chunk_count


def test_rebuild_index_failure_preserves_live_index(tmp_path, monkeypatch):
    crawl_path = tmp_path / "knowledge_base.json"
    crawl_path.write_text(json.dumps([{"url": "https://vvce.ac.in/academic-calendar", "title": "Calendar",
                                      "text": "Academic calendar dates for students. " * 20}]), encoding="utf-8")
    chunks_path = tmp_path / "chunks.json"
    chunks_path.write_text('[{"existing":"chunks"}]', encoding="utf-8")
    old_chunks = [{"url": "https://vvce.ac.in/old", "text": "existing live evidence"}]
    old_index = object()
    monkeypatch.setattr(main, "DATA_PATH", crawl_path)
    monkeypatch.setattr(main, "MANUAL_DATA_PATH", tmp_path / "missing_manual.json")
    monkeypatch.setattr(main, "CHUNKS_PATH", chunks_path)
    monkeypatch.setattr(main, "_chunks", old_chunks)
    monkeypatch.setattr(main, "_index", old_index)
    monkeypatch.setattr(main, "BM25Okapi", lambda _corpus: (_ for _ in ()).throw(RuntimeError("fixture failure")))

    import pytest
    with pytest.raises(RuntimeError, match="fixture failure"):
        main.rebuild_index()

    assert main._chunks is old_chunks
    assert main._index is old_index
    assert json.loads(chunks_path.read_text(encoding="utf-8")) == [{"existing": "chunks"}]
