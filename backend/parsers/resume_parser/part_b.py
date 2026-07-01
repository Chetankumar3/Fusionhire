"""Resume Parser — Part B (Python).

Consumes the intermediate raw JSON produced by Part A (`raw_json/*.json`), applies
the shared `parsers/utils` normalizers, and writes the final parsed record to
`shared_memory/parsed_jsons/` per the parser output contract.

Field extraction here is heuristic (resumes are free-form prose). Notable mapping
decision: PROJECTS are surfaced as `experience` entries (company=null,
title=project name) so the pipeline exercises real date normalization
("May 2026"->"2026-05", "Present"->null) and the experience-merge path. This is
documented in the README.
"""

from __future__ import annotations

import json
import os
import re
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d):
    if os.path.exists(os.path.join(_d, "run_pipeline.py")):
        if _d not in sys.path:
            sys.path.insert(0, _d)
        break
    _d = os.path.dirname(_d)

import paths
from parsers.utils.normalizers import (
    _country_alpha2,
    normalize_date,
    normalize_emails,
    normalize_name,
    normalize_phones,
)
from parsers.utils.record import build_parsed_record, mtime_iso
from parsers.utils.skills_canonicalizer import canonicalize_skills

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+\d[\d\s\-]{6,}\d")
_DATE_RANGE_RE = re.compile(
    r"([A-Za-z]{3,9}\.?\s*\d{4})\s*[-–—]\s*"
    r"([A-Za-z]{3,9}\.?\s*\d{4}|present|current|ongoing)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Profile / contact                                                           #
# --------------------------------------------------------------------------- #


def _extract_emails(raw, links):
    found = [L["url"][len("mailto:"):] for L in links if L["url"].lower().startswith("mailto:")]
    found += _EMAIL_RE.findall(raw)
    return normalize_emails(found)


def _extract_phones(raw):
    return normalize_phones(_PHONE_RE.findall(raw))


def _classify_links(links):
    out = {"linkedin": None, "github": None, "portfolio": None, "other": []}
    for L in links:
        url = L["url"]
        anchor = (L.get("anchor") or "").lower()
        low = url.lower()
        if low.startswith("mailto:") or low.startswith("tel:"):
            continue
        if "linkedin.com" in low:
            out["linkedin"] = out["linkedin"] or url
        elif "github.com" in low:
            # profile (github.com/<user>) vs repo (github.com/<user>/<repo>)
            tail = low.split("github.com/", 1)[1].strip("/")
            if tail and "/" not in tail and out["github"] is None:
                out["github"] = url
            else:
                out["other"].append(url)
        elif "portfo" in anchor:
            out["portfolio"] = out["portfolio"] or url
        else:
            out["other"].append(url)
    out["other"] = list(dict.fromkeys(out["other"]))
    return out


def _extract_headline(sections):
    intro = " ".join(sections.get("INTRODUCTION", [])).strip()
    if not intro:
        return None
    first = intro.split(".")[0].strip()
    return (first + ".") if first else None


# --------------------------------------------------------------------------- #
# Skills                                                                       #
# --------------------------------------------------------------------------- #


def _extract_skill_tokens(sections):
    tokens = []
    for line in sections.get("TECHNICALSKILLS", []):
        s = line.lstrip("•").strip()
        if ":" in s:
            s = s.split(":", 1)[1]
        s = re.sub(r"\([^)]*\)", "", s)  # drop ratings like "(9.5/10)"
        for part in re.split(r"[,&]", s):
            t = part.strip(" .")
            if t:
                tokens.append(t)
    return tokens


# --------------------------------------------------------------------------- #
# Education                                                                    #
# --------------------------------------------------------------------------- #


def _dedup_phrase(text: str) -> str:
    """Collapse a doubled phrase ("Naya Raipur Naya Raipur" -> "Naya Raipur")."""
    words = text.split()
    if words and len(words) % 2 == 0:
        half = len(words) // 2
        if [w.lower() for w in words[:half]] == [w.lower() for w in words[half:]]:
            return " ".join(words[:half])
    return text


def _parse_education(sections):
    lines = sections.get("EDUCATION", [])
    location = {"city": None, "region": None, "country": None}
    education = []
    if not lines:
        return education, location

    inst_line = lines[0]
    institution = inst_line
    parts = [p.strip() for p in inst_line.split(",")]
    if len(parts) >= 2:
        country = _country_alpha2(parts[-1])
        if country:
            institution = parts[0]
            location = {
                "city": _dedup_phrase(parts[-2]) if len(parts) >= 3 else None,
                "region": None,
                "country": country,
            }

    degree = field = None
    end_year = None
    if len(lines) >= 2:
        deg_line = lines[1]
        years = [int(y) for y in re.findall(r"\d{4}", deg_line)]
        end_year = max(years) if years else None
        core = re.sub(r"\([^)]*\)", "", deg_line)
        core = re.sub(r"\d{4}", "", core)
        core = re.sub(r"[–—-]", "", core).strip()
        core = re.sub(r"\s+", " ", core)
        if " in " in core:
            degree, field = core.split(" in ", 1)
            degree, field = degree.strip(), field.strip(" .")
        else:
            degree = core or None

    education.append(
        {
            "institution": institution or None,
            "degree": degree or None,
            "field": field or None,
            "end_year": end_year,
        }
    )
    return education, location


# --------------------------------------------------------------------------- #
# Projects -> experience                                                       #
# --------------------------------------------------------------------------- #


def _parse_projects(sections):
    exps = []
    cur = None
    for line in sections.get("PROJECTS", []):
        m = _DATE_RANGE_RE.search(line)
        if m:
            if cur:
                exps.append(cur)
            title = line[: m.start()]
            title = re.split(r"§|\bGit repo\b|\bGithub\b|\bGit\b", title)[0]
            title = title.strip(" :–-•")
            cur = {
                "company": None,
                "title": title or None,
                "start": normalize_date(m.group(1)),
                "end": normalize_date(m.group(2)),
                "_summary_parts": [],
            }
        elif cur is not None:
            cur["_summary_parts"].append(line.lstrip("•").strip())
    if cur:
        exps.append(cur)

    for e in exps:
        sp = [p for p in e.pop("_summary_parts") if p]
        e["summary"] = " ".join(sp) if sp else None
    return exps


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #


def build_record_from_raw(raw_json: dict, source: str, procured_at: str) -> dict:
    lines = raw_json.get("lines", [])
    sections = raw_json.get("sections", {})
    links = raw_json.get("links", [])
    raw_text = raw_json.get("raw_text", "")

    full_name = normalize_name(lines[0]) if lines else None
    emails = _extract_emails(raw_text, links)
    phones = _extract_phones(raw_text)
    link_obj = _classify_links(links)
    headline = _extract_headline(sections)
    skills = canonicalize_skills(_extract_skill_tokens(sections), source)
    experience = _parse_projects(sections)
    education, location = _parse_education(sections)

    return build_parsed_record(
        source,
        procured_at,
        method="openresume+normalization",
        full_name=full_name,
        emails=emails,
        phones=phones,
        location=location,
        links=link_obj,
        headline=headline,
        skills=skills,
        experience=experience,
        education=education,
    )


def run() -> list[str]:
    paths.ensure_dirs()
    written: list[str] = []
    if not os.path.isdir(paths.RESUME_RAW_DIR):
        print("[part_b] no raw_json/ dir; run Part A first", file=sys.stderr)
        return written

    for fname in sorted(os.listdir(paths.RESUME_RAW_DIR)):
        if not fname.lower().endswith(".json"):
            continue
        raw_path = os.path.join(paths.RESUME_RAW_DIR, fname)
        try:
            with open(raw_path, "r", encoding="utf-8") as f:
                raw_json = json.load(f)
        except Exception as exc:  # garbage intermediate file -> skip, keep going
            print(f"[part_b] skipped {fname}: {exc}", file=sys.stderr)
            continue

        source_file = raw_json.get("source_file", fname)
        source = f"resume_{source_file}"
        pdf_path = os.path.join(paths.RESUMES_DIR, source_file)
        procured_at = mtime_iso(pdf_path) if os.path.exists(pdf_path) else raw_json.get(
            "procured_at", "1970-01-01T00:00:00Z"
        )

        record = build_record_from_raw(raw_json, source, procured_at)
        out_path = os.path.join(paths.PARSED_JSONS, f"{source}.json")
        with open(out_path, "w", encoding="utf-8") as out:
            json.dump(record, out, indent=2, ensure_ascii=False)
        written.append(out_path)

    print(f"[part_b] wrote {len(written)} record(s)")
    return written


if __name__ == "__main__":
    run()
