import { useEffect, useRef, useState } from "react";
import type { FactorCorrelationMatrix, FactorDetail, FactorScoreRow, FactorScorecard, LinePoint, MonitorHistory } from "../api/types";
import AgentChatPanel, { type ChatMsg, type Suggestion } from "../components/AgentChatPanel";
import BentoSubGrid from "../components/BentoSubGrid";
import ChatHistoryList from "../components/ChatHistoryList";
import { chartColors, seriesColors } from "../lib/chartTheme";
import { SeriesChart as SeriesChartEngine } from "../lib/seriesChart";
import { cx } from "../lib/format";
import { useT } from "../lib/i18n";
import { useChatHistory } from "../state/chatHistory";
import { useFactorAgent, type FMRunRec } from "../state/factorAgentRuns";
import { usePalette } from "../state/settings";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState, LoadingState } from "../components/States";
import { MetricCard, TABLE } from "../components/MetricCard";
import { Badge } from "../components/Badge";

const EMPTY_MSGS: ChatMsg[] = [];
const EMPTY_RUNS: FMRunRec[] = [];

interface SeriesSpec { points: LinePoint[]; color: string; title?: string; kind?: "line" | "area" | "histogram" }

function SeriesChart({ series, title }: { series: SeriesSpec[]; title?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const engineRef = useRef<SeriesChartEngine | null>(null);
  const palette = usePalette();
  useEffect(() => {
    if (!ref.current) return;
    const engine = new SeriesChartEngine(ref.current, chartColors());
    engineRef.current = engine;
    return () => { engineRef.current = null; engine.destroy(); };
  }, [palette]);
  useEffect(() => { engineRef.current?.setData(series); }, [series]);
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

export default function FactorMonitorWidget(props: WidgetProps) {
  const { channel, setChannel } = useWidgetSymbol(props);
  const t = useT();

  // Run state lives in the global agent store (keyed by panel id) so it survives unmount / tab
  // switches — same pattern as the backtest agent.
  const jobId = `${props.api.id}:factor_monitor`;
  const job = useFactorAgent((s) => s.jobs[jobId]);
  const messages = job?.messages ?? EMPTY_MSGS;
  const runs = job?.runs ?? EMPTY_RUNS;
  const activeRunId = job?.activeRunId ?? null;
  const busy = job?.status === "running";
  const active = activeRunId ? runs.find((r) => r.id === activeRunId) ?? null : null;

  const history = useChatHistory((s) => s.sessions).filter((h) => h.kind === "factor_monitor");

  // Current state rides along as context so relative edits ("now on sp500") resolve.
  const ctx = (): Record<string, unknown> => active?.params ?? {};
  const send = (goal: string) => useFactorAgent.getState().start(jobId, goal, ctx());
  const selectRun = (id: string) => useFactorAgent.getState().selectRun(jobId, id);

  const colors = seriesColors();
  const board = active?.kind === "board" ? (active.data as FactorScorecard) : null;
  const detail = active?.kind === "detail" ? (active.data as FactorDetail) : null;
  const hist = active?.kind === "history" ? (active.data as MonitorHistory) : null;
  const heat = active?.kind === "heatmap" ? (active.data as FactorCorrelationMatrix) : null;

  const suggestions: Suggestion[] = board
    ? [
        { label: t("fm.sug.drillTop"), prompt: `drill into ${board.rows[0]?.factor ?? "momentum"}` },
        { label: t("fm.sug.switchNdx"), prompt: "now on nasdaq100" },
        { label: t("fm.sug.q10"), prompt: "use 10 quantile buckets" },
        { label: t("fm.sug.rankDrill"), prompt: "rank on the dow then drill into the best factor" },
        { label: "Correlation heatmap", prompt: `show the factor correlation heatmap on ${board.universe}` },
        { label: t("fm.sug.save"), prompt: "save this as my-monitor" },
      ]
    : [
        { label: t("fm.sug.rankDow"), prompt: "rank all factors on the dow" },
        { label: "My factors", prompt: "what factors are available?" },
        { label: "Correlation heatmap", prompt: "show the factor correlation heatmap on the dow" },
        { label: t("fm.sug.drillMom"), prompt: "drill into momentum" },
      ];

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <span className="text-[11px] uppercase tracking-wider text-term-muted">Factor perf</span>
          {active && <span className="truncate font-mono text-[11px] text-term-text">{active.label}</span>}
        </>
      }
    >
      <BentoSubGrid
        storageKey={jobId}
        seed={(api) => {
          api.addPanel({ id: "dashboard", component: "dashboard", title: "Results" });
          const chat = api.addPanel({
            id: "chat",
            component: "chat",
            title: t("fm.chatTitle"),
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
            title: "Results",
            content: (
        <div className="h-full min-h-0 overflow-auto p-2">
          {!active && (busy ? <LoadingState rows={8} /> : <EmptyState title={t("fm.empty")} />)}

          {board && (
            <>
              <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-term-muted">
                <Badge tone="accent">{board.universe}</Badge>
                <span>{board.n_instruments} names · {board.window_start} → {board.window_end} · h={board.horizon}</span>
              </div>
              <table className="w-full font-mono text-[12px]">
                <thead>
                  <tr>
                    <th className={cx(TABLE.th, "text-left")}>Factor</th>
                    <th className={cx(TABLE.th, "text-right")}>Mean IC</th>
                    <th className={cx(TABLE.th, "text-right")}>IC-IR</th>
                    <th className={cx(TABLE.th, "text-right")}>t</th>
                    <th className={cx(TABLE.th, "text-right")}>Hit%</th>
                    <th className={cx(TABLE.th, "text-right")}>Q-spread%</th>
                    <th className={cx(TABLE.th, "text-right")}>Autocorr</th>
                  </tr>
                </thead>
                <tbody>
                  {board.rows.map((r) => <Row key={r.factor} r={r} onClick={() => send(`drill into ${r.factor} on ${board.universe}`)} />)}
                </tbody>
              </table>
              {board.errors.length > 0 && <div className="mt-2 text-[10px] text-term-muted">skipped: {board.errors.map((e) => e.factor).join(", ")}</div>}
            </>
          )}

          {detail && <DetailView detail={detail} colors={colors} />}

          {hist && (
            hist.n_snapshots > 0 ? (
              <div className="flex h-full flex-col gap-2">
                <div className="text-[11px] text-term-muted">{hist.monitor} · {hist.n_snapshots} snapshot(s) · mean IC drift per factor</div>
                <div className="min-h-0 flex-1">
                  <SeriesChart series={Object.entries(hist.factors).map(([f, s], i) => ({ points: s.mean_ic, color: PALETTE[i % PALETTE.length](colors), title: f, kind: "line" }))} />
                </div>
              </div>
            ) : <EmptyState title="No snapshots yet" hint="Run the monitor a few times to record IC drift." />
          )}

          {heat && <CorrHeatmap data={heat} />}
        </div>
            ),
          },
          {
            id: "chat",
            title: t("fm.chatTitle"),
            content: (
              <AgentChatPanel
                title={t("fm.chatTitle")}
                messages={messages}
                busy={busy}
                onSend={send}
                onStop={() => useFactorAgent.getState().stop(jobId)}
                onSelectRun={selectRun}
                activeRunId={activeRunId}
                placeholder={t("fm.chatPlaceholder")}
                emptyHint={t("fm.chatHint")}
                suggestions={suggestions}
              />
            ),
          },
          {
            id: "history",
            title: t("chat.history"),
            content: (
              <ChatHistoryList
                history={history}
                onNewChat={() => useFactorAgent.getState().newChat(jobId)}
                onLoadSession={(sess) => useFactorAgent.getState().loadSession(jobId, sess)}
                onDeleteSession={(id) => useChatHistory.getState().remove(id)}
              />
            ),
          },
        ]}
      />
    </WidgetShell>
  );
}

type Colors = ReturnType<typeof seriesColors>;
type Layer = "returns" | "risk" | "health";

function Card({ label, value, good }: { label: string; value: string; good?: boolean | null }) {
  return <MetricCard label={label} value={value} tone={good == null ? null : good ? "up" : "down"} />;
}

const fmtNum = (v: number | null | undefined, digits = 2): string => (v == null || Number.isNaN(v) ? "—" : v.toFixed(digits));

/** Single-factor drill-down across the three diagnostic layers (Returns / Risk / Health). */
function DetailView({ detail, colors }: { detail: FactorDetail; colors: Colors }) {
  const [layer, setLayer] = useState<Layer>("returns");
  const m = detail.metrics;
  const ret = detail.returns;
  const risk = detail.risk;
  const health = detail.health;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/* layer sub-tabs — the user's Returns / Risk / Health framing */}
      <div className="flex shrink-0">
        <div className="flex items-center overflow-hidden rounded border border-term-border bg-term-sunken text-[11px]">
          {(["returns", "risk", "health"] as Layer[]).map((lk) => (
            <button
              key={lk}
              onClick={() => setLayer(lk)}
              aria-pressed={layer === lk}
              className={cx(
                "focus-ring px-2.5 py-0.5 uppercase tracking-wide transition-colors",
                layer === lk ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:text-term-text",
              )}
            >
              {lk === "returns" ? "Returns" : lk === "risk" ? "Risk" : "Health"}
            </button>
          ))}
        </div>
      </div>

      {layer === "returns" && (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div className="grid shrink-0 grid-cols-5 gap-2 text-[12px]">
            <Card label="LS Total" value={ret ? `${fmtNum(ret.ls_total_return)}%` : "—"} good={ret ? ret.ls_total_return >= 0 : null} />
            <Card label="LS Sharpe" value={ret ? fmtNum(ret.ls_sharpe) : "—"} good={ret ? ret.ls_sharpe > 0 : null} />
            <Card label="Mean IC" value={m.mean_ic.toFixed(4)} good={m.mean_ic >= 0} />
            <Card label="IC-IR" value={m.ic_ir.toFixed(3)} good={m.ic_ir >= 0} />
            <Card label="Monotonic" value={ret ? fmtNum(ret.quantile_monotonicity, 2) : "—"} good={ret && ret.quantile_monotonicity != null ? ret.quantile_monotonicity > 0.5 : null} />
          </div>
          <div className="grid min-h-0 flex-1 grid-cols-2 grid-rows-2 gap-2">
            <SeriesChart title="Long-short decile cum. return %" series={[{ points: ret?.ls_curve ?? [], color: colors.accent, kind: "area" }]} />
            <SeriesChart title="Long-short drawdown %" series={[{ points: ret?.ls_drawdown ?? [], color: colors.down, kind: "area" }]} />
            <SeriesChart title={`Quantile mean fwd-return % (q=${detail.q})`} series={[{ points: detail.quantile_returns.map((d) => ({ time: d.bucket, value: d.value })), color: colors.accent, kind: "histogram" }]} />
            <SeriesChart title={`Rolling IC (${detail.roll_window}d, h=${detail.horizon})`} series={[{ points: detail.ic_series, color: colors.up, kind: "line", title: "IC" }]} />
          </div>
        </div>
      )}

      {layer === "risk" && (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div className="grid shrink-0 grid-cols-4 gap-2 text-[12px]">
            <Card label={`Beta vs ${risk?.benchmark ?? "SPY"}`} value={fmtNum(risk?.beta, 2)} good={risk?.beta != null ? Math.abs(risk.beta) < 0.3 : null} />
            <Card label="Market corr" value={fmtNum(risk?.market_corr, 2)} good={risk?.market_corr != null ? Math.abs(risk.market_corr) < 0.3 : null} />
            <Card label="Alpha (ann %)" value={fmtNum(risk?.alpha_annual, 1)} good={risk?.alpha_annual != null ? risk.alpha_annual >= 0 : null} />
            <Card label="Autocorr" value={m.autocorr.toFixed(3)} />
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <div className="mb-1 text-[10px] uppercase tracking-wider text-term-muted">Correlation to other factors (Spearman) — high = redundant, adds no diversification</div>
            <table className="w-full font-mono text-[12px]">
              <tbody>
                {(risk?.factor_correlations ?? []).map((c) => (
                  <tr key={c.factor} className="border-b border-term-border/30">
                    <td className="px-2 py-0.5">{c.label}</td>
                    <td className={cx("px-2 py-0.5 text-right font-semibold", Math.abs(c.corr) >= 0.6 ? "text-term-down" : c.corr >= 0 ? "text-term-up" : "text-term-muted")}>{c.corr.toFixed(3)}</td>
                  </tr>
                ))}
                {(!risk || risk.factor_correlations.length === 0) && (
                  <tr><td className="px-2 py-1 text-[11px] text-term-muted">No correlation data.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {layer === "health" && (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div className="shrink-0">
            <div className="mb-1 text-[10px] uppercase tracking-wider text-term-muted">Regime breakdown — mean IC and annualized LS return by market regime</div>
            <table className="w-full font-mono text-[12px]">
              <thead>
                <tr>
                  <th className={cx(TABLE.th, "text-left")}>Regime</th>
                  <th className={cx(TABLE.th, "text-right")}>Mean IC</th>
                  <th className={cx(TABLE.th, "text-right")}>LS return % (ann)</th>
                  <th className={cx(TABLE.th, "text-right")}>days</th>
                </tr>
              </thead>
              <tbody>
                {(health?.regimes ?? []).map((r) => (
                  <tr key={r.regime} className="border-b border-term-border/30">
                    <td className="px-2 py-0.5">{r.regime}</td>
                    <td className={cx("px-2 py-0.5 text-right", (r.ic ?? 0) >= 0 ? "text-term-up" : "text-term-down")}>{fmtNum(r.ic, 4)}</td>
                    <td className={cx("px-2 py-0.5 text-right", (r.ls_return ?? 0) >= 0 ? "text-term-up" : "text-term-down")}>{fmtNum(r.ls_return, 1)}</td>
                    <td className="px-2 py-0.5 text-right text-term-muted">{r.n}</td>
                  </tr>
                ))}
                {(!health || health.regimes.length === 0) && (
                  <tr><td className="px-2 py-1 text-[11px] text-term-muted">No regime data (benchmark unavailable).</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-2">
            <SeriesChart title="IC decay by horizon" series={[{ points: detail.ic_decay.map((d) => ({ time: d.horizon, value: d.value })), color: colors.up, kind: "histogram" }]} />
            <SeriesChart title="Turnover % (monthly)" series={[{ points: detail.turnover_series, color: colors.down, kind: "line" }]} />
          </div>
        </div>
      )}
    </div>
  );
}

/** Factor × factor correlation heatmap — diverging green(+)/red(−) cells, intensity = |corr|.
 * Reuses the channel-color CSS vars like the backtest monthly heatmap (no chart engine needed). */
function CorrHeatmap({ data }: { data: FactorCorrelationMatrix }) {
  const f = data.factors;
  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-term-muted">
        <Badge tone="accent">{data.universe}</Badge>
        <span>{data.n_instruments} names · {data.window_start} → {data.window_end} · {data.method} pooled factor correlation — high = redundant</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="border-collapse font-mono text-[11px]">
          <thead>
            <tr>
              <th className="sticky top-0 z-10 bg-term-elev p-1" />
              {f.map((k, j) => (
                <th key={k} className="sticky top-0 z-10 bg-term-elev p-1 font-medium text-term-muted" title={data.labels[j]}>{k}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.matrix.map((row, i) => (
              <tr key={f[i]}>
                <td className="bg-term-elev px-2 text-right text-term-muted" title={data.labels[i]}>{f[i]}</td>
                {row.map((v, j) => {
                  const a = v == null ? 0 : Math.min(Math.abs(v), 1) * 0.7;
                  const bg = v == null ? "rgb(var(--term-border) / 0.1)" : v >= 0 ? `rgb(var(--term-up) / ${a})` : `rgb(var(--term-down) / ${a})`;
                  return (
                    <td key={j} title={`${f[i]} vs ${f[j]}: ${v ?? "—"}`} className="h-8 w-12 border border-term-border/40 text-center" style={{ backgroundColor: bg }}>
                      {v == null ? "" : v.toFixed(2)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {data.errors.length > 0 && <div className="mt-2 text-[10px] text-term-muted">skipped: {data.errors.map((e) => e.factor).join(", ")}</div>}
      </div>
    </div>
  );
}

const PALETTE = [
  (c: ReturnType<typeof seriesColors>) => c.accent,
  (c: ReturnType<typeof seriesColors>) => c.up,
  (c: ReturnType<typeof seriesColors>) => c.down,
  (c: ReturnType<typeof seriesColors>) => c.volume,
];

function Row({ r, onClick }: { r: FactorScoreRow; onClick: () => void }) {
  return (
    <tr onClick={onClick} className={cx("cursor-pointer", TABLE.row)}>
      <td className="px-2 py-0.5">{r.label}</td>
      <td className={cx("px-2 py-0.5 text-right", r.mean_ic >= 0 ? "text-term-up" : "text-term-down")}>{r.mean_ic.toFixed(4)}</td>
      <td className={cx("px-2 py-0.5 text-right font-semibold", r.ic_ir >= 0 ? "text-term-up" : "text-term-down")}>{r.ic_ir.toFixed(3)}</td>
      <td className="px-2 py-0.5 text-right text-term-muted">{r.t_stat.toFixed(2)}</td>
      <td className="px-2 py-0.5 text-right text-term-muted">{r.hit_rate}</td>
      <td className={cx("px-2 py-0.5 text-right", r.q_spread >= 0 ? "text-term-up" : "text-term-down")}>{r.q_spread.toFixed(3)}</td>
      <td className="px-2 py-0.5 text-right text-term-muted">{r.autocorr.toFixed(3)}</td>
    </tr>
  );
}
