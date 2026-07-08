"""Curated multi-asset 'Market Board' catalog — global indices, commodities, bonds, FX, crypto.

Pure metadata: each item is a display ``name`` plus the *fetch* ``symbol`` the frontend passes
to ``/api/quote``. Indices / commodities / bonds / FX are yfinance tickers fetched as
``asset="equity"`` — they all route through the YFinanceDataProvider, which resolves
``^GSPC`` / ``GC=F`` / ``EURUSD=X`` / ``^TNX`` by ticker, so no new data provider is needed.
Crypto pairs are fetched as ``asset="crypto"`` (CCXT/Kraken) and additionally stream live from
the realtime hub. Curated here, server-side, so the symbol list is retuned without a frontend
release — mirroring how universes and the watchlist already live on the server.

Bonds intentionally mixes yield indices (``^TNX`` is the 10Y *yield*, not a price) with tradable
bond ETFs so the tab shows both the rates curve and investable proxies.
"""

from __future__ import annotations

#: Ordered sections; each row is (fetch symbol, display name, asset for /api/quote).
_CATALOG: list[dict] = [
    {
        "key": "indices",
        "label": "Equities",
        "items": [
            {"symbol": "^GSPC", "name": "S&P 500", "asset": "equity"},
            {"symbol": "^IXIC", "name": "Nasdaq Composite", "asset": "equity"},
            {"symbol": "^DJI", "name": "Dow Jones", "asset": "equity"},
            {"symbol": "^RUT", "name": "Russell 2000", "asset": "equity"},
            {"symbol": "^VIX", "name": "CBOE Volatility", "asset": "equity"},
            {"symbol": "^FTSE", "name": "FTSE 100", "asset": "equity"},
            {"symbol": "^GDAXI", "name": "DAX", "asset": "equity"},
            {"symbol": "^STOXX50E", "name": "Euro Stoxx 50", "asset": "equity"},
            {"symbol": "^N225", "name": "Nikkei 225", "asset": "equity"},
            {"symbol": "^HSI", "name": "Hang Seng", "asset": "equity"},
            {"symbol": "000001.SS", "name": "SSE Composite", "asset": "equity"},
        ],
    },
    {
        "key": "commodities",
        "label": "Commodities",
        "items": [
            {"symbol": "GC=F", "name": "Gold", "asset": "equity"},
            {"symbol": "SI=F", "name": "Silver", "asset": "equity"},
            {"symbol": "HG=F", "name": "Copper", "asset": "equity"},
            {"symbol": "CL=F", "name": "WTI Crude", "asset": "equity"},
            {"symbol": "BZ=F", "name": "Brent Crude", "asset": "equity"},
            {"symbol": "NG=F", "name": "Natural Gas", "asset": "equity"},
            {"symbol": "ZC=F", "name": "Corn", "asset": "equity"},
            {"symbol": "ZW=F", "name": "Wheat", "asset": "equity"},
            {"symbol": "ZS=F", "name": "Soybeans", "asset": "equity"},
        ],
    },
    {
        "key": "bonds",
        "label": "Bonds & Rates",
        "items": [
            {"symbol": "^IRX", "name": "US 13-Week Yield", "asset": "equity"},
            {"symbol": "^FVX", "name": "US 5-Year Yield", "asset": "equity"},
            {"symbol": "^TNX", "name": "US 10-Year Yield", "asset": "equity"},
            {"symbol": "^TYX", "name": "US 30-Year Yield", "asset": "equity"},
            {"symbol": "SHY", "name": "1-3Y Treasury ETF", "asset": "equity"},
            {"symbol": "IEF", "name": "7-10Y Treasury ETF", "asset": "equity"},
            {"symbol": "TLT", "name": "20+Y Treasury ETF", "asset": "equity"},
            {"symbol": "AGG", "name": "US Aggregate Bond ETF", "asset": "equity"},
            {"symbol": "LQD", "name": "IG Corporate ETF", "asset": "equity"},
            {"symbol": "HYG", "name": "High Yield ETF", "asset": "equity"},
            {"symbol": "TIP", "name": "TIPS ETF", "asset": "equity"},
        ],
    },
    {
        "key": "fx",
        "label": "FX",
        "items": [
            {"symbol": "DX-Y.NYB", "name": "US Dollar Index", "asset": "equity"},
            {"symbol": "EURUSD=X", "name": "EUR / USD", "asset": "equity"},
            {"symbol": "USDJPY=X", "name": "USD / JPY", "asset": "equity"},
            {"symbol": "GBPUSD=X", "name": "GBP / USD", "asset": "equity"},
            {"symbol": "USDCHF=X", "name": "USD / CHF", "asset": "equity"},
            {"symbol": "USDCAD=X", "name": "USD / CAD", "asset": "equity"},
            {"symbol": "AUDUSD=X", "name": "AUD / USD", "asset": "equity"},
            {"symbol": "NZDUSD=X", "name": "NZD / USD", "asset": "equity"},
        ],
    },
    {
        "key": "crypto",
        "label": "Crypto",
        "items": [
            {"symbol": "BTC/USD", "name": "Bitcoin", "asset": "crypto"},
            {"symbol": "ETH/USD", "name": "Ethereum", "asset": "crypto"},
            {"symbol": "SOL/USD", "name": "Solana", "asset": "crypto"},
            {"symbol": "XRP/USD", "name": "XRP", "asset": "crypto"},
        ],
    },
]


def board_catalog() -> list[dict]:
    """The curated board sections (metadata only; quotes are fetched per-row by the client)."""
    return _CATALOG
