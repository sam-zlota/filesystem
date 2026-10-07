"""Throughput benchmark: the same workload on clusters of different sizes.

    python bench.py --nodes 1 2 4 --records 20000

For each cluster size it starts real node processes, streams the records in
batches from concurrent writers that spread load across the nodes, and
reports two rates:

* ingest   records acknowledged per second (writes are durable on the
           accepting node and broadcast; matching is still running)
* resolved records per second until *every* node has finished matching,
           holds the same version vector and the same entity view
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile
import time

from crdt_er import resolve_from_scratch
from crdt_er.dist.launch import LocalCluster
from crdt_er.dist.state import DistState
from crdt_er.dist.workload import make_records
from crdt_er.matching import PersonMatcher

MATCHER = {"zip_blocking": "zip+initials"}


def proc_cpu_seconds(pid: int) -> float:
    """User+system CPU of a process (Linux), 0.0 where /proc is unavailable."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return 0.0


async def run_one(n_nodes: int, records, args) -> dict:
    workdir = tempfile.mkdtemp(prefix=f"crdt-er-bench-{n_nodes}-")
    cluster = LocalCluster(workdir, n_nodes, shards=args.shards, matcher=MATCHER, fsync=args.fsync)
    cluster.start_all()
    try:
        await cluster.wait_ready()
        batches = [records[i:i + args.batch] for i in range(0, len(records), args.batch)]
        queue: asyncio.Queue = asyncio.Queue()
        for b in batches:
            queue.put_nowait(b)

        async def writer(k: int) -> None:
            client = cluster.client(prefer=cluster.ids[k % n_nodes])
            try:
                while not queue.empty():
                    batch = queue.get_nowait()
                    await client.write([["add", rid, attrs] for rid, attrs in batch])
            finally:
                await client.close()

        t0 = time.perf_counter()
        await asyncio.gather(*(writer(k) for k in range(args.writers)))
        t_ack = time.perf_counter() - t0
        st = await cluster.wait_converged(poll=0.1)
        t_done = time.perf_counter() - t0
        cpu = [proc_cpu_seconds(cluster.procs[n].pid) for n in cluster.ids]

        result = {
            "nodes": n_nodes,
            "ingest_rps": len(records) / t_ack,
            "resolved_rps": len(records) / t_done,
            "seconds": t_done,
            "entities": next(iter(st.values()))["entities"],
            "comparisons": [st[n]["metrics"].get("comparisons", 0) for n in cluster.ids],
            "cpu": cpu,
        }
        if args.verify:
            dump = await cluster.ask(cluster.ids[0], ["dump"], timeout=120)
            ds = DistState()
            ds.merge(dump["state"])
            oracle = resolve_from_scratch(ds.er, PersonMatcher(**MATCHER))
            got = {e: frozenset(m) for e, m in dump["entities"].items()}
            result["oracle_ok"] = got == oracle
        return result
    finally:
        cluster.stop_all()
        shutil.rmtree(workdir, ignore_errors=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, nargs="+", default=[1, 2, 4])
    ap.add_argument("--records", type=int, default=20000)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--writers", type=int, default=8)
    ap.add_argument("--shards", type=int, default=64)
    ap.add_argument("--fsync", action="store_true")
    ap.add_argument("--verify", action="store_true", help="check the final view against the batch oracle")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--zips", type=int, default=6, help="fewer zips = bigger blocks = more matching per record")
    args = ap.parse_args()

    records = make_records(args.records, seed=args.seed, zips=args.zips)
    print(f"{args.records} records, batches of {args.batch}, {args.writers} writers, "
          f"{args.shards} shards, {os.cpu_count()} CPUs, fsync={'on' if args.fsync else 'off'}")
    print(f"{'nodes':>5} {'ingest rec/s':>13} {'resolved rec/s':>15} {'speedup':>8} "
          f"{'seconds':>8} {'entities':>9}  {'cpu s/node':>10}  comparisons per node")
    base = None
    for n in args.nodes:
        r = await run_one(n, records, args)
        base = base or r["resolved_rps"]
        extra = f"  oracle={'ok' if r.get('oracle_ok') else 'MISMATCH'}" if "oracle_ok" in r else ""
        print(f"{r['nodes']:>5} {r['ingest_rps']:>13.0f} {r['resolved_rps']:>15.0f} "
              f"{r['resolved_rps'] / base:>7.2f}x {r['seconds']:>8.1f} {r['entities']:>9}  "
              f"{max(r['cpu']):>10.1f}  {r['comparisons']}{extra}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
