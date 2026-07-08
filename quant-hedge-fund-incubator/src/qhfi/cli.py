"""qhfi command-line entrypoint (Typer).

Subcommands mirror the incubator lifecycle. Bodies are wiring stubs; each composes the
module contracts defined elsewhere — no business logic lives here.

    qhfi data pull       --universe <yaml>
    qhfi backtest run    --strategy <name> --universe <name>
    qhfi report scorecard --backtest <id>
    qhfi research ideate --theme "<text>"
    qhfi paper run-once  --strategy <name> --universe <name> --broker paper
"""

from __future__ import annotations

import typer

app = typer.Typer(help="Quant Hedge Fund Incubator", no_args_is_help=True)

data_app = typer.Typer(help="data lake: pull/inspect normalized bars")
backtest_app = typer.Typer(help="run vectorized backtests / walk-forward")
report_app = typer.Typer(help="scorecards and tearsheets")
research_app = typer.Typer(help="LLM research agents (local stack)")
paper_app = typer.Typer(help="paper trading loop")
ownership_app = typer.Typer(help="13F manager relationship graph: build/inspect/export")
mm_app = typer.Typer(help="high-frequency market making: calibrate + backtest quoting strategies")
app.add_typer(data_app, name="data")
app.add_typer(backtest_app, name="backtest")
app.add_typer(report_app, name="report")
app.add_typer(research_app, name="research")
app.add_typer(paper_app, name="paper")
app.add_typer(ownership_app, name="ownership")
app.add_typer(mm_app, name="mm")


@data_app.command("pull")
def data_pull(universe: str = typer.Option(..., help="path to universe yaml")) -> None:
    """Fetch + normalize + cache daily bars for every instrument in the universe."""
    raise NotImplementedError("TODO: load universe, route to provider per asset_class, store")


@backtest_app.command("run")
def backtest_run(
    strategy: str = typer.Option(...),
    universe: str = typer.Option(...),
) -> None:
    """Load panel → run strategy → engine → persist result + auto-grade scorecard."""
    raise NotImplementedError("TODO: registry.get(strategy) → engine.run → save_backtest")


@report_app.command("scorecard")
def report_scorecard(backtest: str = typer.Option(..., help="backtest id")) -> None:
    """Print the promotion scorecard for a stored backtest."""
    raise NotImplementedError("TODO: load backtest, Scorecard.grade, render to console")


@research_app.command("ideate")
def research_ideate(
    theme: str = typer.Option(...),
    n: int = typer.Option(5),
) -> None:
    """Ask the local LLM stack for structured strategy hypotheses; record them as IDEAs."""
    raise NotImplementedError("TODO: IdeationAgent.ideate → registry IDEA records")


@research_app.command("sector")
def research_sector(
    sector: str = typer.Option(..., help='GICS sector, e.g. "Information Technology"'),
    universe: str = typer.Option("config/instruments/equity_sectors.yaml", help="universe yaml"),
    hypotheses: int = typer.Option(3, help="number of strategy hypotheses to propose"),
) -> None:
    """LLM-grounded equity research note for one GICS sector (ranked longs/shorts + hypotheses).

    Needs the local vLLM proxy warm on :8001. The ranking is deterministic quant; the LLM only
    adds judgment on top. TODO: persist hypotheses as registry IDEA records once registry lands.
    """
    from qhfi.core.universe_io import load_universe
    from qhfi.data.lake import market_store
    from qhfi.factors.market import MarketPanels
    from qhfi.research.agents.sector import SectorResearchAgent, render_note

    uni = load_universe(universe)
    close = MarketPanels.from_store(market_store(), uni).close
    note = SectorResearchAgent().research(sector, close, uni, n_hypotheses=hypotheses)
    render_note(note)


def _graph_store():
    from qhfi.data.lake import lake_root
    from qhfi.ownership.store import ManagerGraphStore
    return ManagerGraphStore(lake_root())


