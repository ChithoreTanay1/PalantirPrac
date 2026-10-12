"""GLEIF adapter: Legal Entity Identifiers (Level 1, "who is who") and accounting-consolidation
relationships (Level 2, "who owns whom").

Golden copy CSVs from https://www.gleif.org/en/lei-data/gleif-golden-copy/download-the-golden-copy:
* `*-gleif-goldencopy-lei2-golden-copy.csv(.zip)`  one row per LEI
* `*-gleif-goldencopy-rr-golden-copy.csv(.zip)`    one row per parent relationship

A relationship row's StartNode is the child and EndNode the parent, so it becomes
parent -OWNS-> child. OpenSanctions records often carry `lei:` identifiers, which is how
GLEIF ownership chains attach to sanctioned companies during entity resolution.
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

LEI_URL = "https://search.gleif.org/#/record/{}"
DOC_L1 = "GLEIF golden copy (LEI-CDF)"
DOC_L2 = "GLEIF golden copy (relationship records)"
REL_ROLE = {
    "IS_DIRECTLY_CONSOLIDATED_BY": "direct accounting parent",
    "IS_ULTIMATELY_CONSOLIDATED_BY": "ultimate accounting parent",
    "IS_INTERNATIONAL_BRANCH_OF": "international branch of",
    "IS_FUND-MANAGED_BY": "fund managed by",
    "IS_SUBFUND_OF": "sub-fund of",
    "IS_FEEDER_TO": "feeder fund to",
}


def _files(path: Path) -> tuple[Path | None, Path | None]:
    if path.is_file():
        return (path, None) if "rr" not in path.name.lower() else (None, path)
    files = sorted(p for p in path.iterdir() if p.suffix in (".csv", ".zip"))
    lei = next((p for p in reversed(files) if "lei2" in p.name.lower()), None)
    rr = next((p for p in reversed(files) if re.search(r"(^|[-_])rr([-_.]|$)", p.name.lower())), None)
    return lei, rr


def _rows(p: Path | None) -> Iterator[dict]:
    if p is None:
        return
    if p.suffix == ".zip":
        with zipfile.ZipFile(p) as zf:
            member = next(m for m in zf.namelist() if m.lower().endswith(".csv"))
            with zf.open(member) as raw:
                yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
    else:
        with open(p, encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)


def _names(row: dict) -> list[str]:
    names = [row.get("Entity.LegalName")]
    names += [v for k, v in row.items() if k and (k.startswith("Entity.OtherEntityNames.OtherEntityName")
                                                   or k.startswith("Entity.TransliteratedOtherEntityNames.TransliteratedOtherEntityName"))
              and not k.endswith(("type", "xmllang"))]
    return uniq(names)


def _rel_start(row: dict) -> str | None:
    for k, v in row.items():
        if k and v and "period" in k.lower() and k.lower().endswith("startdate"):
            return norm_date(v)
    return None


def focus_set(path: Path, hops: int = 2) -> set[str]:
    """LEIs to keep in focused mode: sanctions LEI / name matches plus their corporate family."""
    from . import focus

    seeds_ref = focus.load_seeds()
    if not seeds_ref:
        raise SystemExit("focused GLEIF ingest needs sanctions records first (ingest opensanctions) or use --full")
    lei_file, rr_file = _files(path)
    seeds, total = set(), 0
    for row in _rows(lei_file):
        total += 1
        lei = row.get("LEI")
        if not lei:
            continue
        if seeds_ref.match_id(f"lei:{lei}") or any(seeds_ref.match_name(n, "Company") for n in _names(row)[:4]):
            seeds.add(lei)
    print(f"  [gleif] scanned {total:,} LEIs, {len(seeds):,} seeds; reading relationships...", flush=True)
    adj = focus.new_adjacency()
    for row in _rows(rr_file):
        a, b = row.get("Relationship.StartNode.NodeID"), row.get("Relationship.EndNode.NodeID")
        if a and b:
            adj[a].append(b)
            adj[b].append(a)
    keep = focus.expand(adj, seeds, hops)
    focus.report("gleif", len(seeds), len(keep), total)
    return keep


def read(path: Path, source: str = "gleif", limit: int | None = None, keep: set[str] | None = None) -> Iterator:
    lei_file, rr_file = _files(path)
    if lei_file is None:
        raise SystemExit(f"no GLEIF lei2 golden-copy file under {path}")
    known: set[str] = set()
    for i, row in enumerate(_rows(lei_file)):
        if limit and i >= limit:
            break
        lei = row.get("LEI")
        if not lei or (keep is not None and lei not in keep):
            continue
        known.add(lei)
        names = _names(row)
        cc = country_code(row.get("Entity.LegalAddress.Country"))
        jur = country_code((row.get("Entity.LegalJurisdiction") or "").split("-")[0])
        reg = re.sub(r"[^0-9A-Z]", "", (row.get("Entity.RegistrationAuthority.RegistrationAuthorityEntityID") or "").upper())
        addr = ", ".join(v for v in (row.get("Entity.LegalAddress.FirstAddressLine"), row.get("Entity.LegalAddress.City"),
                                     row.get("Entity.LegalAddress.PostalCode"), row.get("Entity.LegalAddress.Country")) if v)
        props = {k: v for k, v in {
            "legalForm": row.get("Entity.LegalForm.EntityLegalFormCode"),
            "status": row.get("Entity.EntityStatus"),
            "category": row.get("Entity.EntityCategory"),
            "registrationStatus": row.get("Registration.RegistrationStatus"),
            "jurisdiction": row.get("Entity.LegalJurisdiction"),
            "incorporationDate": row.get("Entity.EntityCreationDate"),
            "address": addr,
        }.items() if v}
        yield Record(source, lei, "Company", names[0] if names else lei, names=names,
                     countries=uniq([cc, jur]), identifiers=uniq([f"lei:{lei}", f"reg:{reg}" if len(reg) >= 4 else None]),
                     props=props, source_url=LEI_URL.format(lei), source_doc=DOC_L1)
        created = norm_date(row.get("Entity.EntityCreationDate"))
        pt = centroid(jur or cc, lei)
        if created and pt:
            yield Event(source, f"{source}:{lei}", "Incorporation", created, pt[0], pt[1], "country",
                        country=jur or cc, place=row.get("Entity.LegalAddress.City"),
                        description=f"{names[0] if names else lei} created ({row.get('Entity.LegalJurisdiction') or cc})",
                        source_url=LEI_URL.format(lei), source_doc=DOC_L1)

    for row in _rows(rr_file):
        child, parent = row.get("Relationship.StartNode.NodeID"), row.get("Relationship.EndNode.NodeID")
        if child not in known or parent not in known:
            continue
        rtype = row.get("Relationship.RelationshipType") or ""
        etype = "OWNS" if "CONSOLIDATED" in rtype else "LINKED"
        status = row.get("Relationship.RelationshipStatus")
        yield Edge(source, f"{source}:{parent}", f"{source}:{child}", etype, role=REL_ROLE.get(rtype, rtype.lower()),
                   start_date=_rel_start(row), props={"status": status} if status else {},
                   source_url=LEI_URL.format(child), source_doc=DOC_L2, native_id=f"{child}|{rtype}|{parent}")
