"""Mongita-backed profile store for the merge engine.

Collections
-----------
- ``active_profiles``  : the live, merged canonical profiles.
- ``archive_profiles`` : plain pre-merge backups of any active doc absorbed by a
  merge. Never referenced downstream — purely a safety net for inspection.

The store exposes the small get/find/insert/delete surface the merge engine
needs. Keeping it behind this class means the documented Mongita->JSON fallback
(see master_prompt.txt) could be swapped in without touching merge logic.

Mongita ``$or`` is broken
-------------------------
Verified on mongita 1.2.0: a top-level ``$or`` returns zero matches even for
plain equality, so the spec's "emails $in OR phones $in" query cannot be issued
as a single ``$or``. Instead :meth:`find_matches` runs the two ``$in`` queries
independently (both of which work correctly) and unions the results by ``_id``.
This is semantically identical to the intended OR and stays on Mongita, so the
auto-generated ``_id`` (used as ``candidate_id``) is preserved as the spec wants.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, List

# Make the project root importable even when this file is run directly.
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d):
    if os.path.exists(os.path.join(_d, "run_pipeline.py")):
        if _d not in sys.path:
            sys.path.insert(0, _d)
        break
    _d = os.path.dirname(_d)

from mongita import MongitaClientDisk

import paths


class ProfileStore:
    def __init__(self, db_dir: str | None = None):
        self._db_dir = db_dir or paths.DB_DIR
        self._client = MongitaClientDisk(self._db_dir)
        self._db = self._client.fusionhire
        self.active = self._db.active_profiles
        self.archive = self._db.archive_profiles

    # -- lifecycle ---------------------------------------------------------- #

    def reset(self) -> None:
        """Drop both collections so a run starts from an empty DB (idempotent)."""
        self._db.active_profiles.delete_many({})
        self._db.archive_profiles.delete_many({})

    # -- reads -------------------------------------------------------------- #

    def all_active(self) -> List[dict]:
        return [self._with_id(d) for d in self.active.find({})]

    def find_matches(self, emails: Iterable[str], phones: Iterable[str]) -> List[dict]:
        """Active docs whose emails intersect ``emails`` OR phones intersect ``phones``.

        Implemented as two separate ``$in`` queries unioned by ``_id`` because
        Mongita's ``$or`` is non-functional (see module docstring).
        """
        emails = [e for e in emails if e]
        phones = [p for p in phones if p]

        matched: dict[str, dict] = {}
        if emails:
            for doc in self.active.find({"emails": {"$in": emails}}):
                matched[str(doc["_id"])] = self._with_id(doc)
        if phones:
            for doc in self.active.find({"phones": {"$in": phones}}):
                matched[str(doc["_id"])] = self._with_id(doc)
        return list(matched.values())

    # -- writes ------------------------------------------------------------- #

    def insert_active(self, profile: dict) -> str:
        """Insert a profile into active_profiles; return its candidate_id (_id)."""
        doc = {k: v for k, v in profile.items() if k != "candidate_id"}
        doc.pop("_id", None)
        new_id = str(self.active.insert_one(doc).inserted_id)
        return new_id

    def archive_and_remove(self, docs: Iterable[dict]) -> None:
        """Back up matched active docs into archive, then delete them from active."""
        for doc in docs:
            backup = {k: v for k, v in doc.items() if k not in ("_id", "candidate_id")}
            self.archive.insert_one(backup)
            cid = doc.get("candidate_id") or doc.get("_id")
            if cid is not None:
                self.active.delete_many({"_id": self._oid(cid)})

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _with_id(doc: dict) -> dict:
        """Return a copy with candidate_id set from _id and _id dropped."""
        out = dict(doc)
        if "_id" in out:
            out["candidate_id"] = str(out.pop("_id"))
        return out

    @staticmethod
    def _oid(cid):
        """Coerce a candidate_id string back to Mongita's ObjectId for querying."""
        from bson.objectid import ObjectId

        if isinstance(cid, ObjectId):
            return cid
        try:
            return ObjectId(str(cid))
        except Exception:
            return cid


def init_store(reset: bool = False) -> ProfileStore:
    """Initialize (and optionally reset) the Mongita-backed profile store."""
    paths.ensure_dirs()
    store = ProfileStore()
    if reset:
        store.reset()
    return store


if __name__ == "__main__":
    # Smoke test: prove the store initializes and the email/phone match works.
    s = init_store(reset=True)
    cid = s.insert_active(
        {"emails": ["a@x.com"], "phones": ["+111"], "full_name": "Test"}
    )
    print("inserted candidate_id:", cid)
    print("match by email:", len(s.find_matches(["a@x.com"], [])))
    print("match by phone:", len(s.find_matches([], ["+111"])))
    print("no match:", len(s.find_matches(["nope@x.com"], ["+999"])))
    s.reset()
    print("after reset, active count:", len(s.all_active()))
