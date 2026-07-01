# FusionHire — Multi-Source Candidate Data Transformer

Ingests candidate profiles from multiple structured and unstructured sources,
normalizes them, merges duplicates across sources into **one canonical profile
per candidate**, records provenance + confidence, and projects the result into a
configurable output schema validated with Pydantic.

Sources implemented (one from each required group):
- **Structured** — Recruiter **CSV** export (`name, email, phone, current_company, title`).
- **Unstructured** — **Resume PDF**, parsed positionally with pdf.js (the engine
  OpenResume is built on).

---

## Pipeline at a glance

```
data_sources/                     parsers/                       shared_memory/
  csvs/*.csv      ──▶ csv_parser ─┐                                parsed_jsons/*.json
  resumes/*.pdf   ──▶ resume_parser (Part A: Node/pdf.js          (one normalized
                       ─▶ raw_json ─▶ Part B: Python) ────────────▶ record per source)
                                                                         │
                                                                         ▼
                                          merge_engine ──▶ Mongita (active_profiles)
                                          match by email|phone, merge, confidence
                                                                         │
                                                                         ▼
                                          projector ──▶ config-driven, validated JSON
```

Stages: **parse → normalize → merge → confidence → project → validate.**

---

## Setup

```bash
# Python deps
pip install -r requirements.txt

# (Optional) Resume Part A regeneration — Node deps
cd parsers/resume_parser && npm install && cd ../..
```

- Python 3.10+ (developed on 3.12). Node 18+ only needed to *re-generate* the
  resume intermediate JSON; the committed `parsers/resume_parser/raw_json/` lets
  the pipeline run resume data **without Node**.
- No database to install — **Mongita** is a file-based embedded Mongo, stored in
  `shared_memory/db/`.

---

## Run

**Whole pipeline (idempotent — cleans intermediates, then parse → merge → project):**

```bash
python run_pipeline.py
```

Writes the produced output to:
- `shared_memory/output_default.json` — full canonical schema.
- `shared_memory/output_custom.json` — the custom "contact card" config.

Useful flags:
```bash
python run_pipeline.py --no-node        # reuse committed raw_json/ (skip Part A)
python run_pipeline.py --skip-resume    # CSV source only (graceful-degradation demo)
python run_pipeline.py --config projector/configs/custom_contact_card.json   # also print this config
```

**Projector standalone** (point it at the merged DB + a config):

```bash
python -m projector.projector --config projector/configs/default.json
python -m projector.projector --config projector/configs/custom_contact_card.json --output card.json
python -m projector.projector --config <cfg> --candidate-id <id>
```

**Run components individually:**
```bash
python parsers/csv_parser/csv_parser.py
cd parsers/resume_parser && npm run parse && cd ../..   # Part A
python parsers/resume_parser/part_b.py                  # Part B
python merge_engine/merge.py
```

**Tests:**
```bash
python -m pytest tests/ -q          # 34 tests: normalizers, skills, merge, projector, e2e gold profile
```

---

## The canonical schema

```jsonc
{
  "candidate_id": "…",                 // Mongita _id
  "full_name": "…",
  "emails": ["…"], "phones": ["…"],    // E.164 phones
  "location": { "city", "region", "country" },   // country = ISO-3166 alpha-2
  "links": { "linkedin", "github", "portfolio", "other": ["…"] },
  "headline": "…",
  "years_experience": 0,
  "skills": [ { "name", "confidence", "sources": ["…"] } ],   // canonical names
  "experience": [ { "company", "title", "start", "end", "summary" } ],  // YYYY-MM
  "education": [ { "institution", "degree", "field", "end_year" } ],
  "provenance": [ { "field", "value", "source", "method" } ],
  "overall_confidence": 0.0,
  "total_source_points": 1
}
```

### Normalized formats
- **Phones** → E.164 (`phonenumbers`). Valid *or* possible numbers are kept
  (so well-formed international/reserved ranges survive); unparseable → dropped.
- **Country** → ISO-3166 alpha-2 (`pycountry`); unresolvable → `null`.
- **Dates** → `YYYY-MM`. Ambiguity rules: `DD-MM-YYYY`, `YYYY-MM-DD`,
  `DD-YYYY-MM`; 2-part numeric detects the 4-digit year; month names in either
  order; `present/current/ongoing` → `null` (ongoing); anything unparseable →
  `null` (never guessed). Both `-` and `/` delimiters.
- **Skills** → canonical names via a file-based taxonomy (see below).

---

## Merge policy (`merge_engine/`)

For each parsed record, query `active_profiles` for any doc whose **emails OR
phones intersect** the incoming record; archive + absorb the matches; insert one
merged doc. Rules:

