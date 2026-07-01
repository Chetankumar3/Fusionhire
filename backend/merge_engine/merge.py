"""The merge engine.

Reads every parsed record from ``shared_memory/parsed_jsons/`` and folds each
into the DSU-style store (see ``db.py``): the incoming record is saved to
``normalized_profiles``, every canonical profile it matches (by email/phone
intersection) is gathered, and a fresh canonical profile is rebuilt **from
scratch** out of the raw normalized records under those matches plus the incoming
record. Old canonicals are deleted and their normalized records (plus the
incoming one) repointed to the new canonical.

Merge contract (CHANGE 4)
-------------------------
Every per-field merge function takes ``records`` (the full raw list) and returns
``{value, source, method, confidence}``. The orchestrator uses ``.value`` to
assemble the canonical profile, ``.confidence`` for the weighted
``overall_confidence``, and builds ``provenance`` as exactly one entry per
top-level field: ``{field, value, source, method}``.

- Single-winner fields (full_name, headline, location, links.linkedin/github/
  portfolio, years_experience) report the winning record's own source/method.
- Union fields (emails, phones, links.other, experience, education, projects,
  skills) are always tagged ``source="multiple"``, ``method="union"``.

Determinism: files are processed in sorted order and each merge set is sorted by
``source``; output is byte-identical across runs except ``candidate_id`` (Mongita
auto id). Each canonical is rebuilt from the full raw record set every time, so
merges are inherently order/depth independent.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import OrderedDict
from typing import Callable, Dict, List, Optional

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d):
    if os.path.exists(os.path.join(_d, "run_pipeline.py")):
        if _d not in sys.path:
            sys.path.insert(0, _d)
        break
    _d = os.path.dirname(_d)

import paths
from merge_engine.db import ProfileStore, init_store

WEIGHTS = {
    "full_name": 0.20,
    "emails": 0.15,
    "phones": 0.15,
    "location": 0.10,
    "skills": 0.20,
    "experience": 0.10,
    "education": 0.05,
    "other_fields": 0.05,  # headline, years_experience, links, projects — flat 1.0
}

_UNION = {"source": "multiple", "method": "union"}


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #


def _union(items: List) -> List:
    out: List = []
    for i in items:
        if i and i not in out:
            out.append(i)
    return out


def _present(v) -> bool:
    return v not in (None, "", [], {})


def _size(v) -> int:
    return len(v) if isinstance(v, (list, tuple, dict, str)) else 0


def _better(v_a, t_a, v_b, t_b) -> bool:
    """Is (v_a @ t_a) a better single-winner than (v_b @ t_b)?

    Order: later procured_at, then larger value (longer string / more elements),
    then alphabetically-first by str(value).
    """
    if (t_a or "") != (t_b or ""):
        return (t_a or "") > (t_b or "")
    if _size(v_a) != _size(v_b):
        return _size(v_a) > _size(v_b)
    return str(v_a) < str(v_b)


def _year_of(ym: Optional[str]) -> Optional[int]:
    if ym and re.match(r"^\d{4}", str(ym)):
        return int(str(ym)[:4])
    return None


def _norm_degree(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower()) if value else ""


def _norm_title(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower()) if value else ""


def _agreement_score(values: List, is_phone: bool = False) -> float:
    vals = [v for v in values if v]
    n = len(vals)
    if n == 0:
        return 1.0
    if is_phone:
        norm = [str(v).strip() for v in vals]
    else:
        norm = [re.sub(r"[^a-z0-9]", "", str(v).lower()) for v in vals]
    d = len(set(norm))
    if d < n:
        return (n - d) / n
    return 0.7  # zero agreement -> flat 0.7, not 0


def _winner(records, get) -> dict:
    """Single-winner {value, source, method} over records for a scalar getter."""
    cands = [(get(r), r.get("procured_at", ""), r) for r in records if _present(get(r))]
    if not cands:
        return {"value": None, "source": None, "method": None}
    best = cands[0]
    for c in cands[1:]:
        if _better(c[0], c[1], best[0], best[1]):
            best = c
    return {"value": best[0], "source": best[2].get("source"), "method": best[2].get("method")}


def _winner_scalar(cands: List[tuple]):
    """Winner value over a list of (value, procured_at) pairs (for projects)."""
    if not cands:
        return None
    best = cands[0]
    for c in cands[1:]:
        if _better(c[0], c[1], best[0], best[1]):
            best = c
    return best[0]


# --------------------------------------------------------------------------- #
# Per-field merge functions -> {value, source, method, confidence}            #
# --------------------------------------------------------------------------- #


def field_full_name(records) -> dict:
    w = _winner(records, lambda r: r.get("full_name"))
    w["confidence"] = _agreement_score([r.get("full_name") for r in records])
    return w


def field_headline(records) -> dict:
    w = _winner(records, lambda r: r.get("headline"))
    w["confidence"] = 1.0
    return w


def field_emails(records) -> dict:
    all_emails = [e for r in records for e in r.get("emails", [])]
    return {"value": _union(all_emails), **_UNION, "confidence": _agreement_score(all_emails)}


def field_phones(records) -> dict:
    all_phones = [p for r in records for p in r.get("phones", [])]
    return {
        "value": _union(all_phones),
        **_UNION,
        "confidence": _agreement_score(all_phones, is_phone=True),
    }


def _location_nonnull(loc: dict) -> int:
    return sum(1 for k in ("city", "region", "country") if loc.get(k))


def field_location(records) -> dict:
    empty = {"city": None, "region": None, "country": None}
    cands = [
        (r.get("location") or {}, r.get("procured_at", ""), r)
        for r in records
        if _location_nonnull(r.get("location") or {}) > 0
    ]
    if not cands:
        return {"value": empty, "source": None, "method": None, "confidence": 0.0}
    best = cands[0]
    for c in cands[1:]:
        if (c[1] or "") > (best[1] or ""):  # most recent procured_at wins
            best = c
        elif (c[1] or "") == (best[1] or "") and _location_nonnull(c[0]) > _location_nonnull(best[0]):
            best = c
    loc = {k: best[0].get(k) for k in ("city", "region", "country")}
    return {
        "value": loc,
        "source": best[2].get("source"),
        "method": best[2].get("method"),
        "confidence": _location_nonnull(loc) / 3,
    }


def field_links(records, key: str) -> dict:
    w = _winner(records, lambda r: (r.get("links") or {}).get(key))
    w["confidence"] = 1.0
    return w


def field_links_other(records) -> dict:
    val = _union([o for r in records for o in (r.get("links") or {}).get("other", [])])
    return {"value": val, **_UNION, "confidence": 1.0}


def field_years_experience(records) -> dict:
    pairs = [(r.get("years_experience"), r) for r in records if r.get("years_experience") is not None]
    if not pairs:
        return {"value": None, "source": None, "method": None, "confidence": 1.0}
    mx = max(v for v, _ in pairs)
    # winner among records holding the max value; tie-break alphabetically by source
    winner = sorted((r for v, r in pairs if v == mx), key=lambda r: str(r.get("source", "")))[0]
    return {"value": mx, "source": winner.get("source"), "method": winner.get("method"), "confidence": 1.0}


def field_skills(records) -> dict:
    tsp = len(records)
    m: "OrderedDict[str, List[str]]" = OrderedDict()
    for r in records:
        for s in r.get("skills", []):
            m.setdefault(s["name"], []).extend(s.get("sources", []))
    skills = [
        {"name": name, "sources": srcs, "confidence": len(srcs) / tsp} for name, srcs in m.items()
    ]
    conf = sum(s["confidence"] for s in skills) / len(skills) if skills else 1.0
    return {"value": skills, **_UNION, "confidence": conf}


def field_experience(records) -> dict:
    g: "OrderedDict[tuple, dict]" = OrderedDict()
    for r in records:
        for x in r.get("experience", []):
            key = (x.get("company"), x.get("title"))
            e = g.setdefault(
                key,
                {
                    "company": x.get("company"),
                    "title": x.get("title"),
                    "starts": [],
                    "ends": [],
                    "has_null_end": False,
                    "summaries": [],
                    "years": set(),
                },
            )
            if x.get("start"):
                e["starts"].append(x["start"])
            if x.get("end"):
                e["ends"].append(x["end"])
            else:
                e["has_null_end"] = True
            if x.get("summary"):
                e["summaries"].append(x["summary"])
            sy = _year_of(x.get("start"))
            if sy:
                e["years"].add(sy)

    entries, scores = [], []
    for e in g.values():
        entries.append(
            {
                "company": e["company"],
                "title": e["title"],
                "start": min(e["starts"]) if e["starts"] else None,
                "end": None if e["has_null_end"] else (max(e["ends"]) if e["ends"] else None),
                "summary": max(e["summaries"], key=len) if e["summaries"] else None,
            }
        )
        scores.append(0.6 if len(e["years"]) > 1 else 1.0)  # start-year mismatch
    conf = sum(scores) / len(scores) if scores else 1.0
    return {"value": entries, **_UNION, "confidence": conf}


def field_education(records) -> dict:
    """Dedupe by (institution, normalized degree). Simplified per CHANGE 3:
    end_year winner is read straight off the record's own procured_at."""
    g: "OrderedDict[tuple, dict]" = OrderedDict()
    for r in records:
        pa = r.get("procured_at")
        for ed in r.get("education", []):
            key = (ed.get("institution"), _norm_degree(ed.get("degree")))
            e = g.setdefault(
                key,
                {"institution": ed.get("institution"), "degrees": [], "fields": [], "end_pairs": []},
            )
            if ed.get("degree"):
                e["degrees"].append(ed["degree"])
            if ed.get("field"):
                e["fields"].append(ed["field"])
            if ed.get("end_year") is not None:
                e["end_pairs"].append((ed["end_year"], pa))

    entries, scores = [], []
    for e in g.values():
        pairs = e["end_pairs"]
        with_pa = [(y, pa) for y, pa in pairs if pa]
        if with_pa:
            end_year = max(with_pa, key=lambda yp: yp[1])[0]  # latest procured_at
        elif pairs:
            end_year = pairs[0][0]  # no timestamps: first non-null (order not guaranteed)
        else:
            end_year = None
        entries.append(
            {
                "institution": e["institution"],
                "degree": max(e["degrees"], key=len) if e["degrees"] else None,
                "field": max(e["fields"], key=len) if e["fields"] else None,
                "end_year": end_year,
            }
        )
        distinct = {y for y, _ in pairs}
        scores.append(0.6 if len(distinct) > 1 else 1.0)  # end-year mismatch
    conf = sum(scores) / len(scores) if scores else 1.0
    return {"value": entries, **_UNION, "confidence": conf}


