"""OpenSanctions adapter (FollowTheMoney JSON lines, e.g. `us_ofac_sdn/entities.ftm.json`).

OpenSanctions has already de-duplicated entities across lists and gives them canonical
`NK-...` ids. We deliberately ingest under each list's *native* id (`ofac-12345`,
`eu-fsf-...`) so that our own entity resolution has real work to do, and keep the canonical
id in `gt_id` as ground truth for measuring ER precision/recall.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from ..geo import centroid, country_code
from .common import Edge, Event, Record, norm_date, uniq

SCHEMA_MAP = {
    "Person": "Person",
    "Organization": "Company",
    "Company": "Company",
    "LegalEntity": "Company",
    "PublicBody": "Company",
    "Vessel": "Vessel",
    "Airplane": "Aircraft",
    "Address": "Address",
}

# FtM interval schema -> (edge type, source prop, target prop)
LINK_MAP = {
    "Ownership": ("OWNS", "owner", "asset"),
    "Directorship": ("DIRECTS", "director", "organization"),
    "Representation": ("REPRESENTS", "agent", "client"),
    "Family": ("FAMILY", "person", "relative"),
    "Associate": ("ASSOCIATE", "person", "associate"),
    "Membership": ("MEMBER_OF", "member", "organization"),
    "UnknownLink": ("LINKED", "subject", "object"),
    "Employment": ("LINKED", "employee", "employer"),
}

ID_PROPS = {
    "imoNumber": "imo", "registrationNumber": "reg", "leiCode": "lei", "swiftBic": "bic",
    "taxNumber": "tax", "passportNumber": "passport", "idNumber": "id", "mmsi": "mmsi",
    "ogrnCode": "ogrn", "innCode": "inn", "dunsCode": "duns", "vatCode": "vat",
}
COUNTRY_PROPS = ("country", "nationality", "citizenship", "flag", "jurisdiction")
KEEP_PROPS = (
    "alias", "weakAlias", "previousName", "birthPlace", "position", "gender", "legalForm",
    "incorporationDate", "sector", "website", "email", "type", "buildDate", "callSign",
    "tonnage", "grossRegisteredTonnage", "model", "serialNumber", "notes", "topics",
    "full", "city", "street", "postalCode", "region", "pastFlags", "programId",
)


def norm_identifier(kind: str, value: str) -> str | None:
    v = re.sub(r"[^0-9A-Za-z]", "", value or "").upper()
    if len(v) < 4:
        return None
    if kind == "imo":
        v = v.removeprefix("IMO")
    return f"{kind}:{v}"


def _native_id(ent: dict, prefixes: tuple[str, ...]) -> str:
    if not ent["id"].startswith("NK-"):
        return ent["id"]
    refs = [r for r in ent.get("referents", []) if r.startswith(prefixes)]
    if refs:
        # prefer the list's own numeric id ("ofac-52743") over derived ones ("ofac-pr-...")
        refs.sort(key=lambda r: (not re.fullmatch(r"[a-z-]+-\d+", r), len(r), r))
        return refs[0]
    return ent["id"]


def read(path: Path, dataset: str | None = None, synthetic: bool = False, classification: int = 0) -> Iterator:
    dataset = dataset or path.name.split(".")[0]
    source = f"os_{dataset}" if not synthetic else dataset
    prefixes = {
        "us_ofac_sdn": ("ofac-",),
        "eu_fsf": ("eu-fsf-",),
        "gb_hmt_sanctions": ("gb-hmt-",),
        "un_sc_sanctions": ("unsc-",),
    }.get(dataset, (dataset.replace("_", "-") + "-",))

    entities: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ent = json.loads(line)
                entities[ent["id"]] = ent

    native = {eid: _native_id(e, prefixes) for eid, e in entities.items()}

    def rid(eid: str) -> str:
        return f"{source}:{native.get(eid, eid)}"

    # Pass 1: fold Identification/Passport/Sanction objects into their holders.
    extra_ids: dict[str, list[str]] = {}
    sanctions: dict[str, list[dict]] = {}
    for ent in entities.values():
        p = ent.get("properties", {})
        if ent["schema"] in ("Identification", "Passport"):
            for holder in p.get("holder", []):
                for num in p.get("number", []):
                    kind = "passport" if ent["schema"] == "Passport" else (p.get("type") or ["id"])[0].lower()[:12]
                    ident = norm_identifier(re.sub(r"\W", "", kind) or "id", num)
                    if ident:
                        extra_ids.setdefault(holder, []).append(ident)
        elif ent["schema"] == "Sanction":
            for target in p.get("entity", []):
                sanctions.setdefault(target, []).append({
                    "program": (p.get("program") or p.get("programId") or [None])[0],
                    "authority": (p.get("authority") or [None])[0],
                    "reason": (p.get("reason") or [None])[0],
                    "start_date": norm_date((p.get("startDate") or p.get("listingDate") or [None])[0]),
                    "source_url": (p.get("sourceUrl") or p.get("programUrl") or [None])[0],
                })

    # Pass 2: entities, links and events.
    for ent in entities.values():
        p = ent.get("properties", {})
        schema = SCHEMA_MAP.get(ent["schema"])
        url = (p.get("sourceUrl") or [None])[0]
        if schema:
            countries = uniq(country_code(c) for prop in COUNTRY_PROPS for c in p.get(prop, []))
            idents = [norm_identifier(ID_PROPS[k], v) for k in ID_PROPS for v in p.get(k, [])]
            if schema == "Aircraft":
                idents += [norm_identifier("acsn", f"{m}{s}") for m in p.get("model", []) for s in p.get("serialNumber", [])]
            idents += extra_ids.get(ent["id"], [])
            names = p.get("name", []) + p.get("alias", []) + p.get("previousName", [])
            if schema == "Address":
                names = p.get("full", []) or [ent.get("caption", "")]
            props = {k: p[k] for k in KEEP_PROPS if k in p}
            if ent["id"] in sanctions:
                props["sanctions"] = sanctions[ent["id"]]
            yield Record(
                source=source,
                source_id=native[ent["id"]],
                schema=schema,
                caption=ent.get("caption") or (names[0] if names else ent["id"]),
                names=names,
                countries=countries,
                identifiers=uniq(i for i in idents if i),
                birth_date=norm_date((p.get("birthDate") or [None])[0]),
                props=props,
                classification=classification,
                synthetic=synthetic,
                gt_id=ent.get("gt") or ent["id"],
                source_url=url,
                source_doc=f"{dataset}/entities.ftm.json",
            )
            for addr in p.get("addressEntity", []):
                if addr in entities:
                    yield Edge(source, rid(ent["id"]), rid(addr), "REGISTERED_AT", role="address",
                               source_url=url, source_doc=f"{dataset}/entities.ftm.json", classification=classification)
            cc = countries[0] if countries else None
            for inc in p.get("incorporationDate", [])[:1]:
                d = norm_date(inc)
                pt = centroid(cc, ent["id"] + "inc")
                if d and pt:
                    yield Event(source, rid(ent["id"]), "Incorporation", d, pt[0], pt[1], "country", country=cc,
                                description=f"{ent.get('caption')} incorporated", source_url=url,
                                source_doc=f"{dataset}/entities.ftm.json", classification=classification)
            for s in sanctions.get(ent["id"], []):
                pt = centroid(cc, ent["id"] + "san")
                if s["start_date"] and pt:
                    yield Event(source, rid(ent["id"]), "Sanction designation", s["start_date"], pt[0], pt[1],
                                "country", country=cc,
                                description=f"Designated under {s['program']} by {s['authority']}",
                                source_url=s["source_url"] or url, source_doc=f"{dataset}/entities.ftm.json",
                                classification=classification)
        elif ent["schema"] in LINK_MAP:
            etype, a_prop, b_prop = LINK_MAP[ent["schema"]]
            role = (p.get("role") or p.get("relationship") or [None])[0]
            share = None
            for pct in p.get("percentage", []):
                try:
                    share = float(str(pct).strip("% ")) / 100
                except ValueError:
                    pass
            for a in p.get(a_prop, []):
                for b in p.get(b_prop, []):
                    if a in entities and b in entities:
                        yield Edge(
                            source, rid(a), rid(b), etype, role=role, share=share,
                            start_date=norm_date((p.get("startDate") or [None])[0]),
                            end_date=norm_date((p.get("endDate") or [None])[0]),
                            source_url=url, source_doc=f"{dataset}/entities.ftm.json",
                            native_id=f"{native[ent['id']]}|{a}|{b}", classification=classification,
                        )
