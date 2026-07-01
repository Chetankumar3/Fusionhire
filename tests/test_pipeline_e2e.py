"""End-to-end "gold profile" test on the real sample inputs.

Runs CSV parser + resume Part B (from the committed intermediate raw JSON) +
merge in-process, then asserts the cross-source merge for the candidate who
appears in BOTH the CSV and the resume (Chetankumar). This is the headline
behaviour: one trustworthy profile assembled from two source types.
"""

import os

import paths
from parsers.csv_parser import csv_parser
from parsers.resume_parser import part_b
from merge_engine import merge


def _run_pipeline_inprocess():
    # Clean parsed_jsons, re-parse, merge into a fresh DB.
    for f in os.listdir(paths.PARSED_JSONS):
        if f.endswith(".json"):
            os.remove(os.path.join(paths.PARSED_JSONS, f))
    csv_parser.run()
    part_b.run()  # consumes committed raw_json/
    return merge.run(reset=True)


def test_chetan_merges_across_csv_and_resume():
    store = _run_pipeline_inprocess()
    profiles = store.all_active()

    chetan = next((p for p in profiles if (p.get("full_name") or "").startswith("Chetan")), None)
    assert chetan is not None, "merged Chetan profile should exist"

    # Two source points: csv row + resume.
    assert chetan["total_source_points"] == 2
    assert "chetanmajjagi3@gmail.com" in chetan["emails"]
    assert "+919148808717" in chetan["phones"]

    # Resume-only fields survived the merge.
    assert chetan["location"]["country"] == "IN"
    assert chetan["links"]["linkedin"]
    skill_names = {s["name"] for s in chetan["skills"]}
    assert {"Python", "Docker Swarm"}.issubset(skill_names)

    # CSV "Software Engineer" + 3 resume projects -> experience entries.
    titles = {e["title"] for e in chetan["experience"]}
    assert "Software Engineer" in titles
    assert len(chetan["experience"]) >= 4

    # Confidence is populated and in range.
    assert 0.0 <= chetan["overall_confidence"] <= 1.0


def test_no_garbage_crash_on_distinct_candidates():
    store = _run_pipeline_inprocess()
    # 5 distinct people from 6 records (Chetan appears twice).
    assert len(store.all_active()) == 5
