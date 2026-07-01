# Fusionhire

**Resources**
- Architecture Diagram: [https://excalidraw.com/#json=PFlt79TtX9c9d3q-y9t82,CN9ed7eRT1S74Y3ZCVqX2A]
- Demo Video: [https://drive.google.com/file/d/11cXtcMPNcB0eht71M_1xblmjia1tpl2o/view?usp=sharing]

## Table of Contents
1. [Overview](#1-overview)
2. [How to Run](#2-how-to-run)
3. [Architecture](#3-architecture)
4. [Architectural Decisions](#4-architectural-decisions)
5. [Edge Cases Handled (and Descoped)](#5-edge-cases-handled-and-descoped)
6. [Noteworthy Technical Points](#6-noteworthy-technical-points)

---

## 1. Overview

Fusionhire is a Multi-Source Candidate Data Transformer. It ingests candidate information from disparate structured (CSV) and unstructured (Resume PDF) sources, normalizes the data, resolves conflicts, and merges them into a single, canonical, schema-valid JSON profile.

## 2. How to Run

The pipeline is designed to be completely frictionless with zero external database dependencies.

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Execute the pipeline:
   ```bash
   python .\backend\run_pipeline.py
   ```

## 3. Architecture

*(See Excalidraw link in Resources for the bird's-eye diagram)*

**Sources** → **Independent Extractors** → **Standardized JSON** → **In-Memory Mongita DB** → **Merge Engine** → **Projector (Configurable Output)** → **Final JSON**

## 4. Architectural Decisions

- **Batch Scripts over Always-On Servers:** A single command (`run_pipeline.py`) processes the entire batch, using a document store for persistence and eliminating unnecessary server overhead.
- **Decoupled Layer Architecture:** Ingestion is decoupled from transformation, so new sources only need their own lightweight converter to standardized JSON — the core engine stays unchanged.
- **Database as a File (Mongita):** Match-key grouping requires querying arrays (e.g., email/phone lists). Mongita provides MongoDB-style query power in a zero-dependency, embedded form, and cleanly decouples the `merge_engine` from the `projector`. For production, swap in a real MongoDB instance via environment variables.

## 5. Edge Cases Handled (and Descoped)

- **Email Uniqueness:** Emails are assumed to come from non-recycling providers and are treated as high-confidence primary match keys.
- **Phone Number Recycling:** Acknowledged as a risk, but recency/timestamp checks are descoped due to time constraints.
- **Ambiguous Date Formats:** Resolved deterministically —
  - `XX-XX-XXXX` → `DD-MM-YYYY`
  - `XXXX-XX-XX` → `YYYY-MM-DD` (ISO 8601)
  - `XX-XXXX-XX` → `DD-YYYY-MM`

## 6. Noteworthy Technical Points

- **OpenResume over pyresparser:** OpenResume's structural, rule-based parsing reliably links nested chronological data (e.g., role → company → dates) and guarantees deterministic output, unlike pyresparser's flat entity lists.
- **Archival over Hard Deletion:** Merged records are moved to `archive_profiles` rather than deleted, preserving a full audit trail for `provenance`.
- **Procurement Timestamps:** A `procured_at` timestamp set at ingestion determines the winning value for single-value field conflicts.
- **Deterministic ID Generation:** `candidate_id`s are generated deterministically from primary match keys, avoiding Mongita/MongoDB's probabilistic `_id` generation.