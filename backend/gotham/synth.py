"""Synthetic data generator with planted patterns and ground truth.

Writes fictional data in the *real* source formats (ICIJ CSVs, Companies House PSC JSONL +
basic company CSV, FollowTheMoney JSON) so the production adapters are exercised end to end.
Every name is generated; every record is flagged `synthetic`.

Planted patterns (ids written to ground_truth.json):
  P1 duplicate identity   - one sanctioned person spelled differently across all 3 sources
  P2 hidden ownership     - sanctioned person -> BVI shell -> Panama shell -> UK company -> UK company
  P3 shell-company ring   - circular ownership among three offshore shells sharing an address
  P4 synchronised vessels - two vessels with port calls at the same places on the same days
  P5 classified material  - RESTRICTED / SECRET events and links for permission tests
Plus ~N background identities with fuzzy duplicates across sources for ER precision/recall.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from .db import DATA

OUT = DATA / "synthetic"

FIRST = ["Viktor", "Arkady", "Lev", "Oleg", "Dmitri", "Stefan", "Marek", "Tomas", "Yusuf", "Karim", "Omar",
         "Rafael", "Teodor", "Ivo", "Milan", "Andrei", "Nikolai", "Pavel", "Samir", "Hassan", "Elena", "Irina",
         "Mira", "Lana", "Sofia", "Nadia", "Katya", "Leyla", "Amira", "Daria", "Zora", "Vera", "Ilse", "Greta",
         "Anton", "Boris", "Gleb", "Rustam", "Timur", "Farid", "Jonas", "Emil", "Lukas", "Aurel", "Cosmin"]
SYL_A = ["Vor", "Kal", "Mor", "Dras", "Ost", "Bel", "Tar", "Zan", "Kov", "Rad", "Sel", "Var", "Mel", "Grob",
         "Lisk", "Pet", "Dur", "Fen", "Hal", "Kest", "Brav", "Nov", "Sor", "Tem"]
SYL_B = ["enko", "ovich", "anov", "ari", "elli", "escu", "idis", "ovski", "aran", "ek", "ulin", "ashev",
         "ova", "inski", "oglu", "avin", "etti", "uk", "ich", "avec"]
CO_A = ["Kestrel", "Meridian", "Larch", "Argent", "Halcyon", "Corvid", "Basalt", "Saltmarsh", "Vantor",
        "Ostrel", "Pelion", "Quillon", "Ravel", "Sable", "Tiberon", "Umber", "Verdant", "Wexley", "Yarrow",
        "Zephyr", "Brindle", "Cinder", "Dunmore", "Elmstead", "Fairhold", "Glenrock", "Harrow", "Ironbridge"]
CO_B = ["Bay", "Ridge", "Point", "Crest", "Harbour", "Gate", "Field", "Stone", "Vale", "Water", "Lane", "Cross"]
CO_C = ["Holdings", "Trading", "Investments", "Capital", "Logistics", "Shipping", "Resources", "Ventures",
        "Maritime", "Commodities", "Energy", "Consulting"]
OFFSHORE = [("VGB", "British Virgin Islands", "Ltd"), ("PAN", "Panama", "S.A."), ("SYC", "Seychelles", "Limited"),
            ("BLZ", "Belize", "Ltd"), ("CYP", "Cyprus", "Ltd"), ("ARE", "United Arab Emirates", "FZE"),
            ("MHL", "Marshall Islands", "Inc."), ("BHS", "Bahamas", "Ltd")]
NATIONS = ["Cypriot", "Russian", "Turkish", "Emirati", "Maltese", "Latvian", "Georgian", "British", "Serbian",
           "Ukrainian", "Greek", "Swiss", "Azerbaijani", "Kazakh"]
NATION_CC = {"Cypriot": "CY", "Russian": "RU", "Turkish": "TR", "Emirati": "AE", "Maltese": "MT", "Latvian": "LV",
             "Georgian": "GE", "British": "GB", "Serbian": "RS", "Ukrainian": "UA", "Greek": "GR", "Swiss": "CH",
             "Azerbaijani": "AZ", "Kazakh": "KZ"}
CITIES = {
    "Limassol": (34.684, 33.037, "CY"), "Dubai": (25.204, 55.270, "AE"), "London": (51.507, -0.128, "GB"),
    "Panama City": (8.983, -79.517, "PA"), "Road Town": (18.428, -64.618, "VG"), "Geneva": (46.204, 6.143, "CH"),
    "Istanbul": (41.008, 28.978, "TR"), "Hong Kong": (22.319, 114.169, "HK"), "Singapore": (1.352, 103.820, "SG"),
    "Riga": (56.950, 24.105, "LV"), "Monaco": (43.738, 7.425, "MC"), "Vienna": (48.208, 16.374, "AT"),
    "Valletta": (35.899, 14.514, "MT"), "Tbilisi": (41.716, 44.783, "GE"), "Baku": (40.409, 49.867, "AZ"),
    "Moscow": (55.756, 37.617, "RU"), "Belgrade": (44.787, 20.457, "RS"), "Zurich": (47.377, 8.541, "CH"),
}
PORTS = {
    "Laconian Gulf STS area": (36.55, 22.75, "GR"), "Kalamata anchorage": (36.98, 22.11, "GR"),
    "Ceuta anchorage": (35.90, -5.30, "ES"), "Fujairah anchorage": (25.15, 56.45, "AE"),
    "Port Said": (31.26, 32.30, "EG"), "Novorossiysk": (44.72, 37.78, "RU"), "Piraeus": (37.94, 23.64, "GR"),
    "Istanbul Strait": (41.12, 29.07, "TR"), "Sohar": (24.37, 56.75, "OM"), "Lome anchorage": (6.10, 1.28, "TG"),
    "Malta offshore": (35.80, 14.70, "MT"), "Mersin": (36.79, 34.63, "TR"), "Primorsk": (60.35, 28.67, "RU"),
}


def _person_name(rng: random.Random) -> tuple[str, str, str]:
    first = rng.choice(FIRST)
    patronymic = rng.choice(FIRST) + rng.choice(["ovich", "evich", "ovna", "ich"])
    last = rng.choice(SYL_A) + rng.choice(SYL_B)
    return first, patronymic, last


def _company_name(rng: random.Random, suffix: str) -> str:
    return f"{rng.choice(CO_A)} {rng.choice(CO_B)} {rng.choice(CO_C)} {suffix}"


# --- fuzzing: the realistic ways one name gets written differently across sources ---
TRANSLIT = [("v", "w"), ("y", "i"), ("kh", "h"), ("ov", "off"), ("ks", "x"), ("iy", "y"), ("ei", "ey"),
            ("ch", "tch"), ("yu", "iu"), ("e", "ye")]


def fuzz(name: str, rng: random.Random) -> str:
    ops = rng.sample(["translit", "typo", "case", "reorder", "initial", "drop_middle"], k=rng.randint(1, 2))
    out = name
    for op in ops:
        tokens = out.split()
        if op == "translit":
            a, b = rng.choice(TRANSLIT)
            if a in out.lower():
                i = out.lower().index(a)
                out = out[:i] + b + out[i + len(a):]
        elif op == "typo" and len(out) > 6:
            i = rng.randint(1, len(out) - 2)
            if out[i].isalpha():
                out = out[:i] + out[i + 1] + out[i] + out[i + 2:] if rng.random() < 0.5 else out[:i] + out[i + 1:]
        elif op == "case":
            out = " ".join(t.upper() if j == len(tokens) - 1 else t for j, t in enumerate(tokens))
        elif op == "reorder" and len(tokens) >= 2:
            out = " ".join([tokens[-1].upper()] + tokens[:-1]) if rng.random() < 0.5 else f"{tokens[-1]}, {' '.join(tokens[:-1])}"
        elif op == "initial" and len(tokens) >= 3:
            out = " ".join([tokens[0], tokens[1][0] + "."] + tokens[2:])
        elif op == "drop_middle" and len(tokens) >= 3:
            out = " ".join([tokens[0]] + tokens[2:])
    return out


def generate(seed: int = 7, n_background: int = 900, out: Path = OUT) -> dict:
    rng = random.Random(seed)
    (out / "icij").mkdir(parents=True, exist_ok=True)
    (out / "ch").mkdir(parents=True, exist_ok=True)

    ents, officers, inters, addrs, rels = [], [], [], [], []
    ch_companies, psc_lines, ftm, events = [], [], [], []
    gt = {"patterns": {}, "notes": "Synthetic, fictional data. gt_id columns give identity ground truth."}
    nid = iter(range(90_000_000, 99_999_999))
    ch_num = iter(range(14_000_000, 15_000_000))
    used_names: set[str] = {"Kestrel Bay Holdings"}

    def icij_entity(name, jur, inc, gt_id=None, status="Active", classification=0, address=""):
        node = str(next(nid))
        ents.append({"node_id": node, "name": name, "original_name": "", "former_name": "", "jurisdiction": jur[0],
                     "jurisdiction_description": jur[1], "company_type": "", "address": address,
                     "internal_id": str(rng.randint(100000, 999999)), "incorporation_date": inc.strftime("%d-%b-%Y").upper(),
                     "inactivation_date": "", "struck_off_date": "", "status": status, "service_provider": "Synthetic CSP",
                     "country_codes": jur[0], "countries": jur[1], "sourceID": "Synthetic Leaks", "note": "",
                     "gt_id": gt_id or f"GT-icij-{node}", "classification": classification})
        return node

    def icij_officer(name, cc3, gt_id, birth=""):
        node = str(next(nid))
        officers.append({"node_id": node, "name": name, "countries": "", "country_codes": cc3,
                         "sourceID": "Synthetic Leaks", "note": "", "gt_id": gt_id, "birth_date": birth})
        return node

    def icij_rel(a, b, link, rel="officer_of", share="", classification=0):
        rels.append({"node_id_start": a, "node_id_end": b, "rel_type": rel, "link": link, "status": "",
                     "start_date": "", "end_date": "", "sourceID": "Synthetic Leaks", "share": share,
                     "classification": classification})

    def ch_company(name, inc, gt_id=None):
        num = f"{next(ch_num):08d}"
        ch_companies.append({"CompanyName": name.upper(), "CompanyNumber": num, "RegAddress.AddressLine1":
                             f"{rng.randint(1, 200)} {rng.choice(CO_A)} Street", "RegAddress.PostTown": "LONDON",
                             "RegAddress.PostCode": f"EC{rng.randint(1, 4)}A {rng.randint(1, 9)}XX",
                             "CompanyCategory": "Private Limited Company", "CompanyStatus": "Active",
                             "CountryOfOrigin": "United Kingdom", "IncorporationDate": inc.strftime("%d/%m/%Y"),
                             "gt_id": gt_id or f"GT-ch-{num}"})
        return num

    def ch_psc_person(company, name, dob: date | None, nationality, gt_id, natures, notified):
        key = f"{rng.getrandbits(48):012x}"
        psc_lines.append({"company_number": company, "data": {
            "kind": "individual-person-with-significant-control", "name": name,
            "date_of_birth": {"year": dob.year, "month": dob.month} if dob else None,
            "nationality": nationality, "country_of_residence": rng.choice(["Cyprus", "United Arab Emirates", "England", "Monaco"]),
            "natures_of_control": natures, "notified_on": notified.isoformat(),
            "links": {"self": f"/company/{company}/persons-with-significant-control/individual/{key}"},
            "gt_id": gt_id}})

    def ch_psc_corp(company, name, reg, country, gt_id, natures, notified):
        key = f"{rng.getrandbits(48):012x}"
        psc_lines.append({"company_number": company, "data": {
            "kind": "corporate-entity-person-with-significant-control", "name": name,
            "identification": {"registration_number": reg, "country_registered": country, "legal_form": "Company"},
            "natures_of_control": natures, "notified_on": notified.isoformat(),
            "links": {"self": f"/company/{company}/persons-with-significant-control/corporate-entity/{key}"},
            "gt_id": gt_id}})

    def ftm_ent(eid, schema, props, gt_id=None):
        ftm.append({"id": eid, "schema": schema, "caption": (props.get("name") or [eid])[0],
                    "referents": [], "datasets": ["synth_sanctions"], "properties": props,
                    "gt": gt_id or eid})

    def event(record_id, etype, d, place, coords, desc, classification=0):
        events.append({"record_id": record_id, "type": etype, "date": d.isoformat(), "lat": coords[0],
                       "lon": coords[1], "place": place, "country": coords[2], "description": desc,
                       "classification": classification})

    def rdate(y0=2008, y1=2022) -> date:
        return date(y0, 1, 1) + timedelta(days=rng.randint(0, (y1 - y0) * 365))

    # ------------------------------------------------------------ P1 + P2: Morozan network
    mor_gt = "GT-P1-MOROZAN"
    mor_dob = date(1968, 3, 14)
    ftm_ent("synth-morozan", "Person", {
        "name": ["Viktor Arkadyevich Morozan"], "alias": ["Viktor Morozan"], "birthDate": [mor_dob.isoformat()],
        "nationality": ["cy", "ru"], "passportNumber": ["K4471902"], "topics": ["sanction"],
        "position": ["Beneficial owner, Kestrel group"], "sourceUrl": ["https://example.invalid/synthetic/morozan"]},
        mor_gt)
    ftm_ent("synth-san-morozan", "Sanction", {"entity": ["synth-morozan"], "program": ["SYNTH-RUS"],
                                              "authority": ["Synthetic Sanctions Authority"], "startDate": ["2022-04-08"]})
    kestrel = icij_entity("Kestrel Bay Holdings Ltd", OFFSHORE[0], date(2011, 6, 2), "GT-P2-KESTREL")
    larch = icij_entity("Larchmont Trading S.A.", OFFSHORE[1], date(2012, 1, 19), "GT-P2-LARCHMONT")
    mor_icij = icij_officer("Victor Morozan", "CYP", mor_gt)
    icij_rel(mor_icij, kestrel, "shareholder of", share="100%")
    icij_rel(mor_icij, kestrel, "beneficial owner of")
    icij_rel(kestrel, larch, "shareholder of", share="100%")
    nominee = icij_officer("Andreas Pelionidis", "CYP", "GT-P2-NOMINEE")
    icij_rel(nominee, kestrel, "director of")
    icij_rel(nominee, larch, "director of")
    northgate = ch_company("Northgate Logistics Ltd", date(2014, 9, 1), "GT-P2-NORTHGATE")
    marine = ch_company("Northgate Marine Services Ltd", date(2016, 2, 11), "GT-P2-NGMARINE")
    ch_psc_corp(northgate, "LARCHMONT TRADING SA", "PA-155603211", "Panama", "GT-P2-LARCHMONT",
                ["ownership-of-shares-75-to-100-percent", "voting-rights-75-to-100-percent"], date(2016, 4, 6))
    ch_psc_corp(marine, "NORTHGATE LOGISTICS LIMITED", northgate, "England", "GT-P2-NORTHGATE",
                ["ownership-of-shares-75-to-100-percent"], date(2016, 4, 6))
    ch_psc_person(northgate, "Mr Viktor Arkadievich MOROZAN", mor_dob, "Cypriot", mor_gt,
                  ["significant-influence-or-control"], date(2017, 1, 10))
    ch_psc_person(marine, "Mr Viktor MOROZAN", mor_dob, "Cypriot", mor_gt,
                  ["significant-influence-or-control"], date(2018, 5, 2))
    ftm_ent("synth-vessel-velmora", "Vessel", {"name": ["Velmora Star"], "imoNumber": ["IMO9478812"],
                                               "flag": ["cm"], "type": ["Crude oil tanker"], "topics": ["sanction"]})
    ftm_ent("synth-san-velmora", "Sanction", {"entity": ["synth-vessel-velmora"], "program": ["SYNTH-RUS"],
                                              "authority": ["Synthetic Sanctions Authority"], "startDate": ["2023-02-24"]})
    ftm_ent("synth-ngm", "Company", {"name": ["Northgate Marine Services Limited"], "country": ["gb"],
                                     "registrationNumber": [marine]}, "GT-P2-NGMARINE")
    ftm_ent("synth-link-ngm-velmora", "UnknownLink", {"subject": ["synth-ngm"], "object": ["synth-vessel-velmora"],
                                                      "role": ["Commercial manager of"]})
    gt["patterns"]["P1_duplicate_identity"] = {"gt_id": mor_gt, "expect_records": 4}
    gt["patterns"]["P2_hidden_ownership"] = {"from_gt": mor_gt, "to_gt": "GT-P2-NGMARINE",
                                             "chain": [mor_gt, "GT-P2-KESTREL", "GT-P2-LARCHMONT", "GT-P2-NORTHGATE", "GT-P2-NGMARINE"]}

    # ------------------------------------------------------------ P3: shell ring
    ring_addr = str(next(nid))
    addrs.append({"node_id": ring_addr, "address": "Suite 4, Oceanic House, Victoria, Mahe, Seychelles",
                  "name": "", "countries": "Seychelles", "country_codes": "SYC", "sourceID": "Synthetic Leaks",
                  "valid_until": "", "note": ""})
    csp = str(next(nid))
    inters.append({"node_id": csp, "name": "Meridian Corporate Services Ltd", "status": "ACTIVE", "internal_id": "",
                   "address": "", "countries": "Seychelles", "country_codes": "SYC", "sourceID": "Synthetic Leaks",
                   "valid_until": "", "note": ""})
    ring = [icij_entity(n, j, rdate(2013, 2015), f"GT-P3-{i}")
            for i, (n, j) in enumerate([("Halcyon Ridge Ltd", OFFSHORE[2]), ("Corvid Point Ltd", OFFSHORE[0]),
                                        ("Basalt Crest Ltd", OFFSHORE[3])])]
    for i, node in enumerate(ring):
        icij_rel(node, ring[(i + 1) % 3], "shareholder of", share="60%")
        icij_rel(node, ring_addr, "registered address", rel="registered_address")
        icij_rel(csp, node, "intermediary of", rel="intermediary_of")
    icij_rel(larch, ring[0], "shareholder of", share="40%")
    gt["patterns"]["P3_shell_ring"] = {"gt_ids": ["GT-P3-0", "GT-P3-1", "GT-P3-2"]}

    # ------------------------------------------------------------ P4: synchronised vessels
    ftm_ent("synth-vessel-ostrel", "Vessel", {"name": ["Ostrel Wind"], "imoNumber": ["IMO9312207"],
                                              "flag": ["ga"], "type": ["Products tanker"]})
    vessels = ["synth-vessel-velmora", "synth-vessel-ostrel"]
    for k in range(6):
        vid = f"synth-vessel-bg{k}"
        ftm_ent(vid, "Vessel", {"name": [f"{rng.choice(CO_A)} {rng.choice(['Spirit', 'Glory', 'Dawn', 'Pride'])}"],
                                "imoNumber": [f"IMO9{rng.randint(100000, 999999)}"], "flag": [rng.choice(["pa", "lr", "mh", "mt"])]})
        vessels.append(vid)
    port_names = list(PORTS)
    sync_days = []
    d = date(2023, 3, 1)
    for _ in range(9):
        d += timedelta(days=rng.randint(9, 20))
        port = rng.choice(port_names[:4] + port_names[8:9])
        sync_days.append((d, port))
        for v in vessels[:2]:
            event(f"synth_sanctions:{v}", "Port call / AIS position", d, port, PORTS[port],
                  f"Vessel reported at {port}")
    for v in vessels[2:]:
        dd = date(2023, 1, 1)
        for _ in range(rng.randint(6, 12)):
            dd += timedelta(days=rng.randint(10, 40))
            port = rng.choice(port_names)
            event(f"synth_sanctions:{v}", "Port call / AIS position", dd, port, PORTS[port], f"Vessel reported at {port}")
    gt["patterns"]["P4_synchronised_vessels"] = {"record_ids": [f"synth_sanctions:{v}" for v in vessels[:2]],
                                                 "co_locations": len(sync_days)}

    # ------------------------------------------------------------ P5: classified material
    for dd, city, cls, desc in [
        (date(2021, 11, 3), "Limassol", 1, "Case note: meeting with nominee director (RESTRICTED)"),
        (date(2022, 2, 17), "Dubai", 2, "Source report: cash courier hand-off (SECRET)"),
        (date(2019, 6, 9), "Monaco", 0, "Attended yacht show (public reporting)"),
    ]:
        event("synth_sanctions:synth-morozan", "Meeting" if cls < 2 else "Source report", dd, city, CITIES[city], desc, cls)
    courier = icij_officer("Rustam Belashev", "ARE", "GT-P5-COURIER")
    icij_rel(courier, kestrel, "director of", classification=2)
    gt["patterns"]["P5_classified"] = {"secret_officer_gt": "GT-P5-COURIER"}

    # ------------------------------------------------------------ sanctioned background (synthetic list)
    san_people = []
    for i in range(70):
        f, p, l = _person_name(rng)
        full = f"{f} {p} {l}"
        if full in used_names:
            continue
        used_names.add(full)
        dob = date(rng.randint(1950, 1990), rng.randint(1, 12), rng.randint(1, 28))
        nat = rng.choice(NATIONS)
        eid = f"synth-p{i}"
        g = f"GT-SP-{i}"
        ftm_ent(eid, "Person", {"name": [full], "birthDate": [dob.isoformat()], "nationality": [NATION_CC[nat].lower()],
                                "topics": ["sanction"]}, g)
        ftm_ent(f"synth-san-p{i}", "Sanction", {"entity": [eid], "program": [rng.choice(["SYNTH-RUS", "SYNTH-IRN", "SYNTH-CYBER"])],
                                                "authority": ["Synthetic Sanctions Authority"],
                                                "startDate": [rdate(2014, 2024).isoformat()]})
        san_people.append((full, dob, nat, g, eid))
        city = rng.choice(list(CITIES))
        event(f"synth_sanctions:{eid}", "Meeting", rdate(2016, 2024), city, CITIES[city], "Reported meeting")

    # ------------------------------------------------------------ background identities with fuzzy duplicates
    icij_companies = []
    for i in range(n_background):
        j = rng.choice(OFFSHORE)
        name = _company_name(rng, j[2])
        core = name.rsplit(" ", 1)[0]
        if core in used_names:
            continue
        used_names.add(core)
        icij_companies.append(icij_entity(name, j, rdate(), status=rng.choice(["Active", "Active", "Defaulted", "Struck / Defunct / Deregistered"])))
    ch_cos = []
    for i in range(n_background // 2):
        name = _company_name(rng, "Limited")
        core = name.rsplit(" ", 1)[0]
        if core in used_names:
            continue
        used_names.add(core)
        ch_cos.append(ch_company(name, rdate(2010, 2023)))
    # people: some appear in ICIJ only, some CH only, some both (+ some also on the synthetic sanctions list)
    pool = list(san_people[:35])
    for i in range(n_background):
        full = " ".join(_person_name(rng))
        if full in used_names:
            continue
        used_names.add(full)
        dob = date(rng.randint(1945, 1995), rng.randint(1, 12), rng.randint(1, 28))
        pool.append((full, dob, rng.choice(NATIONS), f"GT-BP-{i}", None))
    for full, dob, nat, g, _ in pool:
        where = rng.random()
        if where < 0.45 or g.startswith("GT-SP"):
            off = icij_officer(fuzz(full, rng) if rng.random() < 0.6 else full, rng.choice(["CYP", "RUS", "ARE", "GBR", "TUR"]), g)
            for _ in range(rng.randint(1, 3)):
                icij_rel(off, rng.choice(icij_companies), rng.choice(["shareholder of", "director of", "beneficiary of"]),
                         share=rng.choice(["", "", "50%", "100%", "33%"]))
        if where > 0.35 or g.startswith("GT-SP"):
            for _ in range(rng.randint(1, 2)):
                ch_psc_person(rng.choice(ch_cos), fuzz(full, rng) if rng.random() < 0.6 else f"Mr {full}", dob, nat, g,
                              [rng.choice(["ownership-of-shares-25-to-50-percent", "ownership-of-shares-50-to-75-percent",
                                           "ownership-of-shares-75-to-100-percent"])], rdate(2016, 2023))
    # Distractors: same surname / same first name, different people. ER must NOT merge these.
    for full, dob, nat, g, _ in pool[:60]:
        f, *_mid, l = full.split()
        other_first = rng.choice([x for x in FIRST if x != f])
        icij_officer(f"{other_first} {l}", "CYP", g + "-DISTRACTOR")
    # offshore companies owning UK companies (cross-source company duplicates)
    for _ in range(n_background // 6):
        node = rng.choice(icij_companies)
        e = next(x for x in ents if x["node_id"] == node)
        target = rng.choice(ch_cos)
        ch_psc_corp(target, fuzz(e["name"], rng).upper() if rng.random() < 0.5 else e["name"].upper(),
                    f"{e['jurisdiction'][:2]}-{rng.randint(10000, 999999)}", e["jurisdiction_description"], e["gt_id"],
                    ["ownership-of-shares-50-to-75-percent"], rdate(2016, 2023))
    # intermediaries
    for _ in range(15):
        node = str(next(nid))
        inters.append({"node_id": node, "name": f"{rng.choice(CO_A)} Corporate Services Ltd", "status": "ACTIVE",
                       "internal_id": "", "address": "", "countries": "", "country_codes": rng.choice(["CYP", "CHE", "GBR", "PAN"]),
                       "sourceID": "Synthetic Leaks", "valid_until": "", "note": ""})
        for c in rng.sample(icij_companies, 25):
            icij_rel(node, c, "intermediary of", rel="intermediary_of")

    # ------------------------------------------------------------ write
    def write_csv(path: Path, rows: list[dict]):
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    write_csv(out / "icij" / "nodes-entities.csv", ents)
    write_csv(out / "icij" / "nodes-officers.csv", officers)
    write_csv(out / "icij" / "nodes-intermediaries.csv", inters)
    write_csv(out / "icij" / "nodes-addresses.csv", addrs)
    write_csv(out / "icij" / "relationships.csv", rels)
    write_csv(out / "ch" / "companies.csv", ch_companies)
    with open(out / "ch" / "psc_snapshot.txt", "w", encoding="utf-8") as f:
        for line in psc_lines:
            f.write(json.dumps(line) + "\n")
    with open(out / "synth_sanctions.ftm.json", "w", encoding="utf-8") as f:
        for e in ftm:
            f.write(json.dumps(e) + "\n")
    write_csv(out / "events.csv", events)
    with open(out / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump(gt, f, indent=2)
    return {"icij_entities": len(ents), "icij_officers": len(officers), "icij_relationships": len(rels),
            "ch_companies": len(ch_companies), "ch_psc": len(psc_lines), "sanctions_ftm": len(ftm), "events": len(events)}


def read_events(path: Path = OUT / "events.csv", source: str = "synth_events"):
    """Adapter for the synthetic event feed (stand-in for AIS / ACLED-style point events)."""
    from .ingest.common import Event

    with open(path, encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            yield Event(source, row["record_id"], row["type"], row["date"], float(row["lat"]), float(row["lon"]),
                        "exact", place=row["place"], country=row["country"], description=row["description"],
                        classification=int(row["classification"]), source_doc="Synthetic event feed",
                        native_id=f"{i}|{row['record_id']}|{row['date']}|{row['place']}")
