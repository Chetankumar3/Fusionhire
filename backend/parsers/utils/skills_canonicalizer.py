"""Skill canonicalization with runtime auto-registration.

`canonicalize_skills(skills, source)` takes the raw skill strings extracted from
one source plus that record's `source` id, and returns canonical skill objects:

    [{"name": "<canonical>", "sources": ["<source>"]}, ...]

Lookup is O(1) via `mappings.json`. On a miss the skill auto-registers itself:
its raw form becomes a new canonical key in both `groupings.json` and
`mappings.json` (written incrementally — this does NOT invoke generate_mappings).
That makes the taxonomy self-extending and keeps runs deterministic: a skill seen
once resolves to the same canonical name on every later lookup and re-run.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

try:  # works both as a package import and as a same-dir script import
    from .skill_norm import normalize_skill
except ImportError:  # pragma: no cover
    from skill_norm import normalize_skill

_HERE = os.path.dirname(os.path.abspath(__file__))
GROUPINGS = os.path.join(_HERE, "groupings.json")
MAPPINGS = os.path.join(_HERE, "mappings.json")


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)


def _register(skill_raw: str, groupings: dict, mappings: dict) -> str:
    """Add a never-seen skill as its own canonical entry; persist both files."""
    norm = normalize_skill(skill_raw)
    # groupings.json: raw skill_name -> [skill_name, normalized_skill_name]
    groupings[skill_raw] = [skill_raw, norm]
    # mappings.json: both the raw and normalized form point at the raw canonical
    mappings[skill_raw] = skill_raw
    mappings[norm] = skill_raw
    _dump(GROUPINGS, groupings)
    _dump(MAPPINGS, mappings)
    return skill_raw


def canonicalize_skills(skills: List[str], source: str) -> List[Dict]:
    """Canonicalize a record's skills. Deduplicates by canonical name within the
    record so each skill object carries exactly one source (the parser-stage
    guarantee the merge engine relies on)."""
    if not skills:
        return []

    groupings = _load(GROUPINGS)
    mappings = _load(MAPPINGS)

    seen: Dict[str, Dict] = {}  # canonical -> skill object (preserves first order)
    for raw in skills:
        if raw is None:
            continue
        raw = str(raw).strip()
        if not raw:
            continue
        norm = normalize_skill(raw)
        if not norm:
            continue
        canonical = mappings.get(norm)
        if canonical is None:
            canonical = _register(raw, groupings, mappings)
        if canonical not in seen:
            seen[canonical] = {"name": canonical, "sources": [source]}

    return list(seen.values())


if __name__ == "__main__":
    # Demo: a known skill, a synonym, and an unknown (auto-registered) one.
    print(canonicalize_skills(["JS", "javascript", "Python", "Svelte"], "demo_1"))