def _resolve_period(store, period: str | None) -> str:
    ps = store.periods()
    if not ps:
        raise typer.BadParameter("no manager-graph snapshots — run scripts/build_manager_graph.py")
    return period or ps[-1]


@ownership_app.command("build-graph")
def ownership_build_graph() -> None:
    """Build + persist a relationship-graph snapshot per quarter from the 13F holdings lake."""
    from qhfi.data.catalog import refresh
    from qhfi.data.holdings import HoldingsStore
    from qhfi.data.lake import lake_root
    from qhfi.ownership.graph import build_all

    store = _graph_store()
    periods = build_all(HoldingsStore(lake_root()), store)
    refresh()
    typer.echo(f"built {len(periods)} quarterly snapshots: {', '.join(periods) or '(none)'}")


@ownership_app.command("heatmap")
def ownership_heatmap(
    period: str = typer.Option(None, help="quarter (period_of_report); default = latest"),
    metric: str = typer.Option("cosine", help="cosine | jaccard | shared_n | shared_usd"),
) -> None:
    """Render the manager×manager similarity matrix as a colored heatmap."""
    from qhfi.ownership.viz import render_graph_heatmap
    store = _graph_store()
    render_graph_heatmap(store, _resolve_period(store, period), metric)


@ownership_app.command("central")
def ownership_central(
    period: str = typer.Option(None, help="quarter; default = latest"),
) -> None:
    """Print the node-centrality table (eigenvector / degree / strength) for one quarter."""
    store = _graph_store()
    p = _resolve_period(store, period)
    nodes = store.load_nodes(p).sort_values("eigenvector_cent", ascending=False)
    cols = ["manager", "eigenvector_cent", "degree", "weighted_degree", "n_positions", "value_usd_bn"]
    typer.echo(f"manager centrality · {p}\n")
    typer.echo(nodes[cols].to_string(index=False))


@ownership_app.command("changes")
def ownership_changes(
    metric: str = typer.Option("cosine", help="edge metric to diff"),
    lookback: int = typer.Option(1, help="quarters back to compare against the latest"),
) -> None:
    """List emerging / fading / new / dropped manager relationships between two quarters."""
    from qhfi.ownership.metrics import relationship_deltas
    df = relationship_deltas(_graph_store(), metric=metric, lookback=lookback)
    if df.empty:
        typer.echo("not enough snapshots to compare (need ≥ lookback+1 quarters).")
        return
    show = df.head(25).copy()
    show["pair"] = show["manager_a"] + " ↔ " + show["manager_b"]
    typer.echo(show[["pair", "prev", "curr", "delta", "status"]].to_string(index=False))


@ownership_app.command("export")
def ownership_export(
    out: str = typer.Option(..., help="output .json path"),
    period: str = typer.Option(None, help="quarter; default = latest"),
    metric: str = typer.Option("cosine"),
    min_weight: float = typer.Option(0.0, help="drop edges below this weight"),
) -> None:
    """Export one quarter's graph as networkx-compatible node-link JSON."""
    from qhfi.ownership.viz import write_node_link
    store = _graph_store()
    p = _resolve_period(store, period)
    path = write_node_link(store, p, out, metric=metric, min_weight=min_weight)
    typer.echo(f"wrote {path} ({p}, metric={metric})")


@ownership_app.command("dashboard")
def ownership_dashboard(
    out: str = typer.Option("reports/manager_graph.html", help="output .html path"),
    metric: str = typer.Option("cosine", help="edge weight: cosine | jaccard | shared_n | shared_usd"),
) -> None:
    """Render a self-contained interactive HTML dashboard (all quarters embedded)."""
    from pathlib import Path

    from qhfi.ownership.dashboard import write_dashboard
    store = _graph_store()
    if not store.periods():
        raise typer.BadParameter("no manager-graph snapshots — run scripts/build_manager_graph.py")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    path = write_dashboard(store, out, metric=metric)
    typer.echo(f"wrote {path} ({len(store.periods())} quarters, metric={metric})")


