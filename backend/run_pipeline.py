"""Idempotent list that deletes all intermediary files enerated in between before starting the run of the pipeline:

Usage:
    python run_pipeline.py                       # full run, default + custom outputs
    python run_pipeline.py --config <cfg.json>   # also print this config's projection
    python run_pipeline.py --skip-resume         # CSV only
    python run_pipeline.py --no-node             # don't run Part A; reuse raw_json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths
from parsers.csv_parser import csv_parser
from parsers.resume_parser import part_b
from merge_engine import merge
from projector import projector

DEFAULT_CONFIG = os.path.join(paths.CONFIGS_DIR, "default.json")
CUSTOM_CONFIG = os.path.join(paths.CONFIGS_DIR, "custom_contact_card.json")
OUT_DEFAULT = os.path.join(paths.SHARED_MEMORY, "output_default.json")
OUT_CUSTOM = os.path.join(paths.SHARED_MEMORY, "output_custom.json")


def _clean_dir(d: str, suffix: str) -> None:
    if not os.path.isdir(d):
        return
    for f in os.listdir(d):
        if f.endswith(suffix):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass


def clean_intermediates(clean_raw: bool) -> None:
    _clean_dir(paths.PARSED_JSONS, ".json")
    if clean_raw:
        _clean_dir(paths.RESUME_RAW_DIR, ".json")
    # DB is reset (delete_many) inside merge.run(reset=True).


def run_part_a() -> bool:
    """Run resume Part A (Node). Returns True if it produced fresh raw JSON."""
    rp = paths.RESUME_PARSER_DIR
    if not shutil.which("node"):
        print("[pipeline] node not found; reusing committed raw_json/", file=sys.stderr)
        return False
    if not os.path.isdir(os.path.join(rp, "node_modules")):
        print(
            "[pipeline] resume_parser/node_modules missing; run `npm install` there. "
            "Reusing committed raw_json/.",
            file=sys.stderr,
        )
        return False
    try:
        subprocess.run(["node", "parse_resume.mjs"], cwd=rp, check=True)
        return True
    except Exception as exc:  # degrade gracefully
        print(f"[pipeline] Part A failed ({exc}); reusing committed raw_json/", file=sys.stderr)
        return False


def project_to_file(config_path: str, out_path: str) -> int:
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    results = projector.project_all(config)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"[pipeline] projected {len(results)} profile(s) -> {out_path}")
    return len(results)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="FusionHire pipeline")
    parser.add_argument("--config", help="Extra projector config to print to stdout")
    parser.add_argument("--skip-resume", action="store_true", help="CSV source only")
    parser.add_argument("--no-node", action="store_true", help="Skip Part A; reuse raw_json/")
    args = parser.parse_args(argv)

    paths.ensure_dirs()

    will_run_part_a = (not args.skip_resume) and (not args.no_node)
    clean_intermediates(clean_raw=will_run_part_a)

    # 1. Parse ---------------------------------------------------------------
    print("== STEP 1: parse ==")
    csv_parser.run()
    if not args.skip_resume:
        if will_run_part_a:
            run_part_a()
        part_b.run()

    # 2. Merge ---------------------------------------------------------------
    print("== STEP 2: merge ==")
    store = merge.run(reset=True)

    # 3. Project -------------------------------------------------------------
    print("== STEP 3: project ==")
    project_to_file(DEFAULT_CONFIG, OUT_DEFAULT)
    project_to_file(CUSTOM_CONFIG, OUT_CUSTOM)

    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
        try:
            results = projector.project_all(config)
        except (projector.ProjectorError, projector.MissingFieldError) as exc:
            print(f"[pipeline] projection error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(results, indent=2, ensure_ascii=False))

    print(f"\nDone. {len(store.all_canonical())} canonical profile(s) in canonical_profiles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
