"""Length-prefixed msgpack framing, shared by the network and the log."""
from __future__ import annotations

import asyncio
import struct
from typing import Any

import msgpack

_HDR = struct.Struct(">I")
MAX_FRAME = 256 * 1024 * 1024


def pack(obj: Any) -> bytes:
    body = msgpack.packb(obj, use_bin_type=True)
    return _HDR.pack(len(body)) + body


def unpack(body: bytes) -> Any:
    return msgpack.unpackb(body, raw=False, strict_map_key=False)


async def read_frame(reader: asyncio.StreamReader) -> Any:
    (n,) = _HDR.unpack(await reader.readexactly(_HDR.size))
    if n > MAX_FRAME:
        raise ValueError(f"frame of {n} bytes exceeds limit")
    return unpack(await reader.readexactly(n))
