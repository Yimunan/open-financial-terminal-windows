"""Asset-class-agnostic domain types — the vocabulary every layer above data speaks.

Strategies, the backtest engine, portfolio construction, and execution all operate on
``Instrument`` + normalized ``Bars`` + ``TargetWeights``. Asset-class differences
(corporate actions, contract rolls, fees, calendars, funding) are resolved *below* this
line, in the data providers, cost models, sizing models, and the engine's accounting.

The taxonomy is **two orthogonal axes** — what the instrument is *on* (``AssetClass``) and
*how it is wrapped* (``InstrumentForm``). A Bund future and an S&P future share a form but
not a class; spot BTC and a BTC perp share a class but not a form. From these two axes the
model derives the things the engine actually branches on: the **funding type** (cash vs
margin) and the **risk basis** (notional vs DV01).
"""

from __future__ import annotations

from datetime import date
from enum import Enum

import pandas as pd
from pydantic import BaseModel, Field

# ── Type aliases (documented intent for plain pandas objects) ──────────────────
# Per-instrument daily OHLCV: UTC DatetimeIndex, columns {open,high,low,close,volume}.
Bars = pd.DataFrame
# Wide frame: index = dates (UTC), columns = instrument ids, values = one field.
Panel = pd.DataFrame
# Wide frame: index = dates, columns = instrument ids. For NOTIONAL instruments a cell is
# the fraction of book equity; for DV01 instruments it is the fraction of the DV01 risk
# budget. Sign encodes direction. The sizing model interprets the cell per-instrument.
TargetWeights = pd.DataFrame


class AssetClass(str, Enum):
    """What the instrument's value derives from."""

    EQUITY = "equity"
    RATES = "rates"          # govies, IRS, rate futures — sized by DV01
    CREDIT = "credit"        # corporate bonds, CDS — sized by DV01 (+ spread risk)
    FX = "fx"                # currency pairs
    COMMODITY = "commodity"  # energy / metals / ags (usually via futures)
    CRYPTO = "crypto"


class InstrumentForm(str, Enum):
    """How the exposure is wrapped — drives funding (cash vs margin) and expiry/roll."""

    CASH = "cash"        # spot / outright bond / spot FX — fully funded
    ETF = "etf"          # fully funded basket
    FUTURE = "future"    # margined, dated, rolled
    PERP = "perp"        # margined, perpetual (crypto), funding-rate carry
    FORWARD = "forward"  # margined OTC (FX forwards)
    SWAP = "swap"        # margined OTC (IRS, total-return)


class RiskBasis(str, Enum):
    NOTIONAL = "notional"   # size by fraction of equity
    DV01 = "dv01"           # size by interest-rate risk (rates / credit)


_MARGINED_FORMS = {InstrumentForm.FUTURE, InstrumentForm.PERP, InstrumentForm.FORWARD, InstrumentForm.SWAP}
_DV01_CLASSES = {AssetClass.RATES, AssetClass.CREDIT}
_CALENDARS = {
    AssetClass.CRYPTO: "24/7",
    AssetClass.FX: "24/5",
    AssetClass.EQUITY: "XNYS",
    AssetClass.RATES: "XNYS",      # TODO: SIFMA/bond calendar
    AssetClass.CREDIT: "XNYS",
    AssetClass.COMMODITY: "CMES",
}


class EquityMeta(BaseModel):
    """Equity-specific classification used by cross-sectional equity strategies — GICS
    grouping (for neutralization), size/liquidity (for segmentation & capacity), and index
    membership (for universe definition). Absent for non-equity instruments.

    NOTE: ``index_membership`` is *current* membership; point-in-time membership (to avoid
    survivorship bias when defining 'S&P 500 as of date t') is a separate, dated dataset —
    a known gap, tracked in ARCHITECTURE.md.
    """

    gics_sector: str | None = None
    gics_industry_group: str | None = None
    gics_industry: str | None = None
    country: str = "US"
    market_cap: float | None = Field(None, description="in quote currency")
    adv_20d: float | None = Field(None, description="20d avg daily $ volume — capacity/liquidity")
    free_float: float | None = None
    index_membership: list[str] = Field(default_factory=list)


class Instrument(BaseModel):
    """A tradable instrument keyed by ``id`` across panels, weights, positions, registry."""

    id: str = Field(..., description="Canonical id, e.g. 'AAPL', 'BTC/USDT', 'EUR/USD', 'ES', 'ZN'")
    asset_class: AssetClass
    form: InstrumentForm = InstrumentForm.CASH
    exchange: str = Field("", description="ccxt exchange, MIC, futures venue, or 'OTC'")
    quote_currency: str = "USD"
    base_currency: str | None = Field(None, description="for FX pairs, e.g. EUR in EUR/USD")
    equity: EquityMeta | None = Field(None, description="equity classification (equities only)")

    # Microstructure / sizing.
    tick_size: float = 0.01
    lot_size: float = 1.0
    contract_multiplier: float = Field(1.0, description="point value; >1 for futures (ES=50)")
    shortable: bool = True

    # Fixed-income / rates specifics (None for non-FI).
    coupon: float | None = None
    maturity: date | None = None
    modified_duration: float | None = Field(None, description="years; DV01 = md·price·mult/1e4")
    day_count: str = "ACT/365"

    # Margin (for margined forms). None → engine uses a default fraction.
    initial_margin: float | None = None
    maint_margin: float | None = None

    # Optional explicit funding override; otherwise derived from `form`.
    funding: str | None = Field(None, description="'cash' | 'margin' to override the form default")

    @property
    def is_margined(self) -> bool:
        if self.funding is not None:
            return self.funding == "margin"
        return self.form in _MARGINED_FORMS

    @property
    def risk_basis(self) -> RiskBasis:
        return RiskBasis.DV01 if self.asset_class in _DV01_CLASSES else RiskBasis.NOTIONAL

    @property
    def calendar_name(self) -> str:
        return _CALENDARS.get(self.asset_class, "XNYS")

    @property
    def sector(self) -> str | None:
        """GICS sector shortcut (None for non-equity or unclassified)."""
        return self.equity.gics_sector if self.equity else None


class Universe(BaseModel):
    """A named set of instruments a strategy trades over."""

    name: str
    instruments: list[Instrument]

    @property
    def ids(self) -> list[str]:
        return [i.id for i in self.instruments]

    def by_id(self, instrument_id: str) -> Instrument:
        for i in self.instruments:
            if i.id == instrument_id:
                return i
        raise KeyError(instrument_id)

    def groups(self, level: str = "gics_sector") -> dict[str, str]:
        """Map instrument_id → classification label at ``level`` (a field on EquityMeta,
        e.g. 'gics_sector', 'gics_industry', 'country'). Unclassified instruments map to
        '__none__'. Feeds factors.transforms.neutralize() for sector-neutral signals."""
        out: dict[str, str] = {}
        for i in self.instruments:
            val = getattr(i.equity, level, None) if i.equity else None
            out[i.id] = val or "__none__"
        return out


class DateRange(BaseModel):
    start: date
    end: date

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.start}..{self.end}"