@paper_app.command("run-once")
def paper_run_once(
    strategy: str = typer.Option(...),
    universe: str = typer.Option(...),
    broker: str = typer.Option("paper"),
) -> None:
    """Execute one daily paper-trading cycle for a VALIDATED strategy."""
    raise NotImplementedError("TODO: build PaperLoop, run_once(), record cycle")


@mm_app.command("calibrate")
def mm_calibrate(
    symbol: str = typer.Option("BTC/USDT", help="crypto symbol"),
    source: str = typer.Option("okx", help="exchange/source"),
) -> None:
    """Estimate Avellaneda–Stoikov inputs (σ, κ, A) from the recorded book/trade lake."""
    import runpy
    import sys as _sys

    _sys.argv = ["calibrate_as_params.py", "--symbol", symbol, "--source", source]
    runpy.run_path("scripts/calibrate_as_params.py", run_name="__main__")


@mm_app.command("backtest")
def mm_backtest(
    symbol: str = typer.Option("BTC/USDT", help="crypto symbol with recorded L2 book"),
    strategy: str = typer.Option("AvellanedaStoikovMM", help="quoting strategy (mm_registry)"),
    source: str = typer.Option("okx", help="exchange/source"),
    gamma: float = typer.Option(0.1, help="AS risk aversion"),
    kappa: float = typer.Option(1.5, help="AS order-arrival decay"),
    q_max: float = typer.Option(100.0, help="inventory limit (units)"),
    obi_alpha: float = typer.Option(0.5, help="OBI tilt on fair value"),
    quote_size: float = typer.Option(1.0, help="size per side"),
    equity: float = typer.Option(100_000.0, help="initial equity"),
    queue_model: bool = typer.Option(True, help="model queue position (needs queue depth)"),
    use_trades: bool = typer.Option(True, help="interleave the trade tape when present"),
) -> None:
    """Replay recorded L2 books through a quoting strategy and print the market-making panel."""
    from qhfi.backtest.eventdriven.engine import MarketMakingEngine
    from qhfi.core.types import AssetClass, Instrument, Universe
    from qhfi.data.highfreq import OrderBookStore, TradeStore
    from qhfi.data.lake import lake_root
    from qhfi.data.microstructure import book_features
    from qhfi.evaluation.mm_metrics import mm_summary
    from qhfi.strategy import mm_registry
    from qhfi.strategy.library.mm.avellaneda_stoikov import ASParams

    root = lake_root()
    obs = OrderBookStore(root)
    if not obs.has(symbol, source=source):
        raise typer.BadParameter(
            f"no recorded book for {symbol} ({source}) — run scripts/pull_orderbook_stream.py first.")
    book = obs.load(symbol, source=source)

    trades = None
    tstore = TradeStore(root)
    if use_trades and tstore.has(symbol, source=source):
        df = tstore.load(symbol, source=source)
        trades = {symbol: df}

    uni = Universe(name="mm", instruments=[
        Instrument(id=symbol, asset_class=AssetClass.CRYPTO, exchange=source, lot_size=1e-12)])
    params = ASParams(gamma=gamma, kappa=kappa, q_max=q_max, obi_alpha=obi_alpha, quote_size=quote_size)
    strat = mm_registry.get(strategy)(params)

    engine = MarketMakingEngine(initial_equity=equity, queue_model=queue_model)
    result = engine.run_quoting(strat, {symbol: book}, uni, trades=trades)

    mid = book_features(book)["mid"]
    summ = mm_summary(result, mid=mid, instrument=symbol)
    typer.echo(f"\nmarket-making backtest · {strategy} · {symbol}@{source}  "
               f"({len(result.equity_curve)} snapshots, tape={'on' if trades else 'off'})\n")
    for k, v in summ.items():
        typer.echo(f"  {k:<22} {v: .4f}")


if __name__ == "__main__":
    app()
