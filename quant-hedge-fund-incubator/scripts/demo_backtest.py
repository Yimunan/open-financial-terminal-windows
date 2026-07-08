"""End-to-end smoke run: real data → momentum factor → long/short strategy → granular
engine → walk-forward OOS → scorecard.

  .venv\\Scripts\\python.exe scripts\\demo_backtest.py            # crypto (ccxt)
  .venv\\Scripts\\python.exe scripts\\demo_backtest.py equity     # US equities (yfinance), sector-neutral

If the network is unreachable, falls back to a synthetic panel (clearly labelled).
"""

from __future__ import annotations

import sys
from datetime import date

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252

from qhfi.backtest.engine import BacktestEngine
from qhfi.backtest.validation import WalkForwardConfig, concat_oos, walk_forward
from qhfi.core.types import AssetClass, DateRange, EquityMeta, Instrument, Universe
from qhfi.data.providers.crypto_ccxt import CcxtDataProvider
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider
from qhfi.evaluation import metrics
from qhfi.evaluation.scorecard import Scorecard
from qhfi.factors.library import MomentumFactor
from qhfi.strategy.library.factor_strategy import FactorStrategy, FactorStrategyParams

SPAN = DateRange(start=date(2024, 6, 1), end=date(2026, 6, 1))

# crypto
CRYPTO_BASES = ["BTC", "ETH", "SOL", "LTC", "ADA"]
EXCHANGES = [("binance", "USDT"), ("kraken", "USD"), ("coinbase", "USD")]

# equities, grouped by GICS sector (for sector-neutral momentum)
EQUITIES = {
    "InfoTech": ["AAPL", "MSFT", "NVDA"], "Energy": ["XOM", "CVX"],
    "Financials": ["JPM", "BAC"], "HealthCare": ["JNJ", "PFE"], "Staples": ["PG", "KO"],
}


def pull_crypto():
    for exch, quote in EXCHANGES:
        provider = CcxtDataProvider(exchange=exch)
        cols, instruments = {}, []
        try:
            for base in CRYPTO_BASES:
                sym = f"{base}/{quote}"
                bars = provider.fetch_daily(Instrument(id=sym, asset_class=AssetClass.CRYPTO), SPAN)
                if len(bars) < 200:
                    continue
                cols[sym] = bars["close"]
                instruments.append(Instrument(id=sym, asset_class=AssetClass.CRYPTO, exchange=exch))
            if len(cols) >= 3:
                return pd.DataFrame(cols).sort_index(), Universe(name=f"crypto@{exch}", instruments=instruments), exch, None
        except Exception as e:  # noqa: BLE001
            print(f"  {exch}: unavailable ({type(e).__name__})")
    return None


def pull_equities():
    provider = YFinanceDataProvider()
    cols, instruments = {}, []
    try:
        for sector, tickers in EQUITIES.items():
            for tk in tickers:
                bars = provider.fetch_daily(Instrument(id=tk, asset_class=AssetClass.EQUITY), SPAN)
                if len(bars) < 200:
                    continue
                cols[tk] = bars["close"]
                instruments.append(Instrument(id=tk, asset_class=AssetClass.EQUITY,
                                               equity=EquityMeta(gics_sector=sector)))
        if len(cols) >= 6:
            uni = Universe(name="us_equities", instruments=instruments)
            return pd.DataFrame(cols).sort_index(), uni, "yfinance", uni.groups("gics_sector")
    except Exception as e:  # noqa: BLE001
        print(f"  yfinance: unavailable ({type(e).__name__})")
    return None


