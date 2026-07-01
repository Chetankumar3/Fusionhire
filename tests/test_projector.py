"""Projector tests: path grammar, normalize/missing contracts, toggles."""

import pytest

from projector.projector import (
    MissingFieldError,
    ProjectorError,
    project_profile,
    resolve_path,
    validate_projection,
)

PROFILE = {
    "full_name": "Jane Doe",
    "emails": ["a@x.com", "b@x.com"],
    "phones": ["+10000000000"],
    "location": {"city": "Pune", "region": None, "country": "IN"},
    "links": {"linkedin": "ln", "github": None, "portfolio": None, "other": ["u1", "u2"]},
    "skills": [
        {"name": "Python", "confidence": 1.0, "sources": ["s1"]},
        {"name": "Go", "confidence": 0.5, "sources": ["s2"]},
    ],
    "experience": [{"company": "C", "title": "Eng", "_start_years": [2020]}],
    "overall_confidence": 0.9,
    "provenance": [{"field": "full_name", "value": "Jane Doe", "source": "s1", "method": "normalization"}],
}


def test_path_grammar_variants():
    assert resolve_path(PROFILE, "full_name") == "Jane Doe"
    assert resolve_path(PROFILE, "emails[0]") == "a@x.com"
    assert resolve_path(PROFILE, "location.city") == "Pune"
    assert resolve_path(PROFILE, "skills[].name") == ["Python", "Go"]
    assert resolve_path(PROFILE, "links.other[]") == ["u1", "u2"]
    assert resolve_path(PROFILE, "experience[0].company") == "C"


def test_more_than_one_array_map_is_unsupported():
    with pytest.raises(ProjectorError):
        resolve_path({"a": [{"b": [1, 2]}]}, "a[].b[]")


def test_unsupported_normalize_errors():
    cfg = {"fields": [{"path": "n", "from": "full_name", "normalize": "uppercase"}]}
    with pytest.raises(ProjectorError):
        project_profile(PROFILE, cfg)


def test_on_missing_error_raises():
    cfg = {"fields": [{"path": "yx", "from": "years_experience"}], "on_missing": "error"}
    with pytest.raises(MissingFieldError):
        project_profile(PROFILE, cfg)


def test_on_missing_omit_drops_key_after_validation():
    cfg = {
        "fields": [
            {"path": "name", "from": "full_name", "type": "string"},
            {"path": "yx", "from": "years_experience", "type": "number"},
        ],
        "on_missing": "omit",
    }
    out = validate_projection(project_profile(PROFILE, cfg), cfg)
    assert out == {"name": "Jane Doe"}


def test_toggles_strip_confidence_and_sources():
    cfg = {
        "fields": [{"path": "skills", "from": "skills", "type": "object[]"}],
        "include_confidence": False,
        "include_provenance": False,
    }
    out = project_profile(PROFILE, cfg)
    assert out["skills"] == [{"name": "Python"}, {"name": "Go"}]
    assert "overall_confidence" not in out
    assert "provenance" not in out


def test_toggles_on_includes_confidence_and_provenance():
    cfg = {
        "fields": [{"path": "skills", "from": "skills", "type": "object[]"}],
        "include_confidence": True,
        "include_provenance": True,
    }
    out = project_profile(PROFILE, cfg)
    assert out["overall_confidence"] == 0.9
    assert out["skills"][0]["confidence"] == 1.0
    assert out["skills"][0]["sources"] == ["s1"]
    assert isinstance(out["provenance"], list)


def test_internal_underscore_keys_are_stripped():
    cfg = {"fields": [{"path": "experience", "from": "experience", "type": "object[]"}]}
    out = project_profile(PROFILE, cfg)
    assert "_start_years" not in out["experience"][0]
