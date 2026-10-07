"""Incremental candidate generation (blocking).

Two indexes, both supporting insert/remove so they can follow record churn:

* MinHash LSH (datasketch) over character shingles of fuzzy fields, catching
  spelling variants ("Jon Smith" / "John Smith" / "Smith, John").
* Exact inverted index over normalized identifiers (email, phone).

Crucially, "is (a, b) a candidate pair" depends only on the contents of a and
b (shared LSH band bucket or shared exact key), never on insertion order or on
other records. That is what lets every replica derive the same candidate graph.
MinHash uses seeded permutations and SHA-1 token hashing, so it is stable
across processes (it does not depend on Python's randomized ``hash()``).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping

from datasketch import MinHash, MinHashLSH

from .config import ResolverConfig
from .similarity import normalize, phone_key


class Blocker:
    def __init__(self, config: ResolverConfig):
        self.config = config
        self._lsh = MinHashLSH(threshold=config.lsh_threshold, num_perm=config.lsh_num_perm)
        self._minhash: dict[str, MinHash] = {}
        self._keys: dict[str, set[str]] = {}
        self._postings: dict[str, set[str]] = defaultdict(set)

    def _shingles(self, record: Mapping[str, str]) -> set[str]:
        k = self.config.shingle_size
        out: set[str] = set()
        for f in self.config.lsh_fields:
            v = record.get(f)
            if not v:
                continue
            # Sort tokens so that "Smith, John" and "John Smith" shingle alike.
            text = " ".join(sorted(normalize(v).split()))
            padded = f" {text} "
            out.update(f"{f}:{padded[i:i + k]}" for i in range(max(1, len(padded) - k + 1)))
        return out

    def _exact_keys(self, record: Mapping[str, str]) -> set[str]:
        keys = set()
        for f in self.config.exact_block_fields:
            v = record.get(f)
            if not v:
                continue
            key = phone_key(v) if f == "phone" else normalize(v)
            if key:
                keys.add(f"{f}={key}")
        return keys

    def remove(self, rid: str) -> None:
        if self._minhash.pop(rid, None) is not None:
            self._lsh.remove(rid)
        for key in self._keys.pop(rid, ()):
            self._postings[key].discard(rid)
            if not self._postings[key]:
                del self._postings[key]

    def upsert(self, rid: str, record: Mapping[str, str]) -> None:
        self.remove(rid)
        shingles = self._shingles(record)
        if shingles:  # an empty MinHash would collide with every other empty one
            mh = MinHash(num_perm=self.config.lsh_num_perm, seed=self.config.lsh_seed)
            for s in sorted(shingles):
                mh.update(s.encode("utf-8"))
            self._minhash[rid] = mh
            self._lsh.insert(rid, mh)
        keys = self._exact_keys(record)
        self._keys[rid] = keys
        for key in keys:
            self._postings[key].add(rid)

    def candidates(self, rid: str) -> set[str]:
        out: set[str] = set()
        mh = self._minhash.get(rid)
        if mh is not None:
            out.update(self._lsh.query(mh))
        for key in self._keys.get(rid, ()):
            out |= self._postings[key]
        out.discard(rid)
        return out
