import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset, PaperBook, PaperOrderIn, PaperOrderType } from "../api/types";
import FlashCell from "../components/FlashCell";
import { cx, fmtCompact, fmtPct, fmtPrice } from "../lib/format";
import type { WidgetParams, WidgetProps } from "../workspace/widgetRegistry";
import { IconButton, SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState } from "../components/States";
import { simBook, useAccounts, useActiveAccountId } from "../state/accounts";

const POLL_MS = 10_000;

/** Tiny dependency-free equity-curve sparkline (SVG polyline, auto-scaled). */
function Sparkline({ data, className }: { data: number[]; className?: string }) {
  if (data.length < 2) return null;
  const W = 120;
  const H = 28;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * W;
      const y = H - ((v - min) / span) * H;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const up = data[data.length - 1] >= data[0];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={cx("h-7 w-full", className)}>
      <polyline
        points={pts}
        fill="none"
        strokeWidth={1.25}
        className={up ? "stroke-term-up" : "stroke-term-down"}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/** One labelled metric in the performance strip. */
function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <span className="flex flex-col leading-tight">
      <span className="text-[8px] uppercase tracking-wider text-term-muted">{label}</span>
      <span className={cx("font-mono text-[11px]", tone)}>{value}</span>
    </span>
  );
}

/** A titled row of metrics in the Stats tab. */
function StatGroup({ title, children }: { title: string; children: import("react").ReactNode }) {
  return (
    <div className="border-b border-term-border/40 px-2 py-1.5">
      <div className="mb-1 text-[8px] uppercase tracking-wider text-term-muted/70">{title}</div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">{children}</div>
    </div>
  );
}

const sgn = (v: number) => (v >= 0 ? "text-term-up" : "text-term-down");

/**
 * Stats tab: trade-level analytics, tail risk, benchmark-relative stats, and engine ops.
 * Everything here is sim-owned; Alpaca books surface a hint instead.
 */
function StatsPanel({
  perf,
  ops,
  viewingSim,
}: {
  perf: import("../api/types").PaperPerformance | undefined;
  ops: import("../api/types").PaperOps | undefined;
  viewingSim: boolean;
}) {
  if (!viewingSim) {
    return <EmptyState title="Stats are sim-only" hint="Switch to the SIM book; Alpaca analytics live on its dashboard." />;
  }
  const ts = perf?.trade_stats;
  const risk = perf?.risk;
  const bm = perf?.benchmark;
  if (!ts || ts.n_trades === 0) {
    return <EmptyState title="No stats yet" hint="Close a round-trip trade to populate analytics." />;
  }
  return (
    <div>
      <StatGroup title="Trades">
        <Metric label="Win rate" value={fmtPct(ts.win_rate * 100)} tone={ts.win_rate >= 0.5 ? "text-term-up" : undefined} />
        <Metric label="W / L" value={`${ts.n_wins} / ${ts.n_losses}`} />
        <Metric label="Profit factor" value={ts.profit_factor.toFixed(2)} tone={ts.profit_factor >= 1 ? "text-term-up" : "text-term-down"} />
        <Metric label="Payoff" value={ts.payoff_ratio.toFixed(2)} />
        <Metric label="Expectancy" value={`${ts.expectancy >= 0 ? "+" : ""}$${fmtCompact(ts.expectancy)}`} tone={sgn(ts.expectancy)} />
        <Metric label="Avg win" value={`$${fmtCompact(ts.avg_win)}`} tone="text-term-up" />
        <Metric label="Avg loss" value={`$${fmtCompact(ts.avg_loss)}`} tone="text-term-down" />
        <Metric label="Best / Worst" value={`$${fmtCompact(ts.largest_win)} / $${fmtCompact(ts.largest_loss)}`} />
      </StatGroup>

      {risk && (
        <StatGroup title="Risk">
          <Metric label="VaR 95%" value={risk.var_95 != null ? fmtPct(risk.var_95 * 100) : "—"} tone="text-term-down" />
          <Metric label="CVaR 95%" value={risk.cvar_95 != null ? fmtPct(risk.cvar_95 * 100) : "—"} tone="text-term-down" />
          <Metric
            label="Roll Sharpe"
            value={risk.rolling_sharpe.length ? risk.rolling_sharpe[risk.rolling_sharpe.length - 1].toFixed(2) : "—"}
          />
        </StatGroup>
      )}

      <StatGroup title={bm ? `Vs ${bm.symbol}` : "Vs SPY"}>
        {bm ? (
          <>
            <Metric label="Alpha (ann)" value={fmtPct(bm.alpha * 100)} tone={sgn(bm.alpha)} />
            <Metric label="Beta" value={bm.beta.toFixed(2)} />
            <Metric label="Info ratio" value={bm.information_ratio.toFixed(2)} tone={sgn(bm.information_ratio)} />
            <Metric label="Track err" value={fmtPct(bm.tracking_error * 100)} />
            <Metric label="Up / Dn cap" value={`${bm.up_capture.toFixed(2)} / ${bm.down_capture.toFixed(2)}`} />
            <Metric label="Corr" value={bm.correlation.toFixed(2)} />
          </>
        ) : (
          <span className="text-[10px] text-term-muted">Needs ≥2 calendar days of history.</span>
        )}
      </StatGroup>

      <StatGroup title="Execution">
        {ops?.applicable ? (
          <>
            <Metric label="Fill rate" value={ops.fill_rate != null ? fmtPct(ops.fill_rate * 100) : "—"} />
            <Metric label="Avg latency" value={ops.latency ? `${ops.latency.avg_s.toFixed(2)}s` : "—"} />
            <Metric label="Max latency" value={ops.latency ? `${ops.latency.max_s.toFixed(2)}s` : "—"} />
            <Metric
              label="Filled/Open/Cxl"
              value={`${ops.counts.filled ?? 0}/${ops.counts.open ?? 0}/${ops.counts.cancelled ?? 0}`}
            />
          </>
        ) : (
          <span className="text-[10px] text-term-muted">Loading…</span>
        )}
      </StatGroup>
    </div>
  );
}

