"""Pull news into the lake under news/ (raw capture, no sentiment).

  Equity (per-ticker): Alpaca (Benzinga, historical to 2015, backtest-grade) if ALPACA_API_KEY /
  ALPACA_API_SECRET are set; otherwise a keyless yfinance recent-headlines bootstrap.
  Macro (general):     GDELT article lists per seed query.

  .venv\\Scripts\\python.exe scripts\\pull_news.py            # ~5y equity + macro
  .venv\\Scripts\\python.exe scripts\\pull_news.py 2          # ~2y equity lookback
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.data.lake import lake_root
from qhfi.data.news import NewsStore
from qhfi.data.providers.gdelt import QUERIES, GdeltProvider
from qhfi.data.providers.news_alpaca import AlpacaNewsProvider
from qhfi.data.providers.news_yfinance import YFinanceNewsProvider

# Liquid, news-heavy underlyings (broad-market ETFs + mega-caps).
SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD"]
MACRO_TOPICS = ["geopolitical_risk", "war", "sanctions", "oil_supply"]


def pull_equity(store: NewsStore, years: int) -> int:
    alp = AlpacaNewsProvider.from_env()
    start = date.today() - timedelta(days=365 * years)
    saved = 0
    if alp.available():
        print(f"Equity news via Alpaca (Benzinga), since {start}:")
        for s in SYMBOLS:
            try:
                df = alp.fetch(s, start=start, end=date.today())
            except Exception as e:                          # noqa: BLE001
                print(f"  {s:<6} ERROR {type(e).__name__}: {e}"); continue
            n = store.save("equity", "alpaca", s, df) if not df.empty else 0
            saved += n
            print(f"  {s:<6} +{n} articles  (fetched {len(df)})")
    else:
        print("No ALPACA_API_KEY/SECRET → keyless yfinance bootstrap (recent headlines only):")
        yf = YFinanceNewsProvider()
        for s in SYMBOLS:
            try:
                df = yf.fetch(s)
            except Exception as e:                          # noqa: BLE001
                print(f"  {s:<6} ERROR {type(e).__name__}: {e}"); continue
            n = store.save("equity", "yfinance", s, df) if not df.empty else 0
            saved += n
            print(f"  {s:<6} +{n} articles  (fetched {len(df)})")
    return saved


def pull_macro(store: NewsStore) -> int:
    gd, saved = GdeltProvider(), 0
    print("\nMacro news via GDELT article lists:")
    for slug in MACRO_TOPICS:
        try:
            df = gd.fetch_articles(QUERIES[slug], timespan="1m")
        except Exception as e:                              # noqa: BLE001 — 429s expected
            print(f"  {slug:<18} ERROR {type(e).__name__}: {e}"); continue
        n = store.save("macro", "gdelt", slug, df) if not df.empty else 0
        saved += n
        print(f"  {slug:<18} +{n} articles  (fetched {len(df)})")
    return saved


def main() -> None:
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    store = NewsStore(lake_root())
    print(f"News → {store.data_dir.resolve()}\n")
    eq = pull_equity(store, years)
    mc = pull_macro(store)

    cat = store.catalog()
    print(f"\nDONE: {eq} equity + {mc} macro articles stored.")
    if not cat.empty:
        print(f"\ncatalog ({len(cat)} feeds):")
        print(cat.to_string(index=False))
    print("\nRaw capture only (no sentiment). PIT anchor = created_at (publish time). Alpaca = "
          "backtest-grade (2015+); yfinance/GDELT artlist = recent-window bootstrap.")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
