"""Delayed market data via yfinance (unofficial Yahoo Finance). Responses are cached in-process
so the UI can poll without hammering Yahoo; a few minutes of staleness is acceptable here."""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable

import pandas as pd
import yfinance as yf

# tab -> [(symbol, display name, group)]
WATCHLISTS: dict[str, list[tuple[str, str, str]]] = {
    "indices": [
        ("^GSPC", "S&P 500", "Americas"), ("^DJI", "Dow Jones", "Americas"), ("^IXIC", "Nasdaq Composite", "Americas"),
        ("^RUT", "Russell 2000", "Americas"), ("^GSPTSE", "S&P/TSX", "Americas"), ("^BVSP", "Bovespa", "Americas"),
        ("^FTSE", "FTSE 100", "Europe"), ("^GDAXI", "DAX", "Europe"), ("^FCHI", "CAC 40", "Europe"),
        ("^STOXX50E", "Euro Stoxx 50", "Europe"),
        ("^N225", "Nikkei 225", "Asia-Pacific"), ("^HSI", "Hang Seng", "Asia-Pacific"), ("000001.SS", "Shanghai Composite", "Asia-Pacific"),
        ("^KS11", "KOSPI", "Asia-Pacific"), ("^BSESN", "BSE Sensex", "Asia-Pacific"), ("^AXJO", "ASX 200", "Asia-Pacific"),
        ("^VIX", "CBOE Volatility (VIX)", "Volatility"),
    ],
    "commodities": [
        ("CL=F", "WTI Crude", "Energy"), ("BZ=F", "Brent Crude", "Energy"), ("NG=F", "Natural Gas (Henry Hub)", "Energy"),
        ("HO=F", "Heating Oil", "Energy"), ("RB=F", "RBOB Gasoline", "Energy"),
        ("GC=F", "Gold", "Metals"), ("SI=F", "Silver", "Metals"), ("PL=F", "Platinum", "Metals"),
        ("PA=F", "Palladium", "Metals"), ("HG=F", "Copper", "Metals"), ("ALI=F", "Aluminium", "Metals"),
        ("ZW=F", "Wheat", "Agriculture"), ("ZC=F", "Corn", "Agriculture"), ("ZS=F", "Soybeans", "Agriculture"),
        ("KC=F", "Coffee", "Agriculture"), ("SB=F", "Sugar", "Agriculture"),
    ],
    "fx": [
        ("DX-Y.NYB", "US Dollar Index", "Dollar"),
        ("EURUSD=X", "EUR/USD", "Majors"), ("GBPUSD=X", "GBP/USD", "Majors"), ("USDJPY=X", "USD/JPY", "Majors"),
        ("USDCHF=X", "USD/CHF", "Majors"), ("AUDUSD=X", "AUD/USD", "Majors"), ("USDCAD=X", "USD/CAD", "Majors"),
        ("USDCNY=X", "USD/CNY", "Emerging"), ("USDINR=X", "USD/INR", "Emerging"), ("USDBRL=X", "USD/BRL", "Emerging"),
        ("USDZAR=X", "USD/ZAR", "Emerging"), ("USDMXN=X", "USD/MXN", "Emerging"),
        ("USDRUB=X", "USD/RUB", "Sanctions-exposed"), ("USDTRY=X", "USD/TRY", "Sanctions-exposed"),
        ("USDAED=X", "USD/AED", "Sanctions-exposed"), ("USDKZT=X", "USD/KZT", "Sanctions-exposed"),
    ],
    "crypto": [
        ("BTC-USD", "Bitcoin", "Majors"), ("ETH-USD", "Ethereum", "Majors"), ("SOL-USD", "Solana", "Majors"),
        ("XRP-USD", "XRP", "Majors"), ("BNB-USD", "BNB", "Majors"), ("XMR-USD", "Monero", "Privacy"),
        ("ZEC-USD", "Zcash", "Privacy"), ("TRX-USD", "Tron", "Payments rails"), ("USDT-USD", "Tether", "Stablecoins"),
        ("USDC-USD", "USD Coin", "Stablecoins"),
    ],
    "rates": [
        ("^IRX", "US 13-week T-bill", "US Treasuries"), ("^FVX", "US 5-year", "US Treasuries"),
        ("^TNX", "US 10-year", "US Treasuries"), ("^TYX", "US 30-year", "US Treasuries"),
        ("TLT", "iShares 20+ Yr Treasury ETF", "Bond ETFs"), ("HYG", "iShares High Yield ETF", "Bond ETFs"),
        ("EMB", "iShares EM USD Bond ETF", "Bond ETFs"),
    ],
    "geopolitical": [
        ("LMT", "Lockheed Martin", "Defense"), ("RTX", "RTX", "Defense"), ("NOC", "Northrop Grumman", "Defense"),
        ("GD", "General Dynamics", "Defense"), ("BA.L", "BAE Systems", "Defense"), ("RHM.DE", "Rheinmetall", "Defense"),
        ("ITA", "iShares Aerospace & Defense ETF", "Defense"),
        ("FRO", "Frontline", "Tankers & shipping"), ("STNG", "Scorpio Tankers", "Tankers & shipping"),
        ("TNK", "Teekay Tankers", "Tankers & shipping"), ("ZIM", "ZIM Integrated", "Tankers & shipping"),
        ("MAERSK-B.CO", "Maersk", "Tankers & shipping"),
        ("XOM", "ExxonMobil", "Energy majors"), ("SHEL", "Shell", "Energy majors"), ("BP", "BP", "Energy majors"),
        ("2222.SR", "Saudi Aramco", "Energy majors"),
    ],
}

