/** Research Loop — autonomous design→generate→evaluate→reflect over the qhfi engine.
 *
 * Launches a goal, streams up to 5 iterations into a left rail (each with its scorecard
 * checks-passed badge + Sharpe), pins the best-so-far, and renders the selected iteration's
 * backtest dashboard (KPI cards + scorecard gate + equity curve) from the `result` frame's
 * shape_result payload — the same `BacktestResponse` the Backtest widget renders.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { openResearchLoopSocket } from "../api/client";
import type {
  BacktestResponse,
  LinePoint,
  ResearchFrame,
  ResearchRecord,
} from "../api/types";
import { MetricCard } from "../components/MetricCard";
import { chartColors, seriesColors } from "../lib/chartTheme";
import { cx } from "../lib/format";
import { SeriesChart as SeriesChartEngine } from "../lib/seriesChart";
import { usePalette } from "../state/settings";
import type { WidgetParams, WidgetProps } from "../workspace/widgetRegistry";
import { TextButton, WidgetShell } from "./shell";

const inputCls =
  "focus-ring w-full rounded border border-term-border bg-term-sunken px-2 py-1 font-mono text-xs focus:border-term-accent";

interface SeriesSpec {
  points: LinePoint[];
  color: string;
  title?: string;
  kind?: "line" | "area" | "histogram";
}

/** A single iteration's accumulated state in the rail. */
interface IterState {
  i: number;
  record?: ResearchRecord;
  experiment?: ResearchRecord["experiment"];
  payload?: BacktestResponse;
  phase?: string;
  reflect?: { text: string; next_change: string };
  error?: string;
}

const CHECK_LABEL: Record<string, string> = {
  sharpe: "Sharpe ≥ 1",
  calmar: "Calmar ≥ 0.5",
  drawdown: "Drawdown ≤ 25%",
  turnover: "Turnover ≤ 50×",
  oos_robustness: "OOS/IS ≥ 0.5",
};

function ChecksBadge({ passed, total }: { passed: number; total: number }) {
  const ok = total > 0 && passed === total;
  return (
    <span
      className={cx(
        "rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold",
        ok ? "bg-term-up/15 text-term-up" : passed > 0 ? "bg-term-accent/15 text-term-accent" : "bg-term-down/15 text-term-down",
      )}
    >
      {passed}/{total || 0} ✓
    </span>
  );
}

/** Generic multi-series chart on the canvas engine (mirrors BacktestWidget's wrapper). */
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

function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : `${v.toFixed(digits)}%`;
}
function num(v: number | null | undefined, digits = 2): string {
  return v == null ? "—" : v.toFixed(digits);
}

