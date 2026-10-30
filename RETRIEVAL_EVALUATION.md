# VVCE Student Assistant — Retrieval Evaluation

**Evaluation date:** 31 October 2026  
**Scope:** BM25 evidence selection against the local corpus. No crawler run, index rebuild, Gemini call, Groq call, or frontend/browser evaluation was performed for this report.

## Executive summary

The local corpus supports several high-value student questions, including facilities, undergraduate programmes, admission eligibility, ISE, Civil Engineering, placements, scholarships, and broad course queries. The retrieval audit also found material weaknesses that automated tests do not currently detect:

- A question asking for the difference between departments and programmes retrieves department pages but misses the admissions programme summary.
- A specific ISE clubs follow-up retrieves a general homepage clubs chunk and an ISE **Mission** chunk, rather than an ISE clubs section.
- An unrelated question, “What is today's cafeteria menu on Mars?”, returns five chunks from unrelated sources instead of no evidence. The close variant without “today's” returns no evidence, so the current positive-score check is sensitive to wording.
- “What is VVCE?” ranks an admissions FAQ first and the homepage second.
- The broad department result covers 14 department URLs, including academic support units, but has no authoritative evidence classifying engineering departments separately from support departments.

For this manually reviewed 23-query smoke benchmark, 14 cases were judged to retrieve good supporting evidence, 5 partial, and 4 failed. These are descriptive manual judgments, not a statistically significant retrieval metric or a gold-labelled test set.

## Corpus and method

The crawler corpus contained **28 documents and 250 BM25 chunks**. All 28 records were marked `crawled`; the manual source file existed but contained **0 records**. The corpus included 14 URLs matching the department-page path. No claim is made here about coverage of the live website or documents outside this local snapshot.

The evaluation loaded the saved crawl records, regenerated chunks with the project chunker, rebuilt a temporary in-memory `BM25Okapi` index using the same tokenization plus title and URL terms, and called `retrieve_evidence`. It did not write to the saved corpus or index. Each query was manually checked for whether its selected chunks contained useful evidence for the requested subject, not merely whether a URL matched a keyword.

The existing focused retrieval regression suite was also run: **14 passed**. That suite verifies several source-selection behaviors, but the absent-evidence case tests only one wording and the club follow-up test checks URL focus without ensuring that the selected section actually supports the answer.

Overall verification after preparing the report: **73 backend tests passed**, **5 frontend tests passed**, and the Vite production build succeeded. These automated results do not replace the manual corpus judgments above and do not imply live-provider or real-browser verification.

## Query results

| Query category | Judgment | Retrieval observation |
|---|---|---|
| College overview — “What is VVCE?” | Partial | Homepage appears, but after the admissions FAQ. Other selected evidence includes old induction-report chunks. |
| Facilities | Good | `/facilities` is ranked first; physical education and library/facility sources add useful detail. |
| List all departments | Partial | 14 distinct department pages are selected. The set includes academic support units such as Chemistry, Physics, Mathematics, Humanities and Physical Education; the corpus has no canonical list clarifying the intended scope. |
| Undergraduate programmes | Good | Admissions summary is selected alone and contains the six-programme summary tested by the existing regression suite. |
| ISE overview | Good | ISE page ranks first, though some extra selected evidence is admissions or unrelated department material. |
| Civil Engineering existence | Good | Civil Engineering page ranks first. Remaining broad-list evidence is mostly unrelated. |
| Difference between departments and programmes | **Fail** | Department pages are returned, but the admissions programme summary is absent. The current department branch takes precedence over the course/programme branch. |
| Available courses | Good | Multiple degree and department pages are selected. A student asking about “courses” may still need clarification between degree programmes and individual curriculum courses. |
| Admissions and eligibility | Good | Admissions eligibility and UG/PG process chunks are selected. |
| Fees | Partial | Admissions results contain scholarship/fee-waiver details and the MBA department has a 2025–26 MBA fee structure. The top evidence does not provide one general engineering tuition schedule. |
| Scholarships | Good | Admissions FAQ evidence explicitly describes scholarships and fee waivers. Some lower-ranked induction reports are unrelated. |
| ISE faculty contacts | Partial | ISE pages are selected, but the top sections are department overview/course content rather than a clearly targeted faculty-contact section. |
| Student clubs | Partial | The homepage chunk contains a list of club names and induction PDFs add dated examples. The selected homepage chunk is labelled `Research Studio`, and the corpus does not establish that its visible names are a complete current list. |
| Placements | Good | The placements page ranks first and appears twice with relevant training/placement sections. |
| Internships | Good | Mechanical, ISE and related curriculum chunks contain internship information. Results are department-specific rather than a single college-wide policy. |
| Student activities and events | Good | Homepage and induction reports contain activities/events. Some evidence is from older induction reports, so dates matter. |
| ISE curriculum | Good | ISE page contains a year-based course/capstone outline. This is not necessarily a complete official syllabus. |
| Academic regulations | **Fail** | Retrieval returns induction reports and Academic Council meeting proceedings, not a student-facing regulations source. Evidence is insufficient for a regulations answer. |
| VVCE contact information | Good | Admissions FAQ contains institution/admissions phone and email contacts. It is not a universal directory for every department. |
| Facility follow-up — “Does it have a library?” | Good | Conversation history focuses retrieval on Facilities and the Library & Information Center. |
| ISE clubs follow-up — “What about its clubs?” | **Fail** | The homepage clubs chunk is selected, but the ISE chunk is labelled `Mission` and does not provide reliable ISE-specific club evidence. URL focus alone is not enough to pass this case. |
| Unsupported — “What is today's cafeteria menu on Mars?” | **Fail** | Five unrelated chunks are returned. The wording variant “What is the cafeteria menu on Mars?” returns no chunks. No relevance threshold or query-intent check separates this false positive. |
| Ambiguous — “When is it?” | Good (abstention) | No evidence is returned without conversational context. The chat layer should ask what event/date the student means. |

