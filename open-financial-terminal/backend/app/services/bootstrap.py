"""First-run data bootstrap.

A fresh install starts with an empty bars cache, so charts/screener/metrics would be blank until
the user manually pulls data. On startup this seeds a small baseline (default: dow30 equities +
crypto_majors) into the lake and a starter watchlist, so the terminal is useful immediately and the
ongoing DataRefreshRunner has live targets.

Safeguards:
  * Runs in a background thread — the window opens right away; data fills in as it lands.
  * Gated by a ``.bootstrapped`` sentinel in the data dir AND an empty-lake check, so it never
    re-runs and never touches an already-populated lake (e.g. the dev install keeps its cache).
  * Per-symbol failures are non-fatal; partial success still marks the sentinel (the scheduled
    refresh fills any gaps later).

Env knobs:
  OFT_BOOTSTRAP_DISABLE=1                              skip entirely
  OFT_BOOTSTRAP_UNIVERSES="dow30,crypto_majors"        which universes to pull
  OFT_BOOTSTRAP_YEARS=3                                history window
  OFT_BOOTSTRAP_WATCHLIST="AAPL:equity,BTC/USDT:crypto" starter watchlist (sym:asset, comma-sep)
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_DEFAULT_UNIVERSES = ("dow30", "crypto_majors")
_DEFAULT_WATCHLIST = (("AAPL", "equity"), ("MSFT", "equity"), ("BTC/USDT", "crypto"), ("ETH/USDT", "crypto"))
_SENTINEL = ".bootstrapped"


@dataclass
class _Status:
    state: str = "idle"   # idle | running | done | skipped | error
    universe: str = ""
    total: int = 0
    done: int = 0
    failed: int = 0
    detail: str = ""


_status = _Status()
_started = False
_lock = threading.Lock()


def status() -> dict:
    """In-memory bootstrap progress (surfaced via /api/health)."""
    s = _status
    return {
        "state": s.state, "universe": s.universe,
        "total": s.total, "done": s.done, "failed": s.failed, "detail": s.detail,
    }


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _universes() -> list[str]:
    raw = os.environ.get("OFT_BOOTSTRAP_UNIVERSES", "").strip()
    return [u.strip() for u in raw.split(",") if u.strip()] or list(_DEFAULT_UNIVERSES)


def _years() -> int:
    try:
        return max(1, min(20, int(os.environ.get("OFT_BOOTSTRAP_YEARS", "3"))))
    except ValueError:
        return 3


def _watchlist() -> list[tuple[str, str]]:
    raw = os.environ.get("OFT_BOOTSTRAP_WATCHLIST", "").strip()
    if not raw:
        return list(_DEFAULT_WATCHLIST)
    out: list[tuple[str, str]] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        sym, _, asset = tok.partition(":")
        if sym.strip():
            out.append((sym.strip(), asset.strip() or "equity"))
    return out


def _lake_has_data(root: Path) -> bool:
    """True if the bars cache already holds any parquet — so we never disturb a populated lake."""
    try:
        return any(root.rglob("*.parquet"))
    except OSError:
        return False


def _write_sentinel(path: Path, note: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{note}\n", "utf-8")
    except OSError:
        pass


def maybe_bootstrap() -> None:
    """Entry point (call once from the app lifespan). Spawns a background pull on a fresh empty lake.

    Idempotent and non-blocking: returns immediately. Decides synchronously whether to run, seeds the
    starter watchlist on the calling thread, then hands the slow network pulls to a daemon thread.
    """
    global _started
    with _lock:
        if _started:
            return
        _started = True

    if _env_flag("OFT_BOOTSTRAP_DISABLE"):
        _status.state, _status.detail = "skipped", "disabled via OFT_BOOTSTRAP_DISABLE"
        return

    from app.config import get_terminal_settings

    data_dir = Path(get_terminal_settings().data_dir)
    sentinel = data_dir / _SENTINEL
    if sentinel.exists():
        _status.state, _status.detail = "skipped", "already bootstrapped"
        return

    # Resolve the bars-cache root; if it already holds data, mark the sentinel and skip.
    try:
        from app.deps import get_data_manager

        root = Path(get_data_manager().store.root)
    except Exception:  # noqa: BLE001 - fall back to the data dir if the DM can't init yet
        root = data_dir
    if _lake_has_data(root):
        _status.state, _status.detail = "skipped", "lake already populated"
        _write_sentinel(sentinel, "lake-already-populated")
        return

    # Seed the starter watchlist on the calling thread (only if the user has none yet).
    try:
        from app.deps import get_store

        store = get_store()
        if not store.list_watchlist():
            for sym, asset in _watchlist():
                try:
                    store.add_watch(sym, asset)
                except Exception:  # noqa: BLE001 - a bad symbol shouldn't abort seeding
                    pass
    except Exception:  # noqa: BLE001
        pass

    threading.Thread(target=_run, args=(sentinel,), name="oft-bootstrap", daemon=True).start()


def _run(sentinel: Path) -> None:
    from qhfi.core.types import DateRange, Universe

    from app.deps import get_data_manager
    from app.services.universe import get_universe

    _status.state = "running"
    try:
        dm = get_data_manager()

        # Collect + de-dup baseline instruments from the configured universes.
        seen: set[str] = set()
        instruments = []
        for uname in _universes():
            try:
                for ins in get_universe(uname).instruments:
                    if ins.id not in seen:
                        seen.add(ins.id)
                        instruments.append(ins)
            except Exception as exc:  # noqa: BLE001 - a missing universe shouldn't abort the rest
                print(f"[bootstrap] universe '{uname}' skipped: {exc}", flush=True)

        _status.universe = ",".join(_universes())
        _status.total = len(instruments)
        years = _years()
        end = date.today()
        span = DateRange(start=end - timedelta(days=365 * years), end=end)
        print(f"[bootstrap] pulling {len(instruments)} symbols ({_status.universe}, {years}y) "
              f"-> {dm.store.root}", flush=True)

        for ins in instruments:
            try:
                dm.update(Universe(name="_bootstrap", instruments=[ins]), span)
            except Exception as exc:  # noqa: BLE001 - per-symbol provider hiccups are non-fatal
                _status.failed += 1
                print(f"[bootstrap] {ins.id} failed: {exc}", flush=True)
            finally:
                _status.done += 1

        # Best-effort: kick the slow daily jobs so the rates/macro widgets populate too.
        try:
            from app.deps import get_data_refresh_runner

            runner = get_data_refresh_runner()
            for job in ("rates", "macro"):
                try:
                    runner.trigger_now(job)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        ok = _status.done - _status.failed
        _status.state = "done"
        _status.detail = f"{ok}/{_status.total} symbols cached"
        _write_sentinel(sentinel, f"bootstrapped {ok}/{_status.total}")
        print(f"[bootstrap] done: {_status.detail}", flush=True)
    except Exception as exc:  # noqa: BLE001 - leave the sentinel unwritten so it retries next launch
        _status.state, _status.detail = "error", str(exc)
        print(f"[bootstrap] error: {exc}", flush=True)
