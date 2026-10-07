"""One cluster node as an OS process: ``python -m crdt_er.dist.server``.

Single asyncio event loop per process (actor style: all state is touched from
one thread, so the Node needs no locks).  Throughput comes from running
several processes, each owning a slice of the matching work.

Peer protocol (frames are ``pack([...])``):
  ["hello", node_id]                 first frame on a peer connection
  ["batch", [msg, ...]]              coalesced peer messages:
      ["hb"]                         heartbeat
      ["d", origin, seq, wire]       a delta
      ["vv", summary, want_reply]    anti-entropy: "here is what I hold"
Client protocol (request/response on the same connection):
  ["write", ops] -> ["ok", results]
  ["stats", {"view_hash": bool}] | ["entities"] | ["entity_of", [rid...]] | ["dump"]
  ["partition", [peer...]]           test hook: drop traffic with these peers
  ["shutdown"]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from ..matching import PersonMatcher
from .node import Delta, Node
from .wal import Wal, bump_incarnation
from .wire import pack, read_frame

log = logging.getLogger("crdt_er.node")


class WorkerPool:
    """Matching side of a node spread over K worker processes.

    Presents the same interface as :class:`BlockMatcher` to the Node
    (``upsert`` / ``remove`` / ``reset`` / ``has_pending`` / ``pending_count``)
    but forwards records to the worker that owns each blocking key's shard
    (worker ``shard % K``).  Facts come back asynchronously and are handed to
    ``on_facts``.  A worker crash takes the whole node down: the cluster
    already tolerates node failures, so there is no separate recovery path.
    """

    def __init__(self, k: int, shards: int, matcher_cfg: Dict[str, Any]) -> None:
        self.k = k
        self.n_shards = shards
        self.cfg = json.dumps({"shards": shards, "matcher": matcher_cfg})
        self.owned: Set[int] = set()
        self.holder: Dict[str, Set[int]] = {}  # rid -> workers that hold it
        self.buf: List[List[Any]] = [[] for _ in range(k)]
        self.sent = [0] * k
        self.applied = [0] * k
        self.remote_pending = [0] * k
        self.events = [asyncio.Event() for _ in range(k)]
        self.procs: List[Any] = []
        self._shard_cache: Dict[str, int] = {}
        self.on_facts = lambda facts: None
        self.metrics: Dict[str, int] = {}

    def shard_of(self, key: str) -> int:
        s = self._shard_cache.get(key)
        if s is None:
            import zlib
            s = self._shard_cache[key] = zlib.crc32(key.encode()) % self.n_shards
        return s

    def _send(self, w: int, msg: List[Any]) -> None:
        self.buf[w].append(msg)
        self.sent[w] += 1
        self.events[w].set()

    @staticmethod
    def _rec(rid: str, ver: Tuple[int, str], nn: Any) -> List[Any]:
        # a matcher that precomputes ``data`` (PersonMatcher) needs only that
        if nn.data is not None:
            return [rid, ver[0], ver[1], list(nn.keys), None, nn.data]
        return [rid, ver[0], ver[1], list(nn.keys), nn.fields, None]

    # -- BlockMatcher interface -----------------------------------------------------
    def upsert(self, rid: str, ver: Tuple[int, str], nn: Any) -> None:
        need = {self.shard_of(k) % self.k for k in nn.keys if self.shard_of(k) in self.owned}
        for w in self.holder.get(rid, set()) - need:
            self._send(w, ["x", rid])
        if need:
            rec = ["u"] + self._rec(rid, ver, nn)
            for w in need:
                self._send(w, rec)
            self.holder[rid] = need
        else:
            self.holder.pop(rid, None)

    def remove(self, rid: str) -> None:
        for w in self.holder.pop(rid, ()):
            self._send(w, ["x", rid])

    def reset(self, owned: Any, records: Any) -> None:
        self.owned = set(owned)
        per_worker: List[List[Any]] = [[] for _ in range(self.k)]
        self.holder = {}
        for rid, ver, nn in records:
            need = {self.shard_of(k) % self.k for k in nn.keys if self.shard_of(k) in self.owned}
            if need:
                rec = self._rec(rid, ver, nn)
                for w in need:
                    per_worker[w].append(rec)
                self.holder[rid] = need
        for w in range(self.k):
            self._send(w, ["reset", sorted(s for s in self.owned if s % self.k == w), per_worker[w]])

    def has_pending(self) -> bool:
        return any(self.sent[w] != self.applied[w] or self.remote_pending[w] for w in range(self.k))

    def pending_count(self) -> int:
        return sum(self.remote_pending) + sum(self.sent[w] - self.applied[w] for w in range(self.k))

    # -- processes ---------------------------------------------------------------------
    async def start(self) -> List[asyncio.Task]:
        tasks = []
        for w in range(self.k):
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "crdt_er.dist.matchworker", self.cfg,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=2**26,
            )
            self.procs.append(proc)
            tasks.append(asyncio.create_task(self._writer(w, proc)))
            tasks.append(asyncio.create_task(self._reader(w, proc)))
        return tasks

    async def _writer(self, w: int, proc: Any) -> None:
        while True:
            await self.events[w].wait()
            self.events[w].clear()
            msgs, self.buf[w] = self.buf[w], []
            proc.stdin.write(pack(["batch", msgs]))
            await proc.stdin.drain()

    async def _reader(self, w: int, proc: Any) -> None:
        while True:
            try:
                kind, facts, applied, pending = await read_frame(proc.stdout)
            except asyncio.IncompleteReadError:
                log.error("matcher worker %d exited; stopping node", w)
                os._exit(3)
            self.applied[w] = applied
            self.remote_pending[w] = pending
            if facts:
                self.on_facts(facts)

    def stop(self) -> None:
        for proc in self.procs:
            if proc.returncode is None:
                proc.kill()


class PeerLink:
    """Outbound connection to one peer with a coalescing send buffer.

    If the peer is unreachable, messages are dropped rather than queued
    without bound; the version-vector anti-entropy repairs the gap later.
    """

    def __init__(self, owner: "NodeServer", peer: str, addr: Tuple[str, int]) -> None:
        self.owner = owner
        self.peer = peer
        self.addr = addr
        self.buf: List[Any] = []
        self.event = asyncio.Event()
        self.connected = False

    def send(self, msg: Any) -> None:
        if self.connected and self.peer not in self.owner.blocked:
            self.buf.append(msg)
            self.event.set()

    async def run(self) -> None:
        while True:
            writer = None
            try:
                _, writer = await asyncio.open_connection(*self.addr)
                writer.write(pack(["hello", self.owner.id]))
                await writer.drain()
                self.connected = True
                while True:
                    await self.event.wait()
                    self.event.clear()
                    msgs, self.buf = self.buf, []
                    for i in range(0, len(msgs), 64):
                        writer.write(pack(["batch", msgs[i:i + 64]]))
                    await writer.drain()
            except (OSError, asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                self.connected = False
                self.buf.clear()
                if writer is not None:
                    writer.close()
            await asyncio.sleep(0.2)


class NodeServer:
    def __init__(
        self,
        node_id: str,
        cluster: Dict[str, Any],
        data_dir: str,
        fsync: bool = False,
        heartbeat_s: float = 0.2,
        suspect_s: float = 1.5,
        ae_interval_s: float = 0.5,
        ae_grace_s: float = 1.0,
        match_slice_s: float = 0.02,
    ) -> None:
        self.id = node_id
        nodes = cluster["nodes"]
        self.addr = tuple(nodes[node_id])
        self.fsync = fsync
        self.heartbeat_s, self.suspect_s = heartbeat_s, suspect_s
        self.ae_interval_s, self.ae_grace_s = ae_interval_s, ae_grace_s
        self.match_slice_s = match_slice_s
        os.makedirs(data_dir, exist_ok=True)

        incarnation = bump_incarnation(data_dir)
        matcher = PersonMatcher(**cluster.get("matcher", {}))
        k = int(cluster.get("match_workers", 0))
        self.pool: Optional[WorkerPool] = (
            WorkerPool(k, cluster.get("shards", 64), cluster.get("matcher", {})) if k > 0 else None
        )
        self.node = Node(
            node_id, incarnation, list(nodes), matcher,
            n_shards=cluster.get("shards", 64),
            compute_replicas=cluster.get("compute_replicas", 1),
            matching=self.pool,
        )
        if self.pool is not None:
            self.pool.on_facts = self._worker_facts
        self.wal = Wal(data_dir)
        t0 = time.perf_counter()
        replayed = sum(1 for d in self.wal.replay() if self.node.receive(*d, now=0.0))
        self.wal.open()
        log.info("%s incarnation %d, replayed %d deltas in %.2fs",
                 node_id, incarnation, replayed, time.perf_counter() - t0)

        self.links = {p: PeerLink(self, p, tuple(a)) for p, a in nodes.items() if p != node_id}
        self.last_heard = {p: time.monotonic() for p in self.links}
        self.blocked: Set[str] = set()
        self.work = asyncio.Event()
        self.work.set()
        self._server: Optional[asyncio.base_events.Server] = None

    # -- delta plumbing ------------------------------------------------------
    def _broadcast(self, delta: Delta) -> None:
        msg = ["d", delta[0], delta[1], delta[2]]
        for link in self.links.values():
            link.send(msg)

    def _originated(self, delta: Delta, sync: bool = False) -> None:
        self.wal.append(*delta, sync=sync)  # log before anyone else can see it
        self._broadcast(delta)

    def _receive(self, origin: str, seq: int, wire: dict) -> None:
        if self.node.receive(origin, seq, wire, now=time.monotonic()):
            self.wal.append(origin, seq, wire)
            if self.node.has_pending():
                self.work.set()

    # -- inbound connections ---------------------------------------------------
    async def _on_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer: Optional[str] = None
        try:
            while True:
                msg = await read_frame(reader)
                kind = msg[0]
                if kind == "hello":
                    peer = msg[1]
                elif kind == "batch":
                    if peer is None or peer in self.blocked:
                        continue
                    self.last_heard[peer] = time.monotonic()
                    for i, m in enumerate(msg[1]):
                        self._on_peer_msg(peer, m)
                        if i % 8 == 7:
                            await asyncio.sleep(0)  # keep heartbeats flowing under load
                else:
                    reply = self._on_client(msg)
                    writer.write(pack(reply))
                    await writer.drain()
                    if kind == "shutdown":
                        asyncio.get_running_loop().call_later(0.05, self._stop)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    def _on_peer_msg(self, peer: str, m: List[Any]) -> None:
        kind = m[0]
        if kind == "d":
            self._receive(m[1], m[2], m[3])
        elif kind == "vv":
            summary, want_reply = m[1], m[2]
            link = self.links[peer]
            for origin, seq, wire in self.node.missing_for(
                summary, now=time.monotonic(), grace=self.ae_grace_s, limit=500
            ):
                link.send(["d", origin, seq, wire])
            if want_reply:
                link.send(["vv", self.node.vv_summary(), False])

    def _on_client(self, msg: List[Any]) -> List[Any]:
        kind = msg[0]
        node = self.node
        if kind == "write":
            results, delta = node.submit(msg[1], now=time.monotonic())
            if delta is not None:
                self._originated(delta, sync=self.fsync)
                if node.has_pending():
                    self.work.set()
            return ["ok", results]
        if kind == "stats":
            st = node.stats()
            # the view hash sorts every entity: only computed when asked for
            if len(msg) > 1 and msg[1].get("view_hash"):
                st["view_hash"] = node.view_hash()
            st["blocked"] = sorted(self.blocked)
            return ["ok", st]
        if kind == "entities":
            return ["ok", {e: sorted(m) for e, m in node.entities().items()}]
        if kind == "entity_of":
            return ["ok", [node.entity_of_record(r) for r in msg[1]]]
        if kind == "dump":
            return ["ok", {"state": node.state.full_wire(),
                           "entities": {e: sorted(m) for e, m in node.entities().items()}}]
        if kind == "partition":
            self.blocked = set(msg[1])
            return ["ok", sorted(self.blocked)]
        if kind == "shutdown":
            return ["ok", None]
        return ["error", f"unknown request {kind!r}"]

    # -- background loops ------------------------------------------------------
    async def _heartbeats(self) -> None:
        while True:
            for link in self.links.values():
                link.send(["hb"])
            await asyncio.sleep(self.heartbeat_s)

    async def _failure_detector(self) -> None:
        while True:
            now = time.monotonic()
            alive = {self.id} | {
                p for p, t in self.last_heard.items()
                if now - t < self.suspect_s and p not in self.blocked
            }
            if alive != self.node.alive:
                before = len(self.node.owned)
                self.node.set_alive(alive)
                log.info("%s membership -> %s (owned shards %d -> %d)",
                         self.id, sorted(alive), before, len(self.node.owned))
                self.work.set()
            await asyncio.sleep(self.heartbeat_s)

    async def _anti_entropy(self) -> None:
        while True:
            await asyncio.sleep(self.ae_interval_s * (0.5 + random.random()))
            peers = [p for p in self.node.alive if p != self.id]
            if peers:
                self.links[random.choice(peers)].send(["vv", self.node.vv_summary(), True])

    def _worker_facts(self, facts: List[Any]) -> None:
        delta = self.node.publish_facts(facts, now=time.monotonic())
        if delta is not None:
            self._originated(delta)

    async def _matcher(self) -> None:
        while True:
            if not self.node.has_pending():
                self.work.clear()
                await self.work.wait()
                continue
            delta = self.node.step_matching(budget_s=self.match_slice_s, now=time.monotonic())
            if delta is not None:
                self._originated(delta)
            await asyncio.sleep(0)

    # -- lifecycle ---------------------------------------------------------------
    def _stop(self) -> None:
        if self.pool is not None:
            self.pool.stop()
        self.wal.close()
        for task in asyncio.all_tasks():
            task.cancel()

    async def serve(self) -> None:
        self._server = await asyncio.start_server(self._on_conn, *self.addr, limit=2**20)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stop)
        log.info("%s listening on %s:%d", self.id, *self.addr)
        tasks = [asyncio.create_task(l.run()) for l in self.links.values()]
        loops = [self._heartbeats, self._failure_detector, self._anti_entropy]
        if self.pool is None:
            loops.append(self._matcher)
        else:
            tasks += await self.pool.start()
        tasks += [asyncio.create_task(c()) for c in loops]
        try:
            async with self._server:
                await asyncio.gather(self._server.serve_forever(), *tasks)
        except asyncio.CancelledError:
            pass


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--id", required=True)
    ap.add_argument("--cluster", required=True, help="JSON file: {nodes: {id: [host, port]}, shards, matcher}")
    ap.add_argument("--data", required=True, help="directory for the log and incarnation")
    ap.add_argument("--fsync", action="store_true", help="fsync each client batch before acknowledging")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(message)s")
    with open(args.cluster) as fh:
        cluster = json.load(fh)
    asyncio.run(NodeServer(args.id, cluster, args.data, fsync=args.fsync).serve())


if __name__ == "__main__":
    main()
