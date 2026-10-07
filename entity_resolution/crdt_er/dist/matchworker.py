"""Matcher worker process: ``python -m crdt_er.dist.matchworker '<json config>'``.

Started by a node's main process (``server.WorkerPool``).  Owns a slice of the
node's shards in a :class:`BlockMatcher` and talks over stdin/stdout with
length-prefixed msgpack frames.

main -> worker (inside ["batch", [msg, ...]] frames)
  ["u", rid, lamport, replica, keys, fields, data]   current live version of a record
                                                     (fields or data is None)
  ["x", rid]                                         record no longer live / not ours
  ["reset", shards, [[rid, lamport, replica, keys, fields, data], ...]]
worker -> main
  ["f", facts, applied, pending]   facts found; messages applied so far; queued work left
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, List

from ..matching import Normalized, PersonMatcher
from .blocking import BlockMatcher
from .wire import pack, read_frame


def to_norm(keys: List[str], fields: Any, data: Any) -> Normalized:
    return Normalized(
        keys=frozenset(keys),
        fields=() if fields is None else tuple(tuple(f) for f in fields),
        data=None if data is None else tuple(data),
    )


async def main() -> None:
    cfg = json.loads(sys.argv[1])
    bm = BlockMatcher(PersonMatcher(**cfg.get("matcher", {})), cfg["shards"])
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=2**26)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout.buffer)
    writer = asyncio.StreamWriter(w_transport, w_protocol, None, loop)
    applied = 0
    reported = -1
    work = asyncio.Event()
    budget = cfg.get("slice_s", 0.02)

    async def read_loop() -> None:
        nonlocal applied
        while True:
            try:
                msg = await read_frame(reader)
            except asyncio.IncompleteReadError:
                loop.stop()  # main process went away
                return
            for m in msg[1]:
                kind = m[0]
                if kind == "u":
                    _, rid, lamport, rep, keys, fields, data = m
                    bm.upsert(rid, (lamport, rep), to_norm(keys, fields, data))
                elif kind == "x":
                    bm.remove(m[1])
                elif kind == "reset":
                    bm.reset(m[1], ((r[0], (r[1], r[2]), to_norm(r[3], r[4], r[5])) for r in m[2]))
                applied += 1
            work.set()

    async def work_loop() -> None:
        nonlocal reported
        while True:
            if bm.has_pending():
                facts = bm.step(budget_s=budget)
                writer.write(pack(["f", [list(f) for f in facts], applied, bm.pending_count()]))
                reported = applied
                await writer.drain()
                await asyncio.sleep(0)  # let queued input in
            else:
                if reported != applied:
                    writer.write(pack(["f", [], applied, 0]))
                    reported = applied
                    await writer.drain()
                work.clear()
                await work.wait()

    await asyncio.gather(read_loop(), work_loop())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        pass  # loop stopped because the main process exited
