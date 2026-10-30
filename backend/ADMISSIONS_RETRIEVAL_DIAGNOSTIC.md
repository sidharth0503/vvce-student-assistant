# Admissions programme retrieval diagnostic

**Question under investigation:** `what programmes can i choose from to admit myself`  
**Source record:** `https://vvce.ac.in/admissions` (`source_type=crawled`, title `Admissions � Vidyavardhaka College of Engineering`, `date=null`; the saved record has `fetched_at=2026-10-30T19:49:02.134861+00:00`).  
**Method:** local saved corpus, production `iter_chunks`, `BM25Okapi`, `retrieve_evidence`, and `prepare_generation_context`. No website request or provider request was made.

## Finding

The saved original extracted text did contain the complete FAQ answer. It reports **6 undergraduate, 5 postgraduate, and 9 research (PhD) programmes** and includes all 20 names across those lists. Extraction was not the cause.

Before the fix, chunking had split the FAQ answer at the list boundary. Its first chunk ended after `Electrical`, while the next chunk held the remainder of the UG list, all PG/PhD lists, and following FAQ content. Retrieval returned only one chunk per URL in the programme branch, so it could not return the continuation from the same admissions URL. The generic programme branch also filled the remaining slots with unrelated department curriculum chunks whose “choose/electives” wording scored highly. `prepare_generation_context` received ten retrieval chunks but retained only the first four under its evidence budget: the incomplete admissions chunk and three unrelated department chunks. It did not drop the admissions chunk, but the list was already incomplete at its chunk boundary.

The student-facing answer described in the report is consistent with that incomplete, noisy evidence. The previous answer and its exact live Groq request are not persisted in this local project, so the causal comparison is based on the production retrieval/prompt path, not a replay of that historical provider response.

## Before-fix production retrieval trace

IDs below are zero-based indices in the locally regenerated chunk list at diagnostic time. The first four rows are the complete evidence retained by `prepare_generation_context` and sent to Groq. Rows 5–10 were selected by retrieval but dropped by the evidence budget. All rows have URL `https://vvce.ac.in/admissions` only where stated; the other URLs show noisy added material.

| Chunk ID | Score | Section | URL | Retrieval / prompt |
|---:|---:|---|---|---|
| 660 | 1.752425 | Frequently Asked Questions | `https://vvce.ac.in/admissions` | Retrieved and sent; incomplete list ends at “Electrical” |
| 225 | 8.841799 | Year 3: Elective Courses and Specializations | `/departments/department-of-electronics-communication-engineering` | Retrieved and sent; unrelated |
| 1784 | 8.841799 | Year 3: Elective Courses and Specializations | `/departments/department-of-electronics-communication-engineering?e-page-fee7152=2` | Retrieved and sent; duplicate paginated content |
| 1850 | 8.841799 | Year 3: Elective Courses and Specializations | `/departments/department-of-electronics-communication-engineering?e-page-fee7152=3` | Retrieved and sent; duplicate paginated content |
| 1916 | 8.841799 | Year 3: Elective Courses and Specializations | `/departments/department-of-electronics-communication-engineering?e-page-fee7152=5` | Retrieved, then omitted by context budget |
| 399 | 8.028781 | Year 4: Capstone and Career Preparation | `/departments/department-of-civil-engineering` | Retrieved, then omitted; unrelated |
| 2428 | 8.028781 | Year 4: Capstone and Career Preparation | `/departments/department-of-civil-engineering?e-page-37f2258=2` | Retrieved, then omitted; duplicate paginated content |
| 2481 | 8.028781 | Year 4: Capstone and Career Preparation | `/departments/department-of-civil-engineering?e-page-37f2258=3` | Retrieved, then omitted; duplicate paginated content |
| 2534 | 8.028781 | Year 4: Capstone and Career Preparation | `/departments/department-of-civil-engineering?e-page-37f2258=5` | Retrieved, then omitted; duplicate paginated content |
| 69 | 6.117542 | Year 3: Specialization and Career Preparation | `/departments/department-of-information-science-engineering` | Retrieved, then omitted; unrelated |

### Complete evidence text actually retained and sent before the fix

**660 — admissions FAQ, 1,241 characters:**

> Section: Frequently Asked Questions Why should my child study at VVCE? - We are an autonomous institute with industry relevant curriculum - We offer a blend of conventional and project-based teaching�learning experience - Our institute is accredited by NBA (National Board of Accreditation) and NAAC (National Assessment and Accreditation Council) with �A� grade - We have qualified and committed faculty members who bring out the best in every student - We provide the perfect ambience for holistic personality development through 40+ student-led clubs - We offer free incubation facilities for student start-ups - We provide excellent integrated training and placement opportunities - We imbibe research culture amongst students with research bent of mind - We have a cordial mentor�mentee guidance system - Our campus is spread over 23 acres of land with excellent infrastructure with state-of-the-art labs and industry collaborations How many programs does VVCE offer? VVCE offers 6 undergraduate programs, 5 postgraduate programs and 9 research (PhD) programs. UG: - Computer Science and Engineering - Computer Science and Engineering (Artificial Intelligence & Machine Learning) - Electronics and Communication Engineering - Electrical

**225 — ECE electives, 490 characters:**

> Section: Year 3: Elective Courses and Specializations In the later stages of the program, students can choose open electives and professional electives that align with their interests and career goals. High-achieving students may participate in honours programs or undergraduate research opportunities, engaging in advanced projects that contribute to the field and enhance academic experience. This flexibility allows students to tailor their education to specific fields within ECE.

**1784 and 1850 — duplicate ECE pagination chunks, each 490 characters:**

