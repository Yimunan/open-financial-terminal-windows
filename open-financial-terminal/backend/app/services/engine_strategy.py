"""qhfi strategy-engine link.

Lists the live qhfi strategy registry (built-ins plus anything the linked strategies dir
registered via drop-in import) and runs a chosen strategy through the *real*
``BacktestEngine`` over a universe — ``Strategy.generate_weights`` → engine → the same
dashboard payload the factor backtest produces (via ``backtest.shape_result``).

Several qhfi built-ins are still stubs (``raise NotImplementedError``) or need extra inputs
(a trained model); those surface a clean ``ValueError`` the router maps to a 4xx. A user who
drops a fully-implemented ``Strategy`` into the linked dir gets a working engine backtest.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from qhfi.backtest.engine import BacktestEngine
from qhfi.core.types import DateRange
from qhfi.data.manager import DataManager

from app.services import backtest as bt
from app.services import registry as reg
from app.services.universe import get_universe


def _registry():
    import qhfi.strategy.library  # noqa: F401 - importing registers the built-in strategies

    from qhfi.strategy import registry as sreg

    return sreg


def _jsonable(v: Any) -> Any:
    return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)


def list_engine_strategies(store: Any) -> list[dict]:
    """The qhfi strategy registry with each strategy's typed params and a one-line doc."""
    reg.load_dir_modules(reg.get_paths(store)["strategies_dir"])
    sreg = _registry()
    out = []
    for name in sreg.all_names():
        cls = sreg.get(name)
        pm = getattr(cls, "params_model", None)
        params = []
        if pm is not None:
            for fname, fld in pm.model_fields.items():
                params.append({
                    "key": fname,
                    "default": _jsonable(fld.default),
                    "type": type(fld.default).__name__ if fld.default is not None else "float",
                })
        out.append({
            "name": name,
            "label": name,
            "doc": (cls.__doc__ or "").strip().split("\n")[0][:160],
            "params": params,
            "source": "linked" if str(cls.__module__).startswith("qhfi_dropin_") else "builtin",
        })
    return out


