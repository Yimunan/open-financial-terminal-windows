"""Singletons wiring the qhfi engine into the web app.

Everything that touches qhfi is constructed once here and shared via FastAPI dependencies,
so routers stay thin and the engine is configured in exactly one place.
"""

from __future__ import annotations

from functools import lru_cache

from qhfi.core.types import AssetClass, Instrument, InstrumentForm
from qhfi.data.base import DataStore
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.manager import DataManager
from qhfi.data.providers.crypto_ccxt import CcxtDataProvider
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider
from qhfi.research.client import LLMClient

from app.commodity_provider import CommodityFuturesProvider
from app.fx_provider import FxProvider
from app.rates_provider import RatesFuturesProvider
from app.config import (
    get_alpaca_creds,
    get_crypto_exchange,
    get_engine_settings,
    get_llm_override,
    get_terminal_settings,
)
from app.services.agent_demo import seed_demo_workflows
from app.store import TerminalStore


@lru_cache
def get_data_manager() -> DataManager:
    s = get_terminal_settings()
    store = DataStore(root=s.data_dir)
    providers = {
        AssetClass.EQUITY: YFinanceDataProvider(),
        # qhfi's provider defaults to binance internally (geo-blocked 451 from this box) —
        # pass the terminal's configured exchange instead (Settings → Market Data override → env).
        AssetClass.CRYPTO: CcxtDataProvider(exchange=get_crypto_exchange()),
        # CME Treasury futures complex (ZT/ZF/ZN/ZB/UB/ZQ) — yfinance =F tickers behind a clean
        # CME-root-id facade, so rates futures are a first-class quote/bars/watchlist asset class.
        AssetClass.RATES: RatesFuturesProvider(),
        # G10 spot FX (EUR/USD, USD/JPY, …) — yfinance =X tickers behind a canonical-pair-id facade.
        AssetClass.FX: FxProvider(),
        # Commodity futures complex (metals/energy/grains/softs/livestock) — yfinance =F tickers
        # behind a clean root-id facade.
        AssetClass.COMMODITY: CommodityFuturesProvider(),
    }
    return DataManager(store=store, providers=providers)


@lru_cache
def get_fundamentals_store() -> FundamentalsStore:
    return FundamentalsStore(root=get_terminal_settings().data_dir)


@lru_cache
def get_fundamentals_provider() -> YFinanceFundamentalsProvider:
    return YFinanceFundamentalsProvider()


# ── SEC EDGAR / public filings ──────────────────────────────────────────────────
# The Public Filings module reads/writes the qhfi parquet lake (original filings, parsed
# insider transactions, 13F holdings, CUSIP crosswalk). One EdgarClient is shared so its
# rate-limit + cache middleware coalesces every filings request across the app.

@lru_cache
def get_edgar_client():
    from qhfi.data.providers.edgar import EdgarClient

    return EdgarClient()  # User-Agent from SEC_USER_AGENT env (SEC fair-access policy)


@lru_cache
def get_filings_store():
    from qhfi.data.filings import FilingsStore

    return FilingsStore(root=get_terminal_settings().qhfi_lake_dir)


@lru_cache
def get_insider_store():
    from qhfi.data.insider import InsiderStore

    return InsiderStore(root=get_terminal_settings().qhfi_lake_dir)


@lru_cache
def get_holdings_store():
    from qhfi.data.holdings import HoldingsStore

    return HoldingsStore(root=get_terminal_settings().qhfi_lake_dir)


@lru_cache
def get_cusip_store():
    from qhfi.data.crosswalk import CusipTickerStore

    return CusipTickerStore(root=get_terminal_settings().qhfi_lake_dir)


# ── Macro & rates ───────────────────────────────────────────────────────────────
# The Macro module reads the qhfi parquet lake's `macro` (one series per indicator) and
# `rates` (wide Treasury yield curve) categories. Both are read-only here — the data is
# produced by qhfi's pull scripts; the terminal just exposes it.

@lru_cache
def get_macro_store():
    from qhfi.data.macro import MacroStore

    return MacroStore(root=get_terminal_settings().qhfi_lake_dir)


@lru_cache
def get_rates_store():
    from qhfi.data.rates import RatesStore

    return RatesStore(root=get_terminal_settings().qhfi_lake_dir)


