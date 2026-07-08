import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { BacktestProposal, BacktestResponse, LinePoint } from "../api/types";
import AgentChatPanel, { type ChatMsg, type Suggestion } from "../components/AgentChatPanel";
import AgentProcessingLog from "../components/AgentProcessingLog";
import BacktestIdeas from "../components/BacktestIdeas";
import BentoSubGrid from "../components/BentoSubGrid";
import ChatHistoryList from "../components/ChatHistoryList";
import { chartColors, seriesColors } from "../lib/chartTheme";
import { SeriesChart as SeriesChartEngine } from "../lib/seriesChart";
import { useAgentRuns, type RunRec } from "../state/agentRuns";
import { useChatHistory } from "../state/chatHistory";

const EMPTY_MSGS: ChatMsg[] = [];
const EMPTY_RUNS: RunRec[] = [];
import { cx, fmtCompact, fmtPct } from "../lib/format";
import { useT, type I18nKey } from "../lib/i18n";
import { usePalette } from "../state/settings";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { EmptyState } from "../components/States";
import { MetricCard, TABLE } from "../components/MetricCard";
import { SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import SendToMenu from "../components/SendToMenu";
import type { SendPayload } from "../state/intents";
import StrategyLab from "./StrategyLab";
import { MarketMakingPanel } from "./MarketMakingWidget";
import BtModeToggle, { type BtMode } from "../components/BtModeToggle";

/* ── building blocks ─────────────────────────────────────────────────────────── */

function Metric({
  label,
  value,
  good,
  emphasis,
}: {
  label: string;
  value: string;
  good?: boolean | null;
  emphasis?: boolean;
}) {
  return (
    <MetricCard
      label={label}
      value={value}
      tone={good == null ? null : good ? "up" : "down"}
      emphasis={emphasis}
    />
  );
}

interface SeriesSpec {
  points: LinePoint[];
  color: string;
  title?: string;
  kind?: "line" | "area" | "histogram";
}

/** Generic multi-series chart for dashboard panes, on the from-scratch canvas engine. */
function SeriesChart({ series, title }: { series: SeriesSpec[]; title?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const engineRef = useRef<SeriesChartEngine | null>(null);
  const palette = usePalette();

  useEffect(() => {
    if (!ref.current) return;
    const engine = new SeriesChartEngine(ref.current, chartColors());
    engineRef.current = engine;
    return () => {
      engineRef.current = null;
      engine.destroy();
    };
  }, [palette]);

  useEffect(() => {
    engineRef.current?.setData(series);
  }, [series]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {title && (
        <div className="mb-1 border-b border-term-border/40 px-1 pb-1 text-[11px] font-medium uppercase tracking-wider text-term-text/80">
          {title}
        </div>
      )}
      <div ref={ref} className="min-h-0 flex-1" />
    </div>
  );
}

const MONTH_LABELS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

function MonthlyHeatmap({ rows, title }: { rows: BacktestResponse["monthly_returns"]; title: string }) {
  const years = Array.from(new Set(rows.map((r) => r.year))).sort();
  const cell = (y: number, m: number) => rows.find((r) => r.year === y && r.month === m);
  const max = Math.max(4, ...rows.map((r) => Math.abs(r.ret)));
  return (
    <div>
      <div className="px-1 pb-1 text-[11px] uppercase tracking-wider text-term-muted">{title}</div>
      <table className="border-collapse font-mono text-[11px]">
        <thead>
          <tr className="bg-term-elev">
            <th />
            {MONTH_LABELS.map((m, i) => (
              <th key={i} className="px-1 py-1 font-medium text-term-muted">{m}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}>
              <td className="bg-term-elev px-1.5 text-right text-term-muted">{y}</td>
              {MONTH_LABELS.map((_, i) => {
                const c = cell(y, i + 1);
                const a = c ? Math.min(Math.abs(c.ret) / max, 1) * 0.6 : 0;
                return (
                  <td
                    key={i}
                    title={c ? `${y}-${String(i + 1).padStart(2, "0")}: ${fmtPct(c.ret)}` : undefined}
                    className="h-7 w-12 border border-term-border/40 text-center"
                    style={{
                      backgroundColor: c
                        ? c.ret >= 0
                          ? `rgb(var(--term-up) / ${a})`
                          : `rgb(var(--term-down) / ${a})`
                        : "rgb(var(--term-border) / 0.1)",
                    }}
                  >
                    {c ? c.ret.toFixed(1) : ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── the dashboard ───────────────────────────────────────────────────────────── */

type Tab = "overview" | "timing" | "factor" | "analysis" | "costs" | "runs";
const TABS: Tab[] = ["overview", "factor", "analysis", "costs", "runs"];
// "Timing" tab is shown only when the active result carried a market-timing overlay.
const TABS_WITH_TIMING: Tab[] = ["overview", "timing", "factor", "analysis", "costs", "runs"];

export default function BacktestWidget(props: WidgetProps) {
  // Two backtesting tools share this widget: the cross-sectional Factor dashboard
  // (default) and the single-symbol Strategy Lab. The choice persists in panel params.
  const btMode = (props.params.btMode ?? "factor") as BtMode;
  const setBtMode = (m: BtMode) => props.api.updateParameters({ btMode: m });
  if (btMode === "lab") return <StrategyLab props={props} onMode={setBtMode} />;
  if (btMode === "mm") return <MarketMakingPanel props={props} onMode={setBtMode} />;
  return <FactorBacktest props={props} onMode={setBtMode} />;
}

function FactorBacktest({
  props,
  onMode,
}: {
  props: WidgetProps;
  onMode: (m: BtMode) => void;
}) {
  const { channel, setChannel } = useWidgetSymbol(props);
  const t = useT();

  // Run state lives in the global store (keyed by panel id + mode) so it survives unmount / tab
  // switches. The ":factor" suffix keeps it separate from the Lab job in the same panel — they
  // hold different result shapes (BacktestResponse vs LabResult).
  const jobId = `${props.api.id}:factor`;
  const job = useAgentRuns((s) => s.jobs[jobId]);
  const messages = job?.messages ?? EMPTY_MSGS;
  const runs = job?.runs ?? EMPTY_RUNS;
  const activeRunId = job?.activeRunId ?? null;
  const busy = job?.status === "running";
  const view =
    (activeRunId ? (runs.find((rr) => rr.id === activeRunId)?.result as BacktestResponse | undefined) : undefined) ??
    null;

  const [tab, setTab] = useState<Tab>("overview");
  useEffect(() => {
    if (activeRunId) setTab("overview");
  }, [activeRunId]);

  const factorHistory = useChatHistory((s) => s.sessions).filter((h) => h.kind === "factor");

  const send = (goal: string) => useAgentRuns.getState().start(jobId, "factor", goal, {});
  const selectRun = (id: string) => {
    useAgentRuns.getState().selectRun(jobId, id);
    setTab("overview");
  };

  // Consume a `screen_result` handed off from another module (e.g. the Screener's "Send to" menu):
  // kick off a factor backtest from the incoming factor+universe, then clear the params so a
  // re-render doesn't re-fire. Same mount-once-then-clear lifecycle as ScreenerWidget's initialQuery.
  const ranIncoming = useRef(false);
  useEffect(() => {
    const factor = props.params.incomingFactor;
    const universe = props.params.incomingUniverse;
    if ((factor || universe) && !ranIncoming.current) {
      ranIncoming.current = true;
      const goal = `${factor ?? "momentum"} long-short on ${universe ?? "the dow"} over 3 years`;
      send(goal);
      setTab("overview");
      props.api.updateParameters({
        incomingFactor: undefined,
        incomingUniverse: undefined,
        incomingSymbols: undefined,
        incomingWeights: undefined,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Render a precomputed backtest streamed in by the Agent Workflow's `backtest` node: inject it as
  // a run so it displays WITHOUT re-running (the node already computed it), then clear the param.
  // Unlike `incomingFactor` above, this fires on every update (the panel may already be mounted when
  // the node finishes), so it watches the param and de-dupes by object identity.
  const injectedResultRef = useRef<unknown>(null);
  useEffect(() => {
    const r = props.params.incomingResult as BacktestResponse | undefined;
    if (r && r !== injectedResultRef.current) {
      injectedResultRef.current = r;
      useAgentRuns.getState().injectRun(jobId, {
        intent: "Agent workflow",
        label: "Agent · backtest",
        result: r,
        params: { universe: r.universe, factor: r.factor, mode: r.mode },
      });
      setTab("overview");
      props.api.updateParameters({ incomingResult: undefined });
    }
  }, [props.params.incomingResult, jobId, props.api]);

  // Hand the active backtest result to the Strategy Library / Paper as a typed payload.
  const buildBtPayload = (): SendPayload | null => {
    if (!view) return null;
    return {
      kind: "backtest_result",
      strategyKey: view.factor,
      universe: view.universe,
      params: { factor: view.factor, mode: view.mode, universe: view.universe },
      metrics: { sharpe: view.metrics.sharpe, cagr: view.metrics.cagr },
    };
  };

  // "Ideas" proposals: factor proposals go through the agent; model proposals run the saved bundle
  // and inject the result so it lands in the same Dashboard / Processing / History.
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const runProposal = (p: BacktestProposal) => {
    setProposalError(null);
    if (p.kind === "factor" && p.prompt) {
      useAgentRuns.getState().start(jobId, "factor", p.prompt, {});
      setTab("overview");
      return;
    }
    if (p.kind === "model" && p.model) {
      setProposalBusy(true);
      api
        .backtestModel({ model: p.model, years: p.years ?? null, mode: p.mode ?? null })
        .then((result) => {
          useAgentRuns.getState().injectRun(jobId, {
            intent: p.label,
            label: p.label,
            result,
            params: { model: p.model!, ...(p.years ? { years: p.years } : {}), ...(p.mode ? { mode: p.mode } : {}) },
          });
          setTab("overview");
        })
        .catch((e: unknown) => setProposalError((e as Error)?.message ?? "model backtest failed"))
        .finally(() => setProposalBusy(false));
    }
  };

  const colors = seriesColors();
  const r = view;

  const suggestions: Suggestion[] = view
    ? [
        view.mode === "long_only"
          ? { label: t("bt.sug.toLS"), prompt: "make it long-short" }
          : { label: t("bt.sug.toLO"), prompt: "make it long-only" },
        { label: t("bt.sug.bestHere"), prompt: `find the best factor on ${view.universe}` },
        { label: t("bt.sug.extend"), prompt: "run it over 5 years" },
        { label: t("bt.sug.valVsQual"), prompt: "compare value vs quality on the dow over 3 years" },
      ]
    : [
        { label: t("bt.sug.best"), prompt: "find the best factor on the dow over 5 years" },
        { label: t("bt.sug.momLS"), prompt: "momentum long-short on the dow, 3 years" },
        { label: t("bt.sug.valVsQual"), prompt: "compare value vs quality on the dow over 3 years" },
        { label: t("bt.sug.lowvol"), prompt: "low volatility on the sp500, long-only, 5 years" },
      ];

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      sendMenu={<SendToMenu kind="backtest_result" build={buildBtPayload} disabled={!view} />}
      toolbar={
        <div className="flex flex-1 items-center gap-1.5">
          <BtModeToggle mode="factor" onMode={onMode} />
          {r && (
            <span className="ml-auto truncate font-mono text-[11px] text-term-muted">
              {r.factor} · {r.mode} · {r.universe} · {r.window_start}→{r.window_end} · {r.n_instruments} {t("bt.names")}
            </span>
          )}
        </div>
      }
    >
      <BentoSubGrid
        storageKey={`${jobId}:v3`}
        seed={(api) => {
          // Dashboard (output) top-left, Ideas top-right; the conversation is a bottom row of
          // three windows: History · Processing (the agent's steps + run rows) · Chat.
          const dash = api.addPanel({ id: "dashboard", component: "dashboard", title: "Dashboard" });
          const ideas = api.addPanel({
            id: "ideas",
            component: "ideas",
            title: "Ideas",
            position: { referencePanel: "dashboard", direction: "right" },
          });
          const hist = api.addPanel({
            id: "history",
            component: "history",
            title: t("chat.history"),
            position: { referencePanel: "dashboard", direction: "below" },
          });
          const processing = api.addPanel({
            id: "processing",
            component: "processing",
            title: "Processing",
            position: { referencePanel: "history", direction: "right" },
          });
          api.addPanel({
            id: "chat",
            component: "chat",
            title: t("bt.agentTitle"),
            position: { referencePanel: "processing", direction: "right" },
          });
          ideas.api.setSize({ width: 300 });
          hist.api.setSize({ width: 200 });
          processing.api.setSize({ width: 260 });
          dash.api.setSize({ height: 340 });
        }}
        panels={[
          {
            id: "ideas",
            title: "Ideas",
            content: (
              <BacktestIdeas
                universe={view?.universe}
                factor={view?.factor}
                onRun={runProposal}
                running={busy || proposalBusy}
                runError={proposalError}
              />
            ),
          },
          {
            id: "dashboard",
            title: "Dashboard",
            content: (
        <div className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 items-center border-b border-term-border px-2 py-1">
          <div className="flex items-center overflow-hidden rounded border border-term-border bg-term-sunken text-[11px]">
            {(view?.timing ? TABS_WITH_TIMING : TABS).map((tabKey) => (
              <button
                key={tabKey}
                onClick={() => setTab(tabKey)}
                aria-pressed={tab === tabKey}
                className={cx(
                  "focus-ring px-2.5 py-0.5 uppercase tracking-wide transition-colors",
                  tab === tabKey ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:text-term-text",
                )}
              >
                {t(`bt.tab.${tabKey}` as I18nKey)}
                {tabKey === "runs" && runs.length > 0 && ` (${runs.length})`}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {busy && !r && <SkeletonRows rows={8} />}
          {!busy && !r && <EmptyState icon="📊" title="No backtest yet" hint="Describe a backtest in the chat." />}

          {r && tab === "overview" && (
            <div className="flex h-full flex-col gap-2 p-2">
              <div className="flex shrink-0 flex-wrap items-baseline gap-x-4 gap-y-1 rounded border border-term-border bg-term-bg/40 px-2.5 py-1.5">
                <span className="text-[11px] uppercase tracking-wider text-term-muted">{t("bt.window")}</span>
                <span className="font-mono text-xs text-term-text">{r.window_start} → {r.window_end}</span>
                <span className="ml-auto text-[11px] uppercase tracking-wider text-term-muted">P&L</span>
                <span className={cx("font-mono text-base font-semibold", r.pnl >= 0 ? "text-term-up" : "text-term-down")}>
                  {r.pnl >= 0 ? "+" : "−"}${fmtCompact(Math.abs(r.pnl))} ({fmtPct(r.pnl_pct)})
                </span>
              </div>
              <div className="grid shrink-0 grid-cols-3 gap-2 md:grid-cols-6">
                <Metric label="CAGR" value={fmtPct(r.metrics.cagr)} good={r.metrics.cagr > 0} emphasis />
                <Metric label={t("bt.vol")} value={fmtPct(r.metrics.ann_vol, false)} />
                <Metric label="Sharpe" value={r.metrics.sharpe.toFixed(2)} good={r.metrics.sharpe > 1} emphasis />
                <Metric label="Sortino" value={r.metrics.sortino.toFixed(2)} />
                <Metric label={t("bt.maxdd")} value={fmtPct(r.metrics.max_drawdown, false)} good={r.metrics.max_drawdown > -25} />
                <Metric label="Calmar" value={r.metrics.calmar.toFixed(2)} />
              </div>
              <div className="grid shrink-0 grid-cols-3 gap-2 md:grid-cols-6">
                <Metric label="P&L" value={`${r.pnl >= 0 ? "+" : "−"}$${fmtCompact(Math.abs(r.pnl))}`} good={r.pnl >= 0} emphasis />
                <Metric label="PSR" value={r.robustness.psr != null ? fmtPct(r.robustness.psr, false) : "—"} good={r.robustness.psr != null ? r.robustness.psr > 90 : null} />
                <Metric label="DSR" value={r.robustness.dsr != null ? fmtPct(r.robustness.dsr, false) : "—"} good={r.robustness.dsr != null ? r.robustness.dsr > 90 : null} />
                <Metric label={t("bt.trials")} value={String(r.robustness.n_trials)} />
                <Metric label={t("bt.turnover")} value={fmtPct(r.avg_turnover, false)} />
                <Metric label={t("bt.finalEq")} value={`$${fmtCompact(r.final_equity)}`} />
              </div>
              <div className="min-h-[160px] flex-[3]">
                <SeriesChart
                  title={t("bt.equityVsBench")}
                  series={[
                    { points: r.benchmark_curve, color: colors.volume, title: r.benchmark_label },
                    { points: r.equity_curve, color: colors.accent, title: "strategy" },
                  ]}
                />
              </div>
              <div className="min-h-[90px] flex-[1]">
                <SeriesChart
                  title={t("bt.drawdown")}
                  series={[{ points: r.drawdown_curve, color: colors.down, kind: "area" }]}
                />
              </div>
            </div>
          )}

          {r && r.timing && tab === "timing" && (
            <div className="flex h-full flex-col gap-2 p-2">
              <div className="flex shrink-0 flex-wrap items-baseline gap-x-4 gap-y-1 rounded border border-term-border bg-term-bg/40 px-2.5 py-1.5">
                <span className="text-[11px] uppercase tracking-wider text-term-muted">Timing</span>
                <span className="font-mono text-xs text-term-text capitalize">
                  {r.timing.kind}
                  {r.timing.kind === "trend"
                    ? ` · ${r.timing.params.ma}-day MA · floor ${Math.round((r.timing.params.floor ?? 0) * 100)}%`
                    : ` · ${r.timing.params.n_regimes} regimes`}
                  {r.rebalance && r.rebalance !== "monthly" ? ` · ${r.rebalance} rebalance` : ""}
                </span>
                <span className="ml-auto text-[11px] uppercase tracking-wider text-term-muted">vs. no timing</span>
              </div>
              <div className="grid shrink-0 grid-cols-3 gap-2">
                <Metric label="Δ Sharpe" value={(r.timing.delta.sharpe > 0 ? "+" : "") + r.timing.delta.sharpe.toFixed(2)} good={r.timing.delta.sharpe > 0} emphasis />
                <Metric label="Δ CAGR" value={fmtPct(r.timing.delta.cagr)} good={r.timing.delta.cagr > 0} />
                <Metric label="Δ MaxDD" value={fmtPct(r.timing.delta.max_drawdown)} good={r.timing.delta.max_drawdown > 0} />
              </div>
              <div className="min-h-[150px] flex-[3]">
                <SeriesChart
                  title="Timed vs. baseline (no timing)"
                  series={[
                    { points: r.timing.baseline_equity_curve, color: colors.volume, title: "no timing" },
                    { points: r.equity_curve, color: colors.accent, title: "timed" },
                  ]}
                />
              </div>
              <div className="min-h-[90px] flex-[1]">
                <SeriesChart
                  title="Market exposure (% invested)"
                  series={[{ points: r.timing.exposure_curve, color: colors.accent, kind: "area" }]}
                />
              </div>
              {r.timing.policy && (
                <div className="shrink-0 overflow-auto">
                  <div className="px-1 pb-1 text-[11px] uppercase tracking-wider text-term-muted">
                    Regime policy <span className="normal-case text-term-muted/60">(in-sample fit)</span>
                  </div>
                  <table className="w-full border-collapse font-mono text-[11px]">
                    <thead>
                      <tr className="text-term-muted">
                        <th className="px-2 py-0.5 text-left">Regime</th>
                        <th className="px-2 py-0.5 text-right">Ann. ret</th>
                        <th className="px-2 py-0.5 text-right">Ann. vol</th>
                        <th className="px-2 py-0.5 text-right">Risky %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.timing.policy.map((p) => (
                        <tr key={p.regime} className="border-t border-term-border/40">
                          <td className="px-2 py-0.5 text-term-text">{p.regime}</td>
                          <td className={cx("px-2 py-0.5 text-right", p.ann_mean >= 0 ? "text-term-up" : "text-term-down")}>{fmtPct(p.ann_mean)}</td>
                          <td className="px-2 py-0.5 text-right text-term-text">{fmtPct(p.ann_vol, false)}</td>
                          <td className="px-2 py-0.5 text-right text-term-accent">{Math.round(p.risky_fraction * 100)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {r && tab === "factor" && (
            <div className="flex h-full flex-col gap-3 overflow-auto p-2">
              {!r.ic && !r.quantile_spread && (
                <EmptyState title={t("bt.factorEmpty")} />
              )}
              {r.ic && (
                <div>
                  <div className="px-1 pb-1 text-[11px] uppercase tracking-wider text-term-muted">
                    {t("bt.ic")}
                  </div>
                  <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                    <Metric label="Mean IC" value={r.ic.mean_ic.toFixed(3)} good={r.ic.mean_ic > 0} emphasis />
                    <Metric label="ICIR" value={r.ic.icir != null ? r.ic.icir.toFixed(2) : "—"} good={r.ic.icir != null ? r.ic.icir > 0.5 : null} />
                    <Metric label="IC Hit Rate" value={fmtPct(r.ic.hit_rate, false)} good={r.ic.hit_rate > 50} />
                    <Metric label="Periods" value={String(r.ic.n_periods)} />
                  </div>
                  {r.ic.series.length > 1 && (
                    <div className="mt-2 min-h-[110px]">
                      <SeriesChart
                        title={t("bt.icSeries")}
                        series={[{ points: r.ic.series, color: colors.accent, kind: "histogram" }]}
                      />
                    </div>
                  )}
                </div>
              )}
              {r.ic_decay && r.ic_decay.length > 0 && (
                <div className="overflow-hidden rounded border border-term-border">
                  <div className="border-b border-term-border bg-term-elev px-2 py-1 text-[11px] uppercase tracking-wider text-term-muted">
                    {t("bt.icDecay")}
                  </div>
                  <table className="w-full border-collapse font-mono text-[11px]">
                    <thead>
                      <tr className="text-term-muted">
                        <th className="px-2 py-0.5 text-left font-medium">Horizon</th>
                        <th className="px-2 py-0.5 text-right font-medium">Mean IC</th>
                        <th className="px-2 py-0.5 text-right font-medium">ICIR</th>
                        <th className="px-2 py-0.5 text-right font-medium">n</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.ic_decay.map((d) => (
                        <tr key={d.horizon} className="border-t border-term-border/30">
                          <td className="px-2 py-0.5">{d.horizon}m</td>
                          <td className={cx("px-2 py-0.5 text-right", d.mean_ic >= 0 ? "text-term-up" : "text-term-down")}>
                            {d.mean_ic.toFixed(3)}
                          </td>
                          <td className="px-2 py-0.5 text-right text-term-muted">{d.icir != null ? d.icir.toFixed(2) : "—"}</td>
                          <td className="px-2 py-0.5 text-right text-term-muted">{d.n}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {r.quantile_spread && (
                <div>
                  <div className="flex items-center gap-3 px-1 pb-1">
                    <span className="text-[11px] uppercase tracking-wider text-term-muted">{t("bt.qspread")}</span>
                    {r.quantile_spread.spread != null && (
                      <span className={cx("font-mono text-[11px]", r.quantile_spread.spread >= 0 ? "text-term-up" : "text-term-down")}>
                        L/S {fmtPct(r.quantile_spread.spread)}
                      </span>
                    )}
                    {r.quantile_spread.monotonicity != null && (
                      <span className="font-mono text-[11px] text-term-muted">mono {r.quantile_spread.monotonicity.toFixed(2)}</span>
                    )}
                  </div>
                  <div className="flex items-end gap-1 px-1" style={{ height: 96 }}>
                    {(() => {
                      const bs = r.quantile_spread.buckets;
                      const max = Math.max(1, ...bs.map((b) => Math.abs(b ?? 0)));
                      return bs.map((v, i) => (
                        <div key={i} className="flex flex-1 flex-col items-center justify-end gap-0.5">
                          <span className={cx("font-mono text-[10px]", (v ?? 0) >= 0 ? "text-term-up" : "text-term-down")}>
                            {v == null ? "—" : fmtPct(v)}
                          </span>
                          <div
                            className={cx("w-full rounded-sm", (v ?? 0) >= 0 ? "bg-term-up/60" : "bg-term-down/60")}
                            style={{ height: Math.max(2, (Math.abs(v ?? 0) / max) * 64) }}
                          />
                          <span className="text-[10px] text-term-muted">Q{i + 1}</span>
                        </div>
                      ));
                    })()}
                  </div>
                </div>
              )}
            </div>
          )}

          {r && tab === "analysis" && (
            <div className="flex h-full flex-col gap-3 overflow-auto p-2">
              {r.benchmark && (
                <div>
                  <div className="px-1 pb-1 text-[11px] uppercase tracking-wider text-term-muted">
                    {t("bt.marketModel")} · <span className="text-term-text/80">{r.benchmark_label}</span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
                    <Metric label="Alpha (ann)" value={fmtPct(r.benchmark.alpha)} good={r.benchmark.alpha > 0} emphasis />
                    <Metric label="Beta" value={r.benchmark.beta.toFixed(2)} />
                    <Metric
                      label="Info Ratio"
                      value={r.benchmark.information_ratio != null ? r.benchmark.information_ratio.toFixed(2) : "—"}
                      good={r.benchmark.information_ratio != null ? r.benchmark.information_ratio > 0.5 : null}
                    />
                    <Metric label="Tracking Err" value={fmtPct(r.benchmark.tracking_error, false)} />
                    <Metric label="Excess CAGR" value={fmtPct(r.benchmark.excess_cagr)} good={r.benchmark.excess_cagr > 0} />
                    <Metric label="R²" value={r.benchmark.r_squared.toFixed(2)} />
                  </div>
                </div>
              )}
              {r.rolling_beta.length > 1 && (
                <div className="min-h-[110px]">
                  <SeriesChart
                    title={t("bt.rollingBeta", { b: r.benchmark_label })}
                    series={[{ points: r.rolling_beta, color: colors.up }]}
                  />
                </div>
              )}
              {r.sharpe_ci && (
                <div>
                  <div className="px-1 pb-1 text-[11px] uppercase tracking-wider text-term-muted">
                    {t("bt.sharpeCi")}
                  </div>
                  <div className="grid grid-cols-4 gap-2">
                    <Metric label="p5" value={r.sharpe_ci.p5.toFixed(2)} good={r.sharpe_ci.p5 > 0} />
                    <Metric label="Median" value={r.sharpe_ci.p50.toFixed(2)} good={r.sharpe_ci.p50 > 0} emphasis />
                    <Metric label="p95" value={r.sharpe_ci.p95.toFixed(2)} good={r.sharpe_ci.p95 > 0} />
                    <Metric label="P(SR>0)" value={fmtPct(r.sharpe_ci.prob_positive, false)} good={r.sharpe_ci.prob_positive > 90} />
                  </div>
                </div>
              )}
              {r.distribution && (
                <div>
                  <div className="px-1 pb-1 text-[11px] uppercase tracking-wider text-term-muted">
                    {t("bt.distribution")}
                  </div>
                  <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
                    <Metric label="Skew" value={r.distribution.skew.toFixed(2)} good={r.distribution.skew > 0} />
                    <Metric label="Ex. Kurtosis" value={r.distribution.kurtosis.toFixed(2)} />
                    <Metric label="VaR 95%" value={fmtPct(r.distribution.var95)} />
                    <Metric
                      label="CVaR 95%"
                      value={r.distribution.cvar95 != null ? fmtPct(r.distribution.cvar95) : "—"}
                    />
                    <Metric label="% Up Days" value={fmtPct(r.distribution.pct_positive, false)} good={r.distribution.pct_positive > 50} />
                    <Metric
                      label="Tail Ratio"
                      value={r.distribution.tail_ratio != null ? r.distribution.tail_ratio.toFixed(2) : "—"}
                      good={r.distribution.tail_ratio != null ? r.distribution.tail_ratio > 1 : null}
                    />
                    <Metric label="Best Day" value={fmtPct(r.distribution.best_day)} good />
                    <Metric label="Worst Day" value={fmtPct(r.distribution.worst_day)} good={false} />
                    <Metric
                      label="Win/Loss"
                      value={r.distribution.win_loss != null ? r.distribution.win_loss.toFixed(2) : "—"}
                      good={r.distribution.win_loss != null ? r.distribution.win_loss > 1 : null}
                    />
                  </div>
                </div>
              )}
              <div className="min-h-[140px] flex-1">
                <SeriesChart
                  title={t("bt.rollingSharpe", { x: r.rolling_window })}
                  series={[{ points: r.rolling_sharpe, color: colors.accent }]}
                />
              </div>
              {r.stability && (
                <div className="shrink-0 overflow-hidden rounded border border-term-border">
                  <div className="flex items-center justify-between border-b border-term-border bg-term-elev px-2 py-1">
                    <span className="text-[11px] uppercase tracking-wider text-term-muted">{t("bt.stability")}</span>
                    <span className="font-mono text-[11px] text-term-muted">
                      {r.stability.positive_periods}/{r.stability.n_periods} {t("bt.posPeriods")} ·{" "}
                      {fmtPct(r.stability.consistency * 100, false)} {t("bt.consistent")}
                    </span>
                  </div>
                  <table className="w-full border-collapse font-mono text-[11px]">
                    <thead>
                      <tr className="text-term-muted">
                        <th className="px-2 py-0.5 text-left font-medium">{t("bt.period")}</th>
                        <th className="px-2 py-0.5 text-right font-medium">Sharpe</th>
                        <th className="px-2 py-0.5 text-right font-medium">{t("bt.return")}</th>
                        <th className="px-2 py-0.5 text-right font-medium">{t("bt.maxdd")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.stability.periods.map((p) => (
                        <tr key={p.start} className="border-t border-term-border/30">
                          <td className="px-2 py-0.5">{p.label}</td>
                          <td className={cx("px-2 py-0.5 text-right", p.sharpe >= 0 ? "text-term-up" : "text-term-down")}>
                            {p.sharpe.toFixed(2)}
                          </td>
                          <td className={cx("px-2 py-0.5 text-right", p.ret >= 0 ? "text-term-up" : "text-term-down")}>
                            {fmtPct(p.ret)}
                          </td>
                          <td className="px-2 py-0.5 text-right text-term-down">{fmtPct(p.max_drawdown, false)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {r.drawdowns.length > 0 && (
                <div className="shrink-0 overflow-hidden rounded border border-term-border">
                  <div className="border-b border-term-border bg-term-elev px-2 py-1 text-[11px] uppercase tracking-wider text-term-muted">
                    {t("bt.worstDD")}
                  </div>
                  <table className="w-full border-collapse font-mono text-[11px]">
                    <thead>
                      <tr className="text-term-muted">
                        <th className="px-2 py-0.5 text-right font-medium">{t("bt.depth")}</th>
                        <th className="px-2 py-0.5 text-left font-medium">{t("bt.peak")}</th>
                        <th className="px-2 py-0.5 text-left font-medium">{t("bt.trough")}</th>
                        <th className="px-2 py-0.5 text-left font-medium">{t("bt.recover")}</th>
                        <th className="px-2 py-0.5 text-right font-medium">{t("bt.underwater")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.drawdowns.map((d) => (
                        <tr key={d.rank} className="border-t border-term-border/30">
                          <td className="px-2 py-0.5 text-right text-term-down">{fmtPct(d.depth, false)}</td>
                          <td className="px-2 py-0.5">{d.peak_date}</td>
                          <td className="px-2 py-0.5">{d.trough_date}</td>
                          <td className="px-2 py-0.5">
                            {d.ongoing ? <span className="text-term-down">{t("bt.ongoing")}</span> : d.recovery_date}
                          </td>
                          <td className="px-2 py-0.5 text-right text-term-muted">
                            {d.underwater_days}
                            {t("bt.days")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="flex shrink-0 flex-wrap items-start gap-6">
                <MonthlyHeatmap rows={r.monthly_returns} title={t("bt.monthly")} />
                {r.top_weights.length > 0 && (
                  <div className="overflow-hidden rounded border border-term-border">
                    <div className="border-b border-term-border bg-term-elev px-2 py-1 text-[11px] uppercase tracking-wider text-term-muted">
                      {t("bt.weights")}
                    </div>
                    <table className="w-full border-collapse font-mono text-[11px]">
                      <tbody>
                        {r.top_weights.map((w) => (
                          <tr key={w.symbol} className="border-b border-term-border/30 last:border-0">
                            <td className="px-2 py-0.5">{w.symbol}</td>
                            <td className={cx("px-2 py-0.5 text-right", w.weight >= 0 ? "text-term-up" : "text-term-down")}>
                              {fmtPct(w.weight)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {r.sector_exposure && r.sector_exposure.length > 0 && (
                  <div className="overflow-hidden rounded border border-term-border">
                    <div className="border-b border-term-border bg-term-elev px-2 py-1 text-[11px] uppercase tracking-wider text-term-muted">
                      {t("bt.sectorExp")}
                    </div>
                    <table className="w-full border-collapse font-mono text-[11px]">
                      <tbody>
                        {r.sector_exposure.map((sx) => (
                          <tr key={sx.sector} className="border-b border-term-border/30 last:border-0">
                            <td className="px-2 py-0.5">{sx.sector}</td>
                            <td className={cx("px-2 py-0.5 text-right", sx.net >= 0 ? "text-term-up" : "text-term-down")}>
                              {fmtPct(sx.net)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}

          {r && tab === "costs" && (
            <div className="flex h-full flex-col gap-3 overflow-auto p-2">
              <div className="min-h-[110px] flex-1">
                <SeriesChart
                  title={t("bt.exposure")}
                  series={[
                    { points: r.gross_exposure, color: colors.accent, title: "gross" },
                    { points: r.net_exposure, color: colors.up, title: "net" },
                  ]}
                />
              </div>
              <div className="min-h-[110px] flex-1">
                <SeriesChart
                  title={t("bt.turnoverMonthly")}
                  series={[{ points: r.turnover_monthly, color: colors.volume, kind: "histogram" }]}
                />
              </div>
              <div className="min-h-[110px] flex-1">
                <SeriesChart
                  title={t("bt.costsCum", { x: fmtCompact(r.total_costs) })}
                  series={[{ points: r.costs_cum, color: colors.down }]}
                />
              </div>
              {r.cost_sensitivity && r.cost_sensitivity.length > 0 && (
                <div className="shrink-0 overflow-hidden rounded border border-term-border">
                  <div className="border-b border-term-border bg-term-elev px-2 py-1 text-[11px] uppercase tracking-wider text-term-muted">
                    {t("bt.costSens")}
                  </div>
                  <table className="w-full border-collapse font-mono text-[11px]">
                    <thead>
                      <tr className="text-term-muted">
                        <th className="px-2 py-0.5 text-right font-medium">Slippage bps</th>
                        <th className="px-2 py-0.5 text-right font-medium">Sharpe</th>
                        <th className="px-2 py-0.5 text-right font-medium">CAGR</th>
                        <th className="px-2 py-0.5 text-right font-medium">Costs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.cost_sensitivity.map((c) => (
                        <tr key={c.bps} className="border-t border-term-border/30">
                          <td className="px-2 py-0.5 text-right">{c.bps}</td>
                          <td className={cx("px-2 py-0.5 text-right", c.sharpe >= 0 ? "text-term-up" : "text-term-down")}>
                            {c.sharpe.toFixed(2)}
                          </td>
                          <td className={cx("px-2 py-0.5 text-right", c.cagr >= 0 ? "text-term-up" : "text-term-down")}>
                            {fmtPct(c.cagr)}
                          </td>
                          <td className="px-2 py-0.5 text-right text-term-muted">${fmtCompact(c.total_costs)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {tab === "runs" && (
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr>
                  <th className={cx(TABLE.th, "text-left")}>{t("bt.run")}</th>
                  <th className={cx(TABLE.th, "text-right")}>Sharpe</th>
                  <th className={cx(TABLE.th, "text-right")}>DSR</th>
                  <th className={cx(TABLE.th, "text-right")}>CAGR</th>
                  <th className={cx(TABLE.th, "text-right")}>{t("bt.maxdd")}</th>
                  <th className={cx(TABLE.th, "text-right")}>{t("bt.turnover")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.length === 0 && (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState title={t("bt.noRuns")} />
                    </td>
                  </tr>
                )}
                {runs.map((item) => {
                  const res = item.result as BacktestResponse;
                  return (
                    <tr
                      key={item.id}
                      onClick={() => selectRun(item.id)}
                      className={cx("cursor-pointer", TABLE.row, activeRunId === item.id && TABLE.rowActive)}
                    >
                      <td className="px-2 py-1 font-mono">{item.label}</td>
                      <td className="px-2 py-1 text-right font-mono">{res.metrics.sharpe.toFixed(2)}</td>
                      <td className="px-2 py-1 text-right font-mono">
                        {res.robustness.dsr != null ? fmtPct(res.robustness.dsr, false) : "—"}
                      </td>
                      <td className={cx("px-2 py-1 text-right font-mono", res.metrics.cagr >= 0 ? "text-term-up" : "text-term-down")}>
                        {fmtPct(res.metrics.cagr)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-term-down">{fmtPct(res.metrics.max_drawdown, false)}</td>
                      <td className="px-2 py-1 text-right font-mono">{fmtPct(res.avg_turnover, false)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        </div>
            ),
          },
          {
            id: "chat",
            title: t("bt.agentTitle"),
            content: (
              <AgentChatPanel
                title={t("bt.agentTitle")}
                messages={messages}
                busy={busy}
                onSend={send}
                onStop={() => useAgentRuns.getState().stop(jobId)}
                roles={["user", "assistant", "error"]}
                placeholder={t("bt.chatPlaceholder")}
                emptyHint={t("bt.chatHint")}
                suggestions={suggestions}
              />
            ),
          },
          {
            id: "processing",
            title: "Processing",
            content: (
              <AgentProcessingLog
                messages={messages}
                busy={busy}
                activeRunId={activeRunId}
                onSelectRun={selectRun}
              />
            ),
          },
          {
            id: "history",
            title: t("chat.history"),
            content: (
              <ChatHistoryList
                history={factorHistory}
                onNewChat={() => useAgentRuns.getState().newChat(jobId)}
                onLoadSession={(sess) => {
                  useAgentRuns.getState().loadSession(jobId, sess);
                  setTab("overview");
                }}
                onDeleteSession={(id) => useChatHistory.getState().remove(id)}
              />
            ),
          },
        ]}
      />
    </WidgetShell>
  );
}
