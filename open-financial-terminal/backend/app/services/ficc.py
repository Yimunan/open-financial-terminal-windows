"""Curated FICC board catalog — the rates / FX / commodity complexes the DataManager now serves.

Pure metadata (display ``name`` + the clean ``symbol`` and ``asset`` the client passes to
``/api/quote``). Unlike the Market Board (which routes yfinance =F/=X tickers through the equity
provider), every row here uses the instrument's NATIVE asset class — ``rates`` / ``fx`` /
``commodity`` — so quotes flow through the dedicated DataManager providers and the qhfi-lake
fallback. Curated server-side (like the Market Board and universes) so the list retunes without a
frontend build.
"""

from __future__ import annotations

#: Ordered sections; each row is {symbol (clean id), name, asset (native class)}.
_CATALOG: list[dict] = [
    {
        "key": "rates",
        "label": "Rates",
        "items": [
            {"symbol": "ZQ", "name": "30D Fed Funds", "asset": "rates"},
            {"symbol": "ZT", "name": "2Y T-Note", "asset": "rates"},
            {"symbol": "ZF", "name": "5Y T-Note", "asset": "rates"},
            {"symbol": "ZN", "name": "10Y T-Note", "asset": "rates"},
            {"symbol": "ZB", "name": "30Y T-Bond", "asset": "rates"},
            {"symbol": "UB", "name": "Ultra Bond", "asset": "rates"},
        ],
    },
    {
        "key": "fx",
        "label": "FX",
        "items": [
            {"symbol": "EUR/USD", "name": "Euro", "asset": "fx"},
            {"symbol": "USD/JPY", "name": "Japanese Yen", "asset": "fx"},
            {"symbol": "GBP/USD", "name": "British Pound", "asset": "fx"},
            {"symbol": "USD/CHF", "name": "Swiss Franc", "asset": "fx"},
            {"symbol": "USD/CAD", "name": "Canadian Dollar", "asset": "fx"},
            {"symbol": "AUD/USD", "name": "Australian Dollar", "asset": "fx"},
            {"symbol": "NZD/USD", "name": "NZ Dollar", "asset": "fx"},
            {"symbol": "EUR/JPY", "name": "Euro / Yen", "asset": "fx"},
            {"symbol": "EUR/GBP", "name": "Euro / Sterling", "asset": "fx"},
            {"symbol": "EUR/CHF", "name": "Euro / Franc", "asset": "fx"},
            {"symbol": "GBP/JPY", "name": "Sterling / Yen", "asset": "fx"},
            {"symbol": "AUD/JPY", "name": "Aussie / Yen", "asset": "fx"},
        ],
    },
    {
        "key": "metals",
        "label": "Metals",
        "items": [
            {"symbol": "GC", "name": "Gold", "asset": "commodity"},
            {"symbol": "SI", "name": "Silver", "asset": "commodity"},
            {"symbol": "HG", "name": "Copper", "asset": "commodity"},
            {"symbol": "PL", "name": "Platinum", "asset": "commodity"},
            {"symbol": "PA", "name": "Palladium", "asset": "commodity"},
        ],
    },
    {
        "key": "energy",
        "label": "Energy",
        "items": [
            {"symbol": "CL", "name": "WTI Crude", "asset": "commodity"},
            {"symbol": "BZ", "name": "Brent Crude", "asset": "commodity"},
            {"symbol": "NG", "name": "Natural Gas", "asset": "commodity"},
            {"symbol": "RB", "name": "Gasoline", "asset": "commodity"},
            {"symbol": "HO", "name": "Heating Oil", "asset": "commodity"},
        ],
    },
    {
        "key": "ags",
        "label": "Agriculture",
        "items": [
            {"symbol": "ZC", "name": "Corn", "asset": "commodity"},
            {"symbol": "ZW", "name": "Wheat", "asset": "commodity"},
            {"symbol": "ZS", "name": "Soybeans", "asset": "commodity"},
            {"symbol": "KC", "name": "Coffee", "asset": "commodity"},
            {"symbol": "SB", "name": "Sugar #11", "asset": "commodity"},
            {"symbol": "CT", "name": "Cotton", "asset": "commodity"},
            {"symbol": "CC", "name": "Cocoa", "asset": "commodity"},
            {"symbol": "LE", "name": "Live Cattle", "asset": "commodity"},
            {"symbol": "HE", "name": "Lean Hogs", "asset": "commodity"},
        ],
    },
]


def ficc_board() -> list[dict]:
    """The curated FICC board sections (metadata only; quotes fetched per-row by the client)."""
    return _CATALOG