export default function PaperTradingWidget(props: WidgetProps) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const qc = useQueryClient();

  // Active sim account: per-widget override (panel param) → else the global active account.
  const globalActiveId = useActiveAccountId();
  const setGlobalActive = useAccounts((s) => s.setActiveAccount);
  const overrideId = (props.params as { accountId?: number }).accountId;
  const accountId = overrideId ?? globalActiveId;

  // Planned orders streamed in by the Agent Workflow's `execution` node: show once as a read-only
  // banner, then clear the param (consume-once, mirrors BacktestWidget.incomingResult). Display-only —
  // it never places an order or touches the live account.
  const [plannedOrders, setPlannedOrders] = useState<WidgetParams["incomingOrders"] | null>(null);
  useEffect(() => {
    const o = props.params.incomingOrders;
    if (o) {
      setPlannedOrders(o);
      props.api.updateParameters({ incomingOrders: undefined });
    }
  }, [props.params.incomingOrders, props.api]);

  const { data: accountsData } = useQuery({ queryKey: ["paper-accounts"], queryFn: api.paperAccounts });
  const accounts = accountsData?.accounts ?? [];

  const { data: cfg } = useQuery({
    queryKey: ["paper-config", accountId],
    queryFn: () => api.paperConfig(accountId),
  });

  // When Alpaca is the primary broker, a local sim sandbox runs alongside it and the user picks
  // whether to view the Alpaca book or a sim account. With no Alpaca keys there's only the sim books.
  const alpacaActive = cfg?.alpaca_active ?? false;
  const [viewPrimary, setViewPrimary] = useState(false);
  const viewingSim = !alpacaActive || !viewPrimary;
  // Sim view targets the selected account (sim:<id>); the Alpaca view targets 'primary'.
  const apiBook: PaperBook = viewingSim ? simBook(accountId) : "primary";

  // Switch the active account: persist as this panel's override AND promote to the global default
  // (the Paper widget is the canonical place to switch the terminal-wide active account).
  const selectAccount = (id: number) => {
    props.api.updateParameters({ accountId: id });
    setGlobalActive(id);
    setViewPrimary(false);
  };

  const { data: account, isLoading } = useQuery({
    queryKey: ["paper-account", apiBook],
    queryFn: () => api.paperAccount(apiBook),
    refetchInterval: POLL_MS,
  });
  const { data: orders } = useQuery({
    queryKey: ["paper-orders", apiBook],
    queryFn: () => api.paperOrders(apiBook),
    refetchInterval: POLL_MS,
  });
  const { data: perf } = useQuery({
    queryKey: ["paper-performance", apiBook],
    queryFn: () => api.paperPerformance(apiBook),
    refetchInterval: POLL_MS,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["paper-account"] });
    qc.invalidateQueries({ queryKey: ["paper-orders"] });
    qc.invalidateQueries({ queryKey: ["paper-performance"] });
    qc.invalidateQueries({ queryKey: ["paper-accounts"] });
  };

  // unrealized P&L summed across open positions; realized comes from the closed-trade ledger
  const unrealized = account?.positions.reduce((s, p) => s + p.unrealized_pnl, 0) ?? 0;
  const [bottomTab, setBottomTab] = useState<"orders" | "closed" | "stats">("orders");

  // operational metrics (sim-owned) — only fetched while the Stats tab is open
  const { data: ops } = useQuery({
    queryKey: ["paper-ops", apiBook],
    queryFn: () => api.paperOps(apiBook),
    refetchInterval: POLL_MS,
    enabled: bottomTab === "stats" && viewingSim,
  });

  // ── order ticket ──
  const [tSymbol, setTSymbol] = useState("");
  const [tAsset, setTAsset] = useState<Asset>("equity");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState(10);
  const [otype, setOtype] = useState<PaperOrderType>("market");
  const [limit, setLimit] = useState(0);
  const [stop, setStop] = useState(0);
  const [trail, setTrail] = useState(5);
  const [confirm, setConfirm] = useState(false);

  const ticketSymbol = (tSymbol || symbol).toUpperCase();
  const ticketAsset = tSymbol ? tAsset : asset;
  const hasLimit = otype === "limit" || otype === "stop_limit";
  const hasStop = otype === "stop" || otype === "stop_limit";
  const hasTrail = otype === "trailing_stop";

  const orderBody = (): PaperOrderIn => ({
    symbol: ticketSymbol,
    asset: ticketAsset,
    side,
    quantity: qty,
    type: otype,
    limit_price: hasLimit ? limit : null,
    stop_price: hasStop ? stop : null,
    trail_pct: hasTrail ? trail : null,
  });

  const submit = useMutation({
    mutationFn: () => api.submitPaperOrder(orderBody(), apiBook),
    onSuccess: () => {
      setConfirm(false);
      refresh();
    },
  });
  const reset = useMutation({ mutationFn: () => api.resetPaperAccount(accountId), onSuccess: refresh });
  const cancel = useMutation({ mutationFn: (id: number | string) => api.cancelPaperOrder(id, apiBook), onSuccess: refresh });
  const closePos = useMutation({
    mutationFn: (p: { symbol: string; asset: Asset }) => api.closePaperPosition(p.symbol, p.asset, apiBook),
    onSuccess: refresh,
  });
  const flatten = useMutation({ mutationFn: () => api.flattenPaper(apiBook), onSuccess: refresh });

  // ── pre-trade preview (fetched when the confirm modal opens) ──
  const { data: preview } = useQuery({
    queryKey: ["paper-preview", accountId, ticketSymbol, ticketAsset, side, qty, otype, limit, stop],
    queryFn: () => api.previewPaperOrder(orderBody(), accountId),
    enabled: confirm && viewingSim,
  });

  // ── realism (sim-only commission + slippage, persisted) ──
  const [showRealism, setShowRealism] = useState(false);
  const [commBps, setCommBps] = useState(0);
  const [slipBps, setSlipBps] = useState(0);
  useEffect(() => {
    if (cfg) {
      setCommBps(cfg.commission_bps);
      setSlipBps(cfg.slippage_bps);
    }
  }, [cfg?.commission_bps, cfg?.slippage_bps]); // eslint-disable-line react-hooks/exhaustive-deps
  const saveRealism = useMutation({
    mutationFn: () => api.setPaperConfig({ commission_bps: commBps, slippage_bps: slipBps }, accountId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["paper-config"] });
      qc.invalidateQueries({ queryKey: ["paper-accounts"] });
      setShowRealism(false);
    },
  });

  // ── account management (create / rename / delete sim books) ──
  const [showAccounts, setShowAccounts] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCash, setNewCash] = useState(100_000);
  const createAcct = useMutation({
    mutationFn: () => api.createPaperAccount({ name: newName.trim(), initial_cash: newCash }),
    onSuccess: (r) => {
      setNewName("");
      qc.invalidateQueries({ queryKey: ["paper-accounts"] });
      if (r.account) selectAccount(r.account.id);
    },
  });
  const renameAcct = useMutation({
    mutationFn: (p: { id: number; name: string }) => api.updatePaperAccount(p.id, { name: p.name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["paper-accounts"] }),
  });
  const deleteAcct = useMutation({
    mutationFn: (id: number) => api.deletePaperAccount(id),
    onSuccess: (_r, id) => {
      if (id === accountId) selectAccount(1);
      qc.invalidateQueries({ queryKey: ["paper-accounts"] });
    },
  });

  const statusColor: Record<string, string> = {
    filled: "text-term-up",
    open: "text-term-accent",
    cancelled: "text-term-muted",
  };

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={viewingSim ? "delayed" : "live"}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">Paper Trading</span>
          {alpacaActive && (
            // both books are live; toggle whether this widget views the Alpaca book or a sim account
            <span className="flex items-center gap-px rounded border border-term-border" title="Switch book">
              <button
                onClick={() => setViewPrimary(true)}
                className={cx(
                  "px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider",
                  viewPrimary ? "bg-term-up/20 text-term-up" : "text-term-muted hover:text-term-text",
                )}
                title="Alpaca paper (real orders)"
              >
                ALPACA
              </button>
              <button
                onClick={() => setViewPrimary(false)}
                className={cx(
                  "px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider",
                  !viewPrimary ? "bg-term-border/60 text-term-text" : "text-term-muted hover:text-term-text",
                )}
                title="Local sim sandbox"
              >
                SIM
              </button>
            </span>
          )}
          {viewingSim ? (
            // account switcher — the global active sim book (also overridable per panel)
            <span className="flex items-center gap-1">
              <select
                value={accountId}
                onChange={(e) => selectAccount(Number(e.target.value))}
                aria-label="Sim account"
                className="focus-ring max-w-[10rem] rounded border border-term-border bg-term-sunken px-1 py-px text-[10px] text-term-text"
                title="Active sim account (book)"
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              <button
                onClick={() => setShowAccounts((s) => !s)}
                className={cx(
                  "rounded border px-1.5 py-px text-[10px]",
                  showAccounts ? "border-term-accent text-term-accent" : "border-term-border text-term-muted hover:text-term-text",
                )}
                title="Manage accounts"
              >
                ⋯
              </button>
            </span>
          ) : (
            <span className="rounded border border-term-up/60 px-1 py-px text-[9px] font-semibold uppercase tracking-wider text-term-up">
              ALPACA
            </span>
          )}
          {account && (
            <span className="ml-2 font-mono text-xs">
              <span className="text-term-muted">eq</span>{" "}
              <FlashCell value={account.equity}>${fmtCompact(account.equity)}</FlashCell>{" "}
              <span className="text-term-muted">cash</span> ${fmtCompact(account.cash)}{" "}
              <span className="text-term-muted">uP&L</span>{" "}
              <span className={unrealized >= 0 ? "text-term-up" : "text-term-down"}>
                {unrealized >= 0 ? "+" : ""}${fmtCompact(unrealized)}
              </span>
              {account.realized_pnl != null && (
                <>
                  {" "}
                  <span className="text-term-muted">rP&L</span>{" "}
                  <span className={account.realized_pnl >= 0 ? "text-term-up" : "text-term-down"}>
                    {account.realized_pnl >= 0 ? "+" : ""}${fmtCompact(account.realized_pnl)}
                  </span>
                </>
              )}
            </span>
          )}
          {viewingSim && (
            <span className="ml-auto flex items-center gap-1">
              <button
                onClick={() => setShowRealism((s) => !s)}
                className={cx(
                  "rounded border px-2 py-0.5 text-[10px] uppercase tracking-wide",
                  cfg && (cfg.commission_bps > 0 || cfg.slippage_bps > 0)
                    ? "border-term-accent text-term-accent"
                    : "border-term-border text-term-muted hover:text-term-text",
                )}
                title="Commission & slippage realism"
              >
                ⚙ {cfg ? `${cfg.commission_bps + cfg.slippage_bps}bps` : "realism"}
              </button>
              <button
                onClick={() => reset.mutate()}
                className="rounded border border-term-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-muted hover:text-term-down"
                title="Reset simulated account"
              >
                Reset
              </button>
            </span>
          )}
        </>
      }
    >
      <div className="flex h-full flex-col">
        {/* ── planned orders from the Agent Workflow (read-only) ── */}
        {plannedOrders && plannedOrders.orders.length > 0 && (
          <div className="flex shrink-0 items-start gap-2 border-b border-term-border bg-term-accent/5 px-2 py-1.5">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-[9px] font-semibold uppercase tracking-wider text-term-accent">Planned orders</span>
                <span
                  className={cx(
                    "rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wider",
                    plannedOrders.armed
                      ? "border-term-accent bg-term-accent/10 text-term-accent"
                      : "border-term-border bg-term-sunken text-term-muted",
                  )}
                >
                  {plannedOrders.armed ? "Armed" : "Dry-run"}
                </span>
                <span className="text-[9px] text-term-muted">· from Agent Workflow</span>
              </div>
              <div className="space-y-0.5">
                {plannedOrders.orders.map((o, i) => (
                  <div key={i} className="font-mono text-[11px] text-term-text/90">{o}</div>
                ))}
              </div>
            </div>
            <button
              onClick={() => setPlannedOrders(null)}
              className="shrink-0 text-term-muted hover:text-term-text"
              title="Dismiss planned orders"
            >
              ×
            </button>
          </div>
        )}
        {/* ── order ticket ── */}
        <div className="shrink-0 border-b border-term-border p-2">
          <div className="flex flex-wrap items-end gap-1.5">
            <div className="flex items-center gap-px rounded border border-term-border">
              {(["buy", "sell"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setSide(s)}
                  className={cx(
                    "px-2.5 py-0.5 text-[10px] font-semibold uppercase",
                    side === s
                      ? s === "buy"
                        ? "bg-term-up/20 text-term-up"
                        : "bg-term-down/20 text-term-down"
                      : "text-term-muted hover:text-term-text",
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
            <input
              value={tSymbol}
              onChange={(e) => setTSymbol(e.target.value)}
              placeholder={symbol}
              aria-label={symbol}
              className="focus-ring w-24 rounded border border-term-border bg-term-sunken px-2 py-0.5 font-mono text-xs uppercase focus:border-term-accent"
            />
            <select
              value={tAsset}
              onChange={(e) => setTAsset(e.target.value as Asset)}
              aria-label="Asset class"
              className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-muted"
            >
              <option value="equity">equity</option>
              <option value="crypto">crypto</option>
            </select>
            <label className="flex items-center gap-1 text-[10px] text-term-muted">
              qty
              <input
                type="number"
                min={0}
                step="any"
                value={qty}
                onChange={(e) => setQty(Number(e.target.value))}
                aria-label="Quantity"
                className="focus-ring w-20 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
              />
            </label>
            <select
              value={otype}
              onChange={(e) => setOtype(e.target.value as PaperOrderType)}
              aria-label="Order type"
              className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-muted"
            >
              <option value="market">market</option>
              <option value="limit">limit</option>
              <option value="stop">stop</option>
              <option value="stop_limit">stop limit</option>
              <option value="trailing_stop">trailing stop</option>
            </select>
            {hasStop && (
              <input
                type="number"
                step="any"
                value={stop}
                onChange={(e) => setStop(Number(e.target.value))}
                placeholder="stop $"
                aria-label="stop $"
                className="focus-ring w-20 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
              />
            )}
            {hasLimit && (
              <input
                type="number"
                step="any"
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                placeholder="limit $"
                aria-label="limit $"
                className="focus-ring w-20 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
              />
            )}
            {hasTrail && (
              <label className="flex items-center gap-1 text-[10px] text-term-muted">
                trail
                <input
                  type="number"
                  min={0}
                  step="any"
                  value={trail}
                  onChange={(e) => setTrail(Number(e.target.value))}
                  aria-label="trail %"
                  className="focus-ring w-16 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
                />
                %
              </label>
            )}
            <button
              onClick={() => setConfirm(true)}
              disabled={qty <= 0}
              className={cx(
                "rounded border px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide disabled:opacity-40",
                side === "buy" ? "border-term-up text-term-up hover:bg-term-up/10" : "border-term-down text-term-down hover:bg-term-down/10",
              )}
            >
              {side} {ticketSymbol}
            </button>
          </div>
          {submit.error && <div className="mt-1 text-[10px] text-term-down">{(submit.error as Error).message}</div>}
        </div>

        {/* ── accounts manager (sim only) ── */}
        {viewingSim && showAccounts && (
          <div className="shrink-0 space-y-1.5 border-b border-term-border bg-term-sunken/40 px-2 py-1.5">
            <div className="text-[9px] uppercase tracking-wider text-term-muted">Sim accounts (books)</div>
            <div className="max-h-28 space-y-0.5 overflow-auto">
              {accounts.map((a) => (
                <div key={a.id} className="flex items-center gap-1.5 text-[11px]">
                  <button
                    onClick={() => selectAccount(a.id)}
                    className={cx(
                      "w-3 shrink-0 text-center",
                      a.id === accountId ? "text-term-accent" : "text-term-muted hover:text-term-text",
                    )}
                    title="Make active"
                  >
                    {a.id === accountId ? "●" : "○"}
                  </button>
                  <span className="min-w-0 flex-1 truncate font-medium">{a.name}</span>
                  <span className="shrink-0 font-mono text-term-muted">${fmtCompact(a.cash)}</span>
                  <button
                    onClick={() => {
                      const name = window.prompt("Rename account", a.name)?.trim();
                      if (name) renameAcct.mutate({ id: a.id, name });
                    }}
                    className="shrink-0 text-term-muted hover:text-term-text"
                    title="Rename"
                  >
                    ✎
                  </button>
                  {a.id !== 1 && (
                    <button
                      onClick={() => {
                        if (window.confirm(`Delete account "${a.name}"? Its book is wiped.`))
                          deleteAcct.mutate(a.id);
                      }}
                      className="shrink-0 text-term-muted hover:text-term-down"
                      title="Delete"
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
            </div>
            <div className="flex flex-wrap items-end gap-1.5 border-t border-term-border pt-1.5">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="New account name"
                aria-label="New account name"
                className="focus-ring w-36 rounded border border-term-border bg-term-sunken px-1.5 py-0.5 text-[11px]"
              />
              <label className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-term-muted">
                cash
                <input
                  type="number"
                  min={0}
                  step="any"
                  value={newCash}
                  onChange={(e) => setNewCash(Number(e.target.value))}
                  aria-label="Initial cash"
                  className="focus-ring w-24 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-[11px]"
                />
              </label>
              <button
                onClick={() => createAcct.mutate()}
                disabled={!newName.trim() || newCash <= 0 || createAcct.isPending}
                className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
              >
                {createAcct.isPending ? "Adding…" : "Add"}
              </button>
            </div>
            {(createAcct.error || deleteAcct.error) && (
              <div className="text-[10px] text-term-down">
                {((createAcct.error || deleteAcct.error) as Error).message}
              </div>
            )}
          </div>
        )}

        {/* ── realism editor (sim only) ── */}
        {viewingSim && showRealism && (
          <div className="flex shrink-0 flex-wrap items-end gap-2 border-b border-term-border bg-term-sunken/40 px-2 py-1.5">
            <label className="flex flex-col text-[9px] uppercase tracking-wider text-term-muted">
              Commission bps
              <input
                type="number"
                min={0}
                step="any"
                value={commBps}
                onChange={(e) => setCommBps(Number(e.target.value))}
                className="focus-ring mt-0.5 w-20 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
              />
            </label>
            <label className="flex flex-col text-[9px] uppercase tracking-wider text-term-muted">
              Slippage bps
              <input
                type="number"
                min={0}
                step="any"
                value={slipBps}
                onChange={(e) => setSlipBps(Number(e.target.value))}
                className="focus-ring mt-0.5 w-20 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
              />
            </label>
            <button
              onClick={() => saveRealism.mutate()}
              disabled={saveRealism.isPending}
              className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
            >
              {saveRealism.isPending ? "Saving…" : "Save"}
            </button>
            <span className="text-[9px] text-term-muted">
              Market fills cross the spread; commission is charged per fill.
            </span>
          </div>
        )}

        {/* ── performance strip ── */}
        {perf && perf.equity_curve.length >= 2 && (
          <div className="flex shrink-0 items-center gap-3 border-b border-term-border px-2 py-1.5">
            <div className="w-28 shrink-0">
              <Sparkline data={perf.equity_curve.map((p) => p.equity)} />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <Metric
                label="Sharpe"
                value={perf.metrics.sharpe != null ? perf.metrics.sharpe.toFixed(2) : "—"}
                tone={(perf.metrics.sharpe ?? 0) >= 0 ? "text-term-up" : "text-term-down"}
              />
              <Metric label="Sortino" value={perf.metrics.sortino != null ? perf.metrics.sortino.toFixed(2) : "—"} />
              <Metric
                label="Max DD"
                value={perf.metrics.max_drawdown != null ? fmtPct(perf.metrics.max_drawdown * 100) : "—"}
                tone="text-term-down"
              />
              <Metric label="CAGR" value={perf.metrics.cagr != null ? fmtPct(perf.metrics.cagr * 100) : "—"} />
            </div>
          </div>
        )}

        {/* ── exposure ── */}
        {account && account.exposure.gross > 0 && (
          <div className="flex shrink-0 items-center gap-3 border-b border-term-border px-2 py-1.5">
            {/* long/short stacked bar, widths ∝ share of gross */}
            <div className="flex h-2.5 w-28 shrink-0 overflow-hidden rounded-sm bg-term-sunken" title="Long vs short share of gross">
              <div className="h-full bg-term-up" style={{ width: `${(account.exposure.long / account.exposure.gross) * 100}%` }} />
              <div className="h-full bg-term-down" style={{ width: `${(account.exposure.short / account.exposure.gross) * 100}%` }} />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <Metric label="Gross" value={`$${fmtCompact(account.exposure.gross)} · ${fmtPct(account.exposure.gross_pct)}`} />
              <Metric
                label="Net"
                value={`$${fmtCompact(account.exposure.net)} · ${fmtPct(account.exposure.net_pct)}`}
                tone={account.exposure.net >= 0 ? "text-term-up" : "text-term-down"}
              />
              <Metric
                label="L / S"
                value={`${account.exposure.long_count} / ${account.exposure.short_count}`}
              />
              <Metric
                label="Concen."
                value={`${fmtPct(account.exposure.largest_pct)} · HHI ${account.exposure.concentration_hhi.toFixed(2)}`}
                tone={account.exposure.concentration_hhi > 0.5 ? "text-term-down" : undefined}
              />
            </div>
          </div>
        )}

        {/* ── positions ── */}
        <div className="min-h-0 flex-1 overflow-auto">
          {isLoading && <SkeletonRows rows={4} />}
          {account && account.positions.length > 0 ? (
            <table className="w-full border-collapse text-[11px]">
              <thead className="sticky top-0 bg-term-panel">
                <tr className="border-b border-term-border text-[9px] uppercase tracking-wider text-term-muted">
                  <th className="px-2 py-1 text-left">Symbol</th>
                  <th className="px-2 py-1 text-right">Qty</th>
                  <th className="px-2 py-1 text-right">Avg</th>
                  <th className="px-2 py-1 text-right">Last</th>
                  <th className="px-2 py-1 text-right">Value</th>
                  <th className="px-2 py-1 text-right">uP&L</th>
                  <th className="px-1 py-1 text-right">
                    <button
                      onClick={() => flatten.mutate()}
                      disabled={flatten.isPending}
                      className="text-[8px] uppercase tracking-wider text-term-muted hover:text-term-down disabled:opacity-40"
                      title="Market-close every position"
                    >
                      Flatten
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {account.positions.map((p) => (
                  <tr
                    key={p.symbol}
                    onClick={() => setTSymbol(p.symbol)}
                    className="cursor-pointer border-b border-term-border/30 hover:bg-term-border/30"
                  >
                    <td className={cx("px-2 py-0.5 font-semibold", p.quantity >= 0 ? "text-term-up" : "text-term-down")}>{p.symbol}</td>
                    <td className="px-2 py-0.5 text-right">{p.quantity}</td>
                    <td className="px-2 py-0.5 text-right text-term-muted">{fmtPrice(p.avg_price)}</td>
                    <td className="px-2 py-0.5 text-right">
                      <FlashCell value={p.last}>{fmtPrice(p.last)}</FlashCell>
                    </td>
                    <td className="px-2 py-0.5 text-right">${fmtCompact(p.market_value)}</td>
                    <td className={cx("px-2 py-0.5 text-right", p.unrealized_pnl >= 0 ? "text-term-up" : "text-term-down")}>
                      {p.unrealized_pnl >= 0 ? "+" : ""}${fmtCompact(p.unrealized_pnl)} ({fmtPct(p.unrealized_pct)})
                    </td>
                    <td className="px-1 py-0.5 text-right">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          closePos.mutate({ symbol: p.symbol, asset: p.asset });
                        }}
                        className="rounded border border-term-border px-1 text-[9px] uppercase text-term-muted hover:text-term-down"
                        title={`Close ${p.symbol}`}
                      >
                        Close
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            account && <EmptyState title="No open positions" hint="Place an order above." />
          )}
        </div>

        {/* ── bottom pane: orders / closed trades ── */}
        <div className="flex h-32 shrink-0 flex-col border-t border-term-border">
          <div className="flex shrink-0 items-center gap-1 px-2 py-1">
            {(["orders", "closed", "stats"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setBottomTab(tab)}
                className={cx(
                  "rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wider",
                  bottomTab === tab ? "bg-term-border/50 text-term-text" : "text-term-muted hover:text-term-text",
                )}
              >
                {tab === "orders" ? "Orders" : tab === "closed" ? "Closed" : "Stats"}
              </button>
            ))}
            {bottomTab === "closed" && perf?.realized_total != null && (
              <span className="ml-auto text-[9px] uppercase tracking-wider text-term-muted">
                realized{" "}
                <span className={cx("font-mono", perf.realized_total >= 0 ? "text-term-up" : "text-term-down")}>
                  {perf.realized_total >= 0 ? "+" : ""}${fmtCompact(perf.realized_total)}
                </span>
              </span>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            {bottomTab === "orders" ? (
              orders && orders.orders.length > 0 ? (
                <table className="w-full border-collapse text-[10px]">
                  <thead className="sticky top-0 bg-term-panel">
                    <tr className="border-b border-term-border text-[9px] uppercase tracking-wider text-term-muted">
                      <th className="px-2 py-1 text-left">Time</th>
                      <th className="px-2 py-1 text-left">Order</th>
                      <th className="px-2 py-1 text-right">Fill</th>
                      <th className="px-2 py-1 text-left">Status</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {orders.orders.map((o) => (
                      <tr key={o.id} className="border-b border-term-border/30">
                        <td className="px-2 py-0.5 text-term-muted">{o.ts.slice(5, 16)}</td>
                        <td className="px-2 py-0.5">
                          <span className={o.side === "buy" ? "text-term-up" : "text-term-down"}>{o.side}</span> {o.quantity}{" "}
                          {o.symbol}
                          {o.type === "limit" ? ` @${fmtPrice(o.limit_price)}` : ""}
                          {o.type === "stop" && o.stop_price != null ? ` stop ${fmtPrice(o.stop_price)}` : ""}
                          {o.type === "stop_limit" && o.stop_price != null
                            ? ` stop ${fmtPrice(o.stop_price)}/${fmtPrice(o.limit_price)}`
                            : ""}
                          {o.type === "trailing_stop"
                            ? ` trail ${o.trail_pct}%${o.stop_price != null ? ` (${fmtPrice(o.stop_price)})` : ""}`
                            : ""}
                        </td>
                        <td className="px-2 py-0.5 text-right text-term-muted">{o.fill_price != null ? fmtPrice(o.fill_price) : "—"}</td>
                        <td className={cx("px-2 py-0.5", statusColor[o.status])}>{o.status}</td>
                        <td className="px-1 py-0.5 text-right">
                          {o.status === "open" && (
                            <IconButton label="Cancel order" onClick={() => cancel.mutate(o.id)} danger title="Cancel">
                              ×
                            </IconButton>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState title="No orders yet" hint="Place an order above." />
              )
            ) : bottomTab === "stats" ? (
              <StatsPanel perf={perf} ops={ops} viewingSim={viewingSim} />
            ) : perf && perf.closed_trades.length > 0 ? (
              <table className="w-full border-collapse text-[10px]">
                <thead className="sticky top-0 bg-term-panel">
                  <tr className="border-b border-term-border text-[9px] uppercase tracking-wider text-term-muted">
                    <th className="px-2 py-1 text-left">Time</th>
                    <th className="px-2 py-1 text-left">Close</th>
                    <th className="px-2 py-1 text-right">Exit</th>
                    <th className="px-2 py-1 text-right">Realized</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {perf.closed_trades
                    .slice()
                    .reverse()
                    .map((t) => (
                      <tr key={t.id} className="border-b border-term-border/30">
                        <td className="px-2 py-0.5 text-term-muted">{t.ts.slice(5, 16)}</td>
                        <td className="px-2 py-0.5">
                          <span className={t.side === "buy" ? "text-term-up" : "text-term-down"}>{t.side}</span> {t.quantity}{" "}
                          {t.symbol}
                        </td>
                        <td className="px-2 py-0.5 text-right text-term-muted">{t.fill_price != null ? fmtPrice(t.fill_price) : "—"}</td>
                        <td className={cx("px-2 py-0.5 text-right", t.realized_pnl >= 0 ? "text-term-up" : "text-term-down")}>
                          {t.realized_pnl >= 0 ? "+" : ""}${fmtCompact(t.realized_pnl)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            ) : (
              <EmptyState title="No closed trades" hint={viewingSim ? "Realized P&L shows here." : "Alpaca P&L is on its dashboard."} />
            )}
          </div>
        </div>
      </div>

      {/* ── confirm ── */}
      {confirm && (
        <>
          <div className="fixed inset-0 z-50 bg-black/50" onClick={() => setConfirm(false)} />
          <div className="fixed left-1/2 top-[28vh] z-50 w-[min(360px,90vw)] -translate-x-1/2 rounded-lg border border-term-border bg-term-elev p-4 shadow-elev-3">
            <div className="mb-2 text-sm font-semibold">Confirm order</div>
            <div className="mb-3 font-mono text-xs">
              <span className={side === "buy" ? "text-term-up" : "text-term-down"}>{side.toUpperCase()}</span>{" "}
              {qty} {ticketSymbol} ({ticketAsset}) · {otype.replace("_", " ")}
              {hasStop ? ` stop ${fmtPrice(stop)}` : ""}
              {hasLimit ? ` limit ${fmtPrice(limit)}` : ""}
              {hasTrail ? ` trail ${trail}%` : ""}
            </div>
            {/* pre-trade preview: estimated cost, buying power, advisory risk warnings */}
            {viewingSim && preview?.applicable && (
              <div className="mb-3 space-y-0.5 rounded border border-term-border bg-term-sunken/40 px-2 py-1.5 text-[11px] font-mono">
                {preview.est_cost != null && (
                  <div className="flex justify-between">
                    <span className="text-term-muted">Est. cost</span>
                    <span>${fmtCompact(preview.est_cost)}</span>
                  </div>
                )}
                {preview.buying_power != null && (
                  <div className="flex justify-between">
                    <span className="text-term-muted">Buying power</span>
                    <span className={preview.buying_power_ok ? "" : "text-term-down"}>
                      ${fmtCompact(preview.buying_power)}
                    </span>
                  </div>
                )}
                {!preview.buying_power_ok && <div className="text-term-down">Insufficient buying power — order will be rejected.</div>}
                {preview.warnings?.map((w) => (
                  <div key={w} className="text-term-accent">⚠ {w}</div>
                ))}
              </div>
            )}
            <div className="mb-3 text-[11px] text-term-muted">
              Routes to <span className="font-semibold">{viewingSim ? "the local simulator" : "Alpaca paper"}</span>.
              {viewingSim ? " No external order." : " Submits a real paper order to Alpaca."}
              {viewingSim && cfg && (cfg.commission_bps > 0 || cfg.slippage_bps > 0) && (
                <span className="mt-1 block text-term-muted">
                  Costs: {cfg.commission_bps}bps commission
                  {otype === "market" && cfg.slippage_bps > 0 ? ` + ${cfg.slippage_bps}bps slippage` : ""}.
                </span>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirm(false)} className="rounded border border-term-border px-3 py-1 text-xs text-term-muted hover:text-term-text">
                Cancel
              </button>
              <button
                onClick={() => submit.mutate()}
                disabled={submit.isPending || (viewingSim && preview?.applicable && preview.buying_power_ok === false)}
                className={cx(
                  "rounded border px-3 py-1 text-xs font-semibold uppercase disabled:opacity-50",
                  side === "buy" ? "border-term-up text-term-up hover:bg-term-up/10" : "border-term-down text-term-down hover:bg-term-down/10",
                )}
              >
                {submit.isPending ? "Placing…" : "Confirm"}
              </button>
            </div>
          </div>
        </>
      )}
    </WidgetShell>
  );
}
