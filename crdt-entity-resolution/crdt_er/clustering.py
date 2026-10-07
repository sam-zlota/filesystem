"""Deterministic constrained clustering of one connected component.

Kruskal-style greedy agglomeration over a union-find:

1. must-link edges first, in canonical (id) order;
2. then scored match edges by descending score, ties broken by ids;
3. a union is skipped if any cannot-link pair would end up in one cluster.

The output depends only on the *set* of nodes/edges/constraints passed in,
never on the order in which they were discovered, which is what makes the
derived clustering identical on every replica holding the same state.
"""

from __future__ import annotations

from typing import Iterable, Mapping

Pair = tuple[str, str]


def constrained_greedy(
    nodes: Iterable[str],
    must_edges: Iterable[Pair],
    scored_edges: Iterable[tuple[float, str, str]],
    cannot_adj: Mapping[str, set[str]],
) -> list[frozenset[str]]:
    parent: dict[str, str] = {n: n for n in nodes}
    members: dict[str, set[str]] = {n: {n} for n in parent}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def violates(ra: str, rb: str) -> bool:
        small, big = (ra, rb) if len(members[ra]) <= len(members[rb]) else (rb, ra)
        big_members = members[big]
        return any(c in big_members for m in members[small] for c in cannot_adj.get(m, ()))

    ordered = [(a, b) for a, b in sorted(must_edges)]
    ordered += [(a, b) for _, a, b in sorted(scored_edges, key=lambda e: (-e[0], e[1], e[2]))]

    for a, b in ordered:
        ra, rb = find(a), find(b)
        if ra == rb or violates(ra, rb):
            continue
        if len(members[ra]) < len(members[rb]):
            ra, rb = rb, ra
        parent[rb] = ra
        members[ra] |= members.pop(rb)

    return [frozenset(m) for m in members.values()]
