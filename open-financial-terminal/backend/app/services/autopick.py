"""Auto-selection ("auto") of the best available market-data producer per asset class.

Settings can pin a concrete order-book depth source ('sim'/'ibkr'/'databento'/'dxfeed') or the
equity realtime source ('alpaca'). The 'auto' token instead resolves — at token/health time — to
the best candidate that is *actually usable right now*, so buying a Databento live license or
starting IB Gateway upgrades the feed with zero clicks, and losing it degrades back to 'sim'
instead of an empty book.

Ranking (order book): ibkr > databento > dxfeed > sim. IBKR first — it is the only vendor with
real L2 across all four classes incl. spot FX; Databento next (best equities/futures quality, no
spot-FX book); dxLink last of the vendors (address-gated, no deep probe available); 'sim' is the
always-available floor (modelled ladder around the real mid, honestly tagged synthetic).

Probes are deliberately deeper than the vendor modules' own ``enabled()`` (which only check
SDK+creds presence):
  * ibkr      → enabled() + a TCP connect to the configured IB Gateway host/port.
  * databento → enabled() + a real Live-gateway subscribe: the key we have is historical-only and
                the gateway rejects it synchronously ("A live data license is required…"), which
                presence checks cannot see. Expensive (~1-2s), so successes cache for an hour.
  * dxfeed    → enabled() only (dxLink has no cheap liveness probe worth its own connection).

Results are TTL-cached; ``invalidate()`` is called from ``deps.reload_market_data()`` so saving
market-data settings or provider creds re-probes immediately.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

log = logging.getLogger("oft.autopick")

#: Vendor ranking for the order-book resolver — first available wins; 'sim' is the implicit floor.
DEPTH_RANKING = ("ibkr", "databento", "dxfeed")

# (ok_ttl, fail_ttl) seconds per probe family. The Databento probe costs a real gateway
# round-trip so it caches longest — but a *revoked* live license should also be noticed within
# minutes (the widget would otherwise sit on a dead vendor), hence 15min, not hours.
_TTLS = {"ibkr": (60.0, 60.0), "databento": (900.0, 300.0), "dxfeed": (60.0, 60.0)}

_cache: dict[str, tuple[bool, float]] = {}
_cache_lock = threading.Lock()


def invalidate() -> None:
    """Drop all probe results (called on settings/provider saves) so the next read re-probes."""
    with _cache_lock:
        _cache.clear()


def _cached(key: str, family: str, probe: Callable[[], bool]) -> bool:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[1] > now:
            return hit[0]
    ok = False
    try:
        ok = bool(probe())  # outside the lock — a probe may take seconds
    except Exception as e:  # noqa: BLE001 - a crashing probe means "not available"
        log.debug("autopick probe %s crashed: %s", key, e)
    ok_ttl, fail_ttl = _TTLS[family]
    with _cache_lock:
        _cache[key] = (ok, now + (ok_ttl if ok else fail_ttl))
    return ok


# ── per-vendor availability ──────────────────────────────────────────────────────────
def _ibkr_up() -> bool:
    """ib_async importable AND a real IB API handshake succeeds against the configured gateway.

    A bare TCP connect is not enough — any listener on the port (AirPlay on 5000/7000, a dev
    server, a gateway with the API disabled or a trusted-IP filter) would pass and auto would
    pick a dead 'ibkr' over the working sim floor. So: TCP fast-fail first (0.6s, catches the
    common gateway-not-running case cheaply), then an actual ``IB.connect`` handshake. Client id
    19 — depth uses 17, options 18, so the probe never collides with a live subscription.
    """
    from app.services import depth_ibkr

    ib_mod = depth_ibkr._import_ib()  # noqa: SLF001 - module-internal by design
    if ib_mod is None:
        return False
    import os

    from app.config import get_provider_config

    ib = get_provider_config().get("ibkr", {})
    host = ib.get("host") or os.getenv("OFT_IBKR_HOST", "127.0.0.1")
    try:
        port = int(ib.get("port") or os.getenv("OFT_IBKR_PORT", "4002") or 4002)
    except ValueError:
        port = 4002
    try:
        with socket.create_connection((host, port), timeout=0.6):
            pass
    except OSError:
        return False
    result: dict[str, bool] = {}

    def _handshake() -> None:
        import asyncio

        loop = asyncio.new_event_loop()  # ib_async needs a loop per thread (same as depth_ibkr)
        asyncio.set_event_loop(loop)
        client = ib_mod.IB()
        try:
            loop.run_until_complete(client.connectAsync(host, port, clientId=19, timeout=2.5))
            result["ok"] = client.isConnected()
        except Exception as e:  # noqa: BLE001 - not a gateway / API off / IP filter
            log.info("ibkr probe handshake %s:%s failed: %s", host, port, e)
            result["ok"] = False
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001 - best-effort
                pass
            loop.close()

    t = threading.Thread(target=_handshake, daemon=True, name="ibkr-probe")
    t.start()
    t.join(4.0)
    return bool(result.get("ok"))


def _databento_live_ok(asset: str) -> bool:
    """Key present AND the *live* gateway accepts a subscribe for this asset's dataset.

    On databento 0.80 the license rejection ("A live data license is required to access
    <DATASET>.") raises synchronously from ``subscribe()`` — the CRAM auth exchange carries the
    dataset — before ``start()`` is even called. The short post-start sleep is defense-in-depth
    for SDK versions that defer the error; a hung gateway counts as unavailable (fail-TTL retry).
    """
    from app.config import get_default_symbol
    from app.services import depth_databento as dd

    db = dd._import_db()  # noqa: SLF001
    key = dd._api_key()  # noqa: SLF001
    if db is None or not key:
        return False
    route = dd._route(asset, get_default_symbol(asset))  # noqa: SLF001
    if route is None:
        return False
    dataset, stype, sym = route
    result: dict[str, bool] = {}

    def _attempt() -> None:
        client = None
        try:
            client = db.Live(key=key)
            client.subscribe(dataset=dataset, schema="mbp-10", stype_in=stype, symbols=[sym])
            client.start()
            time.sleep(1.0)  # license rejections surface here; silence = accepted
            result["ok"] = True
        except Exception as e:  # noqa: BLE001 - license / auth / gateway error
            log.info("databento live probe (%s): %s", dataset, e)
            result["ok"] = False
        finally:
            if client is not None:
                try:
                    client.stop()
                except Exception:  # noqa: BLE001 - best-effort
                    pass

    t = threading.Thread(target=_attempt, daemon=True, name=f"dbn-live-probe-{dataset}")
    t.start()
    t.join(6.0)
    return bool(result.get("ok"))


def _depth_candidate_ok(source: str, asset: str) -> bool:
    """One ranked candidate's availability for an asset class (module enabled + deep probe)."""
    if source == "ibkr":
        from app.services.depth_ibkr import IbkrDepthSource

        return IbkrDepthSource().enabled(asset) and _cached("ibkr:gateway", "ibkr", _ibkr_up)
    if source == "databento":
        from app.services import depth_databento as dd

        if not dd.DatabentoDepthSource().enabled(asset):
            return False
        route = dd._route(asset, "PROBE")  # noqa: SLF001 - dataset only depends on the asset
        dataset = route[0] if route else asset
        return _cached(f"databento:{dataset}", "databento", lambda: _databento_live_ok(asset))
    if source == "dxfeed":
        # Presence-only (package + address) — dxLink offers no cheap liveness probe, so a saved
        # but bad address WOULD out-rank sim. Accepted: the dxfeed package isn't installed today,
        # and a picked-but-broken vendor still emits status frames rather than failing silently.
        from app.services.depth_dxfeed import DxFeedDepthSource

        return _cached(f"dxfeed:{asset}", "dxfeed", lambda: DxFeedDepthSource().enabled(asset))
    return False


# ── resolvers ────────────────────────────────────────────────────────────────────────
def resolve_depth_source(asset: str) -> str:
    """The concrete order-book source 'auto' stands for right now (never 'auto'/'none').

    Crypto: the exchange's real ccxt.pro L2 whenever crypto realtime is on, else 'sim'. Other
    classes: the best-ranked vendor whose deep probe passes, else 'sim' (always available).
    """
    a = (asset or "").lower()
    if a == "crypto":
        from app.config import get_crypto_realtime_enabled

        return "exchange" if get_crypto_realtime_enabled() else "sim"
    for cand in DEPTH_RANKING:
        try:
            if _depth_candidate_ok(cand, a):
                return cand
        except Exception as e:  # noqa: BLE001 - one broken vendor must not block the fallback
            log.debug("autopick candidate %s/%s failed: %s", cand, a, e)
    return "sim"


# Equity price 'auto' resolves in config.get_equity_realtime_resolved (a pure creds check — no
# probes needed), not here.
