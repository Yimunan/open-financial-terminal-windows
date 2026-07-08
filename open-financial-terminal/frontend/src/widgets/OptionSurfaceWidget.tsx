import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { OptionChainResponse } from "../api/types";
import { cx } from "../lib/format";
import { useOptionsStatus } from "../lib/useEquityStream";
import { themeColor, usePalette } from "../state/settings";
import SeriesChart from "../components/SeriesChart";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState } from "../components/States";

type View = "smile" | "term" | "surface";
const MAX_EXPIRIES = 6;

/** ATM call IV for a chain (IV at the ATM strike, else nearest strike with IV). */
function atmIv(c: OptionChainResponse): number | null {
  if (c.atm_strike != null) {
    const hit = c.calls.find((r) => r.strike === c.atm_strike);
    if (hit?.iv) return hit.iv;
  }
  const withIv = c.calls.filter((r) => r.iv && c.spot);
  if (!withIv.length || !c.spot) return null;
  return withIv.reduce((a, b) => (Math.abs(b.strike - c.spot!) < Math.abs(a.strike - c.spot!) ? b : a)).iv;
}

/** Fetch expirations then the nearest N chains for the underlying (one combined query). */
function useSurface(underlying: string, source: string, enabled: boolean) {
  return useQuery({
    queryKey: ["optSurface", underlying, source],
    enabled,
    staleTime: 60_000,
    queryFn: async (): Promise<OptionChainResponse[]> => {
      const exp = await api.optionExpirations(underlying);
      const dates = exp.expirations.slice(0, MAX_EXPIRIES).map((e) => e.date);
      const chains = await Promise.all(dates.map((d) => api.optionChain(underlying, d).catch(() => null)));
      return chains.filter((c): c is OptionChainResponse => !!c);
    },
  });
}

export default function OptionSurfaceWidget(props: WidgetProps) {
  const { symbol, channel, setChannel } = useWidgetSymbol(props);
  const underlying = symbol.toUpperCase();
  const opt = useOptionsStatus();
  const [view, setView] = useState<View>("smile");

  const q = useSurface(underlying, opt.source, opt.enabled);
  const chains = q.data ?? [];

  const ivRange = useMemo(() => {
    const all: number[] = [];
    for (const c of chains) for (const r of [...c.calls, ...c.puts]) if (r.iv) all.push(r.iv);
    if (!all.length) return [0.1, 0.5] as const;
    return [Math.min(...all), Math.max(...all)] as const;
  }, [chains]);

  const [expiry, setExpiry] = useState<string>("");
  const smileChain = chains.find((c) => c.expiry === expiry) ?? chains[0];

  const surfaceStrikes = useMemo(() => {
    const set = new Set<number>();
    for (const c of chains) for (const k of c.strikes) set.add(k);
    const spot = chains.find((c) => c.spot)?.spot ?? null;
    const arr = [...set].sort((a, b) => a - b);
    if (spot && arr.length > 21)
      return arr.sort((a, b) => Math.abs(a - spot) - Math.abs(b - spot)).slice(0, 21).sort((a, b) => a - b);
    return arr;
  }, [chains]);

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={opt.source === "yfinance" ? "eod" : "live"}
      toolbar={
        <>
          <span className="font-mono text-sm font-bold">{underlying}</span>
          <div className="ml-2 flex items-center gap-px rounded border border-term-border text-[10px]">
            {(["smile", "term", "surface"] as View[]).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={cx(
                  "px-1.5 py-0.5 uppercase",
                  view === v ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
                )}
              >
                {v}
              </button>
            ))}
          </div>
          {view === "smile" && chains.length > 0 && (
            <select
              value={smileChain?.expiry ?? ""}
              onChange={(e) => setExpiry(e.target.value)}
              className="focus-ring ml-2 rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-muted"
            >
              {chains.map((c) => (
                <option key={c.expiry} value={c.expiry}>{c.expiry}</option>
              ))}
            </select>
          )}
          {chains.length > 0 && (
            <span className="ml-auto text-[10px] text-term-muted">
              IV {(ivRange[0] * 100).toFixed(0)}–{(ivRange[1] * 100).toFixed(0)}%
            </span>
          )}
        </>
      }
    >
      {!opt.enabled ? (
        <EmptyState title="Options source is off — enable it in Settings → Market Data → Options" />
      ) : q.isLoading ? (
        <EmptyState title={`Loading ${underlying} IV…`} />
      ) : chains.length === 0 ? (
        <EmptyState title={`No options data for ${underlying}`} />
      ) : view === "smile" ? (
        <SmileChart chain={smileChain} />
      ) : view === "term" ? (
        <TermChart chains={chains} />
      ) : (
        <Surface chains={chains} strikes={surfaceStrikes} ivRange={ivRange} />
      )}
    </WidgetShell>
  );
}