/** Focused dashboard for the selected iteration: gate, KPIs, equity vs benchmark. */
function IterationDashboard({ iter }: { iter: IterState }) {
  const sc = seriesColors();
  const p = iter.payload;
  const rec = iter.record;

  const equitySeries = useMemo<SeriesSpec[]>(() => {
    if (!p) return [];
    const out: SeriesSpec[] = [
      { points: p.equity_curve, color: sc.accent, title: "Strategy", kind: "line" },
    ];
    if (p.benchmark_curve?.length)
      out.push({ points: p.benchmark_curve, color: sc.volume, title: p.benchmark_label, kind: "line" });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p]);

  const ddSeries = useMemo<SeriesSpec[]>(
    () => (p?.drawdown_curve?.length ? [{ points: p.drawdown_curve, color: sc.down, title: "Drawdown", kind: "area" }] : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [p],
  );

  if (iter.error) {
    return (
      <div className="p-4 text-xs text-term-down">
        Iteration {iter.i + 1} failed: <span className="font-mono">{iter.error}</span>
      </div>
    );
  }
  if (!rec || !p) {
    return <div className="p-4 text-xs text-term-muted">Running iteration {iter.i + 1}… ({iter.phase ?? "design"})</div>;
  }

  const m = rec.metrics;
  const checks = rec.checks ?? {};
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div>
        <div className="mb-1 flex items-center gap-2">
          <span className="font-mono text-xs text-term-text">{rec.label}</span>
          <ChecksBadge passed={rec.n_checks_passed} total={Object.keys(checks).length} />
          {rec.passed && <span className="rounded bg-term-up/15 px-1.5 py-0.5 text-[10px] font-semibold text-term-up">PASSED GATE</span>}
        </div>
        {iter.experiment?.rationale && (
          <div className="text-[11px] text-term-muted">{iter.experiment.rationale}</div>
        )}
      </div>

      {/* Scorecard gate */}
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(checks).map(([k, ok]) => (
          <span
            key={k}
            className={cx(
              "rounded border px-1.5 py-0.5 font-mono text-[10px]",
              ok ? "border-term-up/40 text-term-up" : "border-term-down/40 text-term-down",
            )}
            title={k}
          >
            {ok ? "✓" : "✕"} {CHECK_LABEL[k] ?? k}
          </span>
        ))}
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        <MetricCard label="Sharpe" value={num(m.sharpe)} tone={m.sharpe != null && m.sharpe >= 1 ? "up" : "down"} emphasis />
        <MetricCard label="Calmar" value={num(m.calmar)} tone={m.calmar != null && m.calmar >= 0.5 ? "up" : "down"} />
        <MetricCard label="Max DD" value={pct(m.max_drawdown != null ? m.max_drawdown * 100 : null)} tone={m.max_drawdown != null && Math.abs(m.max_drawdown) <= 0.25 ? "up" : "down"} />
        <MetricCard label="Ann Turnover" value={m.ann_turnover != null ? `${m.ann_turnover.toFixed(1)}×` : "—"} tone={m.ann_turnover != null && m.ann_turnover <= 50 ? "up" : "down"} />
        <MetricCard label="CAGR" value={pct(m.cagr != null ? m.cagr * 100 : null)} />
        <MetricCard label="OOS/IS" value={num(rec.oos_sharpe_ratio)} tone={rec.oos_sharpe_ratio != null ? (rec.oos_sharpe_ratio >= 0.5 ? "up" : "down") : null} />
      </div>

      {/* Curves */}
      <div className="grid min-h-0 flex-1 grid-rows-2 gap-3">
        <div className="min-h-0"><SeriesChart series={equitySeries} title="Equity curve" /></div>
        <div className="min-h-0"><SeriesChart series={ddSeries} title="Drawdown" /></div>
      </div>
    </div>
  );
}