def run_engine_strategy(
    dm: DataManager,
    store: Any,
    *,
    strategy_key: str,
    universe_name: str = "dow30",
    years: int = 3,
    initial_equity: float = 100_000.0,
    mode: str = "",
    params: dict | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Run one qhfi strategy through the engine and return the dashboard payload.

    Raises ``ValueError`` (→ 4xx) for an unknown strategy, bad params, an unimplemented
    strategy, or insufficient data.
    """
    reg.load_dir_modules(reg.get_paths(store)["strategies_dir"])
    sreg = _registry()
    try:
        cls = sreg.get(strategy_key)
    except KeyError:
        raise ValueError(f"unknown strategy '{strategy_key}'") from None

    universe = get_universe(universe_name)

    # Defined window: explicit start/end win; otherwise `years` back from today (mirrors
    # backtest.run_backtest's prologue). 200d warm-up so signals have lookback.
    today = date.today()
    win_end_d = min(bt._parse_date(end) or today, today)
    win_start_d = bt._parse_date(start) or (win_end_d - timedelta(days=int(years * 365.25)))
    if win_start_d >= win_end_d:
        raise ValueError("window start must be before end")
    span = DateRange(start=win_start_d - timedelta(days=200), end=win_end_d)
    dm.update(universe, span)
    prices = dm.get_panel(universe, "close", span)
    if prices.empty or prices.shape[1] < 3:
        raise ValueError(f"insufficient data for universe '{universe_name}'")

    pm = getattr(cls, "params_model", None)
    try:
        strat = cls(pm(**(params or {}))) if pm is not None else cls()
    except Exception as e:  # noqa: BLE001 - bad hyperparameters → 4xx
        raise ValueError(f"invalid params: {e}") from None

    return _run_on_prices(
        dm, strat, prices, universe, universe_name, win_start_d, win_end_d,
        initial_equity, label=strategy_key, mode=mode,
    )


def _run_on_prices(
    dm: DataManager, strat: Any, prices, universe, universe_name: str,
    win_start_d: date, win_end_d: date, initial_equity: float, *, label: str, mode: str,
) -> dict:
    """Run a Strategy *instance* over an already-loaded price panel → dashboard payload.

    Resolves the universe's investable benchmark (SPY/BTC, falling back to the equal-weight mean)
    so engine-strategy and portfolio backtests carry the same market model as the factor path.
    """
    try:
        weights = strat.generate_weights(prices, universe)
    except NotImplementedError:
        raise ValueError(f"strategy '{label}' is not yet implemented in qhfi") from None
    result = BacktestEngine(initial_equity=initial_equity).run(weights, prices, universe)
    bench, bench_label = bt.index_benchmark_returns(dm, universe, prices.index)
    return bt.shape_result(
        result, prices, universe, universe_name, win_start_d, win_end_d, initial_equity,
        label_extra={"strategy": label, "factor": "", "mode": mode or "engine"},
        bench=bench, bench_label=bench_label,
    )


def run_model_backtest(
    dm: DataManager,
    store: Any,
    fstore: Any,
    fprov: Any,
    model_name: str,
    *,
    years: int | None = None,
    mode: str | None = None,
    top_pct: float | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Back-test a *saved model bundle* directly and return the standard dashboard payload.

    A model bundles {factor|strategy, universe, mode, params}. This reproduces that exact config —
    honoring stored ``params`` (top_pct / years) unless overridden — and dispatches to the factor
    or engine-strategy backtest. Unlike the portfolio agent it has no side effects (builds/saves no
    portfolio); it just answers "how does this saved idea back-test?". Raises ``ValueError`` (→ 4xx)
    for an unknown model or one bundling neither a factor nor a strategy.
    """
    models = {m["name"]: m for m in reg.list_models(store).get("models", [])}
    if model_name not in models:
        raise ValueError(f"unknown model '{model_name}'")
    b = models[model_name]
    params = b.get("params") or {}
    factor = b.get("factor") or None
    strategy = b.get("strategy") or None
    universe = b.get("universe") or "dow30"
    mode = mode or b.get("mode") or "long_short"
    yrs = int(years if years is not None else params.get("years", 3))

    if factor:
        tp = float(top_pct if top_pct is not None else params.get("top_pct", 0.2))
        out = bt.run_backtest(
            dm, fstore, fprov, universe, factor, mode, tp, yrs, start=start, end=end,
        )
        if "error" in out:
            raise ValueError(out["error"])
    elif strategy:
        out = run_engine_strategy(
            dm, store, strategy_key=strategy, universe_name=universe,
            years=yrs, mode=mode, params=params, start=start, end=end,
        )
    else:
        raise ValueError(f"model '{model_name}' bundles neither a factor nor a strategy")
    out["model"] = model_name
    return out


def run_strategy_instance(
    dm: DataManager,
    strat: Any,
    *,
    universe_name: str = "dow30",
    label: str = "sandbox",
    years: int = 3,
    initial_equity: float = 100_000.0,
    mode: str = "",
) -> dict:
    """Load prices for a universe and run a pre-built Strategy instance through the engine.

    Used by the sandbox's *trusted* strategy mode (author a qhfi ``Strategy`` subclass and run
    it without registering it globally). Mirrors ``run_engine_strategy``'s data prologue.
    """
    universe = get_universe(universe_name)
    today = date.today()
    win_start_d = today - timedelta(days=int(max(1, years) * 365.25))
    span = DateRange(start=win_start_d - timedelta(days=200), end=today)
    dm.update(universe, span)
    prices = dm.get_panel(universe, "close", span)
    if prices.empty or prices.shape[1] < 3:
        raise ValueError(f"insufficient data for universe '{universe_name}'")
    return _run_on_prices(dm, strat, prices, universe, universe_name, win_start_d, today, initial_equity, label=label, mode=mode)
