"""UK Companies House adapter.

Reads the free bulk products:
* PSC snapshot (`persons-with-significant-control-snapshot-*.txt`, JSON lines)
  https://download.companieshouse.gov.uk/en_pscdata.html
* Basic company data (`BasicCompanyDataAsOneFile-*.csv`, optional, gives names/addresses)
  https://download.companieshouse.gov.uk/en_output.html

Accepts a directory containing either/both (plain or as the downloaded .zip), or a single PSC
file. Focused mode (see focus.py) keeps only companies within N ownership hops of a sanctions
name / registration-number match; it re-reads the PSC snapshot once per hop instead of holding
the ~10M-statement ownership graph in memory.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from ..geo import centroid, country_code
from .common import Edge, Event, Record, norm_date, uniq

CH_URL = "https://find-and-update.company-information.service.gov.uk/company/{}"
TITLES = re.compile(r"^(mr|mrs|ms|miss|dr|sir|dame|lord|lady|prof|professor|rev)\.?\s+", re.I)
SHARE_BANDS = {
    "25-to-50": 0.375, "50-to-75": 0.625, "75-to-100": 0.875,
    "more-than-25": 0.375, "more-than-50": 0.625, "more-than-75": 0.875,
}
UK = {"GB", None}
UK_REG = re.compile(r"[A-Z]{0,2}\d{6,8}")


def _share(natures: list[str]) -> float | None:
    for n in natures:
        if n.startswith("ownership-of-shares"):
            for band, v in SHARE_BANDS.items():
                if band in n:
                    return v
    return None


def _is_psc(p: Path) -> bool:
    n = p.name.lower()
    return ("psc" in n or "significant-control" in n) and p.suffix in (".txt", ".jsonl", ".json", ".zip")


def _is_basic(p: Path) -> bool:
    return p.suffix == ".csv" or (p.suffix == ".zip" and "basiccompanydata" in p.name.lower())


def _files(path: Path) -> tuple[list[Path], list[Path]]:
    if path.is_file():
        return ([], [path]) if _is_basic(path) else ([path], [])
    psc = sorted(p for p in path.iterdir() if _is_psc(p))
    basic = sorted(p for p in path.iterdir() if _is_basic(p))
    return psc, basic


def _open_text(p: Path, encoding: str = "utf-8") -> Iterator[str]:
    """Lines of a plain file, or of every member of a zip in name order."""
    if p.suffix != ".zip":
        with open(p, encoding=encoding, newline="") as f:
            yield from f
        return
    with zipfile.ZipFile(p) as zf:
        for member in sorted(m for m in zf.namelist() if not m.endswith("/")):
            with zf.open(member) as raw:
                yield from io.TextIOWrapper(raw, encoding=encoding, newline="")


def _basic_rows(files: list[Path]) -> Iterator[dict]:
    for bf in files:
        for raw in csv.DictReader(_open_text(bf, "utf-8-sig")):
            yield {k.strip(): (v or "").strip() for k, v in raw.items() if k}


def _psc_rows(files: list[Path]) -> Iterator[tuple[str, dict, str]]:
    """(company_number, data, raw line) for every PSC statement."""
    for pf in files:
        for line in _open_text(pf):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            number, d = obj.get("company_number"), obj.get("data", {})
            if number and (d.get("kind") or "").endswith("-with-significant-control"):
                yield number.upper(), d, line


def _psc_key(d: dict, line: str) -> str:
    return (d.get("links", {}).get("self") or "").rstrip("/").split("/")[-1] or hashlib.sha1(line.encode()).hexdigest()[:16]


def _uk_owner_reg(d: dict) -> str | None:
    """Registration number when a corporate PSC is itself a UK company."""
    if d.get("kind", "").startswith("individual"):
        return None
    ident = d.get("identification") or {}
    reg = (ident.get("registration_number") or "").strip().upper()
    cc = country_code(ident.get("country_registered") or ident.get("place_registered"))
    return reg if reg and cc in UK and UK_REG.fullmatch(reg) else None


def _owner_node(d: dict, line: str) -> str:
    reg = _uk_owner_reg(d)
    return f"co:{reg}" if reg else f"psc:{_psc_key(d, line)}"


def focus_set(path: Path, hops: int = 2) -> set[str]:
    """Company numbers to keep in focused mode."""
    from . import focus

    seeds_ref = focus.load_seeds()
    if not seeds_ref:
        raise SystemExit("focused Companies House ingest needs sanctions records first (ingest opensanctions) or use --full")
    psc_files, basic_files = _files(path)
    nodes: set[str] = set()
    n_basic = 0
    for row in _basic_rows(basic_files):
        n_basic += 1
        number = (row.get("CompanyNumber") or "").upper()
        if not number:
            continue
        reg = "reg:" + re.sub(r"[^0-9A-Z]", "", number)
        if seeds_ref.match_id(reg) or seeds_ref.match_name(row.get("CompanyName"), "Company"):
            nodes.add(f"co:{number}")
    print(f"  [ch] scanned {n_basic:,} companies, {len(nodes):,} company seeds; scanning PSC owners...", flush=True)
    n_psc = 0
    for number, d, line in _psc_rows(psc_files):
        n_psc += 1
        person = d.get("kind", "").startswith("individual")
        name = TITLES.sub("", d.get("name", "")).strip() if person else d.get("name")
        if seeds_ref.match_name(name, "Person" if person else "Company"):
            nodes.add(_owner_node(d, line))
            nodes.add(f"co:{number}")
    seeds = set(nodes)
    print(f"  [ch] {n_psc:,} PSC statements, {len(seeds):,} seeds total", flush=True)
    for hop in range(hops):
        found: dict[str, set[str]] = defaultdict(set)  # kept node -> newly reached neighbours
        for number, d, line in _psc_rows(psc_files):
            comp, owner = f"co:{number}", _owner_node(d, line)
            if comp in nodes and owner not in nodes:
                found[comp].add(owner)
            elif owner in nodes and comp not in nodes:
                found[owner].add(comp)
        added = 0
        for src, nbrs in found.items():
            if len(nbrs) > focus.MAX_EXPAND_DEGREE and src not in seeds:
                continue  # nominee / formation-agent hub: don't fan out through it
            nodes |= nbrs
            added += len(nbrs)
        print(f"  [ch] hop {hop + 1}: +{added:,} nodes", flush=True)
        if not added:
            break
    keep = {n[3:] for n in nodes if n.startswith("co:")}
    focus.report("ch", len(seeds), len(keep), n_basic or None)
    return keep


def read(path: Path, source: str = "ch", synthetic: bool = False, limit: int | None = None,
         keep: set[str] | None = None) -> Iterator:
    psc_files, basic_files = _files(path)
    companies: set[str] = set()
    doc_basic = "Companies House basic company data"
    doc_psc = "Companies House PSC snapshot"

    def company_id(number: str) -> str:
        return f"{source}:co:{number.upper()}"

    def stub(number: str, name: str | None = None):
        companies.add(number.upper())
        return Record(source, f"co:{number.upper()}", "Company", name or f"UK company {number}",
                      names=[name] if name else [], countries=["GB"], identifiers=[f"reg:{number.upper()}"],
                      synthetic=synthetic, source_url=None if synthetic else CH_URL.format(number), source_doc=doc_basic)

    for i, row in enumerate(_basic_rows(basic_files)):
        if limit and i >= limit:
            break
        number = row.get("CompanyNumber")
        if not number or (keep is not None and number.upper() not in keep):
            continue
        rec = stub(number, row.get("CompanyName"))
        rec.props = {k: row[k] for k in ("CompanyCategory", "CompanyStatus", "IncorporationDate",
                                         "DissolutionDate", "SICCode.SicText_1") if row.get(k)}
        rec.gt_id = row.get("gt_id") or None
        prev = [row[k] for k in row if k.startswith("PreviousName") and k.endswith("CompanyName") and row[k]]
        rec.names += prev
        yield rec
        addr = ", ".join(row[k] for k in ("RegAddress.AddressLine1", "RegAddress.AddressLine2",
                                          "RegAddress.PostTown", "RegAddress.PostCode") if row.get(k))
        if addr:
            aid = hashlib.sha1(addr.upper().encode()).hexdigest()[:16]
            yield Record(source, f"addr:{aid}", "Address", addr, names=[addr], countries=["GB"],
                         props={"full": [addr]}, synthetic=synthetic, source_doc=doc_basic)
            yield Edge(source, company_id(number), f"{source}:addr:{aid}", "REGISTERED_AT",
                       role="registered office", source_doc=doc_basic)
        inc = norm_date(row.get("IncorporationDate"))
        pt = centroid("GB", number)
        if inc and pt:
            yield Event(source, company_id(number), "Incorporation", inc, pt[0], pt[1], "country",
                        country="GB", place=row.get("RegAddress.PostTown"),
                        description=f"{row.get('CompanyName')} incorporated at Companies House",
                        source_url=None if synthetic else CH_URL.format(number), source_doc=doc_basic)

    n = 0
    for number, d, line in _psc_rows(psc_files):
        if limit and n >= limit:
            return
        if keep is not None and number not in keep:
            continue
        n += 1
        kind = d.get("kind", "")
        if number not in companies:
            yield stub(number)
        psc_key = _psc_key(d, line)
        natures = d.get("natures_of_control", [])
        url = None if synthetic else CH_URL.format(number) + "/persons-with-significant-control"
        ident = d.get("identification") or {}
        if kind.startswith("individual"):
            name = TITLES.sub("", d.get("name", "")).strip()
            dob = d.get("date_of_birth") or {}
            birth = f"{dob['year']:04d}-{dob['month']:02d}" if dob.get("year") and dob.get("month") else None
            owner = Record(source, f"psc:{psc_key}", "Person", name, names=[name],
                           countries=uniq([country_code(d.get("nationality")), country_code(d.get("country_of_residence"))]),
                           birth_date=birth, props={"nationality": d.get("nationality"),
                                                     "country_of_residence": d.get("country_of_residence")},
                           synthetic=synthetic, gt_id=d.get("gt_id"), source_url=url, source_doc=doc_psc)
            yield owner
            owner_id = owner.id
        else:
            reg = _uk_owner_reg(d)
            if reg:
                # Owned by another UK company: chain straight to that company record.
                if reg not in companies:
                    yield stub(reg, d.get("name"))
                owner_id = company_id(reg)
            else:
                raw_reg = (ident.get("registration_number") or "").strip()
                cc = country_code(ident.get("country_registered") or ident.get("place_registered"))
                owner = Record(source, f"corp:{psc_key}", "Company", d.get("name", psc_key), names=[d.get("name")],
                               countries=uniq([cc]),
                               identifiers=[f"reg:{re.sub(r'[^0-9A-Z]', '', raw_reg.upper())}"] if len(raw_reg) >= 4 else [],
                               props={k: v for k, v in ident.items() if v}, synthetic=synthetic,
                               gt_id=d.get("gt_id"), source_url=url, source_doc=doc_psc)
                yield owner
                owner_id = owner.id
        yield Edge(source, owner_id, company_id(number), "OWNS", role="; ".join(natures) or kind,
                   share=_share(natures), start_date=norm_date(d.get("notified_on")),
                   end_date=norm_date(d.get("ceased_on")), source_url=url, source_doc=doc_psc,
                   native_id=f"{number}|{psc_key}")
