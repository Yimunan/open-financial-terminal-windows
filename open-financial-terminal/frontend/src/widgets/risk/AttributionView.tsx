/** Shared attribution view with two modes:
 *  • Risk — Barra factor/specific vol decomposition, per-factor + per-position contribution (Euler).
 *  • Realized — the book's realized P&L over the fit window decomposed into factor + specific.
 * Used by the standalone RiskAttributionWidget and the Portfolio "Attribution" tab. */

import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { RiskAttributionResponse, ReturnAttributionResponse, BrinsonAttributionResponse } from "../../api/types";
import { cx, fmtPct, upDownClass } from "../../lib/format";
import { themeColor } from "../../state/settings";
import { EmptyState, ErrorState } from "../../components/States";
import { SkeletonRows } from "../shell";

type Source = "holdings" | "paper";
type Mode = "risk" | "realized" | "brinson";

function Stat({ label, value, cls }: { label: ReactNode; value: ReactNode; cls?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-wider text-term-muted">{label}</span>
      <span className={cx("font-mono text-xs", cls)}>{value}</span>
    </div>
  );
}

/** Horizontal proportional bar (width = |value| / max), colored by token. */
function Bar({ frac, color }: { frac: number; color: string }) {
  return (
    <div className="h-1.5 w-full rounded-sm bg-term-border/30">
      <div
        className="h-full rounded-sm"
        style={{ width: `${Math.min(Math.abs(frac), 1) * 100}%`, backgroundColor: color }}
      />
    </div>
  );
}

