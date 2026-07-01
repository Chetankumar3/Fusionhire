"""Field normalizers — the single place formatting logic lives.

Every parser extracts raw fields and then hands them to these functions; nothing
downstream re-formats. Each function accepts a string / list / dict and returns
normalized JSON-safe output (string, list, or dict). Unknown / unparseable input
returns null/empty — never a guessed value.
"""

from __future__ import annotations

import calendar
import re
from typing import List, Optional, Union

import phonenumbers
import pycountry

# --------------------------------------------------------------------------- #
# Names & emails                                                              #
# --------------------------------------------------------------------------- #


def normalize_name(value: Optional[str]) -> Optional[str]:
    """Collapse internal whitespace and trim. Returns None for empty input."""
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def normalize_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    e = str(value).strip().lower()
    # minimal sanity check; unknown/garbage -> None, never invented
    return e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e) else None


def normalize_emails(value: Union[str, List[str], None]) -> List[str]:
    """Accept a string or list; return a de-duplicated, order-preserved list."""
    items = _as_list(value)
    out: List[str] = []
    for item in items:
        e = normalize_email(item)
        if e and e not in out:
            out.append(e)
    return out


# --------------------------------------------------------------------------- #
# Phones -> E.164                                                             #
# --------------------------------------------------------------------------- #


def normalize_phone(value: Optional[str], default_region: Optional[str] = None) -> Optional[str]:
    """Return an E.164 string, or None if it can't be parsed as a valid number."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        num = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    # Accept valid OR possible numbers: this keeps well-formed international
    # numbers (incl. reserved/fiction ranges like UK 07700 900xxx) while still
    # rejecting true garbage, which fails parsing outright.
    if not (phonenumbers.is_valid_number(num) or phonenumbers.is_possible_number(num)):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def normalize_phones(
    value: Union[str, List[str], None], default_region: Optional[str] = None
) -> List[str]:
    items = _as_list(value)
    out: List[str] = []
    for item in items:
        p = normalize_phone(item, default_region)
        if p and p not in out:
            out.append(p)
    return out


# --------------------------------------------------------------------------- #
# Location -> {city, region, country (ISO-3166 alpha-2)}                      #
# --------------------------------------------------------------------------- #


def _country_alpha2(value: Optional[str]) -> Optional[str]:
    """Resolve a country name / alpha-2 / alpha-3 to ISO-3166 alpha-2, else None."""
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    if len(v) == 2 and v.isalpha():
        rec = pycountry.countries.get(alpha_2=v.upper())
        return rec.alpha_2 if rec else None
    if len(v) == 3 and v.isalpha():
        rec = pycountry.countries.get(alpha_3=v.upper())
        if rec:
            return rec.alpha_2
    try:
        return pycountry.countries.lookup(v).alpha_2
    except LookupError:
        return None


def normalize_location(value) -> dict:
    """Map input to {city, region, country}. country is ISO-3166 alpha-2.

    Accepts a "city, region, country" string (positional), a list, or a dict
    keyed by city/region/country. Missing pieces stay None.
    """
    city = region = country = None

    if isinstance(value, dict):
        city = value.get("city")
        region = value.get("region") or value.get("state")
        country = value.get("country")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
        city = parts[0] if len(parts) > 0 else None
        region = parts[1] if len(parts) > 1 else None
        country = parts[2] if len(parts) > 2 else None
    elif isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        city = parts[0] if len(parts) > 0 else None
        region = parts[1] if len(parts) > 1 else None
        country = parts[2] if len(parts) > 2 else None

    return {
        "city": _clean_str(city),
        "region": _clean_str(region),
        "country": _country_alpha2(country),
    }


# --------------------------------------------------------------------------- #
# Dates -> YYYY-MM                                                            #
# --------------------------------------------------------------------------- #

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

_ONGOING = {"present", "current", "ongoing"}


def _fmt_ym(year: Optional[int], month: Optional[int]) -> Optional[str]:
    if year is None or month is None:
        return None
    if not (1 <= month <= 12):
        return None
    if not (1 <= year <= 9999):
        return None
    return f"{year:04d}-{month:02d}"


def normalize_date(value) -> Optional[str]:
    """Normalize a date to YYYY-MM. Ongoing markers -> None. Unparseable -> None.

    Ambiguity rules (per spec):
      XX-XX-XXXX -> DD-MM-YYYY    XXXX-XX-XX -> YYYY-MM-DD    XX-XXXX-XX -> DD-YYYY-MM
      2-component numeric        -> the 4-digit part is the year, the other is month
      month name + year (either order) -> YYYY-MM
    Both '-' and '/' are valid delimiters.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    if re.sub(r"\s+", "", s.lower()) in _ONGOING:
        return None

    # Month name + 4-digit year, in either order.
    month_from_name = None
    for tok in re.findall(r"[a-zA-Z]+", s.lower()):
        if tok in _MONTHS:
            month_from_name = _MONTHS[tok]
            break
    year_match = re.search(r"\d{4}", s)
    if month_from_name and year_match:
        return _fmt_ym(int(year_match.group()), month_from_name)

    # Pure-numeric components split on '-' or '/'.
    parts = [p for p in re.split(r"[-/]", s) if p.strip()]
    if parts and all(re.fullmatch(r"\d+", p.strip()) for p in parts):
        parts = [p.strip() for p in parts]
        nums = [int(p) for p in parts]
        widths = [len(p) for p in parts]

        if len(parts) == 3:
            if widths[2] == 4:  # XX-XX-XXXX -> DD-MM-YYYY
                return _fmt_ym(nums[2], nums[1])
            if widths[0] == 4:  # XXXX-XX-XX -> YYYY-MM-DD
                return _fmt_ym(nums[0], nums[1])
            if widths[1] == 4:  # XX-XXXX-XX -> DD-YYYY-MM
                return _fmt_ym(nums[1], nums[2])
            return None

        if len(parts) == 2:
            if widths[1] == 4 and widths[0] <= 2:  # MM-YYYY
                return _fmt_ym(nums[1], nums[0])
            if widths[0] == 4 and widths[1] <= 2:  # YYYY-MM
                return _fmt_ym(nums[0], nums[1])
            return None

    return None


def year_from_date(value) -> Optional[int]:
    """Extract a 4-digit year (used for education end_year). Unparseable -> None."""
    if value is None:
        return None
    s = str(value)
    m = re.search(r"\d{4}", s)
    if m:
        y = int(m.group())
        if 1 <= y <= 9999:
            return y
    ym = normalize_date(value)
    return int(ym[:4]) if ym else None


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _as_list(value) -> List:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _clean_str(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None
