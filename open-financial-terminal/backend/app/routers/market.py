"""Market data: symbol search, OHLCV bars (+ indicators), quotes, universe listing."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from qhfi.data.manager import DataManager

from app.deps import get_data_manager
from app.indicators import compute_spec
from app.services import market as mkt
from app.services import universe as uni

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 20) -> dict:
    return {"results": uni.search(q, limit)}


@router.get("/universes")
def universes() -> dict:
    return {"universes": uni.list_universes()}


@router.get("/universe/{name}")
def universe(name: str) -> dict:
    try:
        u = uni.get_universe(name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None
    return {
        "name": u.name,
        "instruments": [
            {"symbol": i.id, "asset": i.asset_class.value, "sector": i.sector} for i in u.instruments
        ],
    }


@router.get("/bars")
def bars(
    symbol: str,
    asset: str = "equity",
    timeframe: str = Query("1d", description="1m, 5m, 15m, 1h or 1d"),
    start: date | None = None,
    end: date | None = None,
    indicators: str | None = Query(None, description="comma list, e.g. sma:20,rsi:14,macd"),
    dm: DataManager = Depends(get_data_manager),
) -> dict:
    intraday = timeframe != "1d"
    if intraday:
        try:
            instrument, frame = mkt.fetch_bars_intraday(symbol, asset, timeframe)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    else:
        instrument, frame = mkt.fetch_bars(dm, symbol, asset, start, end)
    if frame.empty:
        raise HTTPException(404, f"no data for {symbol} ({asset}, {timeframe})")

    payload = mkt.to_candles(frame, intraday=intraday)
    payload["symbol"] = instrument.id
    payload["asset"] = instrument.asset_class.value
    payload["timeframe"] = timeframe
    payload["quote"] = mkt.quote_from_bars(frame)

    overlays = []
    if indicators:
        for spec in (s.strip() for s in indicators.split(",") if s.strip()):
            try:
                overlays.append(compute_spec(frame["close"], spec, intraday=intraday))
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
    payload["indicators"] = overlays
    return payload


@router.get("/quote")
def quote(
    symbol: str,
    asset: str = "equity",
    spark: int = Query(0, ge=0, le=120, description="include last N daily closes for sparklines"),
    dm: DataManager = Depends(get_data_manager),
) -> dict:
    instrument, frame = mkt.fetch_bars(dm, symbol, asset)
    if frame.empty:
        raise HTTPException(404, f"no data for {symbol} ({asset})")
    payload = {"symbol": instrument.id, "asset": instrument.asset_class.value, **mkt.quote_from_bars(frame)}
    if spark:
        payload["spark"] = [round(float(v), 6) for v in frame["close"].tail(spark)]
    return payload
