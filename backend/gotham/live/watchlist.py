"""Watchlists that link live tracks to graph objects.

* Vessels   : IMO / MMSI numbers of (real, non-synthetic) Vessel records.
* Aircraft  : sanctioned aircraft (tail number / serial + model) mapped to ICAO24 transponder
              codes via OpenSky's free aircraft database (data/raw/aircraftDatabase.zip).
* Operators : ICAO airline designators of sanctioned airlines (callsign prefix), kept only
              when a sanctioned Company record with that airline's name exists in the DB.

A match is a lead: registrations get re-used and transponders get swapped. The UI shows the
match basis next to every flag.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field

from ..db import DATA, get_engine, loads, rows

AIRCRAFT_DB = DATA / "raw" / "aircraftDatabase.zip"
AIRCRAFT_DB_URL = "https://s3.opensky-network.org/data-samples/metadata/aircraftDatabase.zip"
AIRCRAFT_CACHE = DATA / "ref" / "live_aircraft_watch.json"

# ICAO airline designator -> airline name as it appears on sanctions lists.
# Only entries that resolve to a sanctioned record in the loaded data are used.
OPERATORS = {
    "IRM": "Mahan Air",
    "IRA": "Iran Air",
    "SYR": "Syrian Arab Airlines",
    "SAW": "Cham Wings",
    "CPN": "Caspian Airlines",
    "QFZ": "Qeshm Fars Air",
    "VCV": "Conviasa",
}


def _norm(v: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (v or "").upper())


@dataclass
class Watchlist:
    imo: dict[str, str] = field(default_factory=dict)       # IMO -> record_id
    mmsi: dict[str, str] = field(default_factory=dict)      # MMSI -> record_id
    icao24: dict[str, dict] = field(default_factory=dict)   # hex -> {record_id, basis, registration}
    operators: dict[str, dict] = field(default_factory=dict)  # ICAO prefix -> {record_id, name}
    captions: dict[str, str] = field(default_factory=dict)  # record_id -> caption

    def summary(self) -> dict:
        return {"vessels_imo": len(self.imo), "vessels_mmsi": len(self.mmsi),
                "aircraft_icao24": len(self.icao24), "operators": sorted(self.operators)}


def _aircraft_matches(aircraft: list[dict]) -> dict[str, dict]:
    if AIRCRAFT_CACHE.exists() and AIRCRAFT_CACHE.stat().st_mtime > AIRCRAFT_DB.stat().st_mtime:
        cached = json.loads(AIRCRAFT_CACHE.read_text())
        if cached.get("n_input") == len(aircraft) and cached.get("version") == 2:
            return cached["matches"]
    regs: dict[str, tuple[str, list[str]]] = {}  # registration -> (record_id, model prefixes)
    serials: dict[str, list[tuple[str, str]]] = {}
    for a in aircraft:
        models = [_norm(m)[:2] for m in a["props"].get("model", []) if _norm(m)]
        for n in a["names"]:
            if 4 <= len(_norm(n)) <= 8:
                regs[_norm(n)] = (a["id"], models)
        for i in a["identifiers"]:
            if i.startswith("reg:"):
                regs[_norm(i[4:])] = (a["id"], models)
        props = a["props"]
        for s in props.get("serialNumber", []):
            for m in props.get("model", []) or [""]:
                serials.setdefault(_norm(s), []).append((_norm(m)[:4], a["id"]))
    matches: dict[str, dict] = {}
    with zipfile.ZipFile(AIRCRAFT_DB) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        for row in csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace")):
            hexcode = (row.get("icao24") or "").strip().lower()
            if not hexcode:
                continue
            reg = _norm(row.get("registration"))
            db_type = _norm(row.get("model")) + _norm(row.get("typecode")) + _norm(row.get("manufacturername"))
            if reg in regs:
                rid, models = regs[reg]
                # registrations get re-used: require the aircraft type to be consistent when both are known
                if not models or not db_type or any(m in db_type for m in models):
                    matches[hexcode] = {"record_id": rid, "basis": "registration", "registration": row.get("registration")}
                continue
            sn = _norm(row.get("serialnumber"))
            if sn and len(sn) >= 3 and sn in serials:
                for prefix, rid in serials[sn]:
                    if len(prefix) >= 3 and prefix in db_type:
                        matches[hexcode] = {"record_id": rid, "basis": "serial+type (weaker)", "registration": row.get("registration")}
                        break
    AIRCRAFT_CACHE.write_text(json.dumps({"version": 2, "n_input": len(aircraft), "matches": matches}))
    return matches


def build() -> Watchlist:
    w = Watchlist()
    with get_engine().connect() as conn:
        recs = rows(conn, "SELECT id, schema, caption, names, identifiers, props FROM records "
                          "WHERE synthetic = 0 AND schema IN ('Vessel', 'Aircraft', 'Company')")
    aircraft = []
    companies = []
    for r in recs:
        r["names"] = loads(r["names"], [])
        r["identifiers"] = loads(r["identifiers"], [])
        r["props"] = loads(r["props"], {})
        w.captions[r["id"]] = r["caption"]
        if r["schema"] == "Vessel":
            for i in r["identifiers"]:
                if i.startswith("imo:"):
                    w.imo[i[4:].lstrip("0")] = r["id"]
                elif i.startswith("mmsi:"):
                    w.mmsi[i[5:]] = r["id"]
        elif r["schema"] == "Aircraft":
            aircraft.append(r)
        else:
            companies.append(r)
    if AIRCRAFT_DB.exists() and aircraft:
        w.icao24 = _aircraft_matches(aircraft)
    for code, airline in OPERATORS.items():
        target = airline.lower()
        for c in companies:
            sanctioned = bool(c["props"].get("sanctions")) or "sanction" in (c["props"].get("topics") or [])
            if sanctioned and any(n.lower().strip() == target or n.lower().startswith(target + " ") for n in c["names"]):
                w.operators[code] = {"record_id": c["id"], "name": c["caption"]}
                break
    return w
