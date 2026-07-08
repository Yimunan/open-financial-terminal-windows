"""Read-only data tools for the grounded assistant.

Each tool wraps an existing terminal service and returns a COMPACT, human-readable string
(plus a structured dict for logging). The assistant agent plans which tools to call, runs
them, and feeds the text back to the LLM as grounded DATA so it answers from real numbers
instead of hallucinating prices / P/E / news.

Everything here is read-only and offline-cheap (qhfi's incremental refresh caches bars/
fundamentals on first use).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.manager import DataManager
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider
from qhfi.research.client import LLMClient

from app.services import factors as fac
from app.services import fundamentals as fa
from app.services import news_router as nr
from app.services import screener as scr
from app.services.market import fetch_bars, quote_from_bars
from app.services.universe import list_universes, search as universe_search

if TYPE_CHECKING:
    from app.store import TerminalStore

#: period token → calendar days back. ``ytd`` is resolved per-call.
_PERIODS = {"1w": 7, "2w": 14, "1m": 31, "3m": 92, "6m": 183, "1y": 365, "ytd": None}


#: Common index names/tickers → a tradable ETF proxy yfinance can actually fetch (raw index tickers
#: like SPX / ^GSPC don't resolve through the equity provider, so a benchmark comparison would
#: otherwise return "insufficient data").
_INDEX_PROXY = {
    "SPX": "SPY", "^GSPC": "SPY", "SP500": "SPY", "S&P500": "SPY", "S&P 500": "SPY",
    "SPX500": "SPY", "GSPC": "SPY", ".SPX": "SPY", "US500": "SPY",
    "NDX": "QQQ", "^NDX": "QQQ", "^IXIC": "QQQ", "NASDAQ": "QQQ", "NASDAQ100": "QQQ",
    "NASDAQ 100": "QQQ", "COMP": "QQQ",
    "DJI": "DIA", "^DJI": "DIA", "DJIA": "DIA", "DOW": "DIA", "DOW30": "DIA", "DOW JONES": "DIA",
    "RUT": "IWM", "^RUT": "IWM", "RUSSELL2000": "IWM", "RUSSELL 2000": "IWM",
}


#: Bare crypto bases → their default USDT pair, so "how is BTC doing" doesn't get mis-fetched as an
#: equity ticker. Only unambiguous majors (none of these is a US-equity ticker) to avoid clobbering
#: real stocks like LINK / DOT / MATIC.
_CRYPTO_BASE = {
    "BTC": "BTC/USDT", "BITCOIN": "BTC/USDT", "XBT": "BTC/USDT",
    "ETH": "ETH/USDT", "ETHER": "ETH/USDT", "ETHEREUM": "ETH/USDT",
    "SOL": "SOL/USDT", "SOLANA": "SOL/USDT", "XRP": "XRP/USDT", "RIPPLE": "XRP/USDT",
    "ADA": "ADA/USDT", "CARDANO": "ADA/USDT", "DOGE": "DOGE/USDT", "DOGECOIN": "DOGE/USDT",
    "AVAX": "AVAX/USDT", "BNB": "BNB/USDT",
}


def _resolve_symbol(symbol: str) -> str:
    """Normalize a user/planner symbol to something the providers can fetch: index → ETF proxy,
    bare crypto base → its USDT pair."""
    if not symbol:
        return symbol
    key = symbol.strip().upper()
    if "/" in key:  # already a crypto pair
        return symbol
    return _INDEX_PROXY.get(key) or _CRYPTO_BASE.get(key, symbol)


def _is_crypto(symbol: str) -> bool:
    return "/" in symbol


def _infer_asset(symbol: str, asset: str | None) -> str:
    if asset in ("equity", "crypto"):
        return asset
    return "crypto" if _is_crypto(symbol) else "equity"


def _fmt_mcap(v: float | None) -> str:
    if not v:
        return "n/a"
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= scale:
            return f"${v / scale:.2f}{unit}"
    return f"${v:,.0f}"


def _fmt_div_yield(v: float | None) -> str:
    """Normalize yfinance's ``info['dividendYield']`` to a percent string.

    The installed yfinance returns this field ALREADY as a percent (SPY 0.98%, JPM 1.84%, AAPL
    0.36%, KO 2.67%) — so it must NOT be multiplied (an earlier ×100 made AAPL read 36%). Older
    yfinance returned a fraction (0.0184). Disambiguate by magnitude: only a value below ~0.05 is a
    fraction (a real annual yield is essentially never <0.05% nor as a fraction >0.05 yet sub-1%)."""
    if v is None:
        return "n/a"
    pct = v * 100 if 0 < v < 0.05 else v
    return f"{pct:.2f}%"


@dataclass
class ToolContext:
    dm: DataManager
    fstore: FundamentalsStore
    fprov: YFinanceFundamentalsProvider
    llm: LLMClient
    model: str
    symbol: str | None = None
    asset: str | None = None
    store: "TerminalStore | None" = None   # tracked Holdings, for portfolio-level tools


# ── individual tools ─────────────────────────────────────────────────────────────────
# Each returns (text, data). ``text`` is what the LLM sees; ``data`` is for logging/tests.

def get_quote(ctx: ToolContext, symbol: str, asset: str | None = None) -> tuple[str, dict]:
    """Latest price + day change (EOD/last-session for equities; last daily incl. the live-forming
    bar for crypto)."""
    symbol = _resolve_symbol(symbol)
    asset = _infer_asset(symbol, asset)
    end = date.today()
    _, bars = fetch_bars(ctx.dm, symbol, asset, end - timedelta(days=14), end)
    if bars.empty:
        return f"{symbol}: no price data available.", {"symbol": symbol}
    q = quote_from_bars(bars)
    live = "latest (crypto, ~live)" if asset == "crypto" else f"close {q['asof']} (EOD/delayed)"
    vol = q.get("volume")
    vol_txt = f"; volume {vol:,.0f}" if isinstance(vol, (int, float)) else ""
    txt = (
        f"{symbol} price: {q['price']} ({q['change_pct']:+.2f}% on the day, "
        f"day H {q['high']} / L {q['low']}{vol_txt}) — {live}."
    )
    return txt, {"symbol": symbol, **q}


def _parse_date(v: object) -> date | None:
    """Parse an ISO-ish date string (YYYY-MM-DD or YYYY-MM) → date; None on anything unparseable."""
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d"):
        try:
            from datetime import datetime
            return datetime.strptime(s if fmt != "%Y-%m" else s[:7], fmt).date()
        except ValueError:
            continue
    return None


def get_performance(
    ctx: ToolContext, symbol: str, period: str = "3m", asset: str | None = None,
    start: str | None = None, end: str | None = None,
) -> tuple[str, dict]:
    """Total price return over a trailing period (1w/2w/1m/3m/6m/1y/ytd) OR an explicit start/end
    date window (ISO dates) when the user asks about a specific historical span."""
    symbol = _resolve_symbol(symbol)
    asset = _infer_asset(symbol, asset)
    d_start, d_end = _parse_date(start), _parse_date(end)
    window_label = period
    if d_start or d_end:  # explicit historical window
        win_end = d_end or date.today()
        win_start = d_start or (win_end - timedelta(days=92))
        window_label = f"{win_start} to {win_end}"
    else:
        period = (period or "3m").lower()
        if period not in _PERIODS:
            period = "3m"
        win_end = date.today()
        days = _PERIODS[period]
        win_start = date(win_end.year, 1, 1) if period == "ytd" else win_end - timedelta(days=(days or 92))
    _, bars = fetch_bars(ctx.dm, symbol, asset, win_start - timedelta(days=7), win_end)
    if bars.empty or len(bars) < 2:
        return f"{symbol}: not enough history for {window_label} performance.", {"symbol": symbol}
    win = bars.loc[str(win_start):str(win_end)]
    if len(win) < 2:
        win = bars
    first, last = float(win["close"].iloc[0]), float(win["close"].iloc[-1])
    ret = (last / first - 1) * 100 if first else 0.0
    hi, lo = float(win["close"].max()), float(win["close"].min())
    txt = (
        f"{symbol} {window_label} performance: {ret:+.2f}% "
        f"(from {first:.2f} on {win.index[0].date()} to {last:.2f} on {win.index[-1].date()}; "
        f"range {lo:.2f}–{hi:.2f})."
    )
    return txt, {"symbol": symbol, "period": window_label, "return_pct": round(ret, 2),
                 "start": first, "end": last}


def _last_price(ctx: ToolContext, symbol: str) -> float | None:
    """Best-effort last close for the symbol (for 52w-range positioning). Never raises."""
    try:
        end = date.today()
        _, bars = fetch_bars(ctx.dm, symbol, _infer_asset(symbol, None), end - timedelta(days=10), end)
        return None if bars.empty else float(bars["close"].iloc[-1])
    except Exception:  # noqa: BLE001
        return None


def get_fundamentals(ctx: ToolContext, symbol: str, asset: str | None = None) -> tuple[str, dict]:
    """Company snapshot — name, sector, market cap, P/E, P/B, EPS, dividend, beta, ROE, 52w range."""
    if _infer_asset(symbol, asset) == "crypto":
        return f"{symbol} is a crypto pair — no equity fundamentals (P/E, market cap, etc.).", {"symbol": symbol}
    symbol = _resolve_symbol(symbol)
    s = fa.snapshot(symbol)
    if not s.get("name") and s.get("market_cap") is None and s.get("pe") is None:
        return f"{symbol}: no fundamentals available.", {"symbol": symbol}
    def n(v, suf=""):  # noqa: ANN001
        return "n/a" if v is None else (f"{v:.2f}{suf}" if isinstance(v, (int, float)) else str(v))
    roe = s.get("roe")  # yfinance returnOnEquity is a fraction (0.16 = 16%, AAPL 1.41 = 141%)
    # Current price + position in the 52w range, so "near its 52-week high?" is answerable here.
    price, pos = _last_price(ctx, symbol), ""
    hi, lo = s.get("high_52w"), s.get("low_52w")
    if price and isinstance(hi, (int, float)) and isinstance(lo, (int, float)) and hi > lo:
        frm_hi = (price / hi - 1) * 100
        pct_rng = (price - lo) / (hi - lo) * 100
        pos = f" current price {price:.2f} ({frm_hi:+.1f}% vs 52w high, {pct_rng:.0f}% of 52w range);"
    txt = (
        f"{symbol} ({s.get('name') or symbol}) — sector: {s.get('sector') or 'n/a'};{pos} "
        f"market cap {_fmt_mcap(s.get('market_cap'))}; trailing P/E {n(s.get('pe'))}; "
        f"forward P/E {n(s.get('forward_pe'))}; P/B {n(s.get('pb'))}; EPS {n(s.get('eps'))}; "
        f"beta {n(s.get('beta'))}; ROE {('n/a' if roe is None else f'{roe * 100:.1f}%')}; "
        f"div yield {_fmt_div_yield(s.get('dividend_yield'))}; "
        f"52w range {n(lo)}–{n(hi)}."
    )
    return txt, {"symbol": symbol, **{k: s.get(k) for k in
                 ("name", "sector", "market_cap", "pe", "forward_pe", "pb", "eps", "beta", "roe")}}


def get_news(ctx: ToolContext, symbol: str, limit: int = 6, asset: str | None = None) -> tuple[str, dict]:
    """Top-ranked recent headlines with LLM sentiment for the symbol."""
    items = nr.news(symbol, limit=None)
    if not items:
        return f"{symbol}: no recent headlines found in the active news sources.", {"symbol": symbol}
    nr.apply_scores(ctx.llm, symbol, items)
    ranked = nr.rank_news(items, symbol)[: max(1, min(int(limit or 6), 12))]
    lines = []
    for it in ranked:
        sent = it.get("sentiment") or "?"
        src = it.get("source") or "?"
        lines.append(f"- [{sent}] {it['title']} ({src})")
    txt = f"{symbol} recent headlines (ranked, with sentiment):\n" + "\n".join(lines)
    return txt, {"symbol": symbol, "count": len(ranked),
                 "headlines": [it["title"] for it in ranked]}


def screen(
    ctx: ToolContext, factor: str = "momentum", universe: str = "dow30", limit: int = 10, **_: object
) -> tuple[str, dict]:
    """Rank a universe by a factor and return the top names. Validates factor/universe."""
    universes = list_universes() or ["dow30"]
    if factor not in fac.CATALOG:
        factor = "momentum"
    if universe not in universes:
        universe = "dow30" if "dow30" in universes else universes[0]
    limit = max(1, min(int(limit or 10), 25))
    res = scr.run_factor_screen(ctx.dm, ctx.fstore, ctx.fprov, universe, factor, limit)
    rows = res.get("results", [])
    if not rows:
        return f"Screen {factor} on {universe}: no results (insufficient data).", res
    label = fac.CATALOG[factor]["label"]
    lines = [
        f"{i + 1}. {r['symbol']} (score {r['score']}, 20d {r.get('ret_20d')}%)"
        for i, r in enumerate(rows)
    ]
    txt = f"Top {len(rows)} by {label} in {universe}:\n" + "\n".join(lines)
    return txt, {"factor": factor, "universe": universe,
                 "top": [r["symbol"] for r in rows]}


def compare(
    ctx: ToolContext, symbols: list[str] | None = None, period: str = "3m",
    start: str | None = None, end: str | None = None, **_: object
) -> tuple[str, dict]:
    """Compare price performance of several symbols over a trailing period OR an explicit start/end
    date window (same window semantics as get_performance, so 'this year' / a date span works)."""
    syms = [s for s in (symbols or []) if s][:6]
    if ctx.symbol and ctx.symbol not in syms:
        syms = [ctx.symbol, *syms][:6]
    if len(syms) < 2:
        return "compare needs at least two symbols.", {"symbols": syms}
    rows = []
    label = ""
    for s in syms:
        _, d = get_performance(ctx, s, period, start=start, end=end)
        rows.append((s, d.get("return_pct")))
        label = d.get("period", period) or period  # the realized window label
    rows.sort(key=lambda r: (r[1] is None, -(r[1] or 0)))
    lines = [f"{s}: {('n/a' if r is None else f'{r:+.2f}%')}" for s, r in rows]
    txt = f"{label} performance comparison (best first):\n" + "\n".join(lines)
    return txt, {"period": label, "results": dict(rows)}


def search_symbols(ctx: ToolContext, query: str | None = None, **_: object) -> tuple[str, dict]:
    """Resolve a name/ticker fragment to real symbols in the terminal's universes (+ EDGAR listings).

    Matches a ticker substring or company name. Useful for 'what's the ticker for X' / 'find the
    <sector> names'. Returns symbol, sector and which universe it's in.
    """
    q = (query or "").strip()
    if not q:
        return "search_symbols needs a query (a name or ticker fragment).", {"query": q}
    hits = universe_search(q, limit=12)
    if not hits:
        return (f"No symbol in the terminal's universes matches '{q}'. "
                "(I can still answer from general knowledge if you name the company.)"), {"query": q, "hits": []}
    lines = [
        f"- {h['symbol']} ({h.get('name') or h.get('sector') or h.get('universe') or ''})".rstrip(" ()")
        for h in hits
    ]
    txt = f"Symbols matching '{q}':\n" + "\n".join(lines)
    return txt, {"query": q, "hits": [h["symbol"] for h in hits]}


def get_risk_attribution(ctx: ToolContext, source: str | None = None, **_: object) -> tuple[str, dict]:
    """Decompose the tracked portfolio's forecast risk into Barra factors vs stock-specific, plus the
    top risk-contributing positions. ``source``: 'holdings' (default) or 'paper'. Equity-only."""
    from app.services import risk_attribution as ra

    src = (source or "holdings").lower()
    src = src if src in ("holdings", "paper") else "holdings"
    if src == "paper":
        from app.deps import get_broker

        positions = [
            {"symbol": s, "asset": "crypto" if "/" in s else "equity", "quantity": float(p.quantity)}
            for s, p in get_broker().get_positions().items()
        ]
    else:
        positions = [
            {"symbol": h["symbol"], "asset": h.get("asset", "equity"), "quantity": float(h["quantity"])}
            for h in (ctx.store.list_holdings() if ctx.store else [])
        ]
    if not positions:
        return f"No {src} positions to attribute.", {"source": src}

    res = ra.compute_attribution(positions, source=src)
    if res.get("insufficient"):
        return f"Risk attribution unavailable: {res.get('reason', 'insufficient data')}.", res

    top_f = ", ".join(f"{f['factor']} {f['pct_total'] * 100:+.0f}%" for f in res["factors"][:3])
    top_p = ", ".join(f"{p['symbol']} {p['pct'] * 100:.0f}%" for p in res["positions"][:3])
    txt = (
        f"{src} portfolio risk ({res['n']} names, as of {res['as_of']}): forecast vol "
        f"{res['total_vol']:.1f}% — {res['pct_factor'] * 100:.0f}% factor / "
        f"{(1 - res['pct_factor']) * 100:.0f}% stock-specific. "
        f"Top factor drivers: {top_f}. Top position risk: {top_p}."
    )
    if res.get("skipped"):
        txt += f" Skipped {len(res['skipped'])} (non-equity / no model coverage)."
    return txt, res


# Stable registry consumed by the agent's planner schema + dispatcher.
TOOLS: dict[str, dict] = {
    "get_quote": {
        "fn": get_quote,
        "desc": "Latest price and day change for a symbol (equity EOD or crypto live-ish).",
        "args": "symbol, asset?",
    },
    "get_performance": {
        "fn": get_performance,
        "desc": "Total return over a period (1w/2w/1m/3m/6m/1y/ytd) for a symbol.",
        "args": "symbol, period?, asset?",
    },
    "get_fundamentals": {
        "fn": get_fundamentals,
        "desc": "Valuation/quality snapshot: market cap, P/E, P/B, EPS, beta, ROE, dividend, 52w range (equities only).",
        "args": "symbol",
    },
    "get_news": {
        "fn": get_news,
        "desc": "Recent ranked headlines with sentiment for a symbol.",
        "args": "symbol, limit?",
    },
    "screen": {
        "fn": screen,
        "desc": (
            "Rank a universe by a factor; returns the top names. "
            f"factors: {', '.join(fac.CATALOG)}. universes from the terminal's list (e.g. dow30, sp500, nasdaq100)."
        ),
        "args": "factor, universe, limit?",
    },
    "compare": {
        "fn": compare,
        "desc": "Compare price performance of 2-6 symbols over a period.",
        "args": "symbols[], period?",
    },
    "search_symbols": {
        "fn": search_symbols,
        "desc": "Resolve a company name or ticker fragment to real symbols in the terminal's universes.",
        "args": "query",
    },
    "get_risk_attribution": {
        "fn": get_risk_attribution,
        "desc": (
            "Decompose the tracked portfolio's forecast risk into Barra factors vs stock-specific "
            "and list the top risk-contributing positions. source: holdings (default) or paper."
        ),
        "args": "source?",
    },
}


def capabilities_text() -> str:
    """Honest, grounded description of what the assistant can actually do (for 'what can you do')."""
    lines = [f"- {name}: {meta['desc']}" for name, meta in TOOLS.items()]
    return (
        "I'm grounded in this terminal's live data and can fetch, before answering:\n"
        + "\n".join(lines)
        + "\nI can also reason about general finance/quant concepts. I do NOT have earnings-call "
        "transcripts, SEC filing text, or analyst price targets, and equity prices are EOD/delayed."
    )


def _as_symbol_list(v: object) -> list[str]:
    """Coerce a planner value into a clean ticker list. Handles a real list, a comma string, or a
    JSON array accidentally encoded as a string ('[\"JPM\", \"BAC\"]')."""
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                v = json.loads(s)
            except Exception:  # noqa: BLE001
                v = [s]
        else:
            v = [s]
    out: list[str] = []
    for item in (v if isinstance(v, list) else [v]):
        out += [p.strip().upper() for p in str(item).split(",") if p.strip()]
    return out


def _repair_args(name: str, a: dict, ctx: ToolContext) -> dict:
    """Defend against the planner misrouting args (factor/universe stuffed into `symbols`, several
    tickers packed into one comma/JSON string, query in `symbol`, etc.) so a slightly-off plan still
    runs correctly."""
    universes = set(list_universes())
    if name == "compare":
        syms = _as_symbol_list(a.get("symbols")) + _as_symbol_list(a.get("symbol"))
        a["symbols"] = list(dict.fromkeys(syms))  # de-dupe, preserve order
        a.pop("symbol", None)
    elif name == "search_symbols":
        if not a.get("query"):
            a["query"] = a.get("symbol") or a.get("universe")
        a.pop("symbol", None)
    elif name == "screen":
        pool = list(a.get("symbols") or [])
        for s in (a.get("symbol"), a.get("factor"), a.get("universe")):
            if isinstance(s, str):
                pool.append(s)
        for v in pool:
            vl = str(v).strip().lower()
            if vl in fac.CATALOG and not a.get("factor"):
                a["factor"] = vl
            elif vl in universes and not a.get("universe"):
                a["universe"] = vl
        a.pop("symbols", None)
        a.pop("symbol", None)
    return a


def run_tool(name: str, ctx: ToolContext, args: dict) -> tuple[str, dict]:
    """Dispatch a planned tool call, filling defaults from the context. Never raises — returns an
    error string the LLM can react to."""
    meta = TOOLS.get(name)
    if not meta:
        return f"(unknown tool '{name}')", {}
    a = _repair_args(name, dict(args or {}), ctx)
    a.setdefault("symbol", ctx.symbol)
    a.setdefault("asset", ctx.asset)
    try:
        # Only pass kwargs the tool understands (functions ignore extras via **_ where relevant).
        return meta["fn"](ctx, **{k: v for k, v in a.items() if v is not None})
    except TypeError:
        # Retry with just the canonical args if the planner emitted spurious keys.
        try:
            return meta["fn"](ctx, **_clean_args(name, a))
        except Exception as e:  # noqa: BLE001
            return f"({name} failed: {type(e).__name__})", {}
    except Exception as e:  # noqa: BLE001 - a flaky fetch shouldn't kill the chat
        return f"({name} failed: {type(e).__name__})", {}


def _clean_args(name: str, a: dict) -> dict:
    keep = {
        "get_quote": ("symbol", "asset"),
        "get_performance": ("symbol", "period", "asset", "start", "end"),
        "get_fundamentals": ("symbol", "asset"),
        "get_news": ("symbol", "limit", "asset"),
        "screen": ("factor", "universe", "limit"),
        "compare": ("symbols", "period", "start", "end"),
        "search_symbols": ("query",),
    }.get(name, ("symbol",))
    return {k: a[k] for k in keep if k in a and a[k] is not None}
