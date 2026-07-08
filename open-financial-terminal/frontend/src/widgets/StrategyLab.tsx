import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { LabResult } from "../api/types";
import AgentChatPanel, { type ChatMsg, type Suggestion } from "../components/AgentChatPanel";
import BentoSubGrid from "../components/BentoSubGrid";
import ChatHistoryList from "../components/ChatHistoryList";
import { useAgentRuns } from "../state/agentRuns";
import { useChatHistory } from "../state/chatHistory";
import { CandleChart } from "../lib/candleChart";
import { chartColors } from "../lib/chartTheme";
import { cx, fmtCompact, fmtPct, fmtPrice } from "../lib/format";
import { useT } from "../lib/i18n";
import { usePalette } from "../state/settings";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState } from "../components/States";
import { MetricCard } from "../components/MetricCard";
import BtModeToggle, { type BtMode } from "../components/BtModeToggle";

type Direction = "long_only" | "short_only" | "both";
type Params = Record<string, string | number>;

const EMPTY_MSGS: ChatMsg[] = [];
const EMPTY_PARAMS: Params = {};

function Big({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" | null }) {
  return <MetricCard label={label} value={value} tone={tone ?? null} emphasis />;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b border-term-border/30 py-0.5 font-mono text-[12px]">
      <span className="text-term-muted">{label}</span>
      <span className="text-term-text">{value}</span>
    </div>
  );
}

/** Return-distribution mini histogram. */
function Histogram({ bins }: { bins: LabResult["histogram"] }) {
  const max = Math.max(1, ...bins.map((b) => b.n));
  return (
    <div className="flex h-12 items-end gap-px">
      {bins.map((b, i) => (
        <div
          key={i}
          title={`${b.lo}%…${b.hi}%: ${b.n}`}
          className={cx("flex-1", b.lo >= 0 ? "bg-term-up/60" : "bg-term-down/60")}
          style={{ height: `${(b.n / max) * 100}%` }}
        />
      ))}
    </div>
  );
}