@lru_cache
def get_qhfi_market_store():
    """DataStore over the qhfi research lake's market bars (rates futures, spot FX, …).

    These FICC instruments are pre-pulled into the qhfi lake by qhfi's pull scripts — a different
    root from the terminal's own equity/crypto bar cache (``data_dir``). Read-only here: the
    DataManager keeps its own cache fresh on demand; this is the offline/first-call fallback and the
    source the Rates module's futures tab reads.
    """
    from qhfi.data.base import DataStore

    return DataStore(root=get_terminal_settings().qhfi_lake_dir)


# Back-compat alias: the Rates module's original name for the qhfi market lake store.
def get_rates_futures_store():
    return get_qhfi_market_store()


@lru_cache
def get_llm_model() -> str:
    """Resolve a model id the proxy actually serves.

    Order: an online-provider override model → explicit OFT_LLM_MODEL → a served model
    matching ``llm_model_prefer`` (default 'gemma') → the first served model → qhfi's
    configured default. The auto-swap proxy keys on full HF ids, not bare tags.
    """
    import httpx

    ov = get_llm_override()
    if ov.get("model"):  # online provider: the user names the model explicitly
        return ov["model"]
    term = get_terminal_settings()
    if term.llm_model:
        return term.llm_model
    eng = get_engine_settings()
    try:
        headers = _auth_headers(eng.llm_api_key)
        r = httpx.get(f"{eng.llm_base_url.rstrip('/')}/models", timeout=3.0, headers=headers)
        ids = [m["id"] for m in r.json().get("data", [])]
        if ids:
            preferred = [i for i in ids if term.llm_model_prefer.lower() in i.lower()]
            return preferred[0] if preferred else ids[0]
    except Exception:  # noqa: BLE001 - fall back to qhfi default if the proxy is unreachable
        pass
    return eng.llm_model


