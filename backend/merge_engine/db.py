"""Mongita-backed profile store (DSU-style).

Collections
-----------
- ``canonical_profiles`` — primary key ``candidate_id`` (Mongita auto ``_id``).
  One document per merged identity. This is the ONLY collection the projector
  reads from.
- ``normalized_profiles`` — primary key ``id`` (Mongita auto ``_id``). One
  document per raw parsed record, ever. Carries a foreign key ``profile_of`` ->
  ``canonical_profiles.candidate_id``. These docs are never merged into each
  other and never mutated except for ``profile_of`` being repointed. This is the
  permanent raw audit trail — there is no separate archive collection.

Ingestion flow (per incoming parsed record) lives in ``merge.run`` and rebuilds
each canonical profile from scratch out of the raw ``normalized_profiles``
records — never from a previous canonical profile's already-merged values.

Mongita ``$or`` is broken
-------------------------
Verified on mongita 1.2.0: a top-level ``$or`` returns zero matches even for
plain equality. The "emails $in OR phones $in" match is therefore issued as two
independent ``$in`` queries (both of which work) and unioned in Python.
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

from bson.objectid import ObjectId
from mongita import MongitaClientDisk

import paths


class ProfileStore:
    def __init__(self, db_dir: str | None = None):
        self._db_dir = db_dir or paths.DB_DIR
        self._client = MongitaClientDisk(self._db_dir)
        self._db = self._client.fusionhire
        self.canonical = self._db.canonical_profiles
        self.normalized = self._db.normalized_profiles

    # -- lifecycle ---------------------------------------------------------- #

    def reset(self) -> None:
        """Empty both collections so a run starts fresh (idempotent)."""
        self._db.canonical_profiles.delete_many({})
        self._db.normalized_profiles.delete_many({})

    # -- normalized_profiles (raw audit trail) ------------------------------ #

    def insert_normalized(self, record: dict) -> str:
        """Insert a raw parsed record with ``profile_of=None``; return its ``id``."""
        doc = {k: v for k, v in record.items() if k not in ("_id", "id")}
        doc["profile_of"] = None
        return str(self.normalized.insert_one(doc).inserted_id)

    def normalized_by_profile_of(self, cand_ids: Iterable[str]) -> List[dict]:
        """Every normalized record currently pointing at one of ``cand_ids``."""
        cand_ids = [c for c in cand_ids if c]
        if not cand_ids:
            return []
        return [self._norm_out(d) for d in self.normalized.find({"profile_of": {"$in": cand_ids}})]

    def repoint(self, normalized_ids: Iterable[str], new_candidate_id: str) -> None:
        """Set ``profile_of = new_candidate_id`` on the given normalized docs."""
        for nid in normalized_ids:
            if nid is None:
                continue
            self.normalized.update_one(
                {"_id": self._oid(nid)}, {"$set": {"profile_of": new_candidate_id}}
            )

    # -- canonical_profiles ------------------------------------------------- #

    def all_canonical(self) -> List[dict]:
        return [self._with_id(d) for d in self.canonical.find({})]

    def find_canonical_ids_by_contact(
        self, emails: Iterable[str], phones: Iterable[str]
    ) -> List[str]:
        """candidate_ids of canonical docs whose emails OR phones intersect the args.

        Two independent ``$in`` queries unioned by id (Mongita ``$or`` is broken).
        """
        emails = [e for e in emails if e]
        phones = [p for p in phones if p]

        found: dict[str, None] = {}  # ordered set of candidate_id strings
        if emails:
            for doc in self.canonical.find({"emails": {"$in": emails}}):
                found[str(doc["_id"])] = None
        if phones:
            for doc in self.canonical.find({"phones": {"$in": phones}}):
                found[str(doc["_id"])] = None
        return list(found.keys())

    def insert_canonical(self, profile: dict) -> str:
        """Insert a merged canonical profile; return its new ``candidate_id``."""
        doc = {k: v for k, v in profile.items() if k not in ("candidate_id", "_id")}
        return str(self.canonical.insert_one(doc).inserted_id)

    def delete_canonical(self, cand_ids: Iterable[str]) -> None:
        for cid in cand_ids:
            if cid is None:
                continue
            self.canonical.delete_one({"_id": self._oid(cid)})

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _with_id(doc: dict) -> dict:
        """Canonical doc -> copy with candidate_id (from _id), _id dropped."""
        out = dict(doc)
        if "_id" in out:
            out["candidate_id"] = str(out.pop("_id"))
        return out

    @staticmethod
    def _norm_out(doc: dict) -> dict:
        """Normalized doc -> copy with id (from _id), _id dropped."""
        out = dict(doc)
        if "_id" in out:
            out["id"] = str(out.pop("_id"))
        return out

    @staticmethod
    def _oid(cid):
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
    # Smoke test: insert two raw records for one person, verify the DSU wiring.
    s = init_store(reset=True)
    r1 = {"source": "csv_1", "emails": ["a@x.com"], "phones": ["+111"], "full_name": "Test"}
    r2 = {"source": "resume_x", "emails": ["a@x.com"], "phones": ["+222"], "full_name": "Test 2"}

    id1 = s.insert_normalized(r1)
    cids = s.find_canonical_ids_by_contact(r1["emails"], r1["phones"])
    print("no canonical yet -> cand_ids:", cids)
    cid = s.insert_canonical({"emails": ["a@x.com"], "phones": ["+111"]})
    s.repoint([id1], cid)

    id2 = s.insert_normalized(r2)
    cids = s.find_canonical_ids_by_contact(r2["emails"], r2["phones"])
    print("match by email -> cand_ids:", cids)
    print("normalized under cand:", len(s.normalized_by_profile_of(cids)))
    s.reset()
    print("after reset, canonical count:", len(s.all_canonical()))