| Field | Rule |
|---|---|
| `full_name`, `headline`, `links.{linkedin,github,portfolio}` | winner = most recent `procured_at`, then longer string, then alpha-first |
| `location` | whole object from latest `procured_at`; tie → most non-null fields |
| `emails`, `phones`, `links.other` | union (no exact dupes) |
| `years_experience` | max non-null |
| `skills` | union by canonical name; `confidence = len(sources)/total_source_points` |
| `experience` | dedupe `(company,title)`; earliest start; `end=null` if any ongoing; longest summary |
| `education` | dedupe `(institution, normalized degree)`; longest field; end_year from latest source |
| `provenance` | flat union |

**Confidence.** Per-field scores feed a weighted `overall_confidence`
(full_name .20, emails .15, phones .15, location .10, skills .20, experience .10,
education .05, other .05). Name/email/phone use an agreement score over all
observed values; experience/education penalize "year-mismatch" merges (0.6);
location scores non-null fraction.

---

## Configurable output (the "twist")

A runtime config reshapes output with **no code changes**. Example
(`projector/configs/custom_contact_card.json`):

```json
{
  "fields": [
    { "path": "full_name", "type": "string", "required": true },
    { "path": "primary_email", "from": "emails[0]", "type": "string", "required": true },
    { "path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164" },
    { "path": "skills", "from": "skills[].name", "type": "string[]", "normalize": "canonical" }
  ],
  "include_confidence": true,
  "include_provenance": false,
  "on_missing": "null"
}
```

- **`from` grammar:** `location.city`, `emails[0]`, `experience[0].company`,
  `skills[].name` (array-map), `links.other[]` (passthrough). More than one `[]`
  → `"path not supported"`.
- **`normalize`:** only `E164` / `canonical` / omitted are accepted; anything
  else → `"<format> is not supported yet"` (hard error).
- **`include_confidence` / `include_provenance`:** toggle `overall_confidence` +
  `skills[].confidence`, and the `provenance` array + `skills[].sources`.
- **`on_missing`:** `null` | `omit` | `error` (stderr + non-zero exit, no partial output).
- Output is validated against a Pydantic model built from the field list.

---

## Skills taxonomy (`parsers/utils/`)

- `groupings.json` — canonical name → raw variants.
- `generate_mappings.py` — **manual** seeding script; regenerates `mappings.json`
  (run by hand after editing groupings; not part of the live pipeline).
- `mappings.json` — reverse lookup for O(1) canonicalization.
- `skills_canonicalizer.py` — looks up each skill; **auto-registers** unknown
  skills (raw form becomes its own canonical key, written incrementally to both
  JSON files). Keeps the taxonomy self-extending and runs deterministic.

---

## Design decisions, assumptions & descoped items

- **`candidate_id` is non-deterministic — by design.** It is Mongita's
  auto-generated `_id`; re-running from an empty DB can assign new ids. Everything
  else is byte-for-byte reproducible for the same input files (verified). Single-
  winner fields are recomputed from the provenance log + a `source→procured_at`
  map, so merges are order/depth independent.
- **Mongita `$or` is broken** (returns 0 even for equality on mongita 1.2.0). The
  spec's "emails OR phones" match is implemented as two `$in` queries unioned in
  Python — same semantics, still on Mongita, so the auto-generated id is kept.
- **Resume Part A uses pdf.js, not the full OpenResume TS extractor.** OpenResume
  isn't published as a library; vendoring its whole TS pipeline was descoped under
  the time budget. We use its underlying **pdf.js positional engine** (recovers
  word spacing that naive text extraction loses) + a heading segmenter + PDF link
  annotations (real LinkedIn/GitHub/portfolio URLs). Field semantics live in the
  Python Part B. A `pdfplumber` fallback is available conceptually; pdf.js was
  reliable here so it's the default.
- **Resume PROJECTS are surfaced as `experience`** (company `null`, title = project
  name). The provided resume has no formal work history, and this exercises real
  date normalization (`"May 2026"`→`2026-05`, `"Present"`→`null`) and the
  experience-merge path. Flagged here as a deliberate mapping choice.
- **Internal merge bookkeeping** (`_start_years`, `_end_years`, `_end_year_time`)
  rides on stored experience/education entries to keep year-mismatch confidence
  and end_year selection correct across re-merges; the projector strips all
  `_`-prefixed keys from output.
- **Descoped:** ATS-JSON / GitHub / LinkedIn sources (one structured + one
  unstructured implemented, as required); region inference for locations; a UI
  (CLI only); the Stage-1 one-pager PDF (`__Eightfold.pdf`) is a separate
  deliverable, not part of this code repo.

---

## Layout

```
data_sources/{csvs,resumes}      input files
parsers/
  utils/                         normalizers, skills taxonomy + canonicalizer, record contract
  csv_parser/                    structured-source parser
  resume_parser/                 Part A (Node/pdf.js) + Part B (Python) + raw_json/
merge_engine/                    models.py, db.py (Mongita store), merge.py
projector/                       projector.py + configs/{default,custom_contact_card}.json
shared_memory/{db,parsed_jsons}  Mongita storage + intermediate records (+ output_*.json)
tests/                           pytest suite
run_pipeline.py                  idempotent end-to-end runner
```
