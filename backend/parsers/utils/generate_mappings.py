"""Manual developer seeding script (NOT part of the active pipeline).

Whenever `groupings.json` is hand-edited, run this once to regenerate
`mappings.json` from scratch:

    python parsers/utils/generate_mappings.py

`mappings.json` is the reverse map (normalized-variant -> canonical name) that
gives the canonicalizer O(1) lookups at runtime. The running pipeline only ever
*reads* mappings.json (and incrementally appends to it on auto-registration);
it never calls this script.
"""

from __future__ import annotations

import json
import os

from skill_norm import normalize_skill  # same-directory import when run as a script

_HERE = os.path.dirname(os.path.abspath(__file__))
GROUPINGS = os.path.join(_HERE, "groupings.json")
MAPPINGS = os.path.join(_HERE, "mappings.json")


def build_mappings(groupings: dict) -> dict:
    """canonical -> [variants]  ==>  {normalized_variant_or_canonical: canonical}."""
    mappings: dict[str, str] = {}
    for canonical, variants in groupings.items():
        # The canonical key itself must resolve to itself.
        mappings[normalize_skill(canonical)] = canonical
        for variant in variants:
            mappings[normalize_skill(variant)] = canonical
    return mappings


def main() -> None:
    with open(GROUPINGS, "r", encoding="utf-8") as f:
        groupings = json.load(f)

    mappings = build_mappings(groupings)

    with open(MAPPINGS, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=2, ensure_ascii=False, sort_keys=True)

    print(f"Wrote {len(mappings)} mappings -> {MAPPINGS}")


if __name__ == "__main__":
    main()