def _auth_headers(api_key: str | None) -> dict:
    """Bearer header for an online API; nothing for the local proxy's 'not-needed' key."""
    return {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "not-needed" else {}


@lru_cache
def get_llm_client() -> LLMClient:
    s = get_engine_settings()
    s.llm_model = get_llm_model()  # single source of truth for the served model id
    return LLMClient(settings=s)


def reload_llm() -> None:
    """Drop cached LLM settings/clients so a saved provider change takes effect immediately."""
    get_engine_settings.cache_clear()
    get_llm_model.cache_clear()
    get_llm_client.cache_clear()


def reload_market_data() -> None:
    """Drop cached data-manager/broker so a saved market-data change takes effect immediately.

    The data manager is rebuilt against the new ccxt exchange; the broker re-selects Alpaca vs the
    local sim from the new credentials. The realtime hub is asked to rebuild the Alpaca equity stream
    (new creds/feed) and the order-book depth producers (new source/creds). The intraday-bars cache
    is cleared separately by the router.
    """
    get_data_manager.cache_clear()
    get_broker.cache_clear()
    get_sim_broker.cache_clear()  # rebuild the sim sandbox against the new data manager
    from app.services import autopick

    autopick.invalidate()  # re-probe 'auto' picks against the new creds/settings immediately
    from app.services.realtime import get_hub

    hub = get_hub()
    hub.request_equity_reset()
    hub.request_depth_reset()
    from app.services import options as _options

    _options.clear_options_cache()  # drop chain cache so a source change applies live


@lru_cache
def get_store() -> TerminalStore:
    s = get_terminal_settings()
    store = TerminalStore(s.db_path)
    store.init(paper_initial_cash=s.paper_initial_cash)
    seed_demo_workflows(store)  # ship a ready-to-run example under Workflows ▾
    from app.services.registry import reload_custom_strategies

    reload_custom_strategies(store)  # register persisted custom strategies into the lab
    return store


def alpaca_active() -> bool:
    """True when Alpaca paper credentials are configured (a real broker runs alongside the sim)."""
    return bool(get_alpaca_creds()[0])


def broker_kind() -> str:
    """'alpaca' when Alpaca keys are configured, else the local 'sim'. This names the *primary*
    broker; the local sim sandbox (``get_sim_broker``) is always available regardless."""
    return "alpaca" if alpaca_active() else "sim"


@lru_cache
def get_runner():
    """The always-on algo trading runner (singleton). Started/stopped by the FastAPI lifespan."""
    from app.services.algo_runner import AlgoRunner

    return AlgoRunner()


@lru_cache
def get_data_refresh_runner():
    """The always-on background data-refresh runner (singleton). Started/stopped by the lifespan."""
    from app.services.data_refresh import DataRefreshRunner

    return DataRefreshRunner()


@lru_cache
def get_sim_broker(account_id: int = 1):
    """The always-available local SimBroker sandbox for one paper account (default 1).

    Present regardless of Alpaca credentials so the sim-only ops (close/flatten/rebalance/reset,
    stop & trailing orders, pre-trade preview) and a local paper book stay usable even while Alpaca
    is the primary broker. State lives in the terminal SQLite, so this and a no-keys ``get_broker``
    share account 1 (``get_broker`` returns ``get_sim_broker(1)`` when no keys are set).

    Cash + realism are read from the account row; a never-seen account falls back to the terminal
    default cash + the global bps config (its row is then created at that cash by the broker). Cache
    is keyed by account_id; ``cache_clear()`` drops every account (no per-key eviction)."""
    from app.services.broker import SimBroker

    store = get_store()
    row = store.get_paper_account(account_id)
    if row:
        initial_cash = float(row["initial_cash"])
        comm, slip = float(row["commission_bps"]), float(row["slippage_bps"])
    else:
        def _bps(key: str) -> float:
            try:
                return max(0.0, min(500.0, float(store.get_config(key, "0") or 0)))
            except (TypeError, ValueError):
                return 0.0

        initial_cash = get_terminal_settings().paper_initial_cash
        comm, slip = _bps("paper_commission_bps"), _bps("paper_slippage_bps")

    return SimBroker(
        store, get_data_manager(), initial_cash,
        commission_bps=comm, slippage_bps=slip, account_id=account_id,
    )


@lru_cache
def get_broker():
    """The primary paper broker: real Alpaca paper if keys are set, else the local SimBroker.

    Credentials come from Settings → Market Data (override → else env), so saving keys there flips
    the primary broker after ``reload_market_data()`` clears this cache. The local sim sandbox
    (``get_sim_broker``) stays available either way.
    """
    api_key, api_secret, paper = get_alpaca_creds()
    if api_key:
        from qhfi.execution.brokers.alpaca_paper import AlpacaPaperBroker

        return AlpacaPaperBroker(api_key=api_key, api_secret=api_secret, paper=paper)
    return get_sim_broker(1)


def parse_book(book: str) -> tuple[str, int | None]:
    """Split a ``book`` token into (kind, account_id). Grammar:

    ``primary`` → ('primary', None); ``sim`` → ('sim', 1); ``sim:<id>`` → ('sim', <id>). A malformed
    ``sim:<x>`` degrades to account 1; anything else is treated as 'primary'."""
    if book == "sim":
        return "sim", 1
    if book.startswith("sim:"):
        try:
            return "sim", int(book.split(":", 1)[1])
        except (TypeError, ValueError):
            return "sim", 1
    if book == "primary":
        return "primary", None
    return "primary", None


def resolve_broker(book: str = "primary"):
    """Pick the broker for a paper request: ``book='sim[:<id>]'`` → that sim account's sandbox;
    anything else → the primary broker (Alpaca when configured, otherwise sim account 1)."""
    kind, account_id = parse_book(book)
    return get_sim_broker(account_id or 1) if kind == "sim" else get_broker()


# ── Instrument construction ────────────────────────────────────────────────────
# The terminal lets users type any symbol, so we build a minimal Instrument on the fly.
# Universe YAMLs (with GICS metadata) are still the source for screens/backtests.

def make_instrument(symbol: str, asset: str = "equity", exchange: str | None = None) -> Instrument:
    """Construct a minimal Instrument from a free-typed symbol + asset class.

    Crypto ids look like ``BTC/USDT`` (slash-quoted); equities are bare tickers.
    """
    asset_class = AssetClass(asset.lower())
    exchange = exchange or get_crypto_exchange()
    if asset_class == AssetClass.CRYPTO:
        quote = symbol.split("/")[-1] if "/" in symbol else "USDT"
        return Instrument(
            id=symbol.upper(),
            asset_class=AssetClass.CRYPTO,
            form=InstrumentForm.CASH,
            exchange=exchange,
            quote_currency=quote,
        )
    return Instrument(id=symbol.upper(), asset_class=asset_class)
