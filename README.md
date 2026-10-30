# VVCE Student Assistant

A small React + FastAPI RAG prototype for public information from [vvce.ac.in](https://vvce.ac.in/). The crawler stores extracted page/PDF text in a local JSON file, BM25 retrieves relevant chunks, and a provider-independent generation layer answers from those excerpts with original source links. Groq is primary; Gemini is the transient-failure fallback.

## Requirements

- Node.js 20+ and npm
- Python 3.10+
- A Groq API key for primary generation and a Gemini API key for fallback (retrieval and health checks work without either)

## Setup

### Start the backend

From the project root in PowerShell:

```powershell
Set-Location D:\Projects\VVCE-Student-Assistant\backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
uvicorn main:app --reload
```

Set `GROQ_API_KEY` in `backend/.env` for primary generation and `GEMINI_API_KEY` for fallback. Groq uses `GROQ_MODEL=openai/gpt-oss-20b` by default and connects through the OpenAI Python SDK to Groq's compatible endpoint. Configure request timeouts with `GROQ_TIMEOUT_SECONDS` and `GEMINI_TIMEOUT_SECONDS` (both default to 90 seconds). Transient connection, timeout, 408, 429 and 5xx failures receive at most two retries with backoff; after exhausted transient Groq failures, the same grounded prompt is sent to Gemini. Authentication and model configuration failures are reported directly rather than hidden by fallback. Provider keys stay in backend environment variables and are never sent to the frontend. Crawling remains separate from server startup; see [authorized crawler settings](#authorized-website-crawling) to run ingestion deliberately. FastAPI loads the existing local index at startup, but chat requests do not trigger a crawl or rebuild. The health endpoint stays available with `knowledge_base_ready: false` if the backend is online but no chunks are indexed.

The Groq request context is bounded before generation to keep large retrieval results and long conversations within the model's token limits. Defaults are `GROQ_EVIDENCE_TOKEN_BUDGET=1800`, `GROQ_HISTORY_TOKEN_BUDGET=350`, `GROQ_MAX_PROMPT_TOKENS=3000`, and `GROQ_COMPLETION_TOKENS=700`. These are conservative character-based token estimates and can be adjusted in `backend/.env`; history and evidence retain their source metadata. Rate-limit retry waits honor Groq's `Retry-After` or reset headers. If the indicated wait exceeds `GROQ_MAX_RETRY_WAIT_SECONDS` (default 15), the request goes to Gemini fallback instead of retrying prematurely. This helps avoid repeat 429s but cannot guarantee compliance with account-specific rolling token quotas.

### Use the CLI

In a second PowerShell terminal:

```powershell
Set-Location D:\Projects\VVCE-Student-Assistant\backend
.\.venv\Scripts\Activate.ps1
python chat_cli.py
```

The CLI connects to `http://localhost:8000` by default. Set `VVCE_API_URL` to use another backend address. It supports `/help`, `/sources`, `/health`, and `/exit`.

### Start the web chatbot

In another terminal at the project root:

```powershell
Set-Location D:\Projects\VVCE-Student-Assistant
npm install
npm run dev
```

Open the Vite URL (usually http://localhost:5173). FastAPI is at http://localhost:8000; interactive API docs are at `/docs`.

## API

- `GET /api/health` — backend, knowledge-base readiness, indexed chunks and configuration status.
- `POST /api/chat` — JSON body `{"message":"...","history":[{"role":"user|assistant","content":"..."}]}`; `history` is optional and bounded to eight recent turns. The response keeps the existing grounded answer and source metadata schema.
- `POST /api/rebuild` — starts a bounded crawl and rebuilds the local index. Bounds are configured with `VVCE_MAX_PAGES`, `VVCE_MAX_DEPTH`, and `VVCE_REQUEST_TIMEOUT`.

The CLI and web interface both use `/api/chat`. They display source URLs from each response; the web interface renders Markdown citations as clickable links and shows backend/knowledge-base status. Both pass a bounded recent conversation so short follow-ups can be resolved. BM25 ranks chunk text together with source titles and URL terms, expands common course/department terms, and selects distinct sources for broad list questions. Neither interface starts a crawl or rebuilds the index when a student sends a message.

### Authorized website crawling

The crawler starts at the VVCE homepage, discovers relevant internal links for academics, exams, departments/courses, admissions, placements, facilities, student activities and contact information, and follows PDFs from those pages. It only requests `vvce.ac.in` and its subdomains, deduplicates URLs and content, follows same-site redirects only, backs off and retries once on HTTP 429, and skips failed or unsupported resources with terminal reasons. Every URL prints a flushed progress line. HTML/PDF bodies stream in bounded blocks and are staged to disk one document at a time; BM25 is rebuilt after crawl completion because BM25's corpus-level IDF requires the full corpus. Existing records are merged atomically, and an interrupted crawl leaves the existing KB intact. The BM25 tokenizer now feeds chunks lazily to avoid holding a second full token corpus in memory.

`VVCE_CRAWL_AUTHORIZED` defaults to `false`. If the project has explicit authorization to crawl the site despite its robots.txt policy, set it to `true` in `backend/.env`. That setting is an explicit robots-policy override for this project; it does not disguise requests or use proxies. With the default false setting, the crawler continues to honor robots.txt and fails closed if robots.txt cannot be retrieved. Page and depth limits default to 120 and 3, with supported ranges 1–500 and 0–6. Request timeout defaults to 15 seconds (range 1–120 seconds); total crawl time defaults to 600 seconds (range 1–3600). Each HTML response is capped at 5 MiB, PDFs at 15 MiB, extracted text at 50,000 characters per document, and PDF extraction at 80 pages. BM25 indexing defaults to a 6,000-chunk cap; exceeding it fails before replacing the in-memory index. A zero-document or zero-chunk crawl fails and preserves the previous crawled knowledge base. Manual documents are stored separately and included in every index rebuild.

Example `backend/.env` values:

```dotenv
VVCE_CRAWL_AUTHORIZED=true
VVCE_MAX_PAGES=120
VVCE_MAX_DEPTH=3
VVCE_REQUEST_TIMEOUT=15
VVCE_MAX_CRAWL_SECONDS=600
VVCE_MAX_HTML_BYTES=5242880
VVCE_MAX_PDF_BYTES=15728640
VVCE_MAX_EXTRACTED_CHARS=50000
VVCE_MAX_PDF_PAGES=80
VVCE_MAX_INDEX_CHUNKS=6000
```

Run `python crawler.py` from `backend` for per-URL terminal progress and the final discovery/failure/document/chunk/duration summary. Or start FastAPI and call `POST /api/rebuild`; that endpoint returns 403 unless the authorization flag is true. The API health response reports crawled and manual chunk counts.

The authorized project crawler can be run from PowerShell with:

```powershell
Set-Location D:\Projects\VVCE-Student-Assistant\backend
if (!(Test-Path .env)) { Copy-Item .env.example .env }
$vvceEnvLines = Get-Content .env
if ($vvceEnvLines -match '^VVCE_CRAWL_AUTHORIZED=') {
  $vvceEnvLines = $vvceEnvLines -replace '^VVCE_CRAWL_AUTHORIZED=.*$', 'VVCE_CRAWL_AUTHORIZED=true'
} else {
  $vvceEnvLines += 'VVCE_CRAWL_AUTHORIZED=true'
}
Set-Content -Path .env -Value $vvceEnvLines
.\.venv\Scripts\Activate.ps1
python crawler.py
uvicorn main:app --reload
```

### Local document ingestion

To populate the knowledge base without crawling, copy public VVCE HTML or PDF files into `backend/documents/`. Add an entry for each file to `backend/documents/sources.json`, keyed by its path relative to that directory:

```json
{
  "academic-calendar.pdf": {
    "url": "https://vvce.ac.in/path/to/original-calendar.pdf",
    "title": "Academic Calendar 2026–27",
    "date": "2026-06-01"
  }
}
```

The original URL is required; title and date are optional. Then, from `backend`, run `python ingest_documents.py` before starting or restarting the API. This extracts HTML/PDF text, records it as `source_type: manual`, creates the same overlapping BM25 chunks, and refreshes `data/chunks.json`. Manual source records are stored separately in `data/manual_documents.json`; crawled records remain in `data/knowledge_base.json`. Both contribute to the same search index, and answer source links identify local documents versus crawled pages. Scanned PDFs without embedded text are skipped. Local source files and generated data are ignored by git.

Run `python ingest_documents.py` for local documents and restart the API so its in-memory index reloads. Crawler rebuilds preserve these manual records.

## Tests and build

From `backend`, run `pytest` for the complete backend suite and `python retrieval_benchmark.py` for the offline labelled retrieval benchmark. The benchmark reports Recall@5/20, MRR, nDCG@5, hit rate, abstention precision/recall, and threshold sweeps against the saved local corpus; it makes no provider or website requests. From the project root, run `npm test -- --run` and `npm run build` for the frontend.

The benchmark's full results and known local-corpus coverage gaps are recorded in [RETRIEVAL_EVALUATION.md](RETRIEVAL_EVALUATION.md). Rebuilding the BM25 index from saved crawl/manual JSON is independent of crawling; do not crawl automatically as part of evaluation.
