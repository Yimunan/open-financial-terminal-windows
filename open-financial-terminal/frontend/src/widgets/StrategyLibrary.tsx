import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BacktestResponse, EngineStrategy, LabResult, RegStrategy } from "../api/types";
import { cx, fmtPct } from "../lib/format";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { EmptyState } from "../components/States";
import RegistrySummary from "../components/RegistrySummary";
import { useWorkspace } from "../state/workspace";
import { WidgetShell, useWidgetSymbol } from "./shell";

const inputCls =
  "focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs focus:border-term-accent";
const selCls = "focus-ring w-full rounded border border-term-border bg-term-sunken px-1 py-0.5 text-xs text-term-muted";

export default function StrategyLibrary(props: WidgetProps) {
  const { symbol, channel, setChannel } = useWidgetSymbol(props);
  const qc = useQueryClient();
  const openWidget = useWorkspace((s) => s.openWidget);
  const { data } = useQuery({ queryKey: ["reg-strategies"], queryFn: api.registryStrategies });
  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.universes });
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<RegStrategy | null>(null);
  const [customSel, setCustomSel] = useState<RegStrategy | null>(null); // selected custom (read-only; authored in Sandbox)
  const [test, setTest] = useState<LabResult["stats"] | null>(null);

  // qhfi-engine strategy (portfolio backtest over a universe)
  const [engSel, setEngSel] = useState<EngineStrategy | null>(null);
  const [engUniverse, setEngUniverse] = useState("dow30");
  const [engYears, setEngYears] = useState(3);
  const [engMode, setEngMode] = useState("long_short");
  const [engResult, setEngResult] = useState<BacktestResponse | null>(null);

  const builtin = (data?.builtin ?? []).filter((s) => match(s, q));
  const custom = (data?.custom ?? []).filter((s) => match(s, q));
  const engine = (data?.engine ?? []).filter((s) => matchEngine(s, q));

  const del = useMutation({
    mutationFn: (name: string) => api.deleteStrategy(name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reg-strategies"] }); setCustomSel(null); setSel(null); },
  });
  const run = useMutation({
    mutationFn: (name: string) =>
      api.labRun({ symbol, asset: "equity", strategy: name, timeframe: "1d", years: 3, direction: "long_only", params: {} }),
    onSuccess: (r) => setTest(r.stats),
  });
  const runEngine = useMutation({
    mutationFn: (name: string) =>
      api.runEngineStrategy({ strategy_key: name, universe_name: engUniverse, years: engYears, mode: engMode, params: {} }),
    onSuccess: (r) => setEngResult(r),
  });

  const openCustom = (s: RegStrategy) => {
    setSel(null);
    setEngSel(null);
    setTest(null);
    setCustomSel(s);
  };
  const openEngine = (s: EngineStrategy) => {
    setSel(null);
    setCustomSel(null);
    setTest(null);
    setEngResult(null);
    runEngine.reset();
    setEngSel(s);
  };
  const openInSandbox = (s: RegStrategy) =>
    openWidget("sandbox", {
      initialMode: "strategy", initialTrust: "sandboxed",
      initialName: s.name, initialCode: s.code ?? "",
      initialMeta: { label: s.label, description: s.description ?? "" },
    });

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">Strategy library</span>
          <button
            onClick={() => { setSel(null); setEngSel(null); setCustomSel(null); setTest(null); openWidget("sandbox", { initialMode: "strategy", initialTrust: "sandboxed" }); }}
            className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10"
          >
            ＋ New
          </button>
        </>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        <RegistrySummary kind="strategies" />
        <div className="flex min-h-0 flex-1">
        <div className="flex w-60 shrink-0 flex-col border-r border-term-border">
          <div className="p-1.5">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search strategies…" aria-label="Search strategies" className={inputCls} />
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-2">
            <Sec label="Built-in (single-symbol lab)">
              {builtin.map((s) => (
                <RowB key={s.name} active={sel?.name === s.name} onClick={() => { setCustomSel(null); setEngSel(null); setTest(null); setSel(s); }}>
                  <span>{s.label}</span>
                  <span className="text-[9px] text-term-muted">{s.params.length}p</span>
                </RowB>
              ))}
            </Sec>
            <Sec label={`qhfi engine — portfolio (${engine.length})`}>
              {engine.length === 0 && <EmptyState title="Registry empty." />}
              {engine.map((s) => (
                <RowB key={s.name} active={engSel?.name === s.name} onClick={() => openEngine(s)}>
                  <span>{s.name}</span>
                  <span className={cx("text-[9px]", s.source === "linked" ? "text-term-accent" : "text-term-muted")}>{s.source}</span>
                </RowB>
              ))}
            </Sec>
            <Sec label={`Custom (${custom.length})`}>
              {custom.length === 0 && <EmptyState title="None yet." />}
              {custom.map((s) => (
                <RowB key={s.name} active={customSel?.name === s.name} onClick={() => openCustom(s)}>
                  <span>{s.name}</span>
                  <span className="text-[9px] text-term-accent">view</span>
                </RowB>
              ))}
            </Sec>
          </div>
        </div>

        <div className="min-w-0 flex-1 overflow-auto p-3">
          {engSel ? (
            <div className="space-y-2 font-mono text-xs">
              <div className="text-sm font-semibold">
                {engSel.name} <span className="text-term-muted">({engSel.source})</span>
              </div>
              {engSel.doc && <div className="leading-snug text-term-muted">{engSel.doc}</div>}
              <div className="text-[9px] uppercase tracking-wider text-term-muted">Params (defaults)</div>
              {engSel.params.length === 0 ? (
                <div className="text-term-muted">none</div>
              ) : (
                engSel.params.map((p) => (
                  <div key={p.key} className="flex justify-between border-b border-term-border/30 py-0.5">
                    <span className="text-term-muted">{p.key}</span>
                    <span className="text-term-text">{String(p.default)} <span className="text-term-muted">· {p.type}</span></span>
                  </div>
                ))
              )}

              <div className="mt-2 grid grid-cols-3 gap-2 rounded border border-term-border bg-term-bg/40 p-2">
                <Fld label="Universe">
                  <select value={engUniverse} onChange={(e) => setEngUniverse(e.target.value)} className={selCls} aria-label="Universe">
                    {(universes?.universes ?? [engUniverse]).map((u) => <option key={u}>{u}</option>)}
                  </select>
                </Fld>
                <Fld label="Mode">
                  <select value={engMode} onChange={(e) => setEngMode(e.target.value)} className={selCls} aria-label="Mode">
                    {["long_short", "long_only"].map((m) => <option key={m}>{m}</option>)}
                  </select>
                </Fld>
                <Fld label="Years">
                  <input
                    type="number" min={1} max={10} value={engYears}
                    onChange={(e) => setEngYears(Math.max(1, Math.min(10, Number(e.target.value) || 3)))}
                    className={inputCls}
                    aria-label="Years"
                  />
                </Fld>
              </div>

              <button
                onClick={() => runEngine.mutate(engSel.name)}
                disabled={runEngine.isPending}
                className="mt-1 rounded border border-term-accent px-3 py-1 text-xs uppercase text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
              >
                {runEngine.isPending ? "Running…" : `Run on ${engUniverse} ▸`}
              </button>
              {runEngine.error && <div className="text-[10px] text-term-down">{(runEngine.error as Error).message}</div>}
              {engResult && <EngineResult r={engResult} />}
            </div>
          ) : customSel ? (
            <div className="space-y-2 font-mono text-xs">
              <div className="text-sm font-semibold">{customSel.label || customSel.name} <span className="text-term-muted">({customSel.name})</span></div>
              {customSel.description && <div className="leading-snug text-term-muted">{customSel.description}</div>}
              <pre className="mt-1 overflow-auto rounded border border-term-border bg-term-bg/40 p-2 text-[11px] leading-snug">{customSel.code ?? ""}</pre>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <button
                  onClick={() => openInSandbox(customSel)}
                  className="rounded border border-term-accent px-3 py-1 text-xs uppercase text-term-accent hover:bg-term-accent/10"
                  title="Run, edit, or backtest this strategy in the Sandbox"
                >
                  Open in Sandbox
                </button>
                <button onClick={() => run.mutate(customSel.name)} disabled={run.isPending} className="rounded border border-term-border px-3 py-1 text-xs uppercase text-term-muted hover:text-term-text disabled:opacity-50" title="Backtest on the linked symbol (single-symbol lab)">
                  {run.isPending ? "Testing…" : `Test on ${symbol} ▸`}
                </button>
                <button onClick={() => del.mutate(customSel.name)} disabled={del.isPending} className="rounded border border-term-down px-3 py-1 text-xs uppercase text-term-down hover:bg-term-down/10 disabled:opacity-50">Delete</button>
                {(run.error || del.error) && <span className="text-[10px] text-term-down">{((run.error || del.error) as Error).message}</span>}
              </div>
              {test && <TestResult s={test} />}
            </div>
          ) : sel ? (
            <div className="space-y-2 font-mono text-xs">
              <div className="text-sm font-semibold">{sel.label} <span className="text-term-muted">({sel.name})</span></div>
              <div className="text-[9px] uppercase tracking-wider text-term-muted">Params</div>
              {sel.params.length === 0 ? (
                <div className="text-term-muted">none</div>
              ) : (
                sel.params.map((p) => (
                  <div key={p.key} className="flex justify-between border-b border-term-border/30 py-0.5">
                    <span className="text-term-muted">{p.label}</span>
                    <span className="text-term-text">{p.default} · [{p.min}–{p.max}]</span>
                  </div>
                ))
              )}
              <button
                onClick={() => run.mutate(sel.name)}
                disabled={run.isPending}
                className="mt-2 rounded border border-term-accent px-3 py-1 text-xs uppercase text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
              >
                {run.isPending ? "Testing…" : `Test on ${symbol} ▸`}
              </button>
              {test && <TestResult s={test} />}
            </div>
          ) : (
            <div className="text-xs text-term-muted">
              Browse built-in templates and qhfi-engine strategies, or author your own (＋ New opens the
              Sandbox). Click a built-in/custom strategy to test it on the linked symbol, or an engine
              strategy to backtest over a universe. Authoring lives in the Sandbox.
            </div>
          )}
        </div>
        </div>
      </div>
    </WidgetShell>
  );
}