RANGES: dict[str, tuple[str, str]] = {  # range -> (period, interval)
    "1d": ("1d", "5m"), "5d": ("5d", "30m"), "1mo": ("1mo", "1d"), "6mo": ("6mo", "1d"),
    "1y": ("1y", "1d"), "5y": ("5y", "1wk"), "max": ("max", "1mo"),
}

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
    except Exception:
        if hit:  # Yahoo hiccup: serve stale rather than fail
            return hit[1]
        raise
    with _lock:
        _cache[key] = (now, val)
    return val


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _column(df: pd.DataFrame, sym: str, field: str) -> pd.Series:
    if isinstance(df.columns, pd.MultiIndex):
        lv0 = df.columns.get_level_values(0)
        if sym in lv0:
            return df[sym][field]
        return df[field][sym]
    return df[field]


def _quotes(items: list[tuple[str, str, str]]) -> list[dict]:
    symbols = [s for s, _, _ in items]
    df = yf.download(symbols, period="1mo", interval="1d", group_by="ticker", progress=False, threads=True, auto_adjust=False)
    out = []
    for sym, name, group in items:
        row: dict[str, Any] = {"symbol": sym, "name": name, "group": group, "price": None, "change": None,
                               "change_pct": None, "spark": [], "ts": None}
        try:
            closes = _column(df, sym, "Close").dropna()
        except KeyError:
            closes = pd.Series(dtype=float)
        if len(closes):
            price = _num(closes.iloc[-1])
            prev = _num(closes.iloc[-2]) if len(closes) > 1 else None
            row["price"] = price
            if price is not None and prev:
                row["change"] = price - prev
                row["change_pct"] = (price - prev) / prev * 100
            row["spark"] = [_num(v) for v in closes.tolist()]
            row["ts"] = closes.index[-1].isoformat()
        out.append(row)
    return out


def tab_quotes(tab: str) -> list[dict]:
    if tab not in WATCHLISTS:
        raise KeyError(tab)
    return _cached(f"tab:{tab}", 120, lambda: _quotes(WATCHLISTS[tab]))


def symbol_quotes(symbols: list[str]) -> list[dict]:
    symbols = sorted({s.strip().upper() for s in symbols if s.strip()})[:40]
    if not symbols:
        return []
    known = {s: (n, g) for items in WATCHLISTS.values() for s, n, g in items}
    items = [(s, *known.get(s, (s, "Watchlist"))) for s in symbols]
    return _cached("syms:" + ",".join(symbols), 120, lambda: _quotes(items))


def history(symbol: str, rng: str) -> dict:
    period, interval = RANGES.get(rng, RANGES["1mo"])

    def load():
        t = yf.Ticker(symbol)
        df = t.history(period=period, interval=interval, auto_adjust=False)
        points = [
            {"t": idx.isoformat(), "o": _num(r["Open"]), "h": _num(r["High"]), "l": _num(r["Low"]),
             "c": _num(r["Close"]), "v": _num(r.get("Volume"))}
            for idx, r in df.iterrows() if _num(r["Close"]) is not None
        ]
        meta: dict[str, Any] = {}
        try:
            fi = t.fast_info
            meta = {"currency": fi.get("currency"), "exchange": fi.get("exchange"),
                    "year_high": _num(fi.get("yearHigh")), "year_low": _num(fi.get("yearLow")),
                    "market_cap": _num(fi.get("marketCap")), "prev_close": _num(fi.get("previousClose"))}
        except Exception:
            pass
        return {"symbol": symbol, "range": rng, "interval": interval, "points": points, "meta": meta}

    return _cached(f"hist:{symbol}:{rng}", 60 if rng == "1d" else 300, load)


def news(symbol: str) -> list[dict]:
    def load():
        out = []
        for n in (yf.Ticker(symbol).news or [])[:12]:
            c = n.get("content", n)  # newer yfinance nests the article under "content"
            url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link")
            ts = c.get("pubDate") or c.get("displayTime")
            if not ts and c.get("providerPublishTime"):
                ts = pd.Timestamp(c["providerPublishTime"], unit="s", tz="UTC").isoformat()
            provider = c.get("provider")
            out.append({"title": c.get("title"), "url": url, "ts": ts,
                        "publisher": provider.get("displayName") if isinstance(provider, dict) else c.get("publisher")})
        return [n for n in out if n["title"]]

    return _cached(f"news:{symbol}", 600, load)
