import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  AllocateResult, BacktestResponse, LabResult, PortfolioExposure,
  SandboxMode, SandboxRunResult, SandboxTrust,
} from "../api/types";
import CodeEditor from "../components/CodeEditor";
import { cx, fmtPct } from "../lib/format";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState } from "../components/States";

const inputCls =
  "focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs focus:border-term-accent";
const selCls = "focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-xs text-term-muted";

const MODES: SandboxMode[] = ["factor", "strategy", "portfolio"];
const TRUSTS: SandboxTrust[] = ["sandboxed", "trusted"];

export default function SandboxWidget(props: WidgetProps) {
  const { symbol, channel, setChannel } = useWidgetSymbol(props);
  const qc = useQueryClient();
  const { data: tpl } = useQuery({ queryKey: ["sandbox-templates"], queryFn: api.sandboxTemplates });
  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.universes });

  // Initial state can be handed in by another widget (e.g. "Open in Sandbox" from the libraries).
  const p = props.params;
  const initialMeta = p.initialMeta as Record<string, unknown> | undefined;

  const [mode, setMode] = useState<SandboxMode>(() => (p.initialMode as SandboxMode) ?? "factor");
  const [trust, setTrust] = useState<SandboxTrust>(() => (p.initialTrust as SandboxTrust) ?? "sandboxed");
  const [code, setCode] = useState(() => (p.initialCode as string) ?? "");
  const [name, setName] = useState(() => (p.initialName as string) ?? "");
  const [ctx, setCtx] = useState(() => ({
    universe: "dow30", years: 3, capital: 1_000_000,
    pmode: (initialMeta?.mode as string) ?? "long_short",
  }));
  // Factor/strategy metadata (mirrors what the Factor/Strategy library editors captured).
  const [fmeta, setFmeta] = useState(() => ({
    kind: (initialMeta?.kind as string) ?? "alpha",
    direction: (initialMeta?.direction as string) ?? "high=long",
    description: (initialMeta?.description as string) ?? "",
    label: (initialMeta?.label as string) ?? "",
  }));
  const [result, setResult] = useState<SandboxRunResult | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  // Prefilled code counts as already user-edited, so the starter effect won't clobber it.
  const dirty = useRef<boolean>(Boolean((p.initialCode as string)?.trim()));

  // All known starter strings, to detect a pristine (unedited) buffer.
  const starterSet = useMemo(() => {
    const s = new Set<string>();
    const st = tpl?.starters;
    if (st) for (const m of MODES) for (const t of TRUSTS) s.add((st[m]?.[t] ?? "").trim());
    return s;
  }, [tpl]);

  const loadStarter = (m: SandboxMode, t: SandboxTrust) => {
    const next = tpl?.starters?.[m]?.[t] ?? "";
    setCode(next);
    dirty.current = false;
  };

  // First template load + swap when toggling mode/trust if the buffer is still a pristine starter.
  useEffect(() => {
    if (!tpl) return;
    if (!dirty.current || code.trim() === "" || starterSet.has(code.trim())) loadStarter(mode, trust);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tpl, mode, trust]);

  const onCode = (v: string) => { setCode(v); dirty.current = !starterSet.has(v.trim()); };

  const runCtx = () => {
    if (mode === "portfolio") return { mode: ctx.pmode, capital: ctx.capital };
    if (mode === "strategy" && trust === "sandboxed") return { symbol, years: ctx.years };
    return { universe: ctx.universe, years: ctx.years };
  };

  const run = useMutation({
    mutationFn: () => api.sandboxRun({ mode, trust, code, context: runCtx() }),
    onSuccess: (r) => { setResult(r); setSaveMsg(null); },
  });

  const save = useMutation({
    mutationFn: () =>
      api.sandboxSave({
        mode, trust, name: name.trim(), code,
        meta:
          mode === "portfolio" ? { mode: ctx.pmode }
          : mode === "factor" ? { kind: fmeta.kind, direction: fmeta.direction, description: fmeta.description }
          : { label: fmeta.label || name.trim(), description: fmeta.description, params: [] },
        allocations: mode === "portfolio" ? result?.allocations ?? null : null,
      }),
    onSuccess: (r) => {
      setSaveMsg(`✓ saved → ${r.saved}${r.path ? ` (${r.path})` : ""}`);
      qc.invalidateQueries({ queryKey: ["reg-factors"] });
      qc.invalidateQueries({ queryKey: ["reg-strategies"] });
      qc.invalidateQueries({ queryKey: ["portfolios"] });
    },
    onError: (e) => setSaveMsg(`✕ ${(e as Error).message}`),
  });

  const canSave = name.trim() !== "" && (mode !== "portfolio" || !!result?.allocations?.length);

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">Sandbox</span>
          <Toggle options={MODES} value={mode} onChange={setMode} />
          <Toggle options={TRUSTS} value={trust} onChange={setTrust} />
        </>
      }
    >
      <div className="flex h-full min-h-0">
        {/* editor + controls */}
        <div className="flex w-1/2 min-w-0 flex-col border-r border-term-border p-2">
          <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[10px]">
            {mode === "strategy" && trust === "sandboxed" ? (
              <span className="text-term-muted">Symbol: <span className="text-term-accent">{symbol}</span> (linked)</span>
            ) : mode === "portfolio" ? (
              <>
                <select value={ctx.pmode} onChange={(e) => setCtx({ ...ctx, pmode: e.target.value })} aria-label="Portfolio mode" className={selCls}>
                  {["long_short", "long_only"].map((m) => <option key={m}>{m}</option>)}
                </select>
                <label className="flex items-center gap-1 text-term-muted">Capital
                  <input type="number" value={ctx.capital} onChange={(e) => setCtx({ ...ctx, capital: +e.target.value || 0 })} className={cx(inputCls, "w-28")} />
                </label>
              </>
            ) : (
              <select value={ctx.universe} onChange={(e) => setCtx({ ...ctx, universe: e.target.value })} aria-label="Universe" className={selCls}>
                {(universes?.universes ?? ["dow30"]).map((u) => <option key={u}>{u}</option>)}
              </select>
            )}
            {mode !== "portfolio" && (
              <label className="flex items-center gap-1 text-term-muted">Years
                <input type="number" min={1} max={10} value={ctx.years} onChange={(e) => setCtx({ ...ctx, years: Math.max(1, Math.min(10, +e.target.value || 3)) })} className={cx(inputCls, "w-12")} />
              </label>
            )}
            <button onClick={() => loadStarter(mode, trust)} className="ml-auto text-[10px] text-term-muted hover:text-term-accent" title="Reset to the starter template">↺ Template</button>
          </div>

          {/* factor/strategy metadata captured on save (so the Sandbox fully replaces the library editors) */}
          {mode !== "portfolio" && (
            <div className="mb-1.5 grid grid-cols-2 gap-1.5 text-[10px]">
              {mode === "factor" ? (
                <>
                  <select value={fmeta.kind} onChange={(e) => setFmeta({ ...fmeta, kind: e.target.value })} aria-label="Kind" className={selCls}>
                    {["alpha", "price", "value", "quality"].map((k) => <option key={k}>{k}</option>)}
                  </select>
                  <select value={fmeta.direction} onChange={(e) => setFmeta({ ...fmeta, direction: e.target.value })} aria-label="Direction" className={selCls}>
                    {["high=long", "low=long", "cheap=long", "formula"].map((d) => <option key={d}>{d}</option>)}
                  </select>
                  <input value={fmeta.description} onChange={(e) => setFmeta({ ...fmeta, description: e.target.value })} placeholder="description" aria-label="Description" className={cx(inputCls, "col-span-2")} />
                </>
              ) : (
                <>
                  <input value={fmeta.label} onChange={(e) => setFmeta({ ...fmeta, label: e.target.value })} placeholder="label" aria-label="Label" className={inputCls} />
                  <input value={fmeta.description} onChange={(e) => setFmeta({ ...fmeta, description: e.target.value })} placeholder="description" aria-label="Description" className={inputCls} />
                </>
              )}
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-auto">
            <CodeEditor value={code} onChange={onCode} rows={18} />
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <button
              onClick={() => run.mutate()}
              disabled={run.isPending}
              className="rounded border border-term-accent px-3 py-1 text-xs uppercase text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
            >
              {run.isPending ? "Running…" : "▸ Run"}
            </button>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="name to save" aria-label="name to save" className={cx(inputCls, "w-32")} />
            <button
              onClick={() => save.mutate()}
              disabled={save.isPending || !canSave}
              className="rounded border border-term-border px-3 py-1 text-xs uppercase text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-40"
              title={trust === "trusted" && mode !== "portfolio" ? "Writes a .py into the linked dir (drop-in)" : "Saves into the registry"}
            >
              Save
            </button>
          </div>
          {run.error && <div className="mt-1 text-[10px] text-term-down">{(run.error as Error).message}</div>}
          {saveMsg && <div className={cx("mt-1 truncate text-[10px]", saveMsg.startsWith("✓") ? "text-term-up" : "text-term-down")} title={saveMsg}>{saveMsg}</div>}
        </div>

        {/* results */}
        <div className="min-w-0 flex-1 overflow-auto p-2">
          {result ? <Result r={result} /> : (
            <EmptyState
              title={`Author ${mode} code (${trust}) and ▸ Run.`}
              hint={
                trust === "sandboxed"
                  ? "Sandboxed: AST-restricted Python (no imports/IO)."
                  : "Trusted: full Python — define a qhfi Factor/Strategy class."
              }
            />
          )}
        </div>
      </div>
    </WidgetShell>
  );
}

