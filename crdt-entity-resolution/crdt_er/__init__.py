"""Prototype CRDT-convergent, dynamic & incremental entity resolution."""

from .config import DEFAULT_CONFIG, FieldRule, ResolverConfig
from .engine import IncrementalResolver, canonical_pair
from .replica import ConfigMismatch, Replica

__all__ = [
    "DEFAULT_CONFIG",
    "ConfigMismatch",
    "FieldRule",
    "IncrementalResolver",
    "Replica",
    "ResolverConfig",
    "canonical_pair",
]