function TestResult({ s }: { s: LabResult["stats"] }) {
  return (
    <div className="mt-2 grid grid-cols-3 gap-1.5 rounded border border-term-border bg-term-bg/40 p-2 font-mono text-[11px]">
      <Cell k="Net %" v={fmtPct(s.net_pnl_pct)} up={s.net_pnl_pct >= 0} />
      <Cell k="Sharpe" v={s.sharpe.toFixed(2)} />
      <Cell k="Trades" v={String(s.total_trades)} />
      <Cell k="Win %" v={`${s.win_rate}%`} />
      <Cell k="Max DD" v={fmtPct(s.max_drawdown, false)} />
      <Cell k="PF" v={s.profit_factor != null ? s.profit_factor.toFixed(2) : "—"} />
    </div>
  );
}

function Cell({ k, v, up }: { k: string; v: string; up?: boolean }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wider text-term-muted">{k}</div>
      <div className={cx(up == null ? "text-term-text" : up ? "text-term-up" : "text-term-down")}>{v}</div>
    </div>
  );
}

function match(s: RegStrategy, q: string): boolean {
  if (!q) return true;
  const t = q.toLowerCase();
  return [s.name, s.label, s.description].some((x) => (x ?? "").toLowerCase().includes(t));
}

function matchEngine(s: EngineStrategy, q: string): boolean {
  if (!q) return true;
  const t = q.toLowerCase();
  return [s.name, s.label, s.doc].some((x) => (x ?? "").toLowerCase().includes(t));
}

