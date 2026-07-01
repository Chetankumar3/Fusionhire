"""Pydantic models for FusionHire.

Two things live here, exactly as the spec's "begin by generating" instruction asks:

1. The **internal canonical profile** model (`CanonicalProfile` and its parts).
   This is the single, fixed, validated shape every merged candidate collapses to.
   It is the contract between the merge engine and the projector.

2. A **factory** (`build_projection_model`) that builds a *dynamic* Pydantic model
   at runtime from a projector config's `fields` list. The projector validates its
   reshaped output against this generated model before printing/writing.

Note on internal merge bookkeeping: the merge engine keeps a couple of private,
underscore-prefixed keys on experience/education entries while merging (e.g. the
set of observed start years used only to compute the year-mismatch confidence
penalty). Those are stripped before a profile is validated against
`CanonicalProfile`, so they never appear in canonical output. See `merge.py`.
"""

from __future__ import annotations

from typing import Any, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, create_model


# --------------------------------------------------------------------------- #
# 1. Internal canonical profile model                                         #
# --------------------------------------------------------------------------- #


class Location(BaseModel):
    """City / region / country. country is ISO-3166 alpha-2 (e.g. "IN", "US")."""

    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None


class Links(BaseModel):
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    other: List[str] = Field(default_factory=list)


class Skill(BaseModel):
    name: str
    # confidence = len(sources) / total_source_points  (see merge.py)
    confidence: float = 1.0
    sources: List[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None  # YYYY-MM
    end: Optional[str] = None  # YYYY-MM or null (null = ongoing)
    summary: Optional[str] = None


class Education(BaseModel):
    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    end_year: Optional[int] = None


class ProvenanceEntry(BaseModel):
    """One observation of one field from one source.

    The full provenance array is the flat union of every observation across every
    merged source, and doubles as the agreement log used by the confidence
    functions for full_name / emails / phones.
    """

    field: str
    source: str
    method: str


class CanonicalProfile(BaseModel):
    """The internal canonical candidate profile — the merge engine's output shape.

    `candidate_id` is Mongita's auto-generated `_id` (a deliberate, documented
    exception to determinism: re-running from an empty DB may assign new ids).
    """

    model_config = ConfigDict(extra="ignore")  # tolerate private "_*" merge keys

    candidate_id: Optional[str] = None
    full_name: Optional[str] = None
    emails: List[str] = Field(default_factory=list)
    phones: List[str] = Field(default_factory=list)
    location: Location = Field(default_factory=Location)
    links: Links = Field(default_factory=Links)
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: List[Skill] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    provenance: List[ProvenanceEntry] = Field(default_factory=list)
    overall_confidence: float = 1.0
    total_source_points: int = 1


# --------------------------------------------------------------------------- #
# 2. Dynamic projection-model factory                                         #
# --------------------------------------------------------------------------- #

# Map the config's `type` strings to Python types for validation.
_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "string[]": List[str],
    "number": float,
    "number[]": List[float],
    "integer": int,
    "integer[]": List[int],
    "boolean": bool,
    "object": dict,
    "object[]": List[dict],
    "any": Any,
}


def _py_type(type_str: Optional[str]) -> Any:
    """Resolve a config `type` string to a Python/typing type (default: Any)."""
    if not type_str:
        return Any
    return _TYPE_MAP.get(type_str, Any)


def build_projection_model(config: dict) -> Type[BaseModel]:
    """Build a Pydantic model from a projector config's `fields` list.

    The model has one attribute per configured field (keyed by its output `path`),
    plus `overall_confidence` when `include_confidence` is on and `provenance`
    when `include_provenance` is on — mirroring exactly what the projector emits.

    Field requiredness:
      - `required: true`            -> required (validation fails if absent/None)
      - otherwise                   -> Optional, default None
    With `on_missing: "omit"` the projector drops absent keys entirely, so even
    "required" fields are modeled as Optional in that mode to avoid a spurious
    validation failure on a legitimately-omitted optional field; a truly required
    field that is missing is already caught by the projector's on_missing contract
    before validation is reached.
    """
    on_missing = config.get("on_missing", "null")
    definitions: dict[str, tuple] = {}

    for field in config.get("fields", []):
        out_key = field["path"]
        base = _py_type(field.get("type"))
        required = bool(field.get("required", False)) and on_missing != "omit"

        if required:
            definitions[out_key] = (base, ...)
        else:
            # Optional[...] with a None default.
            definitions[out_key] = (Optional[base], None)

    if config.get("include_confidence", False):
        definitions["overall_confidence"] = (Optional[float], None)
    if config.get("include_provenance", False):
        definitions["provenance"] = (Optional[List[dict]], None)

    # extra="ignore" keeps validation focused on the requested schema only.
    model = create_model(
        "ProjectedProfile",
        __config__=ConfigDict(extra="ignore"),
        **definitions,
    )
    return model
