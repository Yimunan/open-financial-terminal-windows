import { useEffect, useMemo, useRef } from "react";
import type {
  ChartColorKey,
  ChartHeatmapPayload,
  ChartPricePayload,
  ChartSeriesPayload,
} from "../api/types";
import AgentChatPanel, { type Suggestion } from "../components/AgentChatPanel";
import AgentProcessingLog from "../components/AgentProcessingLog";
import BentoSubGrid from "../components/BentoSubGrid";
import ChatHistoryList from "../components/ChatHistoryList";
import { EmptyState } from "../components/States";
import SeriesChart, { type SeriesSpec } from "../components/SeriesChart";
import { CandleChart, type LineSpec } from "../lib/candleChart";
import { chartColors, seriesColors } from "../lib/chartTheme";
import { cx } from "../lib/format";
import { useChartAgent } from "../state/chartAgentRuns";
import { useChatHistory } from "../state/chatHistory";
import { themeColor, usePalette } from "../state/settings";
import { useWorkspace } from "../state/workspace";
import type { WidgetParams, WidgetProps } from "../workspace/widgetRegistry";

const SUGGESTIONS: Suggestion[] = [
  { label: "price+ind", prompt: "AAPL daily candles with a 50-day SMA and RSI" },
  { label: "compare", prompt: "Compare AAPL, MSFT and NVDA over the last year" },
  { label: "ratio", prompt: "GLD vs SLV price ratio over 1 year" },
  { label: "roll corr", prompt: "Rolling 90-day correlation of AAPL and MSFT" },
  { label: "beta", prompt: "AAPL rolling beta vs the S&P 500 over time" },
  { label: "distrib", prompt: "Distribution of NVDA daily returns over 1 year" },
  { label: "vol cone", prompt: "Volatility cone for NVDA" },
  { label: "trailing", prompt: "AAPL trailing returns across timeframes" },
  { label: "rolling", prompt: "BTC/USDT rolling 90-day Sharpe" },
  { label: "drawdown", prompt: "NVDA underwater drawdown over 180 days" },
  { label: "macro", prompt: "US 10-year Treasury yield" },
  { label: "curve", prompt: "Show the US Treasury yield curve vs a year ago" },
  { label: "spread", prompt: "10Y minus 3M Treasury term spread over time" },
  { label: "cpi", prompt: "US CPI over time" },
  { label: "macro cmp", prompt: "Compare CPI, unemployment and Fed funds since 2015" },
  { label: "corr", prompt: "Correlation heatmap of AAPL MSFT NVDA SPY" },
  { label: "seasonal", prompt: "Monthly return calendar for AAPL" },
  { label: "revenue", prompt: "AAPL annual revenue over time" },
  { label: "margin", prompt: "NVDA net margin by quarter" },
  { label: "crypto", prompt: "BTC/USDT daily area chart with a 200-day EMA" },
];

function resolveColor(key: ChartColorKey): string {
  switch (key) {
    case "up": return themeColor("--term-up");
    case "down": return themeColor("--term-down");
    case "series1": return themeColor("--term-series-1");
    case "series2": return themeColor("--term-series-2");
    case "series3": return themeColor("--term-series-3");
    case "series4": return themeColor("--term-series-4");
    default: return themeColor("--term-accent");
  }
}

/** Price/OHLC chart on the CandleChart engine (same mount/setData pattern as ChartWidget). */
function PriceChart({ payload }: { payload: ChartPricePayload }) {
  const palette = usePalette();
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<CandleChart | null>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const chart = new CandleChart(hostRef.current, chartColors());
    chartRef.current = chart;
    return () => {
      chartRef.current = null;
      chart.destroy();
    };
  }, [palette]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const colors = seriesColors();
    const overlays: LineSpec[] = [];
    const lower: LineSpec[] = [];
    let pi = 0;
    let li = 0;
    for (const ind of payload.indicators) {
      for (const [label, points] of Object.entries(ind.series)) {
        if (ind.pane === "price") {
          overlays.push({ label, points, color: colors.priceLines[pi++ % colors.priceLines.length] });
        } else {
          lower.push({ label, points, color: colors.lowerLines[li++ % colors.lowerLines.length] });
        }
      }
    }
    chart.setData({
      candles: payload.candles,
      volume: payload.volume,
      overlays: payload.style === "area" ? [] : overlays,
      lower,
      mode: payload.style === "area" ? "area" : "candles",
      intraday: payload.timeframe !== "1d",
    });
  }, [payload, palette]);

  return <div ref={hostRef} className="h-full w-full" />;
}

function SeriesView({ payload }: { payload: ChartSeriesPayload }) {
  usePalette(); // recolor on theme change (resolveColor reads tokens at render)
  const specs: SeriesSpec[] = payload.specs.map((s) => ({
    points: s.points,
    color: resolveColor(s.colorKey),
    kind: s.kind,
    title: s.title,
  }));
  const axis = payload.xMode
    ? { xMode: payload.xMode, xTicks: payload.xTicks, xUnit: payload.xUnit }
    : undefined;
  return <SeriesChart series={specs} title={payload.title} axis={axis} />;
}

