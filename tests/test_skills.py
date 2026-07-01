"""Skill canonicalization tests (synonyms collapse; per-record dedup)."""

from parsers.utils.skills_canonicalizer import canonicalize_skills


def test_synonyms_collapse_to_canonical():
    out = canonicalize_skills(["js", "JavaScript", "PY", "docker swarm"], "src_1")
    names = {o["name"] for o in out}
    assert names == {"JavaScript", "Python", "Docker Swarm"}
    # Each record-stage skill carries exactly one source.
    for o in out:
        assert o["sources"] == ["src_1"]


def test_duplicate_canonical_deduped_within_record():
    # "js" and "javascript" both map to JavaScript -> a single skill object.
    out = canonicalize_skills(["js", "javascript"], "src_1")
    assert len(out) == 1
    assert out[0]["name"] == "JavaScript"
