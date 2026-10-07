"""Distributed mode: the CRDT entity resolver as a leaderless multi-process cluster.

* :mod:`.state`  - replicated state (ERState + version-stamped match facts)
* :mod:`.node`   - transport-agnostic node logic (sharding, matching, view)
* :mod:`.sim`    - deterministic in-process cluster simulator
* :mod:`.server` - asyncio node process (``python -m crdt_er.dist.server``)
* :mod:`.client` - fail-over client
* :mod:`.wal`    - write-ahead log and incarnation file
* :mod:`.launch` - spawn local clusters for demos, benchmarks and tests
"""
from .node import Node
from .sim import SimCluster
from .state import DistState

__all__ = ["Node", "SimCluster", "DistState"]