## Key retrieval defects and likely causes

1. **Intent-branch precedence:** `retrieve_evidence` treats a mixed department/programme comparison as a department-list request first. Because the branches are mutually exclusive, the programme summary is never added. This is a selection-logic defect, not a corpus absence.
2. **Weak abstention boundary:** Retrieval returns results whenever the best BM25 score is positive. The cafeteria/Mars example demonstrates that positive BM25 scores alone do not establish relevance. A query-level or score-calibrated abstention policy is needed; its threshold should be tuned against labelled VVCE queries rather than chosen arbitrarily.
3. **Follow-up section mismatch:** Acronym/path focus can satisfy retrieval even if the selected chunk’s `section` does not match the follow-up subject. Follow-up evaluation should check the evidence section and text, not just the URL.
4. **Chunk section metadata drift:** A homepage chunk whose text spans later page sections retains `Research Studio` as its section metadata while containing the club list. This makes retrieval evidence harder to interpret and citations less precise.
5. **Broad-query ranking noise:** “What is VVCE?” ranks the admissions FAQ above the homepage. Generic college terms and metadata can score strongly across pages; broad overview intent should favor an overview source when the corpus has one.
6. **Coverage and scope ambiguity:** The corpus has no canonical department taxonomy, student regulation source, clearly targeted ISE staff contact chunk, or general undergraduate fee schedule. These should be treated as source-coverage gaps or partial evidence, not answered with unsupported completeness claims.

## Answer and citation validation boundary

This report evaluates evidence retrieval only. It does not establish that Groq or Gemini will produce a correct answer from the selected evidence, that the answer is complete, or that generated Markdown citations are valid. Backend response `sources` are drawn from retrieved evidence, but generated answer links still need an explicit validation test against those evidence URLs. No live-provider verification was performed.

## Recommended next evaluation/fix sequence

1. Add a labelled benchmark with relevant chunk/source IDs and negative/ambiguous cases. Track Recall@k, MRR or nDCG alongside abstention precision; do not optimize only for passing URL substring checks.
2. Make mixed department/programme requests select both evidence families, while preserving the authoritative six-undergraduate-programme summary for undergraduate list questions.
3. Add score-margin or calibrated relevance checks so unrelated positive-score matches abstain. Include paraphrases of negative questions in tests.
4. Require topical section/text support for follow-ups; test ISE clubs, faculty contacts and other department-specific subtopics at chunk level.
5. Improve section-aware chunk boundaries so each chunk’s section metadata corresponds to the content it contains.
6. Add or manually ingest authoritative public sources for regulations, current club listings, faculty contacts and general fee schedules where available. Mark stale reports by source date.

## Reproduction

