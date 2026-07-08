import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { MMCompareResult, MMRequest } from "../api/types";
import SeriesChart, { type SeriesSpec } from "../components/SeriesChart";
import { EmptyState } from "../components/States";
import BtModeToggle, { type BtMode } from "../components/BtModeToggle";
import { chartColors } from "../lib/chartTheme";
import { cx, fmtPct } from "../lib/format";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { WidgetShell, useWidgetSymbol } from "./shell";

type Form = Pick<MMRequest, "symbol" | "timeframe" | "spread_bps" | "half_spread_bps" | "skew_bps" | "q_max">;

const DEFAULTS: Form = {
  symbol: "BTC/USDT", timeframe: "1m", spread_bps: 5, half_spread_bps: 5, skew_bps: 10, q_max: 20,
};
const TIMEFRAMES = ["1m", "5m", "15m", "1h"];
// one color per strategy (in backend display order), benchmark stays muted-gray.
const PALETTE = ["#8b949e", "#58a6ff", "#d29922", "#3fb950", "#f85149"];

function Field({ label, value, step, onChange }: {
  label: string; value: number; step: number; onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-wider text-term-muted">{label}</span>
      <input
        type="number" value={value} step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        className="focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs text-term-text"
      />
    </label>
  );
}

export default function MarketMakingWidget(props: WidgetProps) {
  return <MarketMakingPanel props={props} />;
}

/** The Market-Making comparison view. Rendered standalone (the `market_making` widget) and as the
 * "MM" mode of the Backtest widget — `onMode`, when passed, shows the Factor/Lab/MM toggle. */
