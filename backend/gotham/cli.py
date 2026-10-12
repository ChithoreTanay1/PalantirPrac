"""Command line: python -m gotham.cli <command>

  download [targets...]    fetch raw data. Targets: OpenSanctions dataset names, or the groups
                           sanctions (all lists below) | icij | ch | gleif | all
                           (default: us_ofac_sdn eu_fsf). Existing files are skipped unless --force.
  synth                    generate synthetic ICIJ / Companies House / sanctions data
  ingest <source> [path]   opensanctions | icij | ch | gleif | synth (all synthetic feeds)
                           icij / ch / gleif default to *focused* mode: only nodes within --hops
                           relationships of a sanctions-list match. --full ingests everything.
  resolve                  run entity resolution (blocking, scoring, clustering)
  eval                     ER precision / recall against ground truth
  bootstrap                synth + ingest everything available + resolve (one-shot setup)
  reset                    delete the local SQLite DB and staging files

Typical long run:  download all  ->  bootstrap
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from . import resolve, synth
from .db import DATA, database_url, init_db, reset_engine
from .ingest import common, companies_house, gleif, icij, opensanctions

RAW = DATA / "raw"
OS_URL = "https://data.opensanctions.org/datasets/latest/{}/entities.ftm.json"
SANCTIONS_LISTS = [
    "us_ofac_sdn", "us_ofac_cons", "eu_fsf", "gb_fcdo_sanctions", "gb_hmt_sanctions", "un_sc_sanctions",
    "ch_seco_sanctions", "ca_dfatd_sema_sanctions", "au_dfat_sanctions", "nz_russia_sanctions",
    "jp_mof_sanctions", "fr_tresor_gels_avoir", "be_fod_sanctions", "ua_nsdc_sanctions", "ua_war_sanctions",
    "us_bis_denied", "us_trade_csl", "il_mod_terrorists",
]
ICIJ_URL = "https://offshoreleaks-data.icij.org/offshoreleaks/csv/full-oldb.LATEST.zip"
CH_PSC_PAGE = "https://download.companieshouse.gov.uk/en_pscdata.html"
CH_BASIC_PAGE = "https://download.companieshouse.gov.uk/en_output.html"
CH_BASE = "https://download.companieshouse.gov.uk/"
GLEIF_LATEST = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"
GLEIF_DIRECT = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/{}/latest.csv"
UA = {"User-Agent": "mini-gotham/0.1 (personal research project)"}


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def _fetch(url: str, dest: Path, force: bool = False) -> bool:
    """Streamed download with progress. Writes to .part and renames, so an interrupted
    download never leaves a truncated file that looks complete."""
    if dest.exists() and not force:
        print(f"  have {dest.name} ({dest.stat().st_size / 1e6:,.0f} MB), skipping (--force to refetch)")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    print(f"  {url}\n   -> {dest}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, last = 0, 0.0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 3:
                    last = time.time()
                    pct = f" ({done / total:.0%})" if total else ""
                    print(f"     {done / 1e6:,.0f} MB{pct}", flush=True)
    except Exception as e:  # noqa: BLE001 - report and carry on with the other targets
        tmp.unlink(missing_ok=True)
        print(f"  FAILED: {e}")
        return False
    tmp.replace(dest)
    return True


def _page_link(page: str, pattern: str) -> str | None:
    with urllib.request.urlopen(urllib.request.Request(page, headers=UA), timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    links = sorted(set(re.findall(pattern, html)))
    return links[-1] if links else None


def _gleif_urls() -> dict[str, str]:
    """Full-file CSV zip URLs for lei2 and rr from the golden-copy publish index."""
    urls: dict[str, str] = {}
    try:
        with urllib.request.urlopen(urllib.request.Request(GLEIF_LATEST, headers=UA), timeout=60) as r:
            data = json.loads(r.read())

        def walk(o, path=()):
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, path + (str(k),))
            elif isinstance(o, list):
                for v in o:
                    walk(v, path)
            elif isinstance(o, str) and o.startswith("http"):
                p = "/".join(path).lower()
                if "csv" in p and "full" in p and "delta" not in p:
                    for kind in ("lei2", "rr"):
                        if kind in path and kind not in urls:
                            urls[kind] = o

        walk(data)
    except Exception as e:  # noqa: BLE001
        print(f"  GLEIF publish index unavailable ({e}); using direct links")
    for kind in ("lei2", "rr"):
        urls.setdefault(kind, GLEIF_DIRECT.format(kind))
    return urls


def download(targets: list[str], force: bool = False) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    targets = targets or ["us_ofac_sdn", "eu_fsf"]
    if "all" in targets:
        targets = ["sanctions", "icij", "ch", "gleif"]
    datasets: list[str] = []
    for t in targets:
        if t == "sanctions":
            datasets += SANCTIONS_LISTS
        elif t not in ("icij", "ch", "gleif"):
            datasets.append(t)
    for d in dict.fromkeys(datasets):
        print(f"[opensanctions] {d}")
        _fetch(OS_URL.format(d), RAW / f"{d}.ftm.json", force)
    if "icij" in targets:
        print("[icij] Offshore Leaks full database (~70 MB zip)")
        _fetch(ICIJ_URL, RAW / "icij" / "full-oldb.LATEST.zip", force)
    if "ch" in targets:
        print("[ch] Companies House PSC snapshot (~2 GB) + basic company data (~500 MB)")
        for page, pat in ((CH_PSC_PAGE, r"persons-with-significant-control-snapshot-\d{4}-\d{2}-\d{2}\.zip"),
                          (CH_BASIC_PAGE, r"BasicCompanyDataAsOneFile-\d{4}-\d{2}-\d{2}\.zip")):
            try:
                name = _page_link(page, pat)
            except Exception as e:  # noqa: BLE001
                print(f"  FAILED to read {page}: {e}")
                continue
            if name:
                _fetch(CH_BASE + name, RAW / "companies_house" / name, force)
            else:
                print(f"  no file matching {pat} on {page}")
    if "gleif" in targets:
        print("[gleif] golden copy: LEI records (~500 MB) + relationships (~25 MB)")
        for kind, url in _gleif_urls().items():
            _fetch(url, RAW / "gleif" / f"gleif-goldencopy-{kind}-golden-copy.csv.zip", force)


def _focus(module, path: Path, full: bool, hops: int) -> set[str] | None:
    if full:
        print("  whole-dump ingest: entity resolution is in-memory, expect it to be slow on millions of records")
        return None
    t = time.time()
    keep = module.focus_set(path, hops)
    print(f"  focus pass took {time.time() - t:.0f}s")
    return keep


def ingest(source: str, path: str | None, limit: int | None, full: bool = False, hops: int = 2) -> None:
    t = time.time()
    if source == "opensanctions":
        files = [Path(path)] if path else sorted(RAW.glob("*.ftm.json"))
        if not files:
            sys.exit("no OpenSanctions files; run `download` first")
        for f in files:
            ds = f.name.split(".")[0]
            print(f"[opensanctions] {f.name}")
            _print(common.run(f"os_{ds}", opensanctions.read(f, ds), f))
    elif source == "icij":
        p = Path(path) if path else RAW / "icij"
        keep = _focus(icij, p, full or bool(limit), hops)
        _print(common.run("icij", icij.read(p, limit=limit, keep=keep), p))
    elif source == "ch":
        p = Path(path) if path else RAW / "companies_house"
        keep = _focus(companies_house, p, full or bool(limit), hops)
        _print(common.run("ch", companies_house.read(p, limit=limit, keep=keep), p))
    elif source == "gleif":
        p = Path(path) if path else RAW / "gleif"
        keep = _focus(gleif, p, full or bool(limit), hops)
        _print(common.run("gleif", gleif.read(p, limit=limit, keep=keep), p))
    elif source == "synth":
        out = synth.OUT
        if not (out / "events.csv").exists():
            _print(synth.generate())
        print("[synth] sanctions (FtM)")
        _print(common.run("synth_sanctions", opensanctions.read(out / "synth_sanctions.ftm.json", "synth_sanctions",
                                                                synthetic=True), out / "synth_sanctions.ftm.json"))
        print("[synth] ICIJ format")
        _print(common.run("synth_icij", icij.read(out / "icij", source="synth_icij", synthetic=True), out / "icij"))
        print("[synth] Companies House format")
        _print(common.run("synth_ch", companies_house.read(out / "ch", source="synth_ch", synthetic=True), out / "ch"))
        print("[synth] events")
        _print(common.run("synth_events", synth.read_events(out / "events.csv"), out / "events.csv"))
    else:
        sys.exit(f"unknown source {source}")
    print(f"done in {time.time() - t:.1f}s")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="gotham")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("targets", nargs="*")
    d.add_argument("--force", action="store_true")
    s = sub.add_parser("synth")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--n", type=int, default=900)
    i = sub.add_parser("ingest")
    i.add_argument("source", choices=["opensanctions", "icij", "ch", "gleif", "synth"])
    i.add_argument("path", nargs="?")
    i.add_argument("--limit", type=int, help="first N rows per file, no focusing (smoke tests)")
    i.add_argument("--full", action="store_true", help="icij/ch/gleif: ingest the whole dump, not the focused subgraph")
    i.add_argument("--hops", type=int, default=2, help="focused mode: relationship hops kept around sanctions matches")
    r = sub.add_parser("resolve")
    r.add_argument("--threshold", type=float, default=resolve.AUTO_MERGE)
    sub.add_parser("eval")
    b = sub.add_parser("bootstrap")
    b.add_argument("--no-real", action="store_true", help="skip OpenSanctions (synthetic only)")
    b.add_argument("--full", action="store_true", help="ingest bulk sources whole instead of focused")
    b.add_argument("--hops", type=int, default=2)
    sub.add_parser("reset")
    args = ap.parse_args(argv)

    print(f"database: {database_url()}")
    if args.cmd == "download":
        download(args.targets, args.force)
    elif args.cmd == "synth":
        _print(synth.generate(seed=args.seed, n_background=args.n))
    elif args.cmd == "ingest":
        init_db()
        ingest(args.source, args.path, args.limit, args.full, args.hops)
    elif args.cmd == "resolve":
        t = time.time()
        _print(resolve.resolve_all(args.threshold))
        print(f"resolved in {time.time() - t:.1f}s")
    elif args.cmd == "eval":
        _print(resolve.evaluate())
    elif args.cmd == "bootstrap":
        init_db()
        _print(synth.generate())
        ingest("synth", None, None)
        if not args.no_real and list(RAW.glob("*.ftm.json")):
            ingest("opensanctions", None, None)
        # Bulk sources go after the sanctions lists: focused mode seeds from them.
        for src, folder in (("icij", "icij"), ("ch", "companies_house"), ("gleif", "gleif")):
            if (RAW / folder).is_dir() and any((RAW / folder).iterdir()):
                ingest(src, None, None, args.full, args.hops)
        t = time.time()
        _print(resolve.resolve_all())
        print(f"resolved in {time.time() - t:.1f}s")
        _print(resolve.evaluate())
    elif args.cmd == "reset":
        reset_engine()
        for p in [DATA / "gotham.db", DATA / "gotham.db-wal", DATA / "gotham.db-shm"]:
            p.unlink(missing_ok=True)
        shutil.rmtree(common.STAGING, ignore_errors=True)
        print("reset done")


if __name__ == "__main__":
    main()
