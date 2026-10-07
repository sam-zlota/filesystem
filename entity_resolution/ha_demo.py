"""High-availability walk-through on a real 3-process cluster.

    python ha_demo.py

A writer streams records the whole time.  Meanwhile:
  1. node n1 is killed with SIGKILL (no shutdown, unsynced log tail at risk);
  2. survivors detect it and take over its matching shards;
  3. n0 and n2 are partitioned from each other; both keep accepting writes;
  4. the partition heals;
  5. n1 restarts with a new incarnation, replays its log, and catches up
     through version-vector anti-entropy.
At the end all three nodes must hold identical state and entities, equal to
the single-node batch oracle.  No write may fail.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from typing import Any, Dict

from crdt_er import resolve_from_scratch
from crdt_er.dist.launch import LocalCluster
from crdt_er.dist.state import DistState
from crdt_er.dist.workload import make_records
from crdt_er.matching import PersonMatcher

MATCHER = {"zip_blocking": "zip+initials"}


async def run(records: int = 12000, batch: int = 40, rate_batches_per_s: float = 40.0,
              quiet: bool = False, match_workers: int = 0, similarity: str = "python") -> Dict[str, Any]:
    t0 = time.monotonic()

    def say(msg: str) -> None:
        if not quiet:
            print(f"[{time.monotonic() - t0:5.1f}s] {msg}", flush=True)

    workdir = tempfile.mkdtemp(prefix="crdt-er-ha-")
    matcher_cfg = {**MATCHER, "similarity": similarity}
    cluster = LocalCluster(workdir, 3, shards=64, matcher=matcher_cfg, match_workers=match_workers)
    cluster.start_all()
    out: Dict[str, Any] = {"failed_batches": 0}
    try:
        await cluster.wait_ready()
        say(f"cluster up: {cluster.ids}, {match_workers} matcher worker(s) per node, similarity={similarity}")
        data = make_records(records, seed=7)
        batches = [data[i:i + batch] for i in range(0, len(data), batch)]
        acked_by: Dict[str, int] = {}
        clients = [cluster.client(prefer=n, timeout=2.0) for n in cluster.ids]

        async def writer() -> None:
            for i, b in enumerate(batches):
                client = clients[i % len(clients)]
                try:
                    nid, _ = await client.call(["write", [["add", rid, attrs] for rid, attrs in b]])
                    acked_by[nid] = acked_by.get(nid, 0) + len(b)
                except ConnectionError:
                    out["failed_batches"] += 1
                await asyncio.sleep(1.0 / rate_batches_per_s)

        wtask = asyncio.create_task(writer())

        await asyncio.sleep(2.0)
        cluster.kill("n1")
        say("SIGKILL n1 (writes in flight to n1 fail over to other nodes)")

        await asyncio.sleep(2.5)
        st = await cluster.stats(["n0", "n2"])
        owned = sum(v["owned_shards"] for v in st.values())
        say(f"survivors see alive={st['n0']['alive']}; they now own {owned}/64 shards between them")
        out["owned_after_kill"] = owned

        await cluster.ask("n0", ["partition", ["n2"]])
        await cluster.ask("n2", ["partition", ["n0"]])
        say("partition: n0 | n2 (both sides keep accepting writes)")
        await asyncio.sleep(2.5)
        st = await cluster.stats(["n0", "n2"], view_hash=True)
        say(f"during partition: n0 has {st['n0']['records']} records, n2 has {st['n2']['records']}; "
            f"each owns {st['n0']['owned_shards']} and {st['n2']['owned_shards']} shards")
        out["diverged_during_partition"] = st["n0"]["view_hash"] != st["n2"]["view_hash"]

        await cluster.ask("n0", ["partition", []])
        await cluster.ask("n2", ["partition", []])
        say("partition healed")

        await asyncio.sleep(1.0)
        cluster.start("n1")
        await cluster.wait_ready(["n1"])
        st = await cluster.stats(["n1"])
        say(f"n1 restarted as {st['n1']['replica']} with {st['n1']['records']} records from its log")
        out["n1_replica"] = st["n1"]["replica"]

        await wtask
        say(f"writer done: {sum(acked_by.values())} records acked by {dict(sorted(acked_by.items()))}, "
            f"{out['failed_batches']} failed batches, {sum(c.failovers for c in clients)} client failovers")
        out["acked"] = sum(acked_by.values())
        out["failovers"] = sum(c.failovers for c in clients)
        for c in clients:
            await c.close()

        t_conv = time.monotonic()
        st = await cluster.wait_converged(timeout=120)
        say(f"all nodes converged {time.monotonic() - t_conv:.1f}s after the last write: "
            f"{st['n0']['records']} records, {st['n0']['entities']} entities, view {st['n0']['view_hash']}")
        out["records"] = st["n0"]["records"]
        out["entities"] = st["n0"]["entities"]

        dumps = await asyncio.gather(*(cluster.ask(n, ["dump"], timeout=60) for n in cluster.ids))
        states = []
        for d in dumps:
            ds = DistState()
            ds.merge(d["state"])
            states.append(ds.canonical())
        out["states_identical"] = all(s == states[0] for s in states)
        ds = DistState()
        ds.merge(dumps[0]["state"])
        oracle = resolve_from_scratch(ds.er, PersonMatcher(**matcher_cfg))
        out["oracle_ok"] = {e: frozenset(m) for e, m in dumps[0]["entities"].items()} == oracle
        say(f"identical state on all nodes: {out['states_identical']}; "
            f"entities equal batch oracle: {out['oracle_ok']}")
        return out
    finally:
        cluster.stop_all()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--match-workers", type=int, default=0)
    ap.add_argument("--similarity", default="python", choices=["python", "rapidfuzz"])
    a = ap.parse_args()
    asyncio.run(run(match_workers=a.match_workers, similarity=a.similarity))