export function MarketMakingPanel({ props, onMode }: { props: WidgetProps; onMode?: (m: BtMode) => void }) {
  const { channel, setChannel, symbol: linkedSymbol } = useWidgetSymbol(props);
  const [form, setForm] = useState<Form>(() => ({
    ...DEFAULTS, symbol: linkedSymbol.includes("/") ? linkedSymbol : DEFAULTS.symbol,
  }));
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));

  const run = useMutation({ mutationFn: (body: Form) => api.mmCompare(body) });
  const result: MMCompareResult | undefined = run.data;
  const cols = chartColors();

  // best (highest net P&L) strategy — highlighted in the table.
  const bestKey = useMemo(() => {
    if (!result) return null;
    return result.strategies.reduce((b, s) => (s.stats.net_pnl_pct > b.stats.net_pnl_pct ? s : b)).key;
  }, [result]);

  const equitySeries: SeriesSpec[] = useMemo(() => {
    if (!result) return [];
    const strat = result.strategies.map((s, i) => ({
      points: s.equity_curve, color: PALETTE[i % PALETTE.length], title: s.name,
    }));
    return [...strat, { points: result.benchmark_curve, color: cols.muted, title: "Buy & hold", kind: "line" as const }];
  }, [result, cols.muted]);

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge="eod"
      toolbar={
        <>
          {onMode ? (
            <BtModeToggle mode="mm" onMode={onMode} />
          ) : (
            <span className="text-[11px] font-bold uppercase tracking-wide text-term-accent">Market Making</span>
          )}
          <span className="font-mono text-sm font-bold">{form.symbol}</span>
          <span className="truncate font-mono text-[10px] text-term-muted">
            {result ? `OBI α ${result.alpha_bps.toFixed(1)}bps · R²${result.alpha_r2.toFixed(2)} · ${result.meta.snapshots} bars` : "5 quoting strategies · synthetic depth"}
          </span>
          <button
            onClick={() => run.mutate(form)}
            disabled={run.isPending}
            className="ml-auto rounded border border-term-accent px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-term-accent hover:bg-term-accent/15 disabled:opacity-50"
          >
            {run.isPending ? "Running…" : "Compare ▸"}
          </button>
        </>
      }
    >
      <div className="flex h-full min-h-0">
        {/* ── Parameter form ── */}
        <div className="w-40 shrink-0 space-y-2 overflow-auto border-r border-term-border p-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-[9px] uppercase tracking-wider text-term-muted">Pair</span>
            <input
              value={form.symbol}
              onChange={(e) => set("symbol", e.target.value.toUpperCase())}
              className="focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs text-term-text"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[9px] uppercase tracking-wider text-term-muted">Timeframe</span>
            <select
              value={form.timeframe}
              onChange={(e) => set("timeframe", e.target.value)}
              className="focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs text-term-text"
            >
              {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </label>
          <Field label="Book spread bps" value={form.spread_bps} step={0.5} onChange={(v) => set("spread_bps", v)} />
          <Field label="Quote half-spread" value={form.half_spread_bps} step={0.5} onChange={(v) => set("half_spread_bps", v)} />
          <Field label="Inventory skew bps" value={form.skew_bps} step={1} onChange={(v) => set("skew_bps", v)} />
          <Field label="Inventory q_max" value={form.q_max} step={1} onChange={(v) => set("q_max", v)} />
          {run.error && <div className="text-[11px] text-term-down">{(run.error as Error).message}</div>}
        </div>

        {/* ── Center: overlaid equity curves ── */}
        <div className="flex min-w-0 flex-1 flex-col">
          {result ? (
            <SeriesChart series={equitySeries} title="Equity by strategy vs buy & hold" />
          ) : (
            <EmptyState title={run.isPending ? "Running 5 quoting strategies…" : "Compare the market-making strategies over real bars + synthetic depth."} />
          )}
        </div>

        {/* ── Comparison table ── */}
        <div className="w-[20rem] shrink-0 space-y-2 overflow-auto border-l border-term-border p-2">
          {result ? (
            <>
              <table className="w-full border-collapse font-mono text-[11px]">
                <thead>
                  <tr className="border-b border-term-border text-[9px] uppercase tracking-wider text-term-muted">
                    <th className="py-1 text-left">Strategy</th>
                    <th className="py-1 text-right">P&amp;L%</th>
                    <th className="py-1 text-right">Spr</th>
                    <th className="py-1 text-right">Adv</th>
                    <th className="py-1 text-right">Inv</th>
                  </tr>
                </thead>
                <tbody>
                  {result.strategies.map((s, i) => (
                    <tr key={s.key} className={cx("border-b border-term-border/30", s.key === bestKey && "bg-term-up/10")}>
                      <td className="py-0.5">
                        <span className="mr-1 inline-block h-2 w-2 rounded-sm align-middle" style={{ backgroundColor: PALETTE[i % PALETTE.length] }} />
                        <span className="text-term-text">{s.name.replace("MM", "").trim()}</span>
                      </td>
                      <td className={cx("py-0.5 text-right", s.stats.net_pnl_pct >= 0 ? "text-term-up" : "text-term-down")}>{fmtPct(s.stats.net_pnl_pct)}</td>
                      <td className="py-0.5 text-right text-term-up">{s.stats.spread_captured_bps.toFixed(1)}</td>
                      <td className={cx("py-0.5 text-right", s.stats.adv_sel_bps >= 0 ? "text-term-up" : "text-term-down")}>{s.stats.adv_sel_bps.toFixed(1)}</td>
                      <td className="py-0.5 text-right text-term-muted">{s.stats.inv_max_abs.toFixed(0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-[9px] leading-snug text-term-muted">
                <span className="text-term-up">Spr</span> = spread captured (bps), <span className="text-term-down">Adv</span> = markout / adverse selection @ +1 bar (bps, negative = adverse), Inv = max |inventory|. Best P&amp;L highlighted.
              </div>
              <div className="rounded border border-term-border/60 bg-term-bg/30 p-1.5 text-[10px] leading-snug text-term-muted">
                Calibrated OBI alpha <span className="text-term-text">{result.alpha_bps.toFixed(2)} bps/OBI (R²={result.alpha_r2.toFixed(2)})</span>. {result.meta.note}
              </div>
            </>
          ) : (
            <EmptyState title="Compare to see all strategies." />
          )}
        </div>
      </div>
    </WidgetShell>
  );
}