export default function StrategyLab({
  props,
  onMode,
}: {
  props: WidgetProps;
  onMode: (m: BtMode) => void;
}) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const palette = usePalette();
  const t = useT();
  const { data: strategies } = useQuery({ queryKey: ["lab-strategies"], queryFn: api.labStrategies });

  // ── agent chat — run state lives in the global store, survives unmount / tab switches ──
  // ":lab" suffix keeps this job separate from the Factor job in the same panel (different result
  // shapes), so toggling Factor↔Lab never cross-reads the wrong result.
  const jobId = `${props.api.id}:lab`;
  const job = useAgentRuns((s) => s.jobs[jobId]);
  const messages = job?.messages ?? EMPTY_MSGS;
  const activeRunId = job?.activeRunId ?? null;
  const busy = job?.status === "running";
  const activeRun = activeRunId ? job?.runs.find((rr) => rr.id === activeRunId) : undefined;
  const result = (activeRun?.result as LabResult | undefined) ?? null;
  const activeParams: Params = activeRun?.params ?? EMPTY_PARAMS;

  const [selected, setSelected] = useState<number>(-1);
  const [whatifOn, setWhatifOn] = useState(false);
  const [whatif, setWhatif] = useState<{ sl: number; tp: number } | null>(null);
  // reset trade selection / what-if whenever the active run changes
  useEffect(() => {
    setSelected(-1);
    setWhatifOn(false);
  }, [activeRunId]);

  // values the agent resolved — drive what-if recompute, deploy sizing, chart timeframe
  const timeframe = (result?.timeframe ?? "1d") as "1h" | "1d";
  const direction = (activeParams.direction as Direction) ?? "long_only";
  const commission = Number(activeParams.commission_bps ?? 5);
  const leverage = Number(activeParams.leverage ?? 1);
  const sizeFrac = Number(activeParams.size_pct ?? 1);
  const initial = 100000;
  const def = strategies?.strategies.find((st) => st.key === (activeParams.strategy as string));

  const labHistory = useChatHistory((s) => s.sessions).filter((h) => h.kind === "lab");

  const send = (goal: string) =>
    useAgentRuns.getState().start(jobId, "lab", goal, { symbol, asset, timeframe });
  const selectRun = (id: string) => useAgentRuns.getState().selectRun(jobId, id);
  const whatifHandler = useRef<(id: string, price: number) => void>(() => {});
  whatifHandler.current = (id, price) =>
    setWhatif((cur) => (cur ? { ...cur, [id]: Math.max(price, 0) } : cur));

  // seed the stop/target levels from the selected trade whenever it (or the mode) changes
  useEffect(() => {
    if (!whatifOn || selected < 0 || !result) {
      setWhatif(null);
      return;
    }
    const tr = result.trades[selected];
    const long = tr.side === "long";
    const slP = Number(activeParams.sl_pct ?? 0) || 0.05;
    const tpP = Number(activeParams.tp_pct ?? 0) || 0.1;
    setWhatif({
      sl: long ? tr.entry_price * (1 - slP) : tr.entry_price * (1 + slP),
      tp: long ? tr.entry_price * (1 + tpP) : tr.entry_price * (1 - tpP),
    });
  }, [whatifOn, selected, result]); // eslint-disable-line react-hooks/exhaustive-deps

  const candleIndex = useMemo(() => {
    const m = new Map<string | number, number>();
    result?.candles.forEach((c, i) => m.set(c.time, i));
    return m;
  }, [result]);

  // walk the bars within the trade's original window; find the new stop/target exit
  const recompute = useMemo(() => {
    if (!whatif || selected < 0 || !result) return null;
    const tr = result.trades[selected];
    const eI = candleIndex.get(tr.entry_time);
    const xI = candleIndex.get(tr.exit_time);
    if (eI === undefined || xI === undefined) return null;
    const long = tr.side === "long";
    let exit = { time: tr.exit_time as string | number, price: tr.exit_price, reason: tr.reason };
    for (let i = eI + 1; i <= xI; i++) {
      const c = result.candles[i];
      if (long) {
        if (whatif.sl && c.low <= whatif.sl) { exit = { time: c.time, price: whatif.sl, reason: "stop" }; break; }
        if (whatif.tp && c.high >= whatif.tp) { exit = { time: c.time, price: whatif.tp, reason: "target" }; break; }
      } else {
        if (whatif.sl && c.high >= whatif.sl) { exit = { time: c.time, price: whatif.sl, reason: "stop" }; break; }
        if (whatif.tp && c.low <= whatif.tp) { exit = { time: c.time, price: whatif.tp, reason: "target" }; break; }
      }
    }
    const sign = long ? 1 : -1;
    const gross = (sign * (exit.price - tr.entry_price)) / tr.entry_price * 100;
    const netRet = gross - (2 * commission) / 100;
    const notional =
      tr.ret_pct !== 0 ? Math.abs(tr.pnl / (tr.ret_pct / 100)) : initial * sizeFrac * leverage;
    const pnl = (notional * netRet) / 100;
    return { ...exit, netRet, pnl, dRet: netRet - tr.ret_pct, dPnl: pnl - tr.pnl };
  }, [whatif, selected, result, candleIndex, commission, initial, sizeFrac, leverage]);

  // ── chart ──
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<CandleChart | null>(null);
  useEffect(() => {
    if (!hostRef.current) return;
    const c = new CandleChart(hostRef.current, chartColors(), {
      onDragLine: (id, price) => whatifHandler.current(id, price),
    });
    chartRef.current = c;
    return () => {
      chartRef.current = null;
      c.destroy();
    };
  }, [palette]);

  const highlight = selected >= 0 && result ? result.trades[selected]?.entry_time : null;
  useEffect(() => {
    const c = chartRef.current;
    if (!c || !result) return;
    const cols = chartColors();
    c.setData({
      candles: result.candles,
      volume: [],
      overlays: [],
      lower: [],
      mode: "candles",
      intraday: timeframe !== "1d",
      markers: result.markers,
      equity: result.equity_curve,
      highlight,
      dragLines: whatif
        ? [
            { id: "sl", price: whatif.sl, color: cols.down, label: "SL" },
            { id: "tp", price: whatif.tp, color: cols.up, label: "TP" },
          ]
        : undefined,
      whatif: recompute ? { time: recompute.time, price: recompute.price, win: recompute.pnl >= 0 } : null,
    });
  }, [result, palette, highlight, timeframe, whatif, recompute]);

  const s = result?.stats;

  // ── deploy ──
  const [deploy, setDeploy] = useState(false);
  const [deployQty, setDeployQty] = useState(0);
  const [deployBook, setDeployBook] = useState<"primary" | "sim">("primary");
  const [placed, setPlaced] = useState<string | null>(null);
  const { data: paperCfg } = useQuery({ queryKey: ["paper-config"], queryFn: () => api.paperConfig() });
  const qc = useQueryClient();
  const lastPrice = result?.candles.length ? result.candles[result.candles.length - 1].close : 0;
  const deploySide: "buy" | "sell" = direction === "short_only" ? "sell" : "buy";

  useEffect(() => {
    if (deploy && lastPrice > 0) {
      setDeployQty(Math.max(1, Math.floor((initial * sizeFrac * leverage) / lastPrice)));
      setPlaced(null);
    }
  }, [deploy]); // eslint-disable-line react-hooks/exhaustive-deps

  const deployOrder = useMutation({
    mutationFn: () =>
      api.submitPaperOrder(
        { symbol, asset, side: deploySide, quantity: deployQty, type: "market" },
        paperCfg?.alpaca_active ? deployBook : undefined,
      ),
    onSuccess: (r) => {
      setPlaced(r.order_id);
      qc.invalidateQueries({ queryKey: ["paper-account"] });
      qc.invalidateQueries({ queryKey: ["paper-orders"] });
    },
  });

  const suggestions: Suggestion[] = result
    ? [
        { label: t("lab.sug.addStop"), prompt: "add a 2% stop and 4% target" },
        activeParams.strategy === "ema_cross"
          ? { label: t("lab.sug.tryRsi"), prompt: "try RSI mean-reversion" }
          : { label: t("lab.sug.tryEma"), prompt: "try EMA crossover 12/26" },
        { label: t("lab.sug.best"), prompt: `find the best strategy for ${symbol}` },
        { label: t("lab.sug.longer"), prompt: "test it over 5 years" },
      ]
    : [
        { label: t("lab.sug.rsi"), prompt: "RSI mean-reversion with a 2% stop and 4% target" },
        { label: t("lab.sug.sma"), prompt: "SMA crossover 20/50, long only" },
        { label: t("lab.sug.best"), prompt: `find the best strategy for ${symbol}` },
        { label: t("lab.sug.macd"), prompt: "MACD crossover, both directions" },
      ];

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <BtModeToggle mode="lab" onMode={onMode} />
          <span className="font-mono text-sm font-bold">{symbol}</span>
          {result && (
            <span className="truncate font-mono text-[11px] text-term-muted">
              {def?.label ?? activeParams.strategy} · {direction} · {timeframe}
            </span>
          )}
          {result && (
            <button
              onClick={() => setDeploy(true)}
              className="ml-auto rounded border border-term-up px-2 py-0.5 text-[11px] uppercase tracking-wide text-term-up hover:bg-term-up/10"
            >
              {t("lab.deploy")} ▸
            </button>
          )}
        </>
      }
    >
      <BentoSubGrid
        storageKey={jobId}
        seed={(api) => {
          api.addPanel({ id: "dashboard", component: "dashboard", title: "Dashboard" });
          const chat = api.addPanel({
            id: "chat",
            component: "chat",
            title: `${t("lab.agentTitle")} · ${symbol}`,
            position: { referencePanel: "dashboard", direction: "left" },
          });
          api.addPanel({
            id: "history",
            component: "history",
            title: t("chat.history"),
            position: { referencePanel: "chat", direction: "below" },
          });
          chat.api.setSize({ width: 300 });
        }}
        panels={[
          {
            id: "dashboard",
            title: "Dashboard",
            content: (
        <div className="flex h-full min-h-0">
        {/* ── Center: chart + scrubber + trades ── */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div ref={hostRef} className="min-h-0 flex-1" />
          {result && result.trades.length > 0 && (
            <div className="flex items-center gap-2 border-t border-term-border px-2 py-1">
              <span className="text-[11px] text-term-muted">{t("lab.replay")}</span>
              <input
                type="range"
                min={-1}
                max={result.trades.length - 1}
                value={selected}
                onChange={(e) => setSelected(Number(e.target.value))}
                className="flex-1 accent-term-accent"
              />
              <span className="w-14 text-right font-mono text-[11px] text-term-muted">
                {selected < 0 ? "all" : `#${selected + 1}/${result.trades.length}`}
              </span>
              <button
                onClick={() => setWhatifOn((v) => !v)}
                disabled={selected < 0}
                title="Drag the stop/target lines on the chart to test this trade"
                className={cx(
                  "rounded border px-2 py-0.5 text-[11px] uppercase tracking-wide disabled:opacity-40",
                  whatifOn ? "border-term-accent text-term-accent" : "border-term-border text-term-muted hover:text-term-text",
                )}
              >
                {t("lab.whatif")}
              </button>
            </div>
          )}
          {whatifOn && recompute && selected >= 0 && result && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 border-t border-term-accent/40 bg-term-accent/5 px-2 py-1 font-mono text-[11px]">
              <span className="text-term-accent">{t("lab.whatif")} #{selected + 1}</span>
              <span className="text-term-muted">
                exit <span className="text-term-text">{recompute.reason}</span> @ {fmtPrice(recompute.price)} ({recompute.time})
              </span>
              <span className="text-term-muted">
                ret{" "}
                <span className={result.trades[selected].ret_pct >= 0 ? "text-term-up" : "text-term-down"}>
                  {fmtPct(result.trades[selected].ret_pct)}
                </span>{" "}
                →{" "}
                <span className={recompute.netRet >= 0 ? "text-term-up" : "text-term-down"}>{fmtPct(recompute.netRet)}</span>{" "}
                <span className={recompute.dRet >= 0 ? "text-term-up" : "text-term-down"}>
                  ({recompute.dRet >= 0 ? "+" : ""}{recompute.dRet.toFixed(2)})
                </span>
              </span>
              <span className="text-term-muted">
                P&L Δ{" "}
                <span className={recompute.dPnl >= 0 ? "text-term-up" : "text-term-down"}>
                  {recompute.dPnl >= 0 ? "+" : ""}${fmtCompact(recompute.dPnl)}
                </span>
              </span>
              <span className="text-term-muted">
                {t("lab.projFinal")}{" "}
                <span className="text-term-text">${fmtCompact(result.stats.final_equity + recompute.dPnl)}</span>
              </span>
              <span className="text-[10px] text-term-muted/70">{t("lab.whatifNote")}</span>
            </div>
          )}
          {result && (
            <div className="h-36 shrink-0 overflow-auto border-t border-term-border">
              <table className="w-full border-collapse text-[12px]">
                <thead className="sticky top-0 bg-term-panel">
                  <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
                    <th className="px-2 py-1 text-left">#</th>
                    <th className="px-2 py-1 text-left">{t("lab.side")}</th>
                    <th className="px-2 py-1 text-left">{t("lab.entry")}</th>
                    <th className="px-2 py-1 text-left">{t("lab.exit")}</th>
                    <th className="px-2 py-1 text-right">P&amp;L</th>
                    <th className="px-2 py-1 text-right">{t("lab.retPct")}</th>
                    <th className="px-2 py-1 text-right">{t("lab.bars")}</th>
                    <th className="px-2 py-1 text-left">{t("lab.why")}</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {result.trades.map((tr, i) => (
                    <tr
                      key={i}
                      onClick={() => setSelected(i)}
                      className={cx(
                        "cursor-pointer border-b border-term-border/30 hover:bg-term-border/30",
                        selected === i && "bg-term-accent/15",
                      )}
                    >
                      <td className="px-2 py-0.5 text-term-muted">{i + 1}</td>
                      <td className={cx("px-2 py-0.5", tr.side === "long" ? "text-term-up" : "text-term-down")}>{tr.side}</td>
                      <td className="px-2 py-0.5 text-term-muted">{tr.entry_time}</td>
                      <td className="px-2 py-0.5 text-term-muted">{tr.exit_time}</td>
                      <td className={cx("px-2 py-0.5 text-right", tr.pnl >= 0 ? "text-term-up" : "text-term-down")}>
                        {tr.pnl >= 0 ? "+" : ""}{fmtCompact(tr.pnl)}
                      </td>
                      <td className={cx("px-2 py-0.5 text-right", tr.ret_pct >= 0 ? "text-term-up" : "text-term-down")}>
                        {fmtPct(tr.ret_pct)}
                      </td>
                      <td className="px-2 py-0.5 text-right text-term-muted">{tr.bars}</td>
                      <td className="px-2 py-0.5 text-term-muted">{tr.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!result && !busy && <EmptyState title={t("lab.empty")} />}
        </div>

        {/* ── Scorecards ── */}
        <div className="w-48 shrink-0 space-y-2 overflow-auto border-l border-term-border p-2">
          {s ? (
            <>
              <Big label={t("lab.netPnl")} value={`${s.net_pnl >= 0 ? "+" : ""}$${fmtCompact(s.net_pnl)} (${fmtPct(s.net_pnl_pct)})`} tone={s.net_pnl >= 0 ? "up" : "down"} />
              <Big
                label={t("lab.vsHold")}
                value={`${s.vs_buy_hold >= 0 ? "+" : ""}${fmtPct(s.vs_buy_hold)}`}
                tone={s.vs_buy_hold >= 0 ? "up" : "down"}
              />
              <div className="grid grid-cols-2 gap-1.5">
                <Big label={t("lab.profitFactor")} value={s.profit_factor != null ? s.profit_factor.toFixed(2) : "—"} tone={(s.profit_factor ?? 0) >= 1 ? "up" : "down"} />
                <Big label={t("bt.maxdd")} value={fmtPct(s.max_drawdown, false)} tone="down" />
              </div>
              <div className="rounded border border-term-border p-1.5">
                <Stat label={t("lab.winRate")} value={`${s.win_rate}%`} />
                <Stat label={t("lab.trades")} value={String(s.total_trades)} />
                <Stat label={t("lab.avgBars")} value={String(s.avg_bars)} />
                <Stat label={t("lab.expectancy")} value={`$${fmtCompact(s.expectancy)}`} />
                <Stat label={t("lab.avgWin")} value={`$${fmtCompact(s.avg_win)}`} />
                <Stat label={t("lab.avgLoss")} value={`$${fmtCompact(s.avg_loss)}`} />
                <Stat label="Sharpe" value={s.sharpe.toFixed(2)} />
                <Stat label={t("lab.buyHold")} value={fmtPct(s.buy_hold_pct)} />
              </div>
              {result && result.histogram.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-term-muted">{t("lab.returnDist")}</div>
                  <Histogram bins={result.histogram} />
                </div>
              )}
            </>
          ) : (
            <EmptyState title={t("lab.scorecardEmpty")} />
          )}
        </div>
        </div>
            ),
          },
          {
            id: "chat",
            title: `${t("lab.agentTitle")} · ${symbol}`,
            content: (
              <AgentChatPanel
                title={`${t("lab.agentTitle")} · ${symbol}`}
                messages={messages}
                busy={busy}
                onSend={send}
                onStop={() => useAgentRuns.getState().stop(jobId)}
                onSelectRun={selectRun}
                activeRunId={activeRunId}
                placeholder={t("lab.chatPlaceholder", { x: symbol })}
                emptyHint={t("lab.chatHint", { x: symbol })}
                suggestions={suggestions}
              />
            ),
          },
          {
            id: "history",
            title: t("chat.history"),
            content: (
              <ChatHistoryList
                history={labHistory}
                onNewChat={() => useAgentRuns.getState().newChat(jobId)}
                onLoadSession={(sess) => useAgentRuns.getState().loadSession(jobId, sess)}
                onDeleteSession={(id) => useChatHistory.getState().remove(id)}
              />
            ),
          },
        ]}
      />

      {/* ── Deploy guardrail ── */}
      {deploy && s && (
        <>
          <div className="fixed inset-0 z-50 bg-black/50" onClick={() => setDeploy(false)} />
          <div className="fixed left-1/2 top-[22vh] z-50 w-[min(440px,92vw)] -translate-x-1/2 rounded-lg border border-term-border bg-term-elev p-4 shadow-elev-3">
            <div className="mb-2 text-sm font-semibold">{t("lab.deploy")} {symbol} · {def?.label ?? activeParams.strategy}</div>
            <div className="mb-3 space-y-1 rounded border border-term-border bg-term-bg/40 p-2 font-mono text-[12px]">
              <Stat label={t("lab.netReturn")} value={fmtPct(s.net_pnl_pct)} />
              <Stat label={t("lab.maxDrawdown")} value={fmtPct(s.max_drawdown, false)} />
              <Stat label={t("lab.winRate")} value={`${s.win_rate}% (${s.total_trades} ${t("lab.trades").toLowerCase()})`} />
              <Stat label={t("lab.leverage")} value={`${leverage}x`} />
            </div>
            {(s.max_drawdown <= -20 || leverage > 1) && (
              <div className="mb-3 rounded border border-term-down/50 bg-term-down/10 p-2 text-[12px] text-term-down">
                ⚠ This strategy shows a {fmtPct(s.max_drawdown, false)} max drawdown
                {leverage > 1 ? ` at ${leverage}x leverage` : ""}. Size accordingly.
              </div>
            )}
            <div className="mb-3 flex items-center justify-between gap-2 rounded border border-term-border bg-term-bg/40 p-2 text-[12px]">
              <span className="text-term-muted">
                One-shot{" "}
                <span className={deploySide === "buy" ? "text-term-up" : "text-term-down"}>{deploySide}</span>{" "}
                market order
              </span>
              <label className="flex items-center gap-1 font-mono text-term-muted">
                {t("lab.qty")}
                <input
                  type="number"
                  min={1}
                  value={deployQty}
                  onChange={(e) => setDeployQty(Number(e.target.value))}
                  aria-label={t("lab.qty")}
                  className="focus-ring w-20 rounded border border-term-border bg-term-sunken px-1 py-0.5 text-xs text-term-text"
                />
                <span className="text-[11px]">≈ ${fmtCompact(deployQty * lastPrice)}</span>
              </label>
            </div>
            {paperCfg?.alpaca_active && (
              <label className="mb-3 flex items-center justify-between gap-2 rounded border border-term-border bg-term-bg/40 p-2 text-[12px] text-term-muted">
                Book
                <select
                  value={deployBook}
                  onChange={(e) => setDeployBook(e.target.value as "primary" | "sim")}
                  className="focus-ring rounded border border-term-border bg-term-sunken px-1.5 py-0.5 text-xs text-term-text"
                >
                  <option value="primary">Alpaca paper (primary)</option>
                  <option value="sim">Sim sandbox</option>
                </select>
              </label>
            )}
            <div className="mb-3 text-[12px] text-term-muted">
              Routes to{" "}
              <span className="font-semibold">
                {paperCfg?.alpaca_active && deployBook === "primary" ? t("lab.brokerAlpaca") : t("lab.brokerSim")}
              </span>
              . Places a single market order for the strategy's current stance — not a continuous
              auto-trading loop. Manage the position in the Paper Trading widget.
            </div>
            {placed ? (
              <div className="flex items-center justify-between">
                <span className="text-[12px] text-term-up">{t("lab.orderPlaced", { x: placed })}</span>
                <button onClick={() => setDeploy(false)} className="rounded border border-term-border px-3 py-1 text-xs text-term-muted hover:text-term-text">
                  {t("lab.close")}
                </button>
              </div>
            ) : (
              <div className="flex items-center justify-end gap-2">
                {deployOrder.error && (
                  <span className="mr-auto text-[11px] text-term-down">{(deployOrder.error as Error).message}</span>
                )}
                <button onClick={() => setDeploy(false)} className="rounded border border-term-border px-3 py-1 text-xs text-term-muted hover:text-term-text">
                  {t("lab.cancel")}
                </button>
                <button
                  onClick={() => deployOrder.mutate()}
                  disabled={deployOrder.isPending || deployQty < 1}
                  className="rounded border border-term-up px-3 py-1 text-xs font-semibold uppercase text-term-up hover:bg-term-up/10 disabled:opacity-50"
                >
                  {deployOrder.isPending ? t("lab.placing") : t("lab.deployToPaper")}
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </WidgetShell>
  );
}
