"""Focused ingest for the bulk sources (ICIJ, Companies House, GLEIF).

The full dumps are millions of nodes, almost none of which touch the sanctions graph. In
focused mode an adapter first scans its dump for *seeds* (nodes whose name or identifier
matches a record already ingested from a real sanctions list) and then keeps everything
within `hops` relationships of a seed. That is what an investigator would pull anyway, and
it keeps entity resolution (which is in-memory) tractable. `--full` skips this.

Hubs (registered agents, mass-registration addresses with thousands of links) are kept as
nodes but never expanded through, otherwise two hops from anything reaches half the dump.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Hashable, Iterable

from ..db import get_engine, loads
from ..resolve import tokens

MAX_EXPAND_DEGREE = 400


@dataclass
class Seeds:
    name_keys: set[str] = field(default_factory=set)
    identifiers: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.name_keys or self.identifiers)

    def match_name(self, name: str | None, schema: str = "Company") -> bool:
        k = name_key(name, schema)
        return bool(k) and k in self.name_keys

    def match_id(self, ident: str | None) -> bool:
        return bool(ident) and ident in self.identifiers


def name_key(name: str | None, schema: str = "Company") -> str | None:
    """Order-insensitive normalised token key ("SMITH, John" == "John Smith")."""
    if not name:
        return None
    toks = sorted(set(tokens(name, schema)))
    if not toks or (schema == "Person" and len(toks) < 2):
        return None
    key = " ".join(toks)
    return key if len(key) >= 5 else None


def load_seeds() -> Seeds:
    """Names and identifiers of everything ingested from real OpenSanctions lists."""
    seeds = Seeds()
    with get_engine().connect() as conn:
        res = conn.exec_driver_sql("SELECT schema, names, identifiers FROM records WHERE source LIKE 'os%'")
        for schema, names, idents in res:
            if schema == "Address":
                continue
            for n in loads(names, [])[:12]:
                k = name_key(n, schema)
                if k:
                    seeds.name_keys.add(k)
            seeds.identifiers.update(loads(idents, []))
    return seeds


def expand(adjacency: dict[Hashable, list[Hashable]], seeds: Iterable[Hashable], hops: int) -> set:
    """Breadth-first expansion from seeds, not expanding through hub nodes."""
    keep = set(seeds)
    frontier = deque((s, 0) for s in keep)
    while frontier:
        node, d = frontier.popleft()
        if d >= hops:
            continue
        nbrs = adjacency.get(node, ())
        if len(nbrs) > MAX_EXPAND_DEGREE and d > 0:
            continue  # hub reached via a hop: keep it, don't fan out through it
        for n in nbrs:
            if n not in keep:
                keep.add(n)
                frontier.append((n, d + 1))
    return keep


def new_adjacency() -> dict:
    return defaultdict(list)


def report(source: str, seeds: int, kept: int, total: int | None = None) -> None:
    tail = f" of {total:,}" if total else ""
    print(f"  [{source}] focus: {seeds:,} seed nodes matched sanctions records -> keeping {kept:,}{tail} nodes", flush=True)
