"""Async client with fail-over across nodes.

Any node accepts any write, so a client simply tries the next node when one
does not answer.  Retrying a batch on another node is safe when the client
supplies record ids: a re-sent add becomes one more LWW version of the same
record with the same attributes, and a re-sent link adds a second tag to an
assertion that is already present.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .wire import pack, read_frame


class ClusterClient:
    def __init__(self, addrs: Dict[str, Tuple[str, int]], timeout: float = 5.0, prefer: Optional[str] = None):
        self.addrs = dict(addrs)
        self.order = list(self.addrs)
        if prefer in self.order:
            self.order.remove(prefer)
            self.order.insert(0, prefer)
        self.timeout = timeout
        self.conns: Dict[str, Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = {}
        self.failovers = 0

    async def _conn(self, nid: str):
        if nid not in self.conns:
            self.conns[nid] = await asyncio.wait_for(asyncio.open_connection(*self.addrs[nid]), self.timeout)
        return self.conns[nid]

    def _drop(self, nid: str) -> None:
        conn = self.conns.pop(nid, None)
        if conn:
            conn[1].close()

    async def call_node(self, nid: str, msg: List[Any]) -> Any:
        reader, writer = await self._conn(nid)
        try:
            writer.write(pack(msg))
            await writer.drain()
            reply = await asyncio.wait_for(read_frame(reader), self.timeout)
        except BaseException:
            self._drop(nid)
            raise
        if reply[0] != "ok":
            raise RuntimeError(reply[1])
        return reply[1]

    async def call(self, msg: List[Any]) -> Tuple[str, Any]:
        """Send to the first node that answers.  Returns (node, result)."""
        last: Optional[BaseException] = None
        for attempt in range(2 * len(self.order)):
            nid = self.order[0]
            try:
                return nid, await self.call_node(nid, msg)
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError) as exc:
                last = exc
                self.failovers += 1
                self.order.append(self.order.pop(0))
                await asyncio.sleep(0.05 * attempt)
        raise ConnectionError(f"no node answered: {last!r}")

    async def write(self, ops: Sequence[Sequence[Any]]) -> List[Any]:
        return (await self.call(["write", list(ops)]))[1]

    async def close(self) -> None:
        for nid in list(self.conns):
            self._drop(nid)
