"""Shared builder for the parser output contract.

Both parsers (CSV and resume Part B) call `build_parsed_record` so every record
written to `shared_memory/parsed_jsons/` has the exact same shape. Centralizing
this is the point of the contract: the merge engine can trust the structure
regardless of which parser produced it.

Each record carries three flat provenance fields — `source`, `method`,
`procured_at` — used directly by the merge engine (which regenerates the
canonical `provenance` array at merge time). Parsers do NOT emit `provenance` or
`total_source_points`: provenance is rebuilt per canonical field during merge,
and total_source_points is just `len(records)` for the current merge, computed
inline. See merge.py (CHANGE 4).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional


def mtime_iso(path: str) -> str:
    """File last-modified time as an ISO-8601 UTC string (stable across re-runs)."""
    import os

    ts = os.path.getmtime(path)
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_location() -> dict:
    return {"city": None, "region": None, "country": None}


def _empty_links() -> dict:
    return {"linkedin": None, "github": None, "portfolio": None, "other": []}


def build_parsed_record(
    source: str,
    procured_at: str,
    method: str,
    *,
    full_name: Optional[str] = None,
    emails: Optional[List[str]] = None,
    phones: Optional[List[str]] = None,
    location: Optional[dict] = None,
    links: Optional[dict] = None,
    headline: Optional[str] = None,
    years_experience: Optional[float] = None,
    skills: Optional[List[dict]] = None,
    experience: Optional[List[dict]] = None,
    education: Optional[List[dict]] = None,
    projects: Optional[List[dict]] = None,
) -> dict:
    return {
        "source": source,
        "method": method,
        "procured_at": procured_at,
        "full_name": full_name,
        "emails": emails or [],
        "phones": phones or [],
        "location": {**_empty_location(), **(location or {})},
        "links": {**_empty_links(), **(links or {})},
        "headline": headline,
        "years_experience": years_experience,
        "skills": skills or [],
        "experience": experience or [],
        "education": education or [],
        "projects": projects or [],
    }
