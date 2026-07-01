"""Single source of truth for every filesystem location in the pipeline.

Importing this from any component (parsers, merge_engine, projector, tests)
guarantees they all read/write the same folders regardless of the current
working directory. All paths are absolute and derived from this file's location.
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.abspath(__file__))

DATA_SOURCES = os.path.join(ROOT, "data_sources")
CSVS_DIR = os.path.join(DATA_SOURCES, "csvs")
RESUMES_DIR = os.path.join(DATA_SOURCES, "resumes")

PARSERS = os.path.join(ROOT, "parsers")
UTILS_DIR = os.path.join(PARSERS, "utils")
GROUPINGS_JSON = os.path.join(UTILS_DIR, "groupings.json")
MAPPINGS_JSON = os.path.join(UTILS_DIR, "mappings.json")

RESUME_PARSER_DIR = os.path.join(PARSERS, "resume_parser")
# Part A (Node/OpenResume) writes its intermediate raw JSON here for Part B.
RESUME_RAW_DIR = os.path.join(RESUME_PARSER_DIR, "raw_json")

SHARED_MEMORY = os.path.join(ROOT, "shared_memory")
DB_DIR = os.path.join(SHARED_MEMORY, "db")
PARSED_JSONS = os.path.join(SHARED_MEMORY, "parsed_jsons")

PROJECTOR_DIR = os.path.join(ROOT, "projector")
CONFIGS_DIR = os.path.join(PROJECTOR_DIR, "configs")


def ensure_dirs() -> None:
    """Create every directory the pipeline writes into (idempotent)."""
    for d in (
        CSVS_DIR,
        RESUMES_DIR,
        UTILS_DIR,
        RESUME_RAW_DIR,
        DB_DIR,
        PARSED_JSONS,
        CONFIGS_DIR,
    ):
        os.makedirs(d, exist_ok=True)