/** IV vs strike for one expiry — a scatter smile: calls filled dots, puts hollow rings. */
function SmileChart({ chain }: { chain?: OptionChainResponse }) {
  usePalette(); // recompute series colors on theme change
  const series = useMemo(() => {
    if (!chain) return [];
    const scatter = (rows: OptionChainResponse["calls"], color: string, title: string, marker: "filled" | "hollow") => ({
      points: rows.filter((r) => r.iv && r.iv > 0).sort((a, b) => a.strike - b.strike)
        .map((r) => ({ time: r.strike, value: r.iv! * 100 })),
      color, title, kind: "scatter" as const, marker,
    });
    return [
      scatter(chain.calls, themeColor("--term-up"), "calls", "filled"),
      scatter(chain.puts, themeColor("--term-down"), "puts", "hollow"),
    ].filter((s) => s.points.length);
  }, [chain]);
  if (!chain || !series.length) return <EmptyState title="No IV data for this expiry" />;
  return <SeriesChart series={series} title={`${chain.expiry} · IV % vs strike · calls ● / puts ○`} axis={{ xMode: "value", xUnit: "$" }} />;
}

/** ATM implied vol vs days-to-expiry (the term structure), on the shared SeriesChart engine. */
function TermChart({ chains }: { chains: OptionChainResponse[] }) {
  usePalette();
  const points = useMemo(() =>
    chains.map((c) => ({ dte: c.dte, iv: atmIv(c) }))
      .filter((p): p is { dte: number; iv: number } => p.iv != null)
      .sort((a, b) => a.dte - b.dte)
      .map((p) => ({ time: p.dte, value: p.iv * 100 })), [chains]);
  if (points.length < 2) return <EmptyState title="Not enough expiries with IV for a term structure" />;
  return (
    <SeriesChart
      series={[{ points, color: themeColor("--term-accent"), title: "ATM IV %", kind: "line" }]}
      title="ATM IV % vs days-to-expiry"
      axis={{ xMode: "value", xUnit: "d" }}
    />
  );
}

/** Expiry × strike IV heatmap (call IV per cell), colored with theme tokens (reacts to theme/CN). */
function Surface({ chains, strikes, ivRange }: {
  chains: OptionChainResponse[]; strikes: number[]; ivRange: readonly [number, number];
}) {
  usePalette(); // re-read tokens on theme change
  const [lo, hi] = ivRange;
  const spot = chains.find((c) => c.spot)?.spot ?? null;
  const atmK = spot != null && strikes.length
    ? strikes.reduce((a, b) => (Math.abs(b - spot) < Math.abs(a - spot) ? b : a))
    : null;
  const cell = (iv: number) => {
    const t = hi > lo ? Math.max(0, Math.min(1, (iv - lo) / (hi - lo))) : 0.5;
    return themeColor("--term-accent", 0.12 + 0.68 * t); // sequential single-token ramp (IV is one-sided)
  };
  const ivAt = (c: OptionChainResponse, k: number) => c.calls.find((r) => r.strike === k)?.iv ?? null;
  return (
    <div className="h-full overflow-auto p-1">
      <table className="border-collapse font-mono text-[10px]">
        <thead>
          <tr>
            <th className="sticky left-0 bg-term-panel px-1 text-left text-term-muted">exp \ K</th>
            {strikes.map((k) => (
              <th key={k}
                className={cx("px-1 text-right font-normal text-term-muted tabular-nums",
                  k === atmK && "border-l border-term-accent text-term-accent")}>
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {chains.map((c) => (
            <tr key={c.expiry}>
              <td className="sticky left-0 bg-term-panel px-1 text-term-muted">{c.expiry.slice(5)}</td>
              {strikes.map((k) => {
                const iv = ivAt(c, k);
                return (
                  <td key={k}
                    className={cx("px-1 text-right tabular-nums text-term-text",
                      k === atmK && "border-l border-term-accent/50")}
                    style={iv ? { backgroundColor: cell(iv) } : undefined}
                    title={iv ? `${c.expiry} ${k}: ${(iv * 100).toFixed(1)}%` : undefined}>
                    {iv ? (iv * 100).toFixed(0) : ""}
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
