import main
import pytest
from types import SimpleNamespace
from crawler import extract_html_content
from bs4 import BeautifulSoup
import retrieval_benchmark


def _current_corpus_index():
    documents = list(main.iter_json_array(main.DATA_PATH))
    chunks = list(main.iter_chunks(documents))
    index = main.BM25Okapi(
        main.tokenize(f"{chunk['text']} {chunk.get('title', '')} "
                      f"{chunk.get('url', '').split('?', 1)[0].replace('-', ' ').replace('/', ' ')}")
        for chunk in chunks
    )
    return index, chunks


def test_department_question_retrieves_department_pages_not_admissions_only():
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence("What departments are available in VVCE?", index, chunks)
    sources = {item["url"] for item in evidence}
    department_sources = {url for url in sources if "/departments/department-of-" in url}

    assert len(department_sources) >= 10
    assert any("information-science-engineering" in url for url in department_sources)


def test_courses_question_retrieves_programme_pages():
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence("What courses are available?", index, chunks)
    assert any("department-of-" in item["url"] for item in evidence)
    assert not all("admissions" in item["url"] for item in evidence)


def test_what_is_vvce_retrieves_homepage():
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence("What is VVCE?", index, chunks)
    assert any(item["url"].rstrip("/") == "https://vvce.ac.in" for item in evidence)


def test_undergraduate_programmes_retrieve_engineering_department_evidence():
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence("What are these undergraduate programmes?", index, chunks)
    assert len(evidence) == 1
    assert "/admissions" in evidence[0]["url"]
    text = evidence[0]["text"]
    assert "VVCE offers 6 undergraduate programs" in text
    assert "Computer Science and Engineering (Artificial Intelligence & Machine Learning)" in text
    assert "Mechanical Engineering" in text
    assert "Information Science and Engineering" not in text


PROGRAMME_NAMES = {
    "Computer Science and Engineering",
    "Computer Science and Engineering (Artificial Intelligence & Machine Learning)",
    "Electronics and Communication Engineering",
    "Electrical and Electronics Engineering",
    "Civil Engineering",
    "Mechanical Engineering",
    "Masters of Business Administration",
    "MCA",
    "M.Tech in Computer Science and Engineering",
    "M.Tech in Civil Engineering",
    "M.Tech in ECE",
    "MBA",
    "Physics",
    "Chemistry",
    "Mathematics",
}


def _admissions_evidence(query):
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence(query, index, chunks)
    assert evidence, query
    assert all(item["url"].rstrip("/") == "https://vvce.ac.in/admissions" for item in evidence), [
        (item["url"], item.get("section")) for item in evidence]
    return evidence


def test_informal_admission_question_retrieves_complete_admissions_faq():
    evidence = _admissions_evidence("what programmes can i choose from to admit myself")
    text = "\n".join(item["text"] for item in evidence)
    assert "6 undergraduate programs" in text
    assert "5 postgraduate programs" in text
    assert "9 research (PhD) programs" in text
    assert all(name.lower() in text.lower() for name in PROGRAMME_NAMES)


@pytest.mark.parametrize(("query", "required"), [
    ("List all UG programmes", ("Computer Science and Engineering (Artificial Intelligence & Machine Learning)", "Mechanical Engineering")),
    ("What PG programmes are available?", ("Masters of Business Administration", "M.Tech in Civil Engineering")),
    ("Which PhD programmes does VVCE offer?", ("MBA", "Mathematics", "Chemistry")),
    ("List all programmes across every level", ("6 undergraduate programs", "5 postgraduate programs", "9 research (PhD) programs")),
    ("How many programmes are offered and what are their names?", ("6 undergraduate programs", "M.Tech in ECE", "Mathematics")),
])
def test_level_and_count_programme_queries_have_supported_names_and_official_citation(query, required):
    evidence = _admissions_evidence(query)
    text = "\n".join(item["text"] for item in evidence)
    assert all(name.lower() in text.lower() for name in required), (query, text)
    assert all(item["url"].rstrip("/") == "https://vvce.ac.in/admissions" for item in evidence)


def test_all_level_query_preserves_every_admissions_programme_name():
    evidence = _admissions_evidence("List all programmes across every level")
    text = "\n".join(item["text"] for item in evidence).lower()
    assert PROGRAMME_NAMES and all(name.lower() in text for name in PROGRAMME_NAMES)


def test_admissions_chat_passes_complete_faq_and_official_source(monkeypatch):
    index, chunks = _current_corpus_index()
    monkeypatch.setattr(main, "_index", index)
    monkeypatch.setattr(main, "_chunks", chunks)
    observed = {}

    def mocked_generation(_question, evidence, _history=None):
        observed["evidence"] = evidence
        return "VVCE programme lists are on the [official admissions page](https://vvce.ac.in/admissions/)."

    monkeypatch.setattr(main, "generate_answer", mocked_generation)
    response = main.chat(main.ChatRequest(message="what programmes can i choose from to admit myself"))

    answer_evidence = "\n".join(item["text"] for item in observed["evidence"])
    assert all(name.lower() in answer_evidence.lower() for name in PROGRAMME_NAMES)
    assert {item["url"].rstrip("/") for item in response["sources"]} == {"https://vvce.ac.in/admissions"}
    assert "https://vvce.ac.in/admissions/" in response["answer"]