function Result({ r }: { r: SandboxRunResult }) {
  if (r.kind === "factor") {
    return (
      <div>
        <div className="mb-1 text-[10px] text-term-muted">
          {r.name ? `${r.name} · ` : ""}{r.universe} · {r.n_scored ?? r.ranking?.length} scored
          {r.errors ? ` · ${r.errors} errs` : ""}{r.truncated ? " · (truncated)" : ""}
        </div>
        <RankTable rows={r.ranking ?? []} />
      </div>
    );
  }
  if (r.kind === "strategy") {
    const lab = r.engine === "lab";
    const p = r.preview as LabResult & BacktestResponse;
    const cells = lab
      ? [["Net %", fmtPct(p.stats.net_pnl_pct)], ["Sharpe", p.stats.sharpe.toFixed(2)], ["Win %", `${p.stats.win_rate}%`], ["Trades", String(p.stats.total_trades)], ["Max DD", fmtPct(p.stats.max_drawdown, false)]]
      : [["PnL %", fmtPct(p.pnl_pct)], ["CAGR", fmtPct(p.metrics.cagr)], ["Sharpe", p.metrics.sharpe.toFixed(2)], ["Sortino", p.metrics.sortino.toFixed(2)], ["Max DD", fmtPct(p.metrics.max_drawdown, false)]];
    return (
      <div>
        <div className="mb-1 text-[10px] text-term-muted">{lab ? "single-symbol lab" : "portfolio engine"} backtest</div>
        <div className="grid grid-cols-3 gap-1.5 font-mono text-[11px]">
          {cells.map(([k, v]) => (
            <div key={k} className="rounded border border-term-border bg-term-bg/40 p-1.5">
              <div className="text-[9px] uppercase tracking-wider text-term-muted">{k}</div>
              <div className="text-term-text">{v}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }
  // portfolio
  return (
    <div>
      <Exposures e={r.exposures} />
      {r.allocation && <AllocTable a={r.allocation} />}
    </div>
  );
}

function RankTable({ rows }: { rows: { symbol: string; score: number }[] }) {
  const show = rows.slice(0, 20);
  return (
    <table className="w-full font-mono text-[11px]">
      <thead className="text-[9px] uppercase tracking-wider text-term-muted">
        <tr className="border-b border-term-border"><th className="px-2 py-1 text-left">#</th><th className="px-2 py-1 text-left">Symbol</th><th className="px-2 py-1 text-right">Score</th></tr>
      </thead>
      <tbody>
        {show.map((x, i) => (
          <tr key={x.symbol} className="border-b border-term-border/30">
            <td className="px-2 py-0.5 text-term-muted">{i + 1}</td>
            <td className="px-2 py-0.5">{x.symbol}</td>
            <td className={cx("px-2 py-0.5 text-right", x.score >= 0 ? "text-term-up" : "text-term-down")}>{x.score.toFixed(4)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Exposures({ e }: { e?: PortfolioExposure }) {
  if (!e) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-3 rounded border border-term-border bg-term-bg/40 px-2 py-1 font-mono text-[11px]">
      <span className="text-term-muted">Gross <span className="text-term-text">{(e.gross * 100).toFixed(1)}%</span></span>
      <span className="text-term-muted">Net <span className="text-term-text">{(e.net * 100).toFixed(1)}%</span></span>
      <span className="text-term-muted">L/S <span className="text-term-text">{e.n_long}/{e.n_short}</span></span>
    </div>
  );
}

function AllocTable({ a }: { a: AllocateResult }) {
  return (
    <table className="w-full font-mono text-[11px]">
      <thead className="text-[9px] uppercase tracking-wider text-term-muted">
        <tr className="border-b border-term-border">
          <th className="px-2 py-1 text-left">Symbol</th><th className="px-2 py-1 text-right">Weight</th>
          <th className="px-2 py-1 text-right">Notional</th><th className="px-2 py-1 text-right">Shares</th>
        </tr>
      </thead>
      <tbody>
        {a.rows.map((row) => (
          <tr key={row.symbol} className="border-b border-term-border/30">
            <td className="px-2 py-0.5">
              <span className={cx(row.side === "long" ? "text-term-up" : row.side === "short" ? "text-term-down" : "text-term-muted")}>{row.side === "short" ? "▽" : row.side === "long" ? "△" : "·"}</span> {row.symbol}
            </td>
            <td className="px-2 py-0.5 text-right">{fmtPct(row.weight * 100)}</td>
            <td className="px-2 py-0.5 text-right">{row.notional.toLocaleString()}</td>
            <td className="px-2 py-0.5 text-right">{row.shares_int == null ? "—" : row.shares_int}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Toggle<T extends string>({ options, value, onChange }: { options: T[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="flex items-center gap-0.5 rounded border border-term-border p-0.5">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={cx("rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide", value === o ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:text-term-text")}
        >
          {o}
        </button>
      ))}
    </div>
  );
}
