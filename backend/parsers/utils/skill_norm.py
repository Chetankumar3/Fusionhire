"""Shared skill-string normalization.

"Normalized" = lowercased with all spaces and hyphens removed. Used by both
`generate_mappings.py` (seeding) and `skills_canonicalizer.py` (runtime lookup),
so it lives in one place to guarantee they agree.
"""

from __future__ import annotations

import re


def normalize_skill(value: str) -> str:
    """Lowercase and strip spaces and hyphens. e.g. "Docker Swarm" -> "dockerswarm"."""
    return re.sub(r"[\s\-]", "", str(value).lower())
