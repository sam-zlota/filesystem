"""Spawn and control a local multi-process cluster (benchmarks, demos, tests)."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from .client import ClusterClient

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def free_ports(n: int) -> List[int]:
    socks, ports = [], []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        socks.append(s)
        ports.append(s.getsockname()[1])
    for s in socks:
        s.close()
    return ports


class LocalCluster:
    def __init__(self, workdir: str, n: int, shards: int = 64, matcher: Optional[Dict[str, Any]] = None,
                 compute_replicas: int = 1, fsync: bool = False) -> None:
        self.workdir = workdir
        self.ids = [f"n{i}" for i in range(n)]
        ports = free_ports(n)
        self.addrs = {nid: ("127.0.0.1", p) for nid, p in zip(self.ids, ports)}
        self.config = {
            "nodes": {nid: list(a) for nid, a in self.addrs.items()},
            "shards": shards,
            "compute_replicas": compute_replicas,
            "matcher": matcher or {},
        }
        os.makedirs(workdir, exist_ok=True)
        self.config_path = os.path.join(workdir, "cluster.json")
        with open(self.config_path, "w") as fh:
            json.dump(self.config, fh, indent=2)
        self.fsync = fsync
        self.procs: Dict[str, subprocess.Popen] = {}

    def start(self, nid: str) -> None:
        data = os.path.join(self.workdir, nid)
        os.makedirs(data, exist_ok=True)
        cmd = [sys.executable, "-m", "crdt_er.dist.server", "--id", nid,
               "--cluster", self.config_path, "--data", data]
        if self.fsync:
            cmd.append("--fsync")
        logf = open(os.path.join(self.workdir, f"{nid}.log"), "a")
        self.procs[nid] = subprocess.Popen(cmd, cwd=PKG_ROOT, stdout=logf, stderr=subprocess.STDOUT)

    def start_all(self) -> None:
        for nid in self.ids:
            self.start(nid)

    def kill(self, nid: str, sig: int = signal.SIGKILL) -> None:
        proc = self.procs.pop(nid, None)
        if proc is not None:
            proc.send_signal(sig)
            proc.wait(timeout=10)

    def stop_all(self) -> None:
        for nid in list(self.procs):
            self.kill(nid, signal.SIGTERM)

    def client(self, prefer: Optional[str] = None, timeout: float = 5.0) -> ClusterClient:
        return ClusterClient(self.addrs, timeout=timeout, prefer=prefer)

    async def ask(self, nid: str, msg: List[Any], timeout: float = 30.0) -> Any:
        c = ClusterClient({nid: self.addrs[nid]}, timeout=timeout)
        try:
            return await c.call_node(nid, msg)
        finally:
            await c.close()

    async def wait_ready(self, nids: Optional[List[str]] = None, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        for nid in nids or list(self.procs):
            while True:
                try:
                    await self.ask(nid, ["stats"], timeout=1.0)
                    break
                except (OSError, asyncio.TimeoutError, ConnectionError):
                    if time.monotonic() > deadline:
                        raise
                    await asyncio.sleep(0.1)

    async def stats(self, nids: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        nids = nids or list(self.procs)
        res = await asyncio.gather(*(self.ask(n, ["stats"]) for n in nids))
        return dict(zip(nids, res))

    async def wait_converged(self, nids: Optional[List[str]] = None, timeout: float = 600.0,
                             poll: float = 0.2) -> Dict[str, Dict[str, Any]]:
        """Until every node has no pending matching, the same version vector
        and the same entity view, on two consecutive polls."""
        deadline = time.monotonic() + timeout
        stable = 0
        while True:
            st = await self.stats(nids)
            vals = list(st.values())
            same = all(
                v["pending"] == 0 and v["gaps"] == 0 and v["vv"] == vals[0]["vv"]
                and v["view_hash"] == vals[0]["view_hash"]
                for v in vals
            )
            stable = stable + 1 if same else 0
            if stable >= 2:
                return st
            if time.monotonic() > deadline:
                raise TimeoutError(f"cluster did not converge: {st}")
            await asyncio.sleep(poll)
