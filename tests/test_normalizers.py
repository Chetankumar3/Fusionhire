"""Normalizer unit tests, including the trickier date ambiguity rules."""

import pytest

from parsers.utils.normalizers import (
    normalize_date,
    normalize_emails,
    normalize_location,
    normalize_phone,
    year_from_date,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12-03-2022", "2022-03"),   # DD-MM-YYYY
        ("2022-03-15", "2022-03"),   # YYYY-MM-DD
        ("15-2022-03", "2022-03"),   # DD-YYYY-MM
        ("03/2022", "2022-03"),      # MM/YYYY
        ("2022/03", "2022-03"),      # YYYY/MM
        ("Jan 2022", "2022-01"),
        ("January 2022", "2022-01"),
        ("2022 Feb", "2022-02"),
        ("present", None),           # ongoing -> null
        ("Current", None),
        ("garbage", None),
        ("13-2022", None),           # month out of range -> no guess
        ("2022-13-01", None),
    ],
)
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


def test_year_from_date():
    assert year_from_date("May 2021") == 2021
    assert year_from_date("2019") == 2019
    assert year_from_date("nope") is None


def test_normalize_phone_e164():
    assert normalize_phone("+91 9148808717") == "+919148808717"
    assert normalize_phone("+1 (415) 555-0123") == "+14155550123"
    # Well-formed but reserved UK fiction number is kept (possible), not dropped.
    assert normalize_phone("+447700900123") == "+447700900123"
    # True garbage with no country code cannot be parsed -> None, never invented.
    assert normalize_phone("12345") is None


def test_normalize_location_iso_country():
    assert normalize_location("Bengaluru, Karnataka, India") == {
        "city": "Bengaluru",
        "region": "Karnataka",
        "country": "IN",
    }
    assert normalize_location({"city": "Tokyo", "state": "Tokyo", "country": "JP"})["country"] == "JP"
    # Unresolvable country -> None (not invented).
    assert normalize_location("Atlantis, , Nowhereland")["country"] is None


def test_normalize_emails_dedup_and_lowercase():
    assert normalize_emails(["A@X.com", "a@x.com", "bad"]) == ["a@x.com"]
