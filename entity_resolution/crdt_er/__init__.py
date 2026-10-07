"""crdt_er -- a prototype dynamic, incremental, CRDT-based entity resolution engine."""
from .engine import Replica, resolve_from_scratch
from .lattice import LWWMap, ORSet
from .matching import PersonMatcher, jaro_winkler, name_similarity
from .simulate import Network
from .state import LINK, UNLINK, ERState

__all__ = [
    "Replica",
    "resolve_from_scratch",
    "LWWMap",
    "ORSet",
    "PersonMatcher",
    "jaro_winkler",
    "name_similarity",
    "Network",
    "ERState",
    "LINK",
    "UNLINK",
]