def synthetic(equity: bool):
    idx = pd.date_range(SPAN.start, SPAN.end, freq="D", tz="UTC")
    rng = np.random.default_rng(7)
    cols, instruments = {}, []
    names = [t for ts in EQUITIES.values() for t in ts] if equity else [f"{b}/USDT" for b in CRYPTO_BASES]
    sectors = ([s for s, ts in EQUITIES.items() for _ in ts] if equity else [None] * len(names))
    for i, (name, sec) in enumerate(zip(names, sectors)):
        rets = rng.normal((i - len(names) / 2) * 0.0004, 0.02, len(idx))
        cols[name] = 100 * np.cumprod(1 + rets)
        meta = EquityMeta(gics_sector=sec) if sec else None
        ac = AssetClass.EQUITY if equity else AssetClass.CRYPTO
        instruments.append(Instrument(id=name, asset_class=ac, equity=meta))
    uni = Universe(name="synthetic", instruments=instruments)
    groups = uni.groups("gics_sector") if equity else None
    return pd.DataFrame(cols, index=idx), uni, "SYNTHETIC", groups


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "crypto"
    equity = mode == "equity"
    ppy = 252 if equity else 365

    print(f"Pulling daily bars ({mode})…")
    got = pull_equities() if equity else pull_crypto()
    if got is None:
        print("  no source reachable → using synthetic data")
        got = synthetic(equity)
    prices, universe, source, sectors = got

    print(f"\nSource: {source}   |   {prices.shape[1]} instruments   |   "
          f"{prices.index.min().date()} → {prices.index.max().date()} ({len(prices)} days)")
    print("Universe:", ", ".join(universe.ids))
    if sectors:
        print("Sector-neutral:", {s for s in sectors.values()})

    strat = FactorStrategy([MomentumFactor()], sectors=sectors,
                           params=FactorStrategyParams(quantile=0.3, gross=1.0))
    weights = strat.generate_weights(prices, universe)
    result = BacktestEngine().run(weights, prices, universe)

    summ = metrics.summary(result.returns, periods_per_year=ppy)
    folds = walk_forward(strat, prices, universe, BacktestEngine(),
                         WalkForwardConfig(train_days=252, test_days=90, step_days=90, purge_days=5))
    oos = concat_oos(folds)
    card = Scorecard().grade(result, oos_returns=oos, periods_per_year=ppy)

    tag = "sector-neutral " if sectors else ""
    print(f"\n── Backtest ({tag}momentum L/S · 10bps fee · 5bps slip · borrow/financing) ──")
    print(f"  final equity : {result.equity_curve.iloc[-1]:,.0f}  (start {result.meta['initial_equity']:,.0f})")
    print(f"  total return : {result.equity_curve.iloc[-1] / result.meta['initial_equity'] - 1:+.1%}")
    print(f"  CAGR         : {summ['cagr']:+.1%}")
    print(f"  Sharpe       : {summ['sharpe']:.2f}")
    print(f"  max drawdown : {summ['max_drawdown']:.1%}")
    print(f"  ann turnover : {card.metrics['ann_turnover']:.1f}x")
    print(f"  cost drag    : {result.costs.sum():,.0f}")
    print(f"  trades       : {len(result.trades)}")

    if len(oos):
        oos_sharpe = metrics.sharpe(oos, periods_per_year=ppy)
        print(f"\n── Walk-forward OOS ({len(folds)} folds · {len(oos)} OOS days · "
              f"{oos.index.min().date()} → {oos.index.max().date()}) ──")
        print(f"  in-sample Sharpe : {summ['sharpe']:.2f}")
        print(f"  OOS Sharpe       : {oos_sharpe:.2f}")
        if summ["sharpe"] > 0:
            print(f"  OOS/IS ratio     : {oos_sharpe / summ['sharpe']:.2f}   "
                  f"(scorecard floor {Scorecard().t.min_oos_sharpe_ratio})")
        else:
            print("  OOS/IS ratio     : n/a (in-sample Sharpe ≤ 0)")

    print(f"\n  Scorecard    : {'PASS ✓' if card.passed else 'FAIL ✗'}  {card.checks}")
    for note in card.notes:
        print(f"    note: {note}")


if __name__ == "__main__":
    main()