function HeatmapView({ payload }: { payload: ChartHeatmapPayload }) {
  usePalette();
  const { matrix } = payload;
  // Square correlation payloads supply only `labels` (both axes); rectangular payloads (e.g. the
  // monthly-return calendar) supply distinct rows/cols + a color scale + cell format.
  const rowLabels = payload.rows ?? payload.labels;
  const colLabels = payload.cols ?? payload.labels;
  const scale = payload.vmax ?? 1;
  const fmtCell = (v: number) => (payload.fmt === "pct" ? v.toFixed(1) : v.toFixed(2));
  return (
    <div className="h-full overflow-auto p-3">
      {payload.title && (
        <div className="pb-1 text-[10px] uppercase tracking-wider text-term-muted">{payload.title}</div>
      )}
      <table className="border-collapse font-mono text-[11px]">
        <thead>
          <tr>
            <th className="px-1 py-1" />
            {colLabels.map((l) => (
              <th key={l} className="px-1 py-1 text-term-muted">{l}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rowLabels.map((row, i) => (
            <tr key={row}>
              <td className="px-1 py-1 text-right text-term-muted">{row}</td>
              {colLabels.map((col, j) => {
                const v = matrix[i]?.[j];
                const bg =
                  v == null
                    ? "transparent"
                    : themeColor(v >= 0 ? "--term-up" : "--term-down", 0.12 + 0.78 * Math.min(1, Math.abs(v) / scale));
                return (
                  <td
                    key={col}
                    className="px-2 py-1 text-center text-term-text"
                    style={{ background: bg }}
                    title={`${row} · ${col}`}
                  >
                    {v == null ? "—" : fmtCell(v)}
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

export default function ChartStudioWidget(props: WidgetProps) {
  const jobId = props.api.id;
  const job = useChartAgent((s) => s.jobs[jobId]);
  const { start, stop, selectRun, newChat, loadSession } = useChartAgent();
  // Select the stable array and filter in a memo — filtering inside the selector returns a new
  // array each render and sends useSyncExternalStore into an infinite update loop.
  const allSessions = useChatHistory((s) => s.sessions);
  const history = useMemo(() => allSessions.filter((x) => x.kind === "chart_studio"), [allSessions]);
  const removeSession = useChatHistory((s) => s.remove);

  const messages = job?.messages ?? [];
  const runs = job?.runs ?? [];
  const active = runs.find((r) => r.id === job?.activeRunId) ?? null;
  const busy = job?.status === "running";
  const canPin = active?.engine === "price" && !!active.openParams;

  // Sub-windows are a nested Dockview bento grid (like the backtest widget): drag to rearrange,
  // drag borders to resize, tab them together. The per-instance layout persists to localStorage.
  return (
    <BentoSubGrid
      storageKey={`${jobId}:v3`}
      seed={(api) => {
        // Chart (output) fills the top; the conversation is split across a compact bottom row of
        // three windows: History (past sessions) · Processing (the agent's steps + generated charts)
        // · Chat (your prompts + the answers + input).
        api.addPanel({ id: "chart", component: "chart", title: "Chart" });
        const history = api.addPanel({
          id: "history",
          component: "history",
          title: "History",
          position: { referencePanel: "chart", direction: "below" },
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
          title: "Chat",
          position: { referencePanel: "processing", direction: "right" },
        });
        history.api.setSize({ width: 190 });
        processing.api.setSize({ width: 260 });
      }}
      panels={[
        {
          id: "history",
          title: "History",
          content: (
            <ChatHistoryList
              history={history}
              onLoadSession={(sess) => loadSession(jobId, sess)}
              onNewChat={() => newChat(jobId)}
              onDeleteSession={removeSession}
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
              activeRunId={job?.activeRunId ?? null}
              onSelectRun={(id) => selectRun(jobId, id)}
            />
          ),
        },
        {
          id: "chat",
          title: "Chat",
          content: (
            <AgentChatPanel
              title="Chat"
              messages={messages}
              busy={busy}
              onSend={(text) => start(jobId, text)}
              onStop={() => stop(jobId)}
              roles={["user", "assistant", "error"]}
              placeholder="Describe a chart…"
              emptyHint="Describe a chart in plain English — e.g. “AAPL daily with a 50-day SMA and RSI”, “compare AAPL MSFT NVDA over 1 year”, or “BTC rolling 90-day Sharpe”."
              suggestions={SUGGESTIONS}
            />
          ),
        },
        {
          id: "chart",
          title: "Chart",
          content: (
            <div className="flex h-full min-h-0 flex-col">
              <div className="flex min-h-[30px] shrink-0 items-center justify-between gap-2 border-b border-term-border px-2 py-1">
                <span className="truncate text-[11px] text-term-text">{active?.title ?? "No chart yet"}</span>
                {canPin && (
                  <button
                    type="button"
                    onClick={() => useWorkspace.getState().openWidget("chart", active!.openParams as WidgetParams)}
                    className="focus-ring shrink-0 rounded border border-term-border px-2 py-0.5 text-[11px] uppercase tracking-wide text-term-muted hover:border-term-accent hover:text-term-accent"
                    title="Open this chart as a real Chart panel"
                  >
                    ⤢ Pin to layout
                  </button>
                )}
              </div>
              <div className="min-h-0 flex-1">
                {!active ? (
                  busy ? (
                    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-term-accent" aria-hidden />
                      <span className="text-xs text-term-muted">Building your chart…</span>
                    </div>
                  ) : (
                    <EmptyState icon="📈" title="No chart yet" hint="Describe a chart in the chat." />
                  )
                ) : active.engine === "price" && active.price ? (
                  <PriceChart payload={active.price} />
                ) : active.engine === "series" && active.series ? (
                  <SeriesView payload={active.series} />
                ) : active.engine === "heatmap" && active.heatmap ? (
                  <HeatmapView payload={active.heatmap} />
                ) : (
                  <div className={cx("grid h-full place-items-center text-xs text-term-muted")}>Empty chart.</div>
                )}
              </div>
            </div>
          ),
        },
      ]}
    />
  );
}