def field_projects(records) -> dict:
    """Dedupe by normalized title. description/tech_stack each single-winner
    (latest procured_at; tie longer/more-elements; then alpha). No mismatch."""
    g: "OrderedDict[str, dict]" = OrderedDict()
    for r in records:
        pa = r.get("procured_at", "")
        for p in r.get("projects", []):
            key = _norm_title(p.get("title"))
            e = g.setdefault(key, {"title": p.get("title"), "desc": [], "tech": []})
            if p.get("description"):
                e["desc"].append((p["description"], pa))
            if p.get("tech_stack"):
                e["tech"].append((p["tech_stack"], pa))

    entries = []
    for e in g.values():
        entries.append(
            {
                "title": e["title"],
                "description": _winner_scalar(e["desc"]),
                "tech_stack": _winner_scalar(e["tech"]) or [],
            }
        )
    return {"value": entries, **_UNION, "confidence": 1.0}


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #

def merge_documents(records: List[dict]) -> dict:
    """Rebuild one canonical profile from scratch out of raw ``records``."""
    tsp = len(records)

    fn = field_full_name(records)
    hl = field_headline(records)
    em = field_emails(records)
    ph = field_phones(records)
    loc = field_location(records)
    yrs = field_years_experience(records)
    sk = field_skills(records)
    exp = field_experience(records)
    edu = field_education(records)
    prj = field_projects(records)
    li = field_links(records, "linkedin")
    gh = field_links(records, "github")
    pf = field_links(records, "portfolio")
    other = field_links_other(records)

    profile = {
        "full_name": fn["value"],
        "emails": em["value"],
        "phones": ph["value"],
        "location": loc["value"],
        "links": {
            "linkedin": li["value"],
            "github": gh["value"],
            "portfolio": pf["value"],
            "other": other["value"],
        },
        "headline": hl["value"],
        "years_experience": yrs["value"],
        "skills": sk["value"],
        "experience": exp["value"],
        "education": edu["value"],
        "projects": prj["value"],
        "total_source_points": tsp,  # derived metadata only
    }

    # Provenance: exactly one entry per top-level field.
    def entry(field, res, value=None):
        return {
            "field": field,
            "value": res["value"] if value is None else value,
            "source": res["source"],
            "method": res["method"],
        }

    profile["provenance"] = [
        entry("full_name", fn),
        entry("emails", em),
        entry("phones", ph),
        entry("location", loc),
        entry("headline", hl),
        entry("years_experience", yrs),
        entry("links.linkedin", li),
        entry("links.github", gh),
        entry("links.portfolio", pf),
        entry("links.other", other),
        entry("experience", exp),
        entry("education", edu),
        entry("projects", prj),
        # skills provenance value = list of canonical skill names (per-skill
        # `sources` lists live on the skill objects themselves, unchanged).
        entry("skills", sk, value=[s["name"] for s in sk["value"]]),
    ]

    profile["overall_confidence"] = round(
        WEIGHTS["full_name"] * fn["confidence"]
        + WEIGHTS["emails"] * em["confidence"]
        + WEIGHTS["phones"] * ph["confidence"]
        + WEIGHTS["location"] * loc["confidence"]
        + WEIGHTS["skills"] * sk["confidence"]
        + WEIGHTS["experience"] * exp["confidence"]
        + WEIGHTS["education"] * edu["confidence"]
        + WEIGHTS["other_fields"] * 1.0,  # headline, years, links, projects
        6,
    )
    return profile