> Section: Year 3: Elective Courses and Specializations In the later stages of the program, students can choose open electives and professional electives that align with their interests and career goals. High-achieving students may participate in honours programs or undergraduate research opportunities, engaging in advanced projects that contribute to the field and enhance academic experience. This flexibility allows students to tailor their education to specific fields within ECE.

The ECE text above was repeated for IDs 1784 and 1850. It contained no programme-list evidence.

### Complete text of other retrieved chunks (not sent to Groq)

**1916 — same duplicated ECE text as IDs 225/1784/1850:**

> Section: Year 3: Elective Courses and Specializations In the later stages of the program, students can choose open electives and professional electives that align with their interests and career goals. High-achieving students may participate in honours programs or undergraduate research opportunities, engaging in advanced projects that contribute to the field and enhance academic experience. This flexibility allows students to tailor their education to specific fields within ECE.

**399, 2428, 2481, and 2534 — repeated Civil Engineering chunk, each 473 characters:**

> Section: Year 4: Capstone and Career Preparation Capstone Project: | Solve real-world issues through an interdisciplinary project. Electives: | Choose advanced courses like Urban Transportation planning, Pre-Stressed Concrete Career Focus: | Strengthen skills with resume writing, interviews, and job fairs. Graduate Prep: | Plan for further studies, exams like GATE, or international graduate programs. Over All Development: | Moulding oneself as an Industry ready person.

**69 — ISE career preparation, 485 characters:**

> Section: Year 3: Specialization and Career Preparation Core Specializations: | Dive deeper into areas like artificial intelligence, Cybersecurity, | machine learning, data science and software engineering. Advanced Electives: | Choose electives that align with the career goals and emerging | technologies. Job Preparation: | Focus on resume building, interview preparation and attending Job fairs. Research Opportunities: | Consider engaging in research projects or publishing papers.

### Adjacent admissions chunk the old source cap excluded

**661 — FAQ continuation, 1,228 characters, same official admissions URL and section:**

> Section: Frequently Asked Questions offers 6 undergraduate programs, 5 postgraduate programs and 9 research (PhD) programs. UG: - Computer Science and Engineering - Computer Science and Engineering (Artificial Intelligence & Machine Learning) - Electronics and Communication Engineering - Electrical and Electronics Engineering - Civil Engineering - Mechanical Engineering PG: - Masters of Business Administration - MCA - M.Tech in Computer Science and Engineering - M.Tech in Civil Engineering - M.Tech in ECE PhD: - Computer Science and Engineering - Electronics and Communication Engineering - Electrical and Electronics Engineering - Civil Engineering - Mechanical Engineering - MBA - Physics - Chemistry - Mathematics Is the institute accredited? Yes, it is accredited by the National Assessment and Accreditation Council (NAAC) with A grade since 2017 and by the National Board of Accreditation (NBA), New Delhi. What is the cut-off of CET & COMED-K ranking to get admission in VVCE? Please find the details about the cut-off ranks here: | Cut-off ranks Where does the CET document verification happen in Mysore? Its completely online. For more information | visit https://kea.kar.nic.in/ When do admissions to Engineering courses start? The admissions for the management quota begin in the month of April/May. For more details, please contact or mail to | ao@vvce.ac.in | / | admissions@vvce.ac.in |, Contact numbers: 0821-4276201 / 203 / 225. For admissions through CET, please visit | www.kea.kar.nic.in | For admissions through COMED-K, please visit | www.comedk.org

## After-fix production retrieval trace

The admissions FAQ parser now creates one question/answer chunk for the “How many programs…” FAQ. Programme-list, level-specific, count, and admission-choice intents prioritize that FAQ and do not pad its evidence with unrelated department pages.

| Chunk ID | Score | Section | URL | Characters |
|---:|---:|---|---|---:|
| 662 | 3.951732 | Frequently Asked Questions — How many programs does VVCE offer? | `https://vvce.ac.in/admissions` | 799 |

The evidence passed through the normal prompt-context budget remains one chunk (estimated whole-prompt size: 626 tokens). Complete evidence text:

> Section: Frequently Asked Questions � How many programs does VVCE offer? How many programs does VVCE offer? VVCE offers 6 undergraduate programs, 5 postgraduate programs and 9 research (PhD) programs. UG: - Computer Science and Engineering - Computer Science and Engineering (Artificial Intelligence & Machine Learning) - Electronics and Communication Engineering - Electrical and Electronics Engineering - Civil Engineering - Mechanical Engineering PG: - Masters of Business Administration - MCA - M.Tech in Computer Science and Engineering - M.Tech in Civil Engineering - M.Tech in ECE PhD: - Computer Science and Engineering - Electronics and Communication Engineering - Electrical and Electronics Engineering - Civil Engineering - Mechanical Engineering - MBA - Physics - Chemistry - Mathematics

The source citation returned by `/api/chat` is the official admissions URL. A regression test captures the actual Groq SDK request with a mocked client and verifies that this full evidence, all counts, and all names are present in its prompt; it also verifies the returned source URL. No live provider response was used.

## Related long FAQ/list behavior

The failure pattern was structural rather than unique to this question: a long FAQ section stored questions as plain text lines, so a fixed word-count chunk boundary could divide a single answer or list. The chunker now starts a separate evidence section at each question line in sections titled FAQ(s) or Frequently Asked Questions. This keeps each question with its answer, applies generally to other long saved FAQ pages, and retains source URL/title/date metadata. The local corpus contains 118 records at this run; no crawl was performed. List answers are verified against chunk text before being treated as supported.

## Regression coverage

`test_rag_quality.py` covers informal admission wording, all UG, PG and PhD lists, all levels, counts plus names, FAQ chunk atomicity, complete names in prompt context, and the official citation in the chat response. Provider calls are mocked.
