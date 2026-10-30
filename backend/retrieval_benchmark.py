"""Repeatable offline, source-labelled VVCE retrieval benchmark (no provider calls)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import main


@dataclass(frozen=True)
class Case:
    name: str
    query: str
    source_patterns: tuple[str, ...] = ()
    content_any: tuple[str, ...] = ()
    required_source_groups: tuple[tuple[str, ...], ...] = ()
    history: tuple[dict, ...] = ()
    abstain: bool = False
    all_department_sources: bool = False


CASES = (
    Case("overview", "What is VVCE?", ("https://vvce.ac.in/",), ("vvce", "college", "engineering")),
    Case("overview_paraphrase", "Tell me about the college", ("https://vvce.ac.in/",)),
    Case("facilities", "What facilities are available at VVCE?", ("/facilities",), ("hostel", "sports", "gym", "health center")),
    Case("gym_paraphrase", "Does the campus have a gym?", ("/facilities",), ("gym",)),
    Case("facilities_library", "Which facilities include a hostel and library?", ("/facilities", "/library-information-center"),
         required_source_groups=(("/facilities",), ("/library-information-center",))),
    Case("all_departments", "List all departments at VVCE", all_department_sources=True),
    Case("department_paraphrase", "Which engineering branches can I study?", ("/departments/department-of-",)),
    Case("undergraduate_programmes", "What undergraduate programmes does VVCE offer?", ("/admissions",),
         ("vvce offers 6 undergraduate programs",)),
    Case("bachelors_paraphrase", "Which bachelor's degrees can I study?", ("/admissions",),
         ("vvce offers 6 undergraduate programs",)),
    Case("ise_full_name", "Tell me about Information Science and Engineering",
         ("information-science-engineering",), ("information science", "ise")),
    Case("ise_acronym", "What is ISE?", ("information-science-engineering",)),
    Case("civil_department", "Does VVCE have a Civil Engineering department?",
         ("department-of-civil-engineering",), ("civil engineering",)),
    Case("departments_and_programmes", "What is the difference between departments and programmes?",
         required_source_groups=(("/admissions",), ("/departments/department-of-",))),
    Case("courses", "What courses are available?", ("/departments/department-of-",)),
    Case("admissions_eligibility", "What are the admission eligibility requirements?", ("/admissions",),
         ("eligibility", "admission process")),
    Case("mba_fee", "What is the MBA fee structure for 2025-26?",
         ("/department-of-business-administration",), ("mba fee structure",)),
    Case("scholarship", "Are scholarships available for students?", ("/admissions",),
         ("scholarship", "fee waiver")),
    Case("financial_aid_paraphrase", "Can students get financial aid?", ("/admissions",),
         ("scholarship", "financial aid")),
    Case("contact", "How can I contact VVCE admissions?", ("/admissions",),
         ("admissions@vvce.ac.in", "0821-4276201")),
    Case("student_clubs", "What student clubs are there at VVCE?", ("https://vvce.ac.in/",),
         ("orators club", "vvce acm student chapter")),
    Case("clubs_followup", "What about its clubs?", ("https://vvce.ac.in/",),
         ("orators club", "vvce acm student chapter"),
         history=({"role": "user", "content": "Tell me about Information Science and Engineering"},)),
    Case("placements", "Tell me about placements at VVCE", ("/placements",), ("training", "placement")),
    Case("internships", "Does VVCE provide internship opportunities?", ("/departments/department-of-",),
         ("internship", "internships")),
    Case("curriculum", "Where can I find the ISE curriculum?",
         ("information-science-engineering",), ("curriculum", "year 4")),
    Case("calendar", "Where can I see the academic calendar?", ("/academic-calendar",), ("calendar of events",)),
    Case("exam_notice", "Show me current examination notifications", ("/exam-notifications",), ("timetable", "exams")),
    Case("student_activities", "What student activities and events are available?",
         ("https://vvce.ac.in/", "/departments/")),
    Case("faculty_contacts", "How can I contact ISE faculty?",
         ("information-science-engineering",), ("teaching staff", "faculty coordinator", "hod")),
    Case("regulations_missing", "What are the student academic regulations?",
         abstain=True),
    Case("negative_mars_menu", "What is today's cafeteria menu on Mars?", abstain=True),
    Case("negative_lunch", "Can I see today's lunch menu at VVCE?", abstain=True),
    Case("negative_intergalactic", "When is the intergalactic shuttle timetable?", abstain=True),
    Case("negative_lunar_meals", "Do VVCE students get meals on the Moon?", abstain=True),
    Case("negative_exact_fee", "What is the exact annual BE tuition fee?", abstain=True),
    Case("ambiguous_when", "When is it?", abstain=True),
    Case("ambiguous_followup", "What about it?", abstain=True),
    Case("facility_followup", "Does it have a library?", ("/library-information-center",),
         ("library",), history=({"role": "user", "content": "What facilities are available at VVCE?"},)),
    Case("all_programmes_and_departments", "List all VVCE departments and undergraduate programmes",
         required_source_groups=(("/admissions",), ("/departments/department-of-",))),
)


def _matches_pattern(url: str, patterns: Iterable[str]) -> bool:
    url = url.lower()
    return any(pattern.lower() in url for pattern in patterns)


def _content_matches(item: dict, terms: tuple[str, ...]) -> bool:
    if not terms:
        return True
    content = " ".join((item.get("text", ""), item.get("section") or "", item.get("title", ""))).lower()
    return any(term.lower() in content for term in terms)


def evaluate(index, chunks, cases: Iterable[Case] = CASES, coverage: float = main.MIN_QUERY_CONCEPT_COVERAGE):
    all_department_urls = sorted({chunk.get("url", "").split("?", 1)[0].rstrip("/") for chunk in chunks
                                  if "/departments/department-of-" in chunk.get("url", "")})
    rows = []
    for case in cases:
        evidence = main.retrieve_evidence(case.query, index, chunks, list(case.history), min_coverage=coverage)
        sources = list(dict.fromkeys(item.get("url", "") for item in evidence))
        if case.all_department_sources:
            expected = set(all_department_urls)
            relevant_sources = {url for url in sources if url.split("?", 1)[0].rstrip("/") in expected}
        elif case.source_patterns:
            relevant_sources = {item.get("url", "") for item in evidence
                                if _matches_pattern(item.get("url", ""), case.source_patterns)
                                and _content_matches(item, case.content_any)}
        elif case.required_source_groups:
            relevant_sources = {url for url in sources if any(_matches_pattern(url, group) for group in case.required_source_groups)}
        else:
            relevant_sources = set()
        content_supported = any(_content_matches(item, case.content_any) for item in evidence) if case.content_any else bool(relevant_sources)
        group_hits = [any(_matches_pattern(url, group) for url in sources) for group in case.required_source_groups]
        relevant = bool(relevant_sources and content_supported) if (case.source_patterns or case.all_department_sources) else all(group_hits)
        if case.required_source_groups:
            relevant = all(group_hits)
        if case.all_department_sources:
            relevant = bool(expected) and expected.issubset({url.split("?", 1)[0].rstrip("/") for url in sources})
        predicted_abstain = not evidence
        rows.append({
            "name": case.name, "query": case.query, "expected_abstain": case.abstain,
            "predicted_abstain": predicted_abstain, "evidence_count": len(evidence),
            "sources": sources, "relevant_sources": sorted(relevant_sources),
            "relevant": relevant, "required_groups_hit": group_hits,
            "top_scores": [item.get("retrieval_score") for item in evidence[:3]],
            "top_sections": [item.get("section") for item in evidence[:5]],
            "query_coverage": [item.get("query_coverage") for item in evidence[:5]],
        })
    return rows


def _dcg(relevance: list[int]) -> float:
    return sum(value / math.log2(index + 2) for index, value in enumerate(relevance))


def summarize(rows: list[dict], chunks: list[dict], k: int = 5) -> dict:
    positive = [row for row in rows if not row["expected_abstain"]]
    negative = [row for row in rows if row["expected_abstain"]]
    precisions, recalls, reciprocal_ranks, ndcgs = [], [], [], []
    for row in positive:
        expected_urls = set(row["relevant_sources"])
        ranked_urls = row["sources"]
        relevant_ranked = [url in expected_urls for url in ranked_urls]
        if expected_urls:
            precisions.append(sum(relevant_ranked[:k]) / k)
            recalls.append(len({url for url in ranked_urls[:k] if url in expected_urls}) / len(expected_urls))
            first = next((i + 1 for i, value in enumerate(relevant_ranked) if value), None)
            reciprocal_ranks.append(1 / first if first else 0.0)
            ideal_count = min(k, len(expected_urls))
            ideal = sum(1 / math.log2(i + 2) for i in range(ideal_count))
            ndcgs.append(_dcg([int(x) for x in relevant_ranked[:k]]) / ideal if ideal else 0.0)
    true_abstain = sum(row["predicted_abstain"] for row in negative)
    false_abstain = sum(row["predicted_abstain"] for row in positive)
    false_answer = sum(not row["predicted_abstain"] for row in negative)
    abstention_precision = true_abstain / max(1, true_abstain + false_abstain)
    abstention_recall = true_abstain / max(1, len(negative))
    positive_recall = (len(positive) - false_abstain) / max(1, len(positive))
    recall20 = []
    hit_at_k = []
    for row in positive:
        expected_urls = set(row["relevant_sources"])
        if expected_urls:
            recall20.append(len({url for url in row["sources"][:20] if url in expected_urls}) / len(expected_urls))
            hit_at_k.append(float(any(url in expected_urls for url in row["sources"][:k])))
    return {
        "cases": len(rows), "positive_cases": len(positive), "expected_abstentions": len(negative),
        "precision_at_k": sum(precisions) / max(1, len(precisions)),
        "recall_at_k": sum(recalls) / max(1, len(recalls)),
        "mrr": sum(reciprocal_ranks) / max(1, len(reciprocal_ranks)),
        "ndcg_at_k": sum(ndcgs) / max(1, len(ndcgs)),
        "recall_at_20": sum(recall20) / max(1, len(recall20)),
        "hit_rate_at_k": sum(hit_at_k) / max(1, len(hit_at_k)),
        "abstention_precision": abstention_precision, "abstention_recall": abstention_recall,
        "positive_nonabstention_rate": positive_recall,
        "negative_false_answer_count": false_answer,
        "k": k, "coverage_threshold": main.MIN_QUERY_CONCEPT_COVERAGE,
    }


def build_corpus():
    documents = list(main.iter_json_array(main.DATA_PATH))
    chunks = list(main.iter_chunks(documents))
    index = main.BM25Okapi(
        main.tokenize(f"{chunk['text']} {chunk.get('title', '')} "
                      f"{chunk.get('url', '').split('?', 1)[0].replace('-', ' ').replace('/', ' ')}")
        for chunk in chunks
    )
    return documents, chunks, index


def run_threshold_sweep(chunks, index, thresholds=(0.0, 0.25, 0.4, 0.5, 0.6, 0.7, 0.8)):
    rows = []
    for threshold in thresholds:
        results = evaluate(index, chunks, coverage=threshold)
        metrics = summarize(results, chunks)
        metrics["coverage_threshold"] = threshold
        rows.append(metrics)
    return rows


def run_margin_sweep(chunks, index, thresholds=(0.0, 0.05, 0.1, 0.2, 0.4, 0.6)):
    """Diagnostic only: test whether top-score margin alone is a safe abstention signal."""
    baseline = evaluate(index, chunks, coverage=0.0)
    results = []
    for threshold in thresholds:
        true_abstain = false_abstain = false_answer = positive_accept = 0
        for row in baseline:
            scores = row["top_scores"]
            top = scores[0] if scores and scores[0] is not None else 0.0
            second = scores[1] if len(scores) > 1 and scores[1] is not None else 0.0
            relative_margin = max(0.0, (top - second) / top) if top > 0 else 0.0
            abstain = top <= 0 or relative_margin < threshold
            if row["expected_abstain"]:
                true_abstain += int(abstain)
                false_answer += int(not abstain)
            else:
                false_abstain += int(abstain)
                positive_accept += int(not abstain)
        results.append({
            "relative_margin_threshold": threshold,
            "abstention_precision": true_abstain / max(1, true_abstain + false_abstain),
            "abstention_recall": true_abstain / max(1, len([r for r in baseline if r["expected_abstain"]])),
            "positive_nonabstention_rate": positive_accept / max(1, len([r for r in baseline if not r["expected_abstain"]])),
            "negative_false_answer_count": false_answer,
        })
    return results


def main_cli():
    docs, chunks, index = build_corpus()
    rows = evaluate(index, chunks)
    result = {
        "documents": len(docs), "chunks": len(chunks),
        "metrics": summarize(rows, chunks),
        "threshold_sweep": run_threshold_sweep(chunks, index),
        "score_margin_sweep": run_margin_sweep(chunks, index),
        "cases": rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main_cli()
