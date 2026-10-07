# crdt-er: CRDT-convergent, dynamic, incremental entity resolution (prototype)

Replicas ingest and edit records and human match/non-match feedback while
offline. They sync in any order and converge on the **same entities**. Each
change re-resolves only the part of the graph it touches.

* Replicated state: **pycrdt** (Yjs). Matching: **rapidfuzz**.
  Blocking: **datasketch** MinHash LSH. Tests: **hypothesis**.
* Design, data structures, algorithms and assumptions: see [DESIGN.md](DESIGN.md).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
python examples/demo.py      # 3 replicas, concurrent edits & conflicting feedback, convergence
pytest -q                    # property tests: convergence + incremental == batch
```

```python
from crdt_er import Replica

crm, support = Replica(1), Replica(2)          # unique replica ids
crm.upsert("crm:1", {"name": "John Smith", "email": "jsmith@acme.com"})
support.upsert("sup:7", {"name": "Smith, Jon", "email": "JSmith@acme.com"})
crm.sync(support)                              # or ship crm.changes_since(sv) bytes yourself
crm.entities()   # [{'id': 'crm:1', 'members': ['crm:1', 'sup:7'], 'fields': {...}}]

support.assert_non_match("crm:1", "sup:7", author="bob")   # human feedback splits it
support.sync(crm)
```
