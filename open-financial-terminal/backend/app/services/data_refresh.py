"""Always-on background data-refresh runner — keeps the persisted lake current on a schedule.

The terminal only refreshes data *lazily* today (``market.py::fetch_bars`` updates a symbol when a
widget asks for it), and the macro/rates/filings lakes are read-only — only manual qhfi pull scripts
fill them. This runner closes that gap: a registry of refresh **jobs**, each on its own cadence,
keeping fresh exactly what the terminal serves.

Design mirrors :class:`app.services.algo_runner.AlgoRunner`: one async ``_tick_loop`` started by the
FastAPI lifespan, all blocking work (yfinance/ccxt/FRED/EDGAR network + parquet writes) pushed OFF
the event loop via ``run_in_executor`` (a blocking call on the loop froze the whole server once —
never again), per-job enable/interval/last-run persisted in the SQLite ``config`` table so settings
survive a restart and can be changed live from Settings → Data Refresh.

Jobs are **serialized** by a single lock (at most one pass runs at a time): different domains write
different parquet trees so concurrency would be safe, but serializing keeps yfinance/EDGAR bursts
gentle and sidesteps any same-name read-modify-write collision with a lazy ``fetch_bars``.

Each job writes to the SAME store/root the UI reads from:
* ``market_bars`` → ``get_data_manager()`` (``data_dir``) — active watchlist/holdings/algo names.
* ``rates`` / ``macro`` → ``get_rates_store()`` / ``get_macro_store()`` (``qhfi_lake_dir``), global.
* ``filings`` → the live EDGAR feed cache (``qhfi_lake_dir``), active equities (off by default; EDGAR
  is rate-limited and needs ``SEC_USER_AGENT``).
* ``news`` → warms the *live* per-symbol news cache (``news_router``) for active equities; OFT news is
  fetched live, not lake-backed, so this keeps the widget instant + fresh rather than writing a lake.

Fundamentals are intentionally absent: OFT serves them live from yfinance per request (not lake-backed
and uncached), so there is nothing to schedule.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from qhfi.core.types import DateRange, Universe

log = logging.getLogger("oft.data_refresh")

TICK_SECONDS = 60          # how often the loop checks which jobs are due
MIN_INTERVAL_S = 120       # floor so a misconfigured interval can't hammer a provider
_MASTER_KEY = "data_refresh:enabled"
_MHO_KEY = "data_refresh:market_hours_only"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


class RefreshJob:
    """One scheduled refresh: a name, its default cadence, default enabled state, and the sync
    entry function (run only in the executor / a sync route — never on the event loop)."""

    def __init__(self, name: str, default_interval_s: int, default_enabled: bool, fn, label: str):
        self.name = name
        self.default_interval_s = max(MIN_INTERVAL_S, int(default_interval_s))
        self.default_enabled = default_enabled
        self.fn = fn
        self.label = label


class DataRefreshRunner:
    """Singleton scheduler + per-domain refresh functions. Constructed via ``deps.get_data_refresh_runner()``."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = threading.Lock()          # serialize passes (rate-friendly + write-safe)
        self._running: set[str] = set()        # job names with a pass in flight (no double-fire)
        self._last_result: dict[str, dict] = {}
        self._jobs = self._build_registry()

    def _build_registry(self) -> dict[str, RefreshJob]:
        from app.config import get_terminal_settings

        s = get_terminal_settings()
        jobs = [
            RefreshJob("market_bars", s.data_refresh_bars_s, True, self._refresh_bars, "Market data (daily bars)"),
            RefreshJob("news", s.data_refresh_news_s, True, self._refresh_news, "News"),
            RefreshJob("rates", s.data_refresh_rates_s, True, self._refresh_rates, "Interest-rate curve"),
            RefreshJob("macro", s.data_refresh_macro_s, True, self._refresh_macro, "Macro indicators"),
            RefreshJob("filings", s.data_refresh_filings_s, False, self._refresh_filings, "SEC filings"),
        ]
        return {j.name: j for j in jobs}

    # ── lifecycle (started/stopped from the FastAPI lifespan) ──────────────────
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._tick_loop())
            log.info("data refresh runner started (master=%s)", self._master_enabled())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best-effort teardown
                pass
            self._task = None

    # ── config (persisted in the SQLite store; env seeds the registry defaults) ─
    def _master_enabled(self) -> bool:
        from app.config import get_terminal_settings
        from app.deps import get_store

        v = get_store().get_config(_MASTER_KEY)
        return get_terminal_settings().data_refresh_enabled if v is None else v == "1"

    def _market_hours_only(self) -> bool:
        from app.config import get_terminal_settings
        from app.deps import get_store

        v = get_store().get_config(_MHO_KEY)
        return get_terminal_settings().data_refresh_market_hours_only if v is None else v == "1"

    def _enabled(self, name: str) -> bool:
        from app.deps import get_store

        v = get_store().get_config(f"data_refresh:{name}:enabled")
        return self._jobs[name].default_enabled if v is None else v == "1"

    def _interval_s(self, name: str) -> int:
        from app.deps import get_store

        v = get_store().get_config(f"data_refresh:{name}:interval_s")
        if not v:
            return self._jobs[name].default_interval_s
        try:
            return max(MIN_INTERVAL_S, int(v))
        except (TypeError, ValueError):
            return self._jobs[name].default_interval_s

    def set_master_enabled(self, value: bool) -> None:
        from app.deps import get_store

        get_store().set_config(_MASTER_KEY, "1" if value else "0")

    def set_market_hours_only(self, value: bool) -> None:
        from app.deps import get_store

        get_store().set_config(_MHO_KEY, "1" if value else "0")

    def set_enabled(self, name: str, value: bool) -> None:
        from app.deps import get_store

        self._require(name)
        get_store().set_config(f"data_refresh:{name}:enabled", "1" if value else "0")

    def set_interval_s(self, name: str, seconds: int) -> None:
        from app.deps import get_store

        self._require(name)
        get_store().set_config(f"data_refresh:{name}:interval_s", str(max(MIN_INTERVAL_S, int(seconds))))

    def _require(self, name: str) -> None:
        if name not in self._jobs:
            raise ValueError(f"unknown refresh job '{name}'")

    # ── scheduling ─────────────────────────────────────────────────────────────
    def _due(self, name: str, now: datetime) -> bool:
        from app.deps import get_store

        last = _parse(get_store().get_config(f"data_refresh:{name}:last_run"))
        return last is None or (now - last).total_seconds() >= self._interval_s(name)

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad tick must never kill the loop
                log.exception("data refresh tick failed")
            await asyncio.sleep(TICK_SECONDS)

    async def _tick(self) -> None:
        if not self._master_enabled():
            return
        now = _now()
        loop = asyncio.get_event_loop()
        for name in self._jobs:
            if name in self._running or not self._enabled(name) or not self._due(name, now):
                continue
            self._running.add(name)
            # blocking refresh (network + parquet) off the event loop; fire-and-forget
            loop.run_in_executor(None, self._guarded_run, name)

    def _guarded_run(self, name: str) -> None:
        try:
            with self._lock:  # serialize: at most one pass at a time
                self._run_job(name)
        except Exception:  # noqa: BLE001 - already logged inside; protect the executor thread
            log.exception("data refresh job crashed: %s", name)
        finally:
            self._running.discard(name)

    def _run_job(self, name: str) -> dict:
        """Run one job's refresh fn, persist last-run, record the result. Sync; off the loop only."""
        from app.deps import get_store

        started = _now()
        try:
            result = self._jobs[name].fn() or {}
            status = "ok"
        except Exception as e:  # noqa: BLE001 - surface as a logged result, never raise to the loop
            log.warning("data refresh '%s' failed: %s", name, e)
            result, status = {"error": str(e)}, "error"
        get_store().set_config(f"data_refresh:{name}:last_run", started.isoformat())
        rec = {"ts": started.isoformat(), "status": status,
               "duration_s": round((_now() - started).total_seconds(), 2), **result}
        self._last_result[name] = rec
        log.info("data refresh '%s': %s", name, rec)
        return rec

    def trigger_now(self, name: str) -> dict:
        """Run a job immediately (manual 'Refresh now'). Returns ``busy`` at once if this job — or
        any other pass — is already running (non-blocking lock), so the endpoint never hangs behind a
        slow pass. Sync — invoked from the /run route's threadpool thread, off the event loop."""
        self._require(name)
        if name in self._running or not self._lock.acquire(blocking=False):
            return {"status": "busy", **(self._last_result.get(name) or {})}
        self._running.add(name)
        try:
            return self._run_job(name)
        finally:
            self._running.discard(name)
            self._lock.release()

    # ── status (for the API / Settings UI) ─────────────────────────────────────
    def status(self) -> dict:
        from app.deps import get_store

        store = get_store()
        by_asset = self._active_by_asset()
        counts = {a: len(v) for a, v in by_asset.items()}
        jobs = {}
        for name, job in self._jobs.items():
            last = _parse(store.get_config(f"data_refresh:{name}:last_run"))
            interval = self._interval_s(name)
            jobs[name] = {
                "label": job.label,
                "enabled": self._enabled(name),
                "interval_s": interval,
                "interval_minutes": round(interval / 60, 2),
                "last_run": last.isoformat() if last else None,
                "next_run": (last + timedelta(seconds=interval)).isoformat() if last else None,
                "running": name in self._running,
                "last_result": self._last_result.get(name),
            }
        return {
            "running": self._task is not None and not self._task.done(),
            "master_enabled": self._master_enabled(),
            "market_hours_only": self._market_hours_only(),
            "active_by_asset": counts,  # {equity, crypto, fx, rates} → count in the active set
            # legacy mirrors (kept for back-compat with existing callers)
            "active_equities": counts.get("equity", 0),
            "active_crypto": counts.get("crypto", 0),
            "jobs": jobs,
        }

    # ── active symbol set (shared by symbol-scoped jobs) ───────────────────────
    def _active_pairs(self) -> set[tuple[str, str]]:
        from app.deps import get_store
        from app.services.universe import get_universe

        store = get_store()
        pairs: set[tuple[str, str]] = set()
        for w in store.list_watchlist():
            pairs.add((str(w["symbol"]).upper(), w.get("asset", "equity")))
        for h in store.list_holdings():
            pairs.add((str(h["symbol"]).upper(), h.get("asset", "equity")))
        for a in store.list_algos():
            if a.get("kind") == "xsection":
                try:
                    for ins in get_universe(a.get("universe", "dow30")).instruments:
                        pairs.add((ins.id.upper(), ins.asset_class.value))
                except Exception:  # noqa: BLE001 - a bad algo universe shouldn't sink the set
                    log.debug("active set: bad algo universe", exc_info=True)
            elif a.get("symbol"):
                pairs.add((str(a["symbol"]).upper(), a.get("asset", "equity")))
        return pairs

    def _active_by_asset(self) -> dict[str, list[tuple]]:
        """Active names grouped by asset class → ``{asset: [(symbol, asset, Instrument), …]}``,
        deduped + sorted. Generic over every asset class the terminal serves
        (equity/crypto/fx/rates/commodity) — a new class flows through with no change here."""
        from app.deps import make_instrument

        by: dict[str, list[tuple]] = {}
        for sym, asset in sorted(self._active_pairs()):
            try:
                ins = make_instrument(sym, asset)
            except Exception:  # noqa: BLE001 - skip un-constructable symbols
                continue
            by.setdefault(asset, []).append((sym, asset, ins))
        return by

    def _equity_gate_open(self, now: datetime) -> bool:
        """True when US equities are worth refreshing (Mon-Fri ~09:00-17:30 ET), unless the
        market-hours gate is off. pandas tz math (stdlib zoneinfo is flaky on Windows)."""
        if not self._market_hours_only():
            return True
        try:
            ny = pd.Timestamp(now).tz_convert("America/New_York")
            if ny.dayofweek >= 5:  # Sat/Sun
                return False
            return (9, 0) <= (ny.hour, ny.minute) <= (17, 30)
        except Exception:  # noqa: BLE001 - bad tz data → permissive
            return True

    def _weekday_gate_open(self, now: datetime) -> bool:
        """True on US weekdays (FX, rates + commodity futures trade ~24h Mon-Fri, so the intraday
        equity window doesn't apply — but their daily bars don't change over the weekend). Off →
        always open."""
        if not self._market_hours_only():
            return True
        try:
            return pd.Timestamp(now).tz_convert("America/New_York").dayofweek < 5
        except Exception:  # noqa: BLE001 - bad tz data → permissive
            return True

    def _bars_gate_open(self, asset: str, now: datetime) -> bool:
        """Per-asset refresh window: crypto 24/7, equity = US session, everything else (fx, rates,
        commodity, …) = US weekdays."""
        if asset == "crypto":
            return True
        if asset == "equity":
            return self._equity_gate_open(now)
        return self._weekday_gate_open(now)  # fx, rates, commodity (and any ~24h-weekday class)

    # ── per-domain refresh functions (sync; run only in the executor) ──────────
    def _refresh_bars(self) -> dict:
        from app.config import get_history_years
        from app.deps import get_data_manager

        now = _now()
        by_asset = self._active_by_asset()         # equity / crypto / fx / rates
        dm = get_data_manager()
        end = date.today()
        refreshed = 0
        skipped: list[str] = []
        for asset, items in sorted(by_asset.items()):
            if not self._bars_gate_open(asset, now):  # off-session for this asset class
                skipped.append(asset)
                continue
            instruments = [it[2] for it in items]
            start = end - timedelta(days=365 * get_history_years(asset))
            universe = Universe(name=f"_refresh_{asset}", instruments=instruments)
            try:
                dm.update(universe, DateRange(start=start, end=end))  # incremental; one call iterates
                refreshed += len(instruments)
            except Exception:  # noqa: BLE001 - a provider hiccup shouldn't fail the whole pass
                log.warning("bars refresh failed (%s)", asset, exc_info=True)
        return {"refreshed": refreshed,
                "by_asset": {a: len(v) for a, v in by_asset.items()},
                "skipped_closed": skipped}

    def _refresh_news(self) -> dict:
        from app.services import news_router

        # Per-symbol news for the active equities. news() routes through active_sources(), so this
        # honors the user's News settings (enabled built-in sources + weights + custom feeds).
        eq = self._active_by_asset().get("equity", [])
        warmed = 0
        for sym, _asset, _ins in eq:
            try:
                news_router.news(sym)
                warmed += 1
            except Exception:  # noqa: BLE001
                log.debug("news warm failed: %s", sym, exc_info=True)
        # Topic news too, so the Topic News widget follows the same News config: built-in
        # Market/Macro + the user's keyword topics from Settings → News Topics (available_topics()
        # reads config.get_news_topics()). Dedupe by key — a multi-label topic shares one feed.
        topics = 0
        try:
            seen: set[str] = set()
            for t in news_router.available_topics():
                key = t.get("key")
                if not key or key in seen:
                    continue
                seen.add(key)
                try:
                    news_router.topic_news(key)
                    topics += 1
                except Exception:  # noqa: BLE001
                    log.debug("topic warm failed: %s", key, exc_info=True)
        except Exception:  # noqa: BLE001 - topic enumeration is best-effort
            log.debug("topic enumeration failed", exc_info=True)
        return {"symbols": len(eq), "warmed_symbols": warmed, "warmed_topics": topics}

    @staticmethod
    def _fast_clients():
        """A short-timeout, no-retry httpx + ManagedClient pair so FRED fails FAST instead of
        hanging the job (and holding the serialization lock) when it's unreachable — exactly what
        scripts/pull_rates.py does. Returns (httpx_client, managed_client)."""
        import httpx
        from qhfi.api.client import ManagedClient

        http = httpx.Client(timeout=8.0, follow_redirects=True, headers={"User-Agent": "qhfi-research"})
        managed = ManagedClient(rate_per_sec=5.0, max_retries=0, backoff_base=0.0)
        return http, managed

    def _refresh_rates(self) -> dict:
        from app.deps import get_rates_store

        curve = self._treasury_curve()
        get_rates_store().save("treasury_curve", curve)
        return {"rows": int(curve.shape[0]), "tenors": int(curve.shape[1])}

    def _treasury_curve(self) -> pd.DataFrame:
        """FRED full curve (fast-fail), falling back to yfinance rate tickers (per pull_rates.py)."""
        try:
            from qhfi.data.providers.fred import FredRatesProvider

            http, managed = self._fast_clients()
            return FredRatesProvider(http=http, managed=managed).treasury_curve()
        except Exception as e:  # noqa: BLE001 - FRED unreachable → yfinance tickers
            log.warning("FRED rates failed (%s) → yfinance fallback", type(e).__name__)
            import yfinance as yf

            tenors = {"3M": "^IRX", "5Y": "^FVX", "10Y": "^TNX", "30Y": "^TYX"}
            raw = yf.download(list(tenors.values()), period="1y", auto_adjust=False,
                              progress=False)["Close"]
            curve = raw.rename(columns={v: k for k, v in tenors.items()})[list(tenors)]
            if curve.stack().median() > 20:  # some Yahoo tickers quote yield ×10
                curve = curve / 10.0
            curve.index = pd.to_datetime(curve.index, utc=True)
            return curve.dropna(how="all").sort_index()

    def _refresh_macro(self) -> dict:
        from app.deps import get_macro_store
        from qhfi.data.providers.macro import MACRO_SERIES, MacroProvider

        store = get_macro_store()
        # MacroProvider rides the keyless FRED CSV endpoint with a fast-fail cooldown: after one
        # timeout the rest of the batch returns empty immediately (see qhfi _fredcsv), and FRED is
        # retried on the next run once the cooldown lapses.
        provider = MacroProvider()
        saved = 0
        for sid in MACRO_SERIES:
            try:
                s = provider.fetch_series(sid)
                if len(s):
                    store.save(sid, s)
                    saved += 1
            except Exception:  # noqa: BLE001 - skip a flaky series, keep going
                log.debug("macro series failed: %s", sid, exc_info=True)
        if not saved and MACRO_SERIES:
            # An all-miss batch is an outage (FRED unreachable / cooldown), not a success — surface
            # it as an error so status readers see the failure instead of a silent empty lake.
            raise RuntimeError(f"0/{len(MACRO_SERIES)} series saved — FRED unreachable?")
        return {"series": saved, "total": len(MACRO_SERIES)}

    def _refresh_filings(self) -> dict:
        from app.deps import get_edgar_client, get_filings_store
        from app.services import filings as fl

        eq = self._active_by_asset().get("equity", [])
        edgar = get_edgar_client()
        fs = get_filings_store()
        done = 0
        for sym, _asset, _ins in eq:
            try:
                fl.feed(edgar, fs, sym)            # fetch + cache the filings feed (rate-limited)
                done += 1
            except Exception:  # noqa: BLE001
                log.debug("filings refresh failed: %s", sym, exc_info=True)
        return {"symbols": done}
