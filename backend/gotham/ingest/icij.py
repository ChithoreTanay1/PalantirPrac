"""ICIJ Offshore Leaks adapter.

Reads the CSV layout of `full-oldb.LATEST.zip` (https://offshoreleaks.icij.org/pages/database):
nodes-entities.csv, nodes-officers.csv, nodes-intermediaries.csv, nodes-addresses.csv and
nodes-others.csv and relationships.csv. Accepts an extracted directory, the zip itself, or a
directory holding the zip (what `cli download icij` produces).
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Iterator

from ..geo import centroid, country_code
from .common import Edge, Event, Record, norm_date, uniq

ICIJ_URL = "https://offshoreleaks.icij.org/nodes/{}"

# ICIJ `link` text -> our edge type. Anything unmatched becomes LINKED.
LINK_RULES = [
    (re.compile(r"shareholder|beneficial owner|owner|beneficiary|nominee shareholder", re.I), "OWNS"),
    (re.compile(r"director|secretary|president|officer|signatory|protector|trustee|power of attorney", re.I), "DIRECTS"),
    (re.compile(r"intermediary", re.I), "INTERMEDIARY_OF"),
    (re.compile(r"address", re.I), "REGISTERED_AT"),
]


def _open_tables(path: Path, limit: int | None):
    if path.is_dir() and not (path / "nodes-entities.csv").exists():
        zips = sorted(path.glob("*.zip"))
        if zips:
            path = zips[-1]
    if path.suffix == ".zip":
        zf = zipfile.ZipFile(path)
        names = {Path(n).name: n for n in zf.namelist()}

        def reader(name):
            if name not in names:
                return iter(())
            return csv.DictReader(io.TextIOWrapper(zf.open(names[name]), encoding="utf-8", newline=""))
    else:
        def reader(name):
            p = path / name
            if not p.exists():
                return iter(())
            return csv.DictReader(open(p, encoding="utf-8", newline=""))

    def limited(name):
        for i, row in enumerate(reader(name)):
            if limit and i >= limit:
                break
            yield row

    return limited


def _countries(row: dict) -> list[str]:
    raw = (row.get("country_codes") or "").split(";") + [row.get("jurisdiction") or ""]
    return uniq(country_code(c) for c in raw if c and c not in ("XXX", "XX"))


SKIP_RELS = ("same_name_as", "similar", "same_as", "same_company_as", "same_id_as", "same_intermediary_as")
NODE_FILES = (("nodes-entities.csv", "Company"), ("nodes-others.csv", "Company"),
              ("nodes-officers.csv", None), ("nodes-intermediaries.csv", "Company"))


def _nid(v: str):
    return int(v) if v.isdigit() else v


def focus_set(path: Path, hops: int = 2) -> set[str]:
    """Pass 1 for focused mode: seed on sanctions name matches, expand along relationships."""
    from . import focus

    seeds_ref = focus.load_seeds()
    if not seeds_ref:
        raise SystemExit("focused ICIJ ingest needs sanctions records first (ingest opensanctions) or use --full")
    table = _open_tables(path, None)
    seeds, total = set(), 0
    for fname, schema in NODE_FILES:
        for row in table(fname):
            total += 1
            names = [row.get("name"), row.get("original_name"), row.get("former_name")]
            for n in names:
                if n and (seeds_ref.match_name(n, "Company") or seeds_ref.match_name(n, "Person")):
                    seeds.add(_nid(row["node_id"]))
                    break
    print(f"  [icij] scanned {total:,} nodes, {len(seeds):,} seeds; reading relationships...", flush=True)
    adj = focus.new_adjacency()
    for row in table("relationships.csv"):
        if (row.get("rel_type") or "") in SKIP_RELS:
            continue
        a, b = _nid(row["node_id_start"]), _nid(row["node_id_end"])
        adj[a].append(b)
        adj[b].append(a)
    keep = focus.expand(adj, seeds, hops)
    del adj
    focus.report("icij", len(seeds), len(keep), total)
    return {str(k) for k in keep}


def read(path: Path, source: str = "icij", synthetic: bool = False, limit: int | None = None,
         keep: set[str] | None = None) -> Iterator:
    table = _open_tables(path, limit)
    known: set[str] = set()

    def rec_id(node_id: str) -> str:
        return f"{source}:{node_id}"

    def doc(row) -> str:
        return f"ICIJ Offshore Leaks / {row.get('sourceID') or 'unknown leak'}"

    for row in (r for f in ("nodes-entities.csv", "nodes-others.csv") for r in table(f)):
        nid = row["node_id"]
        if keep is not None and nid not in keep:
            continue
        known.add(nid)
        names = [row.get("name"), row.get("original_name"), row.get("former_name")]
        countries = _countries(row)
        idents = []
        if row.get("internal_id"):
            idents.append(f"icij-internal:{row['internal_id']}")
        props = {k: row[k] for k in ("jurisdiction_description", "company_type", "status", "service_provider",
                                     "incorporation_date", "inactivation_date", "struck_off_date", "address", "note")
                 if row.get(k)}
        yield Record(source, nid, "Company", row.get("name") or nid, names=uniq(names), countries=countries,
                     identifiers=idents, props=props, synthetic=synthetic,
                     gt_id=row.get("gt_id") or None, classification=int(row.get("classification") or 0),
                     source_url=ICIJ_URL.format(nid) if not synthetic else None, source_doc=doc(row))
        inc = norm_date(row.get("incorporation_date"))
        jur = country_code(row.get("jurisdiction")) or (countries[0] if countries else None)
        pt = centroid(jur, nid)
        if inc and pt:
            yield Event(source, rec_id(nid), "Incorporation", inc, pt[0], pt[1], "country", country=jur,
                        place=row.get("jurisdiction_description"),
                        description=f"{row.get('name')} incorporated in {row.get('jurisdiction_description') or jur}",
                        source_url=ICIJ_URL.format(nid) if not synthetic else None, source_doc=doc(row))
        struck = norm_date(row.get("struck_off_date"))
        if struck and pt:
            yield Event(source, rec_id(nid), "Struck off", struck, pt[0], pt[1], "country", country=jur,
                        description=f"{row.get('name')} struck off the register", source_doc=doc(row))

    for fname, schema in (("nodes-officers.csv", None), ("nodes-intermediaries.csv", "Company")):
        for row in table(fname):
            nid = row["node_id"]
            if keep is not None and nid not in keep:
                continue
            known.add(nid)
            name = row.get("name") or nid
            # Officers can be people or companies; ICIJ does not say. Heuristic on legal suffixes.
            kind = schema or ("Company" if re.search(
                r"\b(ltd|limited|inc|corp|s\.?a\.?|llc|gmbh|trust|holdings?|foundation|nominees?|services|bank|b\.?v\.?)\b",
                name, re.I) else "Person")
            yield Record(source, nid, kind, name, names=[name], countries=_countries(row),
                         props={k: row[k] for k in ("status", "address", "note") if row.get(k)},
                         synthetic=synthetic, gt_id=row.get("gt_id") or None,
                         birth_date=norm_date(row.get("birth_date")),
                         source_url=ICIJ_URL.format(nid) if not synthetic else None, source_doc=doc(row))

    for row in table("nodes-addresses.csv"):
        nid = row["node_id"]
        if keep is not None and nid not in keep:
            continue
        known.add(nid)
        addr = row.get("address") or row.get("name") or nid
        yield Record(source, nid, "Address", addr, names=[addr], countries=_countries(row),
                     props={"full": [addr]}, synthetic=synthetic,
                     source_url=ICIJ_URL.format(nid) if not synthetic else None, source_doc=doc(row))

    for row in table("relationships.csv"):
        a, b = row["node_id_start"], row["node_id_end"]
        if a not in known or b not in known:
            continue
        rel, link = row.get("rel_type") or "", row.get("link") or ""
        if rel in SKIP_RELS:
            continue  # ICIJ's own fuzzy-match hints; our ER makes these decisions itself
        etype = "REGISTERED_AT" if rel == "registered_address" else "LINKED"
        if rel != "registered_address":
            for pattern, t in LINK_RULES:
                if pattern.search(link) or pattern.search(rel):
                    etype = t
                    break
        share = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", row.get("share") or link)
        if m:
            share = float(m.group(1)) / 100
        yield Edge(source, rec_id(a), rec_id(b), etype, role=link or rel, share=share,
                   start_date=norm_date(row.get("start_date")), end_date=norm_date(row.get("end_date")),
                   source_doc=doc(row), native_id=f"{a}|{rel}|{link}|{b}",
                   classification=int(row.get("classification") or 0))
