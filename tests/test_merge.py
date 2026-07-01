"""Merge-engine unit tests against synthetic records (no DB needed)."""

from parsers.utils.record import build_parsed_record
from merge_engine.merge import merge_documents

T1 = "2026-01-01T00:00:00Z"
T2 = "2026-05-01T00:00:00Z"
ST = {"csv_1": T1, "csv_2": T2}


def _r(source, t, **kw):
    return build_parsed_record(source, t, **kw)


def test_email_phone_union_and_max_years_and_tsp():
    r1 = _r("csv_1", T1, full_name="A B", emails=["a@x.com"], phones=["+10000000000"], years_experience=3)
    r2 = _r("csv_2", T2, full_name="A B", emails=["a2@x.com"], phones=["+10000000000"], years_experience=5)
    m = merge_documents([r1, r2], ST)
    assert set(m["emails"]) == {"a@x.com", "a2@x.com"}
    assert m["phones"] == ["+10000000000"]          # union, no dup
    assert m["years_experience"] == 5               # max
    assert m["total_source_points"] == 2


def test_full_name_winner_is_latest_procured_at():
    r1 = _r("csv_1", T1, full_name="Bob", emails=["a@x.com"])
    r2 = _r("csv_2", T2, full_name="Robert Long", emails=["a@x.com"])
    m = merge_documents([r1, r2], ST)
    assert m["full_name"] == "Robert Long"          # T2 > T1


def test_skill_confidence_is_occurrences_over_source_points():
    r1 = _r("csv_1", T1, emails=["a@x.com"], skills=[{"name": "Python", "sources": ["csv_1"]}])
    r2 = _r("csv_2", T2, emails=["a@x.com"],
            skills=[{"name": "Python", "sources": ["csv_2"]}, {"name": "Go", "sources": ["csv_2"]}])
    m = merge_documents([r1, r2], ST)
    conf = {s["name"]: s["confidence"] for s in m["skills"]}
    assert conf["Python"] == 1.0                    # 2 / 2
    assert conf["Go"] == 0.5                         # 1 / 2


def test_experience_dedup_and_year_mismatch_flag():
    # EDGE CASE: same (company,title) with disagreeing start years -> mismatch.
    r1 = _r("csv_1", T1, emails=["a@x.com"],
            experience=[{"company": "Acme", "title": "Eng", "start": "2020-01", "end": "2021-01", "summary": "short"}])
    r2 = _r("csv_2", T2, emails=["a@x.com"],
            experience=[{"company": "Acme", "title": "Eng", "start": "2019-06", "end": None, "summary": "a much longer summary"}])
    m = merge_documents([r1, r2], ST)
    assert len(m["experience"]) == 1
    e = m["experience"][0]
    assert e["start"] == "2019-06"                   # earliest
    assert e["end"] is None                          # any-null-end wins (ongoing)
    assert e["summary"] == "a much longer summary"   # longest
    assert e["_start_years"] == [2019, 2020]         # mismatch recorded
    # mismatch should pull experience confidence below 1.0 -> overall below the
    # no-mismatch case.
    clean = merge_documents([r1], {"csv_1": T1})
    assert m["overall_confidence"] < clean["overall_confidence"]


def test_education_dedup_keeps_latest_end_year():
    r1 = _r("csv_1", T1, emails=["a@x.com"],
            education=[{"institution": "IIIT", "degree": "B.Tech", "field": "CS", "end_year": 2026}])
    r2 = _r("csv_2", T2, emails=["a@x.com"],
            education=[{"institution": "IIIT", "degree": "b tech", "field": "Computer Science", "end_year": 2027}])
    m = merge_documents([r1, r2], ST)
    assert len(m["education"]) == 1                  # normalized-degree dedup
    ed = m["education"][0]
    assert ed["end_year"] == 2027                    # latest procured_at source
    assert ed["field"] == "Computer Science"         # longest field string