function EngineResult({ r }: { r: BacktestResponse }) {
  return (
    <div className="mt-2 space-y-1.5">
      <div className="text-[10px] text-term-muted">
        {r.universe} · {r.n_instruments} names · {r.window_start} → {r.window_end}
      </div>
      <div className="grid grid-cols-3 gap-1.5 rounded border border-term-border bg-term-bg/40 p-2 font-mono text-[11px]">
        <Cell k="PnL %" v={fmtPct(r.pnl_pct)} up={r.pnl_pct >= 0} />
        <Cell k="CAGR" v={fmtPct(r.metrics.cagr)} up={r.metrics.cagr >= 0} />
        <Cell k="Sharpe" v={r.metrics.sharpe.toFixed(2)} />
        <Cell k="Sortino" v={r.metrics.sortino.toFixed(2)} />
        <Cell k="Max DD" v={fmtPct(r.metrics.max_drawdown, false)} />
        <Cell k="Calmar" v={r.metrics.calmar.toFixed(2)} />
      </div>
    </div>
  );
}

function Sec({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="px-1 py-1 text-[9px] uppercase tracking-wider text-term-muted">{label}</div>
      {children}
    </div>
  );
}

function RowB({ active, onClick, children }: { active?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={cx("flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-[11px] hover:bg-term-border/40", active && "bg-term-border/50")}>
      {children}
    </button>
  );
}

function Fld({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-[9px] uppercase tracking-wider text-term-muted">{label}</span>
      {children}
    </label>
  );
}
