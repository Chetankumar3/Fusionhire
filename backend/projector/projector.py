"""The projector.

Reads a runtime config, fetches merged profiles from Mongita, reshapes each into
the requested output schema, validates with a generated Pydantic model, and
prints/writes the result. Keeps the canonical record and the projection cleanly
separated: the engine never changes, only the config does.

CLI:
    python -m projector.projector --config projector/configs/default.json
    python -m projector.projector --config <cfg> --output out.json
    python -m projector.projector --config <cfg> --candidate-id <id>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, List, Optional

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d):
    if os.path.exists(os.path.join(_d, "run_pipeline.py")):
        if _d not in sys.path:
            sys.path.insert(0, _d)
        break
    _d = os.path.dirname(_d)

import paths
from merge_engine.db import init_store
from merge_engine.models import build_projection_model

SUPPORTED_NORMALIZE = {"E164", "canonical"}


class ProjectorError(Exception):
    """Config/path error -> stderr + non-zero exit (per the error contract)."""


class MissingFieldError(Exception):
    """Raised for on_missing='error' when a required value is absent."""


# --------------------------------------------------------------------------- #
# `from` path grammar                                                          #
# --------------------------------------------------------------------------- #

_SENTINEL = object()  # distinguishes "absent" from a legitimate None value


def _split_path(path: str):
    parts = []
    for seg in path.split("."):
        m = re.match(r"^([A-Za-z_]\w*)?(\[\d*\])?$", seg)
        if not m or (m.group(1) is None and m.group(2) is None):
            raise ProjectorError(f"path not supported: {path}")
        name, br = m.group(1), m.group(2)
        if br is None:
            parts.append((name, None))
        elif br == "[]":
            parts.append((name, "[]"))
        else:
            parts.append((name, int(br[1:-1])))
    return parts


def _resolve(obj, parts, mapped: bool):
    if not parts:
        return obj
    (name, br), rest = parts[0], parts[1:]
    cur = obj
    if name is not None:
        if not isinstance(cur, dict) or name not in cur:
            return _SENTINEL
        cur = cur[name]
    if br is None:
        return _resolve(cur, rest, mapped)
    if br == "[]":
        if mapped:
            raise ProjectorError("path not supported")  # more than one [] level
        if cur is None:
            return _SENTINEL
        if not isinstance(cur, (list, tuple)):
            return _SENTINEL
        if not rest:
            return list(cur)  # bare array passthrough
        out = [_resolve(e, rest, True) for e in cur]
        return [v for v in out if v is not _SENTINEL]
    # fixed index
    if not isinstance(cur, (list, tuple)) or br >= len(cur):
        return _SENTINEL
    return _resolve(cur[br], rest, mapped)


def resolve_path(profile: dict, from_path: str):
    """Resolve a config `from` path; returns _SENTINEL if the value is absent."""
    return _resolve(profile, _split_path(from_path), mapped=False)


# --------------------------------------------------------------------------- #
# Cleaning helpers                                                             #
# --------------------------------------------------------------------------- #


def _strip_underscores(obj):
    """Recursively drop internal "_*" keys (merge bookkeeping) from output."""
    if isinstance(obj, dict):
        return {k: _strip_underscores(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_underscores(v) for v in obj]
    return obj


def _strip_keys(obj, keys: set):
    if isinstance(obj, dict):
        return {k: _strip_keys(v, keys) for k, v in obj.items() if k not in keys}
    if isinstance(obj, list):
        return [_strip_keys(v, keys) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# Projection                                                                   #
# --------------------------------------------------------------------------- #


def project_profile(data, on_missing_strategy):
    if isinstance(data, dict):
        processed_dict = {}
        
        for key, value in data.items():
            processed_val = project_profile(value, on_missing_strategy)
            
            is_absent = (
                processed_val is None or 
                processed_val == "" or 
                processed_val == [] or 
                processed_val == {}
            )
            
            if isinstance(processed_val, dict) and all(v is None for v in processed_val.values()):
                is_absent = True
                
            if is_absent:
                if on_missing_strategy == "error":
                    raise ValueError(f"Missing value encountered at key: '{key}'")
                elif on_missing_strategy == "omit":
                    continue  # Skip adding this key entirely
                elif on_missing_strategy == "null":
                    processed_dict[key] = None
            else:
                processed_dict[key] = processed_val

        if not processed_dict or all(v is None for v in processed_dict.values()):
            return None if on_missing_strategy == "null" else {}

        return processed_dict

    elif isinstance(data, list):
        processed_list = []
        
        for item in data:
            processed_item = project_profile(item, on_missing_strategy)
            
            is_absent = (
                processed_item is None or 
                processed_item == "" or 
                processed_item == [] or 
                processed_item == {}
            )
            
            if isinstance(processed_item, dict) and all(v is None for v in processed_item.values()):
                is_absent = True

            if is_absent:
                if on_missing_strategy == "error":
                    raise ValueError("Missing value encountered inside an array.")
                elif on_missing_strategy == "omit":
                    continue
                elif on_missing_strategy == "null":
                    processed_list.append(None)
            else:
                processed_list.append(processed_item)

        if not processed_list or all(v is None for v in processed_list):
            return None if on_missing_strategy == "null" else []

        return processed_list

    else:
        return data


def validate_projection(projected: dict, config: dict) -> dict:
    """Validate against a Pydantic model built from the config's field list."""
    from pydantic import ValidationError

    model = build_projection_model(config)
    try:
        validated = model(**projected)
    except ValidationError as exc:
        raise ProjectorError(f"validation failed: {exc}") from exc
    # exclude_unset so on_missing="omit" fields (never set on `projected`) don't
    # get re-materialized as null by the model's defaults.
    return validated.model_dump(exclude_unset=True)


def project_all(config: dict, candidate_id: Optional[str] = None) -> List[dict]:
    store = init_store(reset=False)
    profiles = store.all_canonical()
    if candidate_id:
        profiles = [p for p in profiles if str(p.get("candidate_id")) == str(candidate_id)]

    results = []
    for profile in profiles:
        projected = project_profile(profile, config)
        results.append(validate_projection(projected, config))
    return results


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="FusionHire projector")
    parser.add_argument("--config", required=True, help="Path to a projector config JSON")
    parser.add_argument("--output", help="Write JSON here instead of stdout")
    parser.add_argument("--candidate-id", help="Project only this candidate_id")
    args = parser.parse_args(argv)

    try:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as exc:
        print(f"[projector] cannot read config: {exc}", file=sys.stderr)
        return 2

    try:
        results = project_all(config, candidate_id=args.candidate_id)
    except (ProjectorError, MissingFieldError) as exc:
        # Error contract: clear stderr message, non-zero exit, no partial output.
        print(f"[projector] error: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(results, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
        print(f"[projector] wrote {len(results)} profile(s) -> {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