def test_admissions_chat_sends_complete_faq_to_groq_and_returns_official_citation(monkeypatch):
    index, chunks = _current_corpus_index()
    monkeypatch.setattr(main, "_index", index)
    monkeypatch.setattr(main, "_chunks", chunks)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    calls = []
    answer = ("VVCE lists these programmes: " + "; ".join(sorted(PROGRAMME_NAMES)) +
              ". [Official admissions page](https://vvce.ac.in/admissions/).")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs:
        calls.append(kwargs) or SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=answer), finish_reason="stop")],
            usage=None))))
    monkeypatch.setattr(main, "OpenAI", lambda **_kwargs: client)

    response = main.chat(main.ChatRequest(message="what programmes can i choose from to admit myself"))

    prompt = calls[0]["messages"][0]["content"]
    assert all(name.lower() in prompt.lower() for name in PROGRAMME_NAMES)
    assert "6 undergraduate programs" in prompt
    assert "5 postgraduate programs" in prompt
    assert "9 research (PhD) programs" in prompt
    assert len(response["sources"]) == 1
    assert response["sources"][0]["url"].rstrip("/") == "https://vvce.ac.in/admissions"
    assert "[official admissions page](https://vvce.ac.in/admissions/)" in response["answer"].lower()
    assert all(name in response["answer"] and name.lower() in prompt.lower() for name in PROGRAMME_NAMES)


def test_programme_faq_question_and_answer_are_chunked_atomically():
    document = next(doc for doc in main.iter_json_array(main.DATA_PATH)
                    if doc["url"].rstrip("/") == "https://vvce.ac.in/admissions")
    chunks = list(main.iter_chunks([document]))
    faq = [chunk for chunk in chunks if "how many programs does vvce offer" in (chunk.get("section") or "").lower()]

    assert len(faq) == 1
    assert faq[0]["section"].startswith("Frequently Asked Questions")
    assert "6 undergraduate programs" in faq[0]["text"]
    assert all(name.lower() in faq[0]["text"].lower() for name in PROGRAMME_NAMES)
    assert faq[0]["url"].rstrip("/") == "https://vvce.ac.in/admissions"


def test_mixed_courses_or_departments_retrieves_multiple_departments():
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence("What courses or departments are in this college?", index, chunks)
    assert len({item["url"] for item in evidence if "/departments/department-of-" in item["url"]}) >= 10


def test_ise_acronym_resolves_to_information_science_department():
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence("Tell me about ISE", index, chunks)
    assert evidence
    assert all("information-science-engineering" in item["url"] for item in evidence)


def test_facilities_question_retrieves_facilities_source():
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence("What facilities are available?", index, chunks)
    assert any(item["url"].rstrip("/").endswith("/facilities") for item in evidence)


def test_clubs_question_retrieves_student_club_content():
    index, chunks = _current_corpus_index()
    evidence = main.retrieve_evidence("What student clubs are there?", index, chunks)
    assert evidence
    assert any("club" in (item["text"] + item["title"]).lower() for item in evidence)
    homepage = [item for item in evidence if item["url"].rstrip("/") == "https://vvce.ac.in"]
    assert homepage
    assert "Student clubs that cater" in homepage[0]["text"]
    assert not any("induction-report" in item["url"] for item in evidence)


def test_follow_up_uses_recent_conversation_to_focus_ise_clubs():
    index, chunks = _current_corpus_index()
    history = [
        {"role": "user", "content": "Tell me about ISE"},
        {"role": "assistant", "content": "ISE means Information Science and Engineering."},
    ]
    evidence = main.retrieve_evidence("What clubs does it have?", index, chunks, history)
    assert evidence
    assert any("orators club" in ((item.get("section") or "") + " " + item["text"]).lower()
               for item in evidence)
    assert not any("information-science-engineering" in item["url"] and item.get("section") == "Mission"
                   for item in evidence)


def test_missing_evidence_returns_no_retrieval_results():
    index, chunks = _current_corpus_index()
    for query in (
        "What is the cafeteria menu on Mars?",
        "What is today's cafeteria menu on Mars?",
        "Can I see today's lunch menu at VVCE?",
        "When is the intergalactic shuttle timetable?",
        "Do VVCE students get meals on the Moon?",
        "What is the exact annual BE tuition fee?",
    ):
        assert main.retrieve_evidence(query, index, chunks) == [], query