export default function ResearchLoopWidget(props: WidgetProps) {
  const [goal, setGoal] = useState<string>(
    (props.params.initialQuery as string) ?? "Find a robust long-short factor strategy that passes the promotion scorecard",
  );
  const [maxIters, setMaxIters] = useState(2);
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [iters, setIters] = useState<IterState[]>([]);
  const [best, setBest] = useState<ResearchRecord | null>(null);
  const [selected, setSelected] = useState<number>(0);
  const [log, setLog] = useState<string[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  // A precomputed result streamed in by the Agent Workflow's `research` node: show once as a summary
  // banner (no re-run), then clear the param. Independent of the live-run state above.
  const [incomingRes, setIncomingRes] = useState<WidgetParams["incomingResearch"] | null>(null);
  useEffect(() => {
    const r = props.params.incomingResearch;
    if (r) {
      setIncomingRes(r);
      props.api.updateParameters({ incomingResearch: undefined });
    }
  }, [props.params.incomingResearch, props.api]);

  useEffect(() => () => wsRef.current?.close(), []);

  const patch = (i: number, up: Partial<IterState>) =>
    setIters((cur) => {
      const next = [...cur];
      const at = next.findIndex((x) => x.i === i);
      if (at === -1) next.push({ i, ...up });
      else next[at] = { ...next[at], ...up };
      return next.sort((a, b) => a.i - b.i);
    });

  // Apply one research frame to local state — shared by the live WS run (below) and by frames
  // forwarded from an Agent Workflow `research` node, so the module mirrors the agent's loop live.
  const applyFrame = (f: ResearchFrame) => {
    switch (f.type) {
      case "started":
        setRunId(f.run_id);
        setLog((l) => [...l, `▸ ${f.goal}`]);
        setRunning(true);
        break;
      case "phase":
        patch(f.iteration, { phase: f.phase });
        break;
      case "design":
        patch(f.iteration, { experiment: f.experiment, phase: "generate" });
        break;
      case "result":
        patch(f.iteration, { payload: f.payload });
        break;
      case "iteration":
        patch(f.iteration, { record: f.record, error: f.record.error ?? undefined });
        setBest(f.best);
        setSelected(f.iteration);
        break;
      case "reflect":
        patch(f.iteration, { reflect: { text: f.text, next_change: f.next_change } });
        setLog((l) => [...l, `↻ iter ${f.iteration + 1}: ${f.text} → ${f.next_change}`]);
        break;
      case "error":
        if (f.iteration != null) patch(f.iteration, { error: f.detail });
        setLog((l) => [...l, `✕ ${f.detail}`]);
        break;
      case "done":
        setBest(f.best);
        if (f.best_iteration != null) setSelected(f.best_iteration);
        setLog((l) => [...l, `✓ done — best: iteration ${f.best_iteration != null ? f.best_iteration + 1 : "—"}`]);
        setRunning(false);
        break;
    }
  };

  // Live frames forwarded from an Agent Workflow `research` node (an append-only array in panel
  // params). Apply only newly-arrived frames; a shrunk array means a fresh agent run → replay clean.
  const appliedFramesRef = useRef(0);
  useEffect(() => {
    const frames = props.params.incomingResearchFrames as ResearchFrame[] | undefined;
    if (!frames || frames.length === 0) return;
    if (frames.length < appliedFramesRef.current) {
      appliedFramesRef.current = 0;
      setIters([]);
      setBest(null);
      setSelected(0);
      setLog([]);
    }
    for (let k = appliedFramesRef.current; k < frames.length; k++) applyFrame(frames[k]);
    appliedFramesRef.current = frames.length;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.params.incomingResearchFrames]);

  const run = () => {
    if (running) return;
    setIters([]);
    setBest(null);
    setSelected(0);
    setLog([]);
    setRunning(true);
    const ws = openResearchLoopSocket();
    wsRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ op: "run", goal, max_iters: maxIters }));
    ws.onmessage = (e) => {
      const f = JSON.parse(e.data) as ResearchFrame;
      applyFrame(f);
      if (f.type === "done") ws.close();
    };
    ws.onerror = () => {
      setRunning(false);
      setLog((l) => [...l, "✕ socket error"]);
    };
    ws.onclose = () => setRunning(false);
  };

  const stop = () => {
    wsRef.current?.close();
    setRunning(false);
  };

  const selectedIter = iters.find((x) => x.i === selected);

  return (
    <WidgetShell
      toolbar={
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className="shrink-0 text-xs font-medium text-term-text">Research Loop</span>
          {runId && <span className="shrink-0 font-mono text-[10px] text-term-muted">{runId}</span>}
        </div>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        {/* ── result streamed in by the Agent Workflow's research node (read-only) ──
            Only a fallback: when the node streamed live frames the rail below already shows the
            best + every iteration, so the compact banner is suppressed (iters populated). */}
        {incomingRes?.best && iters.length === 0 && (
          <div className="flex shrink-0 items-start gap-2 border-b border-term-border bg-term-accent/5 px-2 py-1.5">
            <div className="min-w-0 flex-1">
              <div className="mb-0.5 flex items-center gap-2">
                <span className="shrink-0 text-[9px] font-semibold uppercase tracking-wider text-term-accent">Workflow result</span>
                <span className="truncate font-mono text-[11px] text-term-text">{incomingRes.best.label ?? "—"}</span>
              </div>
              <div className="font-mono text-[10px] text-term-muted">
                {`Sharpe ${incomingRes.best.metrics?.sharpe ?? "—"} · Calmar ${incomingRes.best.metrics?.calmar ?? "—"} · checks ${incomingRes.best.n_checks_passed ?? "—"} · gate ${incomingRes.best.passed ? "passed" : "not passed"}`}
                {incomingRes.n_iterations != null ? ` · ${incomingRes.n_iterations} iters` : ""}
                {incomingRes.target_sharpe
                  ? ` · target ${incomingRes.target_sharpe}: ${incomingRes.target_met ? "met ✓" : `not met (best ${incomingRes.best_sharpe ?? "—"})`}`
                  : ""}
              </div>
            </div>
            <button onClick={() => setIncomingRes(null)} className="shrink-0 text-term-muted hover:text-term-text" title="Dismiss">×</button>
          </div>
        )}
        {/* controls */}
        <div className="flex items-center gap-2 border-b border-term-border px-2 py-1.5">
          <input
            className={inputCls}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Research goal…"
            disabled={running}
            onKeyDown={(e) => e.key === "Enter" && run()}
          />
          <label className="flex shrink-0 items-center gap-1 text-[11px] text-term-muted">
            iters
            <select
              className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
              value={maxIters}
              disabled={running}
              onChange={(e) => setMaxIters(Number(e.target.value))}
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          {running ? (
            <TextButton danger onClick={stop}>Stop</TextButton>
          ) : (
            <TextButton active onClick={run}>Run loop</TextButton>
          )}
        </div>

        {/* body: rail + dashboard */}
        <div className="flex min-h-0 flex-1">
          {/* rail */}
          <div className="flex w-52 shrink-0 flex-col overflow-auto border-r border-term-border">
            {best && (
              <button
                type="button"
                onClick={() => setSelected(best.i)}
                className={cx(
                  "border-b border-term-border px-2 py-1.5 text-left",
                  selected === best.i && "bg-term-elev",
                )}
              >
                <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-term-accent">★ Best so far</div>
                <div className="truncate font-mono text-[11px] text-term-text">{best.label}</div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <ChecksBadge passed={best.n_checks_passed} total={Object.keys(best.checks ?? {}).length} />
                  <span className="font-mono text-[10px] text-term-muted">Sh {num(best.metrics?.sharpe)}</span>
                </div>
              </button>
            )}
            {iters.map((it) => (
              <button
                key={it.i}
                type="button"
                onClick={() => setSelected(it.i)}
                className={cx(
                  "border-b border-term-border/60 px-2 py-1.5 text-left hover:bg-term-elev/60",
                  selected === it.i && "bg-term-elev",
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] uppercase tracking-wider text-term-muted">Iteration {it.i + 1}</span>
                  {it.record && !it.error && (
                    <ChecksBadge passed={it.record.n_checks_passed} total={Object.keys(it.record.checks ?? {}).length} />
                  )}
                  {it.error && <span className="text-[10px] text-term-down">error</span>}
                  {!it.record && !it.error && <span className="text-[10px] text-term-accent">{it.phase ?? "…"}</span>}
                </div>
                <div className="truncate font-mono text-[11px] text-term-text">
                  {it.record?.label ?? (it.experiment ? `${it.experiment.factors.join(" + ")} · ${it.experiment.mode}` : "designing…")}
                </div>
                {it.record && !it.error && (
                  <div className="mt-0.5 font-mono text-[10px] text-term-muted">
                    Sh {num(it.record.metrics?.sharpe)} · OOS {num(it.record.oos_sharpe_ratio)}
                  </div>
                )}
              </button>
            ))}
            {!iters.length && !running && (
              <div className="p-3 text-[11px] text-term-muted">Enter a goal and run the loop. It designs, backtests, grades against the promotion scorecard, reflects, and redoes up to {maxIters}×.</div>
            )}
          </div>

          {/* dashboard + reflection log */}
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-auto">
              {selectedIter ? (
                <IterationDashboard iter={selectedIter} />
              ) : (
                <div className="p-4 text-xs text-term-muted">No iteration selected yet.</div>
              )}
            </div>
            {log.length > 0 && (
              <div className="max-h-24 shrink-0 overflow-auto border-t border-term-border bg-term-sunken px-2 py-1">
                {log.map((line, i) => (
                  <div key={i} className="font-mono text-[10px] text-term-muted">{line}</div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </WidgetShell>
  );
}
