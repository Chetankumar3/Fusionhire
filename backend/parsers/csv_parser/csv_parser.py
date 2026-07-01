"""CSV parser (structured source).

Reads every ``*.csv`` under ``data_sources/csvs/``, normalizes each row via
``parsers/utils``, and writes one parsed JSON record per row to
``shared_memory/parsed_jsons/``. Pure Python; no third-party CSV deps.

Expected header: ``name, email, phone, current_company, title``.
Each data row becomes ``source = csv_<n>`` where ``n`` is the 1-based data-row
index within its file (header excluded). ``procured_at`` is the CSV file's
last-modified time so it stays stable across re-runs.
"""

from __future__ import annotations

import csv
import json
import os
import sys

# Bootstrap project root onto sys.path so this runs standalone or via pipeline.
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d):
    if os.path.exists(os.path.join(_d, "run_pipeline.py")):
        if _d not in sys.path:
            sys.path.insert(0, _d)
        break
    _d = os.path.dirname(_d)

import paths
from parsers.utils.normalizers import (
    normalize_emails,
    normalize_name,
    normalize_phones,
)
from parsers.utils.record import build_parsed_record, mtime_iso
from parsers.utils.skills_canonicalizer import canonicalize_skills


def _row_to_record(row: dict, source: str, procured_at: str) -> dict:
    full_name = normalize_name(row.get("name"))
    emails = normalize_emails(row.get("email"))
    phones = normalize_phones(row.get("phone"))

    company = (row.get("current_company") or "").strip() or None
    title = normalize_name(row.get("title"))
    experience = []
    if company or title:
        experience.append(
            {"company": company, "title": title, "start": None, "end": None, "summary": None}
        )

    # The recruiter CSV has no skills column, but support one if present.
    raw_skills = row.get("skills")
    skills = []
    if raw_skills:
        skill_list = [s.strip() for s in str(raw_skills).split(";") if s.strip()]
        skills = canonicalize_skills(skill_list, source)

    return build_parsed_record(
        source,
        procured_at,
        method="normalization",
        full_name=full_name,
        emails=emails,
        phones=phones,
        experience=experience,
        skills=skills,
    )


def parse_csv_file(csv_path: str) -> list[dict]:
    procured_at = mtime_iso(csv_path)
    records = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            source = f"csv_{i}"
            records.append(_row_to_record(row, source, procured_at))
    return records


def run() -> list[str]:
    """Parse every CSV and write records. Returns the written file paths."""
    paths.ensure_dirs()
    written: list[str] = []
    if not os.path.isdir(paths.CSVS_DIR):
        return written

    for fname in sorted(os.listdir(paths.CSVS_DIR)):
        if not fname.lower().endswith(".csv"):
            continue
        csv_path = os.path.join(paths.CSVS_DIR, fname)
        try:
            records = parse_csv_file(csv_path)
        except Exception as exc:  # degrade gracefully: skip a garbage file, keep going
            print(f"[csv_parser] skipped {fname}: {exc}", file=sys.stderr)
            continue
        for rec in records:
            out_path = os.path.join(paths.PARSED_JSONS, f"{rec['source']}.json")
            with open(out_path, "w", encoding="utf-8") as out:
                json.dump(rec, out, indent=2, ensure_ascii=False)
            written.append(out_path)

    print(f"[csv_parser] wrote {len(written)} record(s)")
    return written


if __name__ == "__main__":
    run()
