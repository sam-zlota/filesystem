"""Append-only write-ahead log of accepted deltas, plus the incarnation file.

Every delta a node accepts (originated or received) is appended before it is
acknowledged or forwarded.  Restart = replay the log into a fresh Node.  A
torn final frame (crash mid-write) is detected and truncated.

``sync=True`` fsyncs; the server does that for client writes when started
with ``--fsync`` (group commit: one fsync per client batch).  Deltas received
from peers are never fsynced: if they are lost, anti-entropy re-fetches them.
"""
from __future__ import annotations

import os
import struct
from typing import Iterator, Tuple

from .wire import pack, unpack

_HDR = struct.Struct(">I")


def bump_incarnation(data_dir: str) -> int:
    path = os.path.join(data_dir, "incarnation")
    current = 0
    if os.path.exists(path):
        with open(path) as fh:
            current = int(fh.read().strip() or 0)
    new = current + 1
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(str(new))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(data_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return new


class Wal:
    def __init__(self, data_dir: str) -> None:
        self.path = os.path.join(data_dir, "deltas.log")
        self._fh = None

    def replay(self) -> Iterator[Tuple[str, int, dict]]:
        good = 0
        if os.path.exists(self.path):
            with open(self.path, "rb") as fh:
                data = fh.read()
            pos = 0
            while pos + _HDR.size <= len(data):
                (n,) = _HDR.unpack_from(data, pos)
                end = pos + _HDR.size + n
                if end > len(data):
                    break
                try:
                    origin, seq, wire = unpack(data[pos + _HDR.size:end])
                except Exception:
                    break
                yield origin, seq, wire
                pos = good = end
            if good < len(data):
                with open(self.path, "r+b") as fh:
                    fh.truncate(good)

    def open(self) -> None:
        self._fh = open(self.path, "ab")

    def append(self, origin: str, seq: int, wire: dict, sync: bool = False) -> None:
        assert self._fh is not None
        self._fh.write(pack([origin, seq, wire]))
        self._fh.flush()
        if sync:
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