/** Minimal inline-SVG sparkline of a cumulative series (no chart lib). */
function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => `${(i / (values.length - 1)) * 100},${28 - ((v - min) / span) * 28}`)
    .join(" ");
  const zeroY = 28 - ((0 - min) / span) * 28;
  const color = themeColor(values[values.length - 1] >= 0 ? "--term-up" : "--term-down");
  return (
    <svg viewBox="0 0 100 28" preserveAspectRatio="none" className="h-14 w-full">
      <line x1="0" y1={zeroY} x2="100" y2={zeroY} stroke={themeColor("--term-border")} strokeWidth="0.5" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Toggle<T extends string>({ value, options, onChange }: {
  value: T; options: readonly T[]; onChange: (v: T) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={cx(
            "rounded px-2 py-0.5 text-[10px] uppercase tracking-wide",
            value === o ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
          )}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

const factorColor = (kind: string) => themeColor(kind === "style" ? "--term-accent" : "--term-series-2");

function RiskBody({ data }: { data: RiskAttributionResponse }) {
  const maxFactor = useMemo(
    () => Math.max(1e-9, ...(data.factors ?? []).map((f) => Math.abs(f.pct_total))),
    [data],
  );
  const maxPos = useMemo(
    () => Math.max(1e-9, ...(data.positions ?? []).map((p) => Math.abs(p.pct))),
    [data],
  );
  return (
    <>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-term-border bg-term-sunken/40 p-3 sm:grid-cols-4">
        <Stat label="Forecast vol" value={fmtPct(data.total_vol ?? 0, false)} />
        <Stat label="Factor vol" value={fmtPct(data.factor_vol ?? 0, false)} />
        <Stat label="Specific vol" value={fmtPct(data.specific_vol ?? 0, false)} />
        <Stat label="% Factor" value={fmtPct((data.pct_factor ?? 0) * 100, false)} cls="text-term-accent" />
      </div>

      <div className="px-2 pt-2">
        <div className="px-1 pb-1 text-[10px] uppercase tracking-wider text-term-muted">Factor contribution to risk</div>
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
              <th className="px-2 py-1 text-left font-medium">Factor</th>
              <th className="px-2 py-1 text-right font-medium">Exposure</th>
              <th className="px-2 py-1 text-left font-medium">% of risk</th>
            </tr>
          </thead>
          <tbody>
            {(data.factors ?? []).map((f) => (
              <tr key={f.factor} className="border-b border-term-border/30">
                <td className="px-2 py-1 font-mono">
                  <span className="mr-1.5 inline-block h-2 w-2 rounded-sm align-middle" style={{ backgroundColor: factorColor(f.kind) }} title={f.kind} />
                  {f.factor}
                </td>
                <td className="px-2 py-1 text-right font-mono text-term-muted">{f.exposure.toFixed(2)}</td>
                <td className="px-2 py-1">
                  <div className="flex items-center gap-2">
                    <Bar frac={f.pct_total / maxFactor} color={factorColor(f.kind)} />
                    <span className="w-12 shrink-0 text-right font-mono text-[11px]">{fmtPct(f.pct_total * 100, false)}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="px-2 pb-2 pt-3">
        <div className="px-1 pb-1 text-[10px] uppercase tracking-wider text-term-muted">Position contribution to risk</div>
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
              <th className="px-2 py-1 text-left font-medium">Symbol</th>
              <th className="px-2 py-1 text-right font-medium">Weight</th>
              <th className="px-2 py-1 text-right font-medium" title="Marginal contribution to risk">MCTR</th>
              <th className="px-2 py-1 text-right font-medium" title="Component contribution to risk">CCTR</th>
              <th className="px-2 py-1 text-left font-medium">% of risk</th>
            </tr>
          </thead>
          <tbody>
            {(data.positions ?? []).map((p) => (
              <tr key={p.symbol} className="border-b border-term-border/30">
                <td className="px-2 py-1 font-mono font-semibold">{p.symbol}</td>
                <td className="px-2 py-1 text-right font-mono">{fmtPct(p.weight * 100, false)}</td>
                <td className="px-2 py-1 text-right font-mono text-term-muted">{fmtPct(p.mctr, false)}</td>
                <td className="px-2 py-1 text-right font-mono">{fmtPct(p.cctr, false)}</td>
                <td className="px-2 py-1">
                  <div className="flex items-center gap-2">
                    <Bar frac={p.pct / maxPos} color={themeColor("--term-accent")} />
                    <span className="w-12 shrink-0 text-right font-mono text-[11px]">{fmtPct(p.pct * 100, false)}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function RealizedBody({ data }: { data: ReturnAttributionResponse }) {
  const maxC = useMemo(
    () => Math.max(1e-9, ...(data.contributions ?? []).map((c) => Math.abs(c.contribution))),
    [data],
  );
  return (
    <>
      <div className="grid grid-cols-3 gap-x-4 gap-y-2 border-b border-term-border bg-term-sunken/40 p-3">
        <Stat label="Total return" value={fmtPct(data.total_return ?? 0)} cls={upDownClass(data.total_return ?? 0)} />
        <Stat label="Factor" value={fmtPct(data.factor_return ?? 0)} cls={upDownClass(data.factor_return ?? 0)} />
        <Stat label="Selection" value={fmtPct(data.specific_return ?? 0)} cls={upDownClass(data.specific_return ?? 0)} />
      </div>

      {data.series && data.series.total.length > 1 && (
        <div className="px-3 pt-2">
          <div className="pb-1 text-[10px] uppercase tracking-wider text-term-muted">
            Cumulative return · {data.series.times[0]} → {data.series.times[data.series.times.length - 1]}
          </div>
          <Sparkline values={data.series.total} />
        </div>
      )}

      <div className="px-2 pb-2 pt-2">
        <div className="px-1 pb-1 text-[10px] uppercase tracking-wider text-term-muted">Factor contribution to return</div>
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
              <th className="px-2 py-1 text-left font-medium">Factor</th>
              <th className="px-2 py-1 text-left font-medium">Contribution</th>
            </tr>
          </thead>
          <tbody>
            {(data.contributions ?? []).map((c) => (
              <tr key={c.factor} className="border-b border-term-border/30">
                <td className="px-2 py-1 font-mono">
                  <span className="mr-1.5 inline-block h-2 w-2 rounded-sm align-middle" style={{ backgroundColor: factorColor(c.kind) }} title={c.kind} />
                  {c.factor}
                </td>
                <td className="px-2 py-1">
                  <div className="flex items-center gap-2">
                    <Bar frac={c.contribution / maxC} color={themeColor(c.contribution >= 0 ? "--term-up" : "--term-down")} />
                    <span className={cx("w-14 shrink-0 text-right font-mono text-[11px]", upDownClass(c.contribution))}>{fmtPct(c.contribution)}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function BrinsonBody({ data }: { data: BrinsonAttributionResponse }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-term-border bg-term-sunken/40 p-3 sm:grid-cols-4">
        <Stat label="Active return" value={fmtPct(data.active_return ?? 0)} cls={upDownClass(data.active_return ?? 0)} />
        <Stat label="Allocation" value={fmtPct(data.allocation ?? 0)} cls={upDownClass(data.allocation ?? 0)} />
        <Stat label="Selection" value={fmtPct(data.selection ?? 0)} cls={upDownClass(data.selection ?? 0)} />
        <Stat label="Interaction" value={fmtPct(data.interaction ?? 0)} cls={upDownClass(data.interaction ?? 0)} />
      </div>
      <div className="px-2 pt-1 text-[10px] text-term-muted">
        vs {data.benchmark} · single-period over the window
      </div>
      <div className="px-2 pb-2 pt-1">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
              <th className="px-2 py-1 text-left font-medium">Sector</th>
              <th className="px-2 py-1 text-right font-medium" title="Portfolio / benchmark weight">wP / wB</th>
              <th className="px-2 py-1 text-right font-medium" title="Over/underweight effect">Alloc</th>
              <th className="px-2 py-1 text-right font-medium" title="Stock picking within sector">Select</th>
              <th className="px-2 py-1 text-right font-medium">Total</th>
            </tr>
          </thead>
          <tbody>
            {(data.sectors ?? []).map((s) => (
              <tr key={s.sector} className="border-b border-term-border/30">
                <td className="px-2 py-1 font-mono">{s.sector}</td>
                <td className="px-2 py-1 text-right font-mono text-term-muted">{s.w_port.toFixed(0)} / {s.w_bench.toFixed(0)}%</td>
                <td className={cx("px-2 py-1 text-right font-mono", upDownClass(s.allocation))}>{fmtPct(s.allocation)}</td>
                <td className={cx("px-2 py-1 text-right font-mono", upDownClass(s.selection))}>{fmtPct(s.selection)}</td>
                <td className={cx("px-2 py-1 text-right font-mono font-semibold", upDownClass(s.total))}>{fmtPct(s.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function AttributionView({
  source,
  onSourceChange,
  windowDays,
  account,
  onAccountChange,
  accounts,
}: {
  source: Source;
  onSourceChange?: (s: Source) => void;
  windowDays?: number;
  /** Which sim account to attribute when source="paper" (omit → primary broker). */
  account?: number;
  onAccountChange?: (id: number) => void;
  accounts?: import("../../api/types").PaperSimAccount[];
}) {
  const [mode, setMode] = useState<Mode>("risk");
  // account only applies to the paper source; fold it into the request opts + query keys
  const acct = source === "paper" ? account : undefined;
  const opts = { ...(windowDays ? { window_days: windowDays } : {}), ...(acct != null ? { account: acct } : {}) };
  const wd = Object.keys(opts).length ? opts : undefined;

  const riskQ = useQuery({
    queryKey: ["risk-attribution", source, windowDays ?? null, acct ?? null],
    queryFn: () => api.riskAttribution(source, wd),
    enabled: mode === "risk",
  });
  const realizedQ = useQuery({
    queryKey: ["return-attribution", source, windowDays ?? null, acct ?? null],
    queryFn: () => api.returnAttribution(source, wd),
    enabled: mode === "realized",
  });
  const brinsonQ = useQuery({
    queryKey: ["brinson-attribution", source, windowDays ?? null, acct ?? null],
    queryFn: () => api.brinsonAttribution(source, wd),
    enabled: mode === "brinson",
  });

  const q = mode === "risk" ? riskQ : mode === "realized" ? realizedQ : brinsonQ;
  const data = q.data as
    | (RiskAttributionResponse & ReturnAttributionResponse & BrinsonAttributionResponse)
    | undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-term-border px-2 py-1">
        <Toggle value={mode} options={["risk", "realized", "brinson"] as const} onChange={setMode} />
        {data?.as_of && !data.insufficient && <span className="font-mono text-[10px] text-term-muted">{data.as_of}</span>}
        {onSourceChange && <div className="ml-auto"><Toggle value={source} options={["holdings", "paper"] as const} onChange={onSourceChange} /></div>}
        {source === "paper" && onAccountChange && accounts && accounts.length > 0 && (
          <select
            value={account ?? 1}
            onChange={(e) => onAccountChange(Number(e.target.value))}
            aria-label="Sim account"
            className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-text"
            title="Which sim account to attribute"
          >
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {q.isLoading && <SkeletonRows />}
      {q.error && <ErrorState message={(q.error as Error).message} />}
      {!q.isLoading && !q.error && data?.insufficient && (
        <EmptyState title={data.reason || "Not enough equity holdings to attribute."} />
      )}

      {!q.isLoading && !q.error && data && !data.insufficient && (
        <div className="min-h-0 flex-1 overflow-auto">
          {mode === "risk" ? <RiskBody data={data} /> : mode === "realized" ? <RealizedBody data={data} /> : <BrinsonBody data={data} />}
          {data.skipped && data.skipped.length > 0 && (
            <p className="px-3 pb-3 pt-1 text-[10px] text-term-muted">
              Skipped {data.skipped.length}: {data.skipped.map((s) => `${s.symbol} (${s.reason})`).join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