def test_labelled_retrieval_benchmark_checks_content_multisource_and_abstention():
    _documents, chunks, index = retrieval_benchmark.build_corpus()
    rows = retrieval_benchmark.evaluate(index, chunks)
    metrics = retrieval_benchmark.summarize(rows, chunks)

    assert len(rows) >= 35
    assert all(row["relevant"] for row in rows if not row["expected_abstain"]), [
        (row["name"], row["sources"], row["top_sections"]) for row in rows
        if not row["expected_abstain"] and not row["relevant"]]
    assert all(row["predicted_abstain"] for row in rows if row["expected_abstain"]), [
        (row["name"], row["sources"]) for row in rows
        if row["expected_abstain"] and not row["predicted_abstain"]]
    assert metrics["abstention_precision"] == 1.0
    assert metrics["abstention_recall"] == 1.0
    assert metrics["positive_nonabstention_rate"] == 1.0
    assert metrics["mrr"] >= 0.9
    assert metrics["ndcg_at_k"] >= 0.9

    mixed = next(row for row in rows if row["name"] == "all_programmes_and_departments")
    assert mixed["required_groups_hit"] == [True, True]
    all_departments = next(row for row in rows if row["name"] == "all_departments")
    assert len(all_departments["relevant_sources"]) == 14

    coverage_sweep = {row["coverage_threshold"]: row for row in retrieval_benchmark.run_threshold_sweep(
        chunks, index, thresholds=(0.0, 0.4, 0.5, 0.6, 0.7))}
    assert coverage_sweep[0.5]["negative_false_answer_count"] > 0
    assert coverage_sweep[0.6]["negative_false_answer_count"] == 0
    assert coverage_sweep[0.7]["positive_nonabstention_rate"] < 1.0


def test_grounded_prompt_uses_history_and_student_friendly_guidance():
    prompt = main._build_grounded_prompt("And what about its clubs?", [
        {"title": "ISE", "url": "https://vvce.ac.in/departments/department-of-information-science-engineering",
         "text": "Student clubs information.", "section": "Student Clubs"}
    ], [{"role": "user", "content": "Tell me about ISE"}])
    assert "Tell me about ISE" in prompt
    assert "Answer the current question directly" in prompt
    assert "Section: Student Clubs" in prompt
    assert "https://vvce.ac.in/departments/department-of-information-science-engineering" in prompt


def test_structure_aware_chunks_keep_heading_with_page_evidence():
    docs = [{"url": "https://vvce.ac.in/departments/ise", "title": "ISE",
             "text": "## Student Clubs\n" + "Code Club activities and opportunities. " * 20 +
                     "\n## Curriculum\n" + "Engineering course details. " * 20}]
    chunks = main.make_chunks(docs, size=70, overlap=10)
    assert chunks
    assert any(chunk.get("section") == "Student Clubs" and "Code Club" in chunk["text"] for chunk in chunks)
    assert any(chunk.get("section") == "Curriculum" and "course details" in chunk["text"] for chunk in chunks)
    assert all(not (chunk.get("section") == "Student Clubs" and "Engineering course details" in chunk["text"])
               for chunk in chunks)


def test_heading_only_card_lists_are_preserved_as_one_citable_section():
    doc = {"url": "https://vvce.ac.in/", "title": "VVCE homepage", "text":
           "## Student clubs that cater to a range of interests\n## Orators Club\n## VVCE ACM Student Chapter\n"
           "## Placement information\nStudents are prepared for career opportunities. " * 3}
    chunks = main.make_chunks([doc])
    club = next(chunk for chunk in chunks if chunk.get("section", "").startswith("Student clubs"))
    assert "Orators Club" in club["text"]
    assert "VVCE ACM Student Chapter" in club["text"]
    assert "career opportunities" not in club["text"]


def test_html_extraction_preserves_club_card_text_inside_generic_layout_divs():
    soup = BeautifulSoup("""<main><div class='card'><h3>Orators Club</h3>
      <div><span>Builds public speaking confidence.</span></div></div>
      <nav>Navigation should be omitted</nav><script>ignore()</script></main>""", "html.parser")
    text = extract_html_content(soup.main, 5000)
    assert "## Orators Club" in text
    assert "Builds public speaking confidence." in text
    assert "Navigation should be omitted" not in text


def test_chat_response_contract_keeps_sources_and_passes_history(monkeypatch):
    document = {"url": "https://vvce.ac.in/departments/department-of-information-science-engineering",
                "title": "Department of Information Science & Engineering", "date": "2026-10-20",
                "text": "Information Science Engineering students have access to clubs. " * 30}
    chunks = list(main.iter_chunks([document]))
    index = object()
    monkeypatch.setattr(main, "_index", index)
    monkeypatch.setattr(main, "_chunks", chunks)
    monkeypatch.setattr(main, "retrieve_evidence", lambda _q, _index, _chunks, _history: chunks)
    seen = {}

    def answer(question, evidence, history=None):
        seen["history"] = history
        seen["evidence"] = evidence
        return "The department has student clubs. [Source](https://vvce.ac.in/departments/department-of-information-science-engineering)"

    monkeypatch.setattr(main, "generate_answer", answer)
    response = main.chat(main.ChatRequest(message="What clubs does it have?", history=[
        main.ChatTurn(role="user", content="Tell me about ISE")
    ]))

    assert response["answer"].startswith("The department")
    assert response["sources"][0]["url"] == document["url"]
    assert response["sources"][0]["date"] == "2026-10-20"
    assert seen["history"] == [{"role": "user", "content": "Tell me about ISE"}]
    assert seen["evidence"]