def load_parsed_records() -> List[dict]:
    records = []
    if not os.path.isdir(paths.PARSED_JSONS):
        return records
    for fname in sorted(os.listdir(paths.PARSED_JSONS)):
        if not fname.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(paths.PARSED_JSONS, fname), "r", encoding="utf-8") as f:
                records.append(json.load(f))
        except Exception as exc:  # garbage parsed file -> skip, keep going
            print(f"[merge] skipped {fname}: {exc}", file=sys.stderr)
    return records


def run(reset: bool = True, store: Optional[ProfileStore] = None) -> ProfileStore:
    store = store or init_store(reset=reset)
    if reset:
        store.reset()

    records = load_parsed_records()

    for rec in records:
        # 1. Persist the incoming record to the raw audit trail (profile_of=null).
        incoming_id = store.insert_normalized(rec)

        # 2. Canonical profiles this record links to (email OR phone intersect).
        cand_ids = store.find_canonical_ids_by_contact(
            rec.get("emails", []), rec.get("phones", [])
        )

        # 3-4. All raw records under those canonicals + the incoming record.
        matched_norm = store.normalized_by_profile_of(cand_ids)
        merge_records = sorted(matched_norm + [rec], key=lambda r: r.get("source", ""))

        # 5. Rebuild one canonical profile from scratch out of the raw records.
        canonical = merge_documents(merge_records)

        # 6. Insert the new canonical -> new_candidate_id.
        new_cid = store.insert_canonical(canonical)

        # 7. Delete the now-absorbed canonical profiles.
        store.delete_canonical(cand_ids)

        # 8. Repoint every absorbed normalized record + the incoming one.
        repoint_ids = [d.get("id") for d in matched_norm] + [incoming_id]
        store.repoint(repoint_ids, new_cid)

    print(f"[merge] {len(records)} record(s) -> {len(store.all_canonical())} canonical profile(s)")
    return store


if __name__ == "__main__":
    run(reset=True)
