"""Single-process throughput benchmark.

    python benchmarks/bench.py [sizes...]      default: 1000 5000 20000

Synthetic data: ~N/2.5 people, each with 1-4 noisy records (typos, name
order swaps, missing/changed emails and phones), so duplicate clusters and
near-miss candidates are realistic rather than trivially separable.
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crdt_er import IncrementalResolver, Replica  # noqa: E402

FIRST = """james mary john patricia robert jennifer michael linda william elizabeth david barbara
richard susan joseph jessica thomas sarah charles karen christopher lisa daniel nancy matthew
betty anthony margaret mark sandra donald ashley steven kimberly paul emily andrew donna joshua
michelle kenneth carol kevin amanda brian melissa george deborah timothy stephanie ronald rebecca
wei li ana maria jose carlos priya arjun""".split()
LAST = """smith johnson williams brown jones garcia miller davis rodriguez martinez hernandez lopez
gonzalez wilson anderson thomas taylor moore jackson martin lee perez thompson white harris
sanchez clark ramirez lewis robinson walker young allen king wright scott torres nguyen hill
flores green adams nelson baker hall rivera campbell mitchell carter roberts chen wang patel
kim singh""".split()


def typo(rnd, s):
    if len(s) < 4 or rnd.random() > 0.3:
        return s
    i = rnd.randrange(1, len(s) - 1)
    return s[:i] + s[i + 1:] if rnd.random() < .5 else s[:i] + s[i + 1] + s[i] + s[i + 2:]


def dataset(n, seed=0):
    rnd = random.Random(seed)
    people = []
    for p in range(max(1, int(n / 2.5))):
        f, l = rnd.choice(FIRST), rnd.choice(LAST)
        people.append({
            "first": f, "last": l,
            "email": f"{f[0]}{l}{p}@{rnd.choice(['gmail.com', 'acme.com', 'mail.org'])}",
            "phone": f"{rnd.randrange(200, 999)}-555-{p % 10000:04d}",
            "address": f"{rnd.randrange(1, 999)} {rnd.choice(LAST).title()} St",
        })
    recs = {}
    for i in range(n):
        p = rnd.choice(people)
        name = f"{typo(rnd, p['first']).title()} {typo(rnd, p['last']).title()}"
        if rnd.random() < .2:
            name = f"{p['last'].title()}, {p['first'].title()}"
        r = {"name": name}
        if rnd.random() < .6:
            r["email"] = p["email"]
        if rnd.random() < .5:
            r["phone"] = p["phone"]
        if rnd.random() < .4:
            r["address"] = p["address"]
        recs[f"r{i}"] = r
    return recs


def rate(count, seconds):
    return f"{count / seconds:>8,.0f}/s  ({seconds / count * 1e3:6.2f} ms/op)"


def bench(n):
    recs = dataset(n)
    ids = list(recs)
    rnd = random.Random(1)
    print(f"\n### {n:,} records")

    # 1. local ingest through the full stack (CRDT write + incremental resolve)
    a = Replica(1)
    t = time.perf_counter()
    for rid in ids:
        a.upsert(rid, recs[rid])
    print(f"  ingest (local upsert, new record)   {rate(n, time.perf_counter() - t)}")
    st = a.resolver.stats
    print(f"    entities={len(a.resolver.clusters):,}  pairs scored={st['pairs_scored']:,}  "
          f"max component={st['max_component']}")

    # 2. engine only, same work without the CRDT layer
    eng = IncrementalResolver()
    t = time.perf_counter()
    for rid in ids:
        eng.apply({rid: recs[rid]})
    print(f"  ingest (resolver only, no CRDT)     {rate(n, time.perf_counter() - t)}")

    # 3. full batch rebuild from state
    t = time.perf_counter()
    IncrementalResolver.batch(recs, [], [])
    bt = time.perf_counter() - t
    print(f"  batch rebuild (one apply of all)    {rate(n, bt)}   total {bt:.2f}s")

    # 4. steady-state edits on existing records
    k = min(1000, n // 10)
    t = time.perf_counter()
    for _ in range(k):
        rid = rnd.choice(ids)
        a.upsert(rid, {"email": f"x{rnd.randrange(n)}@new.com"})
    print(f"  edit existing record                {rate(k, time.perf_counter() - t)}")

    # 5. human feedback
    t = time.perf_counter()
    for _ in range(k):
        x, y = rnd.sample(ids, 2)
        (a.assert_match if rnd.random() < .5 else a.assert_non_match)(x, y)
    print(f"  match / non-match assertion         {rate(k, time.perf_counter() - t)}")

    # 6. delete
    t = time.perf_counter()
    for rid in rnd.sample(ids, k):
        a.remove(rid)
    print(f"  delete record                       {rate(k, time.perf_counter() - t)}")

    # 7. replication: a peer receives everything in one update
    t = time.perf_counter()
    upd = a.changes_since()
    enc = time.perf_counter() - t
    b = Replica(2)
    t = time.perf_counter()
    b.receive(upd)
    rt = time.perf_counter() - t
    live = len(a.resolver.records)
    print(f"  remote bulk receive ({live:,} live recs) {rate(live, rt)}   "
          f"update {len(upd) / 1e6:.1f} MB, encode {enc * 1e3:.0f} ms")

    # 8. replication: small incremental syncs (anti-entropy every 10 ops)
    c = Replica(3)
    c.sync(a)
    ops, t = 0, time.perf_counter()
    for _ in range(100):
        for _ in range(10):
            a.upsert(rnd.choice(list(a.resolver.records)), {"phone": f"{rnd.randrange(10**9, 10**10)}"})
            ops += 1
        c.sync(a)
    print(f"  edit + sync every 10 ops (2 nodes)  {rate(ops, time.perf_counter() - t)}")
    assert c.resolver.snapshot() == a.resolver.snapshot()


if __name__ == "__main__":
    for n in [int(x) for x in sys.argv[1:]] or [1000, 5000, 20000]:
        bench(n)