From the `backend` directory, run the current retrieval regression suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_rag_quality.py
```

The corpus experiment used the same functions as the application and left the knowledge base unchanged. A new crawl or index rebuild is **not required to reproduce this report**. A fresh authorized crawl would only be needed to evaluate newer source content or changed extraction output.

---

## Updated regression benchmark and implementation results — 31 October 2026

The 23-case diagnostic above is retained as the initial manual audit. The expanded repeatable benchmark is implemented in `backend/retrieval_benchmark.py` and currently contains 38 labelled cases: 30 answerable and 8 negative/ambiguous. It evaluates returned chunk text and expected source groups rather than passing on URL-only matches. It includes paraphrased unsupported questions, mixed department/programme requests, broad lists, overview, follow-ups, and multi-source topics. Run it from `backend` with:

```powershell
python retrieval_benchmark.py
```

On the current saved local corpus (118 source records, 2,785 regenerated chunks; no live crawl), the current run produced:

| Metric | Result |
|---|---:|
| Recall@5 | 0.879 |
| Recall@20 | 1.000 |
| MRR | 0.950 |
| nDCG@5 | 0.964 |
| Hit rate@5 | 1.000 |
| Abstention precision / recall | 1.000 / 1.000 |
| Answerable cases returned with relevant evidence | 30 / 30 |
| Negative/ambiguous cases incorrectly answered | 0 / 8 |

These are benchmark-set results, not a claim about arbitrary queries or answer faithfulness from a live LLM. Recall@k/MRR/nDCG are calculated at source-group level; the case evaluator also checks supporting text for relevant single-source cases. The benchmark’s fixed cases are useful regressions but remain a small, curated sample.

The relevance policy uses query concept coverage in addition to positive BM25 scores. On the current corpus, a sweep found that 0.40/0.50 coverage allowed one false answer and 0.00/0.25 allowed three; 0.60 rejected all eight labelled negatives while retaining all 30 positives. At 0.70, no labelled negatives passed but 3 of 30 positive cases were rejected. A margin-only sweep at 0.05 rejected most valid cases (only 30% of positive cases remained answerable) even though it rejected all eight negatives. Therefore the 0.60 coverage setting is supported by this benchmark; score margin alone is too destructive to be a useful abstention rule.

### Corrected software defects

- Mixed department/programme intent now retrieves both evidence families. Comprehensive department requests aggregate all 14 local department-path sources rather than a small score-ranked top-k; department pages are distinguished from the separate six-item undergraduate programme summary.
- Follow-up selection now requires topical section/text evidence. An empty ISE club section no longer counts as proof that ISE has clubs; the answer can use a clearly college-wide club list and should not claim department-specific coverage.
- Extraction preserves heading levels; chunking isolates sections and groups heading-only list cards under their appropriate list heading. Rebuilt chunks no longer inherit a prior unrelated section label.
- Overview requests prefer the homepage; query aliases and intent routing cover common department/programme/facility terms. Routed but unsupported subjects abstain rather than falling through to arbitrary BM25 matches.
- Previous assistant-generated text is no longer used as retrieval input or factual context for the next grounded answer. User wording remains available to interpret follow-ups.
- Index rebuild now installs the new BM25 index only after successful preparation, BM25 construction and atomic chunks-file replacement, preserving the live index on errors and retaining manual records.
- Generated Markdown citations are checked against retrieved evidence URLs; unsupported links are removed. Groq/Gemini truncation finish reasons return a clear incomplete-response error instead of silently passing a partial answer.

### Remaining corpus gaps and verification limits

The saved corpus still lacks a canonical authoritative taxonomy separating academic degree departments from support departments, a student-facing academic regulations source, a general current undergraduate fee schedule, and clearly populated ISE-specific clubs/faculty-contact sections. Some activity evidence is in dated induction reports. These are ingestion/source gaps, not retrieval defects; add current public material through the authorized crawl or the manual-document workflow before claiming complete coverage. The current local corpus does not establish whether those pages/documents now exist on the live site.

No crawl was run, no credentials were accessed or changed, and no Groq/Gemini calls or real-browser session were used. The latest backend run passed 88 tests; frontend tests passed 5; the Vite production build succeeded. These tests verify mocked provider behavior and component rendering, not live-provider reliability or browser deployment. Rebuild the index from saved JSON by restarting FastAPI; no fresh crawl is needed for the code-level retrieval/chunking changes. A fresh authorized crawl is only needed to refresh source coverage or acquire the crawler's newly preserved heading hierarchy.

### Later workspace audit — 31 October 2026

The workspace corpus has since grown from the original 28 documents to 118 saved records. This expanded snapshot surfaced additional selection defects that were corrected: 38 department-page URLs collapsed to 14 canonical department paths, but the previous 24-result cap was consumed by pagination duplicates; broad department aggregation now keeps one result per path. “Financial aid” queries could rank stale disclosure PDFs above the current admissions scholarship FAQ; scholarship intents now prioritize matching admissions FAQ evidence. Incidental mentions of academic regulations in induction reports were also treated like a dedicated policy source; the regulations route now requires a dedicated policy URL or section and abstains otherwise. Regression tests and benchmark expectations use canonical department source paths to avoid treating paginated copies as different departments.

The admission-list investigation is documented with the before/after chunk IDs, source URLs, section metadata, scores, full evidence text, and prompt-budget trace in [backend/ADMISSIONS_RETRIEVAL_DIAGNOSTIC.md](backend/ADMISSIONS_RETRIEVAL_DIAGNOSTIC.md).
