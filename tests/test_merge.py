"""Merge-engine unit tests against synthetic records (no DB needed).

Reflects the CHANGE 4 architecture: merge_documents(records) rebuilds a canonical
profile from scratch; per-field functions return {value, source, method,
confidence}; provenance is one entry per top-level field.
"""

from parsers.utils.record import build_parsed_record
from merge_engine.merge import merge_documents, field_experience, field_projects

T1 = "2026-01-01T00:00:00Z"
T2 = "2026-05-01T00:00:00Z"


def _r(source, t, **kw):
    return build_parsed_record(source, t, "normalization", **kw)


def test_email_phone_union_and_max_years_and_tsp():
    r1 = _r("csv_1", T1, full_name="A B", emails=["a@x.com"], phones=["+10000000000"], years_experience=3)
    r2 = _r("csv_2", T2, full_name="A B", emails=["a2@x.com"], phones=["+10000000000"], years_experience=5)
    m = merge_documents([r1, r2])
    assert set(m["emails"]) == {"a@x.com", "a2@x.com"}
    assert m["phones"] == ["+10000000000"]          # union, no dup
    assert m["years_experience"] == 5               # max
    assert m["total_source_points"] == 2            # = len(records)


def test_full_name_winner_is_latest_procured_at():
    r1 = _r("csv_1", T1, full_name="Bob", emails=["a@x.com"])
    r2 = _r("csv_2", T2, full_name="Robert Long", emails=["a@x.com"])
    m = merge_documents([r1, r2])
    assert m["full_name"] == "Robert Long"          # T2 > T1


def test_skill_confidence_is_occurrences_over_len_records():
    r1 = _r("csv_1", T1, emails=["a@x.com"], skills=[{"name": "Python", "sources": ["csv_1"]}])
    r2 = _r("csv_2", T2, emails=["a@x.com"],
            skills=[{"name": "Python", "sources": ["csv_2"]}, {"name": "Go", "sources": ["csv_2"]}])
    m = merge_documents([r1, r2])
    conf = {s["name"]: s["confidence"] for s in m["skills"]}
    assert conf["Python"] == 1.0                    # 2 / 2 records
    assert conf["Go"] == 0.5                         # 1 / 2 records


def test_experience_dedup_and_year_mismatch_confidence():
    # EDGE CASE: same (company,title) with disagreeing start years -> 0.6 score.
    r1 = _r("csv_1", T1, emails=["a@x.com"],
            experience=[{"company": "Acme", "title": "Eng", "start": "2020-01", "end": "2021-01", "summary": "short"}])
    r2 = _r("csv_2", T2, emails=["a@x.com"],
            experience=[{"company": "Acme", "title": "Eng", "start": "2019-06", "end": None, "summary": "a much longer summary"}])
    res = field_experience([r1, r2])
    assert len(res["value"]) == 1
    e = res["value"][0]
    assert e["start"] == "2019-06"                   # earliest
    assert e["end"] is None                          # any-null-end wins (ongoing)
    assert e["summary"] == "a much longer summary"   # longest
    assert "_start_years" not in e                   # no internal bookkeeping leaks
    assert res["confidence"] == 0.6                  # mismatch penalty
    # no mismatch -> 1.0
    assert field_experience([r1])["confidence"] == 1.0


def test_education_dedup_keeps_latest_end_year():
    r1 = _r("csv_1", T1, emails=["a@x.com"],
            education=[{"institution": "IIIT", "degree": "B.Tech", "field": "CS", "end_year": 2026}])
    r2 = _r("csv_2", T2, emails=["a@x.com"],
            education=[{"institution": "IIIT", "degree": "b tech", "field": "Computer Science", "end_year": 2027}])
    m = merge_documents([r1, r2])
    assert len(m["education"]) == 1                  # normalized-degree dedup
    ed = m["education"][0]
    assert ed["end_year"] == 2027                    # latest procured_at source
    assert ed["field"] == "Computer Science"         # longest field string


def test_projects_dedup_by_title_with_winner_fields():
    r1 = _r("csv_1", T1, emails=["a@x.com"],
            projects=[{"title": "Ping", "description": "short", "tech_stack": ["Go"]}])
    r2 = _r("csv_2", T2, emails=["a@x.com"],
            projects=[{"title": "ping", "description": "a longer newer description", "tech_stack": ["Go", "Redis"]}])
    res = field_projects([r1, r2])
    assert len(res["value"]) == 1                    # normalized-title dedup
    p = res["value"][0]
    assert p["description"] == "a longer newer description"   # latest procured_at
    assert p["tech_stack"] == ["Go", "Redis"]        # latest procured_at (more elems too)
    assert res["source"] == "multiple" and res["method"] == "union"


def test_provenance_is_one_entry_per_field():
    r1 = _r("csv_1", T1, full_name="A B", emails=["a@x.com"],
            skills=[{"name": "Python", "sources": ["csv_1"]}])
    r2 = _r("resume_x", T2, full_name="A B", emails=["a@x.com"],
            links={"linkedin": "ln"})
    m = merge_documents([r1, r2])
    prov = {e["field"]: e for e in m["provenance"]}
    for f in ("full_name", "emails", "phones", "location", "headline", "years_experience",
              "links.linkedin", "links.github", "links.portfolio", "links.other",
              "experience", "education", "projects", "skills"):
        assert f in prov, f
    # Union fields carry the sentinels.
    assert prov["emails"]["source"] == "multiple" and prov["emails"]["method"] == "union"
    # Single-winner field carries the winning record's own source.
    assert prov["links.linkedin"]["source"] == "resume_x"
    # skills provenance value = list of canonical names.
    assert prov["skills"]["value"] == ["Python"]
