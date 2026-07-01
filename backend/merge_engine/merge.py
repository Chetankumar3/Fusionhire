"""The merge engine.

Reads every parsed record from ``shared_memory/parsed_jsons/`` and folds each
into the DSU-style store (see ``db.py``): the incoming record is saved to
``normalized_profiles``, every canonical profile it matches (by email/phone
intersection) is gathered, and a fresh canonical profile is rebuilt **from
scratch** out of the raw normalized records under those matches plus the incoming
record — never from a previous canonical profile's already-merged values. The
old canonicals are deleted and all their normalized records (plus the incoming
one) are repointed to the new canonical.

Determinism
-----------
Records are processed in sorted filename order and each merge set is sorted by
``source``, so a given set of input files always yields byte-identical merged
output *except* for ``candidate_id`` (Mongita's auto-generated id — the one
documented exception). Because each canonical is rebuilt from the full raw record
set every time, merges are inherently order/depth independent.

Internal bookkeeping
--------------------
Merged experience/education entries carry private ``_start_years`` / ``_end_years``
lists (and ``_end_year_time``) used only to (a) detect "year-mismatch merges" for
the confidence penalty and (b) keep end_year winner-selection correct across
re-merges. These ``_``-prefixed keys never reach canonical output — the projector
strips them.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import OrderedDict
from typing import Dict, List, Optional

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
    "other_fields": 0.05,  # headline, years_experience, links — flat 1.0
}


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #


def _union(items: List) -> List:
    out: List = []
    for i in items:
        if i and i not in out:
            out.append(i)
    return out


def _year_of(ym: Optional[str]) -> Optional[int]:
    if ym and re.match(r"^\d{4}", str(ym)):
        return int(str(ym)[:4])
    return None


def _norm_degree(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower()) if value else ""


def _prov_values(provenance: List[dict], field: str) -> List:
    return [p["value"] for p in provenance if p.get("field") == field and p.get("value") is not None]


def _is_better(a, b) -> bool:
    """Total order for single-winner fields: later time > longer string > alpha-first."""
    (va, ta), (vb, tb) = a, b
    if (ta or "") != (tb or ""):
        return (ta or "") > (tb or "")
    if len(str(va)) != len(str(vb)):
        return len(str(va)) > len(str(vb))
    return str(va) < str(vb)


def _winner_from_prov(provenance, field, source_time) -> Optional[str]:
    cands = [
        (p["value"], source_time.get(p["source"], ""))
        for p in provenance
        if p.get("field") == field and p.get("value")
    ]
    if not cands:
        return None
    best = cands[0]
    for c in cands[1:]:
        if _is_better(c, best):
            best = c
    return best[0]


def _location_winner(provenance, source_time) -> dict:
    empty = {"city": None, "region": None, "country": None}
    cands = [
        (p["value"], source_time.get(p["source"], ""))
        for p in provenance
        if p.get("field") == "location" and isinstance(p.get("value"), dict)
    ]
    if not cands:
        return empty

    def nonnull(loc):
        return sum(1 for k in ("city", "region", "country") if loc.get(k))

    best = cands[0]
    for c in cands[1:]:
        if (c[1] or "") > (best[1] or ""):  # most recent procured_at wins
            best = c
        elif (c[1] or "") == (best[1] or "") and nonnull(c[0]) > nonnull(best[0]):
            best = c
    return {k: best[0].get(k) for k in ("city", "region", "country")}


# --------------------------------------------------------------------------- #
# Per-field merges                                                             #
# --------------------------------------------------------------------------- #


def _merge_skills(docs, total_source_points) -> List[dict]:
    m: "OrderedDict[str, List[str]]" = OrderedDict()
    for d in docs:
        for s in d.get("skills", []):
            m.setdefault(s["name"], []).extend(s.get("sources", []))
    return [
        {"name": name, "sources": srcs, "confidence": len(srcs) / total_source_points}
        for name, srcs in m.items()
    ]


def _merge_experience(docs) -> List[dict]:
    g: "OrderedDict[tuple, dict]" = OrderedDict()
    for d in docs:
        for x in d.get("experience", []):
            key = (x.get("company"), x.get("title"))
            e = g.get(key)
            if e is None:
                e = {
                    "company": x.get("company"),
                    "title": x.get("title"),
                    "starts": [],
                    "ends": [],
                    "has_null_end": False,
                    "summaries": [],
                    "years": set(),
                }
                g[key] = e
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
            for y in x.get("_start_years", []):
                e["years"].add(y)

    out = []
    for e in g.values():
        out.append(
            {
                "company": e["company"],
                "title": e["title"],
                "start": min(e["starts"]) if e["starts"] else None,
                "end": None if e["has_null_end"] else (max(e["ends"]) if e["ends"] else None),
                "summary": max(e["summaries"], key=len) if e["summaries"] else None,
                "_start_years": sorted(e["years"]),
            }
        )
    return out


def _merge_education(docs, source_time) -> List[dict]:
    g: "OrderedDict[tuple, dict]" = OrderedDict()
    for d in docs:
        doc_time = source_time.get(d.get("source", ""), "") if d.get("source") else ""
        for ed in d.get("education", []):
            key = (ed.get("institution"), _norm_degree(ed.get("degree")))
            ed_time = ed.get("_end_year_time") or doc_time
            e = g.get(key)
            if e is None:
                e = {
                    "institution": ed.get("institution"),
                    "degree": ed.get("degree"),
                    "fields": [],
                    "end_year": ed.get("end_year"),
                    "end_year_time": ed_time,
                    "end_years": set(),
                }
                g[key] = e
            elif ed.get("end_year") is not None:
                # end_year winner = source with most recent procured_at
                if e["end_year"] is None or (ed_time or "") > (e["end_year_time"] or ""):
                    e["end_year"] = ed.get("end_year")
                    e["end_year_time"] = ed_time
            if ed.get("field"):
                e["fields"].append(ed["field"])
            if ed.get("end_year") is not None:
                e["end_years"].add(ed["end_year"])
            for y in ed.get("_end_years", []):
                e["end_years"].add(y)

    out = []
    for e in g.values():
        out.append(
            {
                "institution": e["institution"],
                "degree": e["degree"],
                "field": max(e["fields"], key=len) if e["fields"] else None,
                "end_year": e["end_year"],
                "_end_years": sorted(e["end_years"]),
                "_end_year_time": e["end_year_time"],
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Confidence                                                                   #
# --------------------------------------------------------------------------- #


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


def _avg_year_mismatch(entries: List[dict], years_key: str) -> float:
    if not entries:
        return 1.0
    scores = [0.6 if len(set(e.get(years_key, []))) > 1 else 1.0 for e in entries]
    return sum(scores) / len(scores)


def _compute_overall_confidence(profile: dict, provenance: List[dict]) -> float:
    fn = _agreement_score(_prov_values(provenance, "full_name"))
    em = _agreement_score(_prov_values(provenance, "emails"))
    ph = _agreement_score(_prov_values(provenance, "phones"), is_phone=True)

    loc = profile["location"]
    loc_score = sum(1 for k in ("city", "region", "country") if loc.get(k)) / 3

    exp_score = _avg_year_mismatch(profile["experience"], "_start_years")
    edu_score = _avg_year_mismatch(profile["education"], "_end_years")

    skills = profile["skills"]
    sk_score = sum(s["confidence"] for s in skills) / len(skills) if skills else 1.0

    return round(
        WEIGHTS["full_name"] * fn
        + WEIGHTS["emails"] * em
        + WEIGHTS["phones"] * ph
        + WEIGHTS["location"] * loc_score
        + WEIGHTS["skills"] * sk_score
        + WEIGHTS["experience"] * exp_score
        + WEIGHTS["education"] * edu_score
        + WEIGHTS["other_fields"] * 1.0,
        6,
    )


# --------------------------------------------------------------------------- #
# Document merge + run                                                         #
# --------------------------------------------------------------------------- #


def merge_documents(docs: List[dict], source_time: Dict[str, str]) -> dict:
    """Collapse 1+ documents into one canonical profile dict (no candidate_id)."""
    tsp = sum(int(d.get("total_source_points", 1)) for d in docs)

    provenance: List[dict] = []
    for d in docs:
        provenance.extend(d.get("provenance", []))

    profile = {
        "full_name": _winner_from_prov(provenance, "full_name", source_time),
        "emails": _union([e for d in docs for e in d.get("emails", [])]),
        "phones": _union([p for d in docs for p in d.get("phones", [])]),
        "location": _location_winner(provenance, source_time),
        "links": {
            "linkedin": _winner_from_prov(provenance, "links.linkedin", source_time),
            "github": _winner_from_prov(provenance, "links.github", source_time),
            "portfolio": _winner_from_prov(provenance, "links.portfolio", source_time),
            "other": _union([o for d in docs for o in d.get("links", {}).get("other", [])]),
        },
        "headline": _winner_from_prov(provenance, "headline", source_time),
        "years_experience": _max_or_none(
            [d.get("years_experience") for d in docs]
        ),
        "skills": _merge_skills(docs, tsp),
        "experience": _merge_experience(docs),
        "education": _merge_education(docs, source_time),
        "provenance": provenance,
        "total_source_points": tsp,
    }
    profile["overall_confidence"] = _compute_overall_confidence(profile, provenance)
    return profile


def _max_or_none(values):
    nums = [v for v in values if v is not None]
    return max(nums) if nums else None


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
        source_time = {r["source"]: r.get("procured_at", "") for r in merge_records}
        canonical = merge_documents(merge_records, source_time)

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
