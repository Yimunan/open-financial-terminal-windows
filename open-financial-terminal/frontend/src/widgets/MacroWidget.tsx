/** Macro module — macroeconomic dashboard over the qhfi lake (FRED + World Bank + Treasury
 * curve). One widget, four tabs: an at-a-glance US indicators grid, the Treasury yield curve,
 * a series explorer over the full catalog, and a World Bank cross-country panel. Not symbol-
 * keyed, so it carries no link channel.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CurvePoint, MacroCard } from "../api/types";
import { chartColors, seriesColors } from "../lib/chartTheme";
import { SeriesChart as SeriesChartEngine, type ChartSeries } from "../lib/seriesChart";
import { cx, fmtCompact, upDownClass } from "../lib/format";
import { usePalette } from "../state/settings";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { TextButton, WidgetShell } from "./shell";
import Sparkline from "../components/Sparkline";
import { EmptyState, ErrorState, LoadingState } from "../components/States";

type Tab = "grid" | "curve" | "explorer" | "cross";
const TABS: { id: Tab; label: string }[] = [
  { id: "grid", label: "Indicators" },
  { id: "curve", label: "Yield Curve" },
  { id: "explorer", label: "Explorer" },
  { id: "cross", label: "Cross-Country" },
];

const STALE = 5 * 60_000;

/** Macro values span tiny rates (4.2%) to huge aggregates (M2). Pick decimals accordingly. */
function fmtNum(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 10_000) return fmtCompact(v);
  if (a >= 100) return v.toFixed(1);
  return v.toFixed(2);
}

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

/** Canvas line/area chart wrapper (shared engine, re-instantiated on palette change). */
function LineChart({ series }: { series: ChartSeries[] }) {
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
  return <div ref={ref} className="h-full w-full" />;
}

// ── Indicators grid ────────────────────────────────────────────────────────────
function IndicatorsTab() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["macro-grid"],
    queryFn: () => api.macroGrid(),
    staleTime: STALE,
    retry: 1,
  });
  if (isLoading) return <LoadingState rows={8} />;
  if (error) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;
  if (!data?.cards.length) return <EmptyState title="No macro indicators in the lake" />;
  return (
    <div className="grid grid-cols-2 gap-2 p-3 lg:grid-cols-3 xl:grid-cols-4">
      {data.cards.map((c) => (
        <IndicatorCard key={c.id} card={c} />
      ))}
    </div>
  );
}

function IndicatorCard({ card }: { card: MacroCard }) {
  const { latest } = card;
  const chg = latest.change_pct;
  return (
    <div className="flex flex-col gap-1 rounded border border-term-border/50 bg-term-bg/40 p-2.5">
      <div className="truncate text-[10px] uppercase tracking-wide text-term-muted" title={card.label}>
        {card.label}
      </div>
      <div className="flex items-end justify-between gap-2">
        <span className="font-mono text-lg leading-none">{fmtNum(latest.value)}</span>
        <Sparkline points={card.spark} width={56} height={20} />
      </div>
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-term-muted">{latest.date ?? "—"}</span>
        <span className={cx("font-mono", upDownClass(latest.change))}>
          {latest.change == null ? "—" : `${latest.change >= 0 ? "+" : ""}${fmtNum(latest.change)}`}
          {chg != null && ` (${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%)`}
        </span>
      </div>
    </div>
  );
}

// ── Treasury yield curve ─────────────────────────────────────────────────────────
function YieldCurveTab() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["macro-curve"],
    // ~5y of history keeps the per-tenor time chart light; the current curve uses `latest`.
    queryFn: () => api.macroRatesCurve(isoDaysAgo(5 * 365)),
    staleTime: STALE,
    retry: 1,
  });
  const colors = seriesColors();
  const histSeries = useMemo<ChartSeries[]>(() => {
    if (!data) return [];
    return data.tenors.map((t, i) => ({
      title: t,
      color: colors.priceLines[i % colors.priceLines.length],
      kind: "line" as const,
      points: data.rows
        .map((r) => ({ time: r.date, value: r.rates[t] }))
        .filter((p): p is { time: string; value: number } => p.value != null),
    }));
  }, [data, colors]);

  if (isLoading) return <LoadingState rows={8} />;
  if (error) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;
  if (!data) return <EmptyState title="No Treasury curve in the lake" />;

  return (
    <div className="flex flex-col gap-3 p-3">
      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <span className="text-[10px] uppercase tracking-wider text-term-muted">
            Current curve · {data.latest.date ?? "—"}
          </span>
        </div>
        <CurveSvg points={data.latest.points} />
      </div>
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wider text-term-muted">Tenors over time (%)</div>
        <div className="h-[300px]">
          <LineChart series={histSeries} />
        </div>
      </div>
    </div>
  );
}

/** Current yield curve as an inline SVG (yield vs tenor, tenors spaced evenly). */
function CurveSvg({ points }: { points: CurvePoint[] }) {
  const pts = points.filter((p) => p.value != null) as (CurvePoint & { value: number })[];
  const W = 520;
  const H = 150;
  const PAD = { l: 36, r: 12, t: 12, b: 22 };
  if (pts.length < 2) return <EmptyState title="Not enough curve data" />;
  const ys = pts.map((p) => p.value);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = max - min || 1;
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const xOf = (i: number) => PAD.l + (pts.length === 1 ? plotW / 2 : (i / (pts.length - 1)) * plotW);
  const yOf = (v: number) => PAD.t + (1 - (v - min) / span) * plotH;
  const path = pts.map((p, i) => `${i === 0 ? "M" : "L"}${xOf(i).toFixed(1)},${yOf(p.value).toFixed(1)}`).join(" ");
  const ticks = [min, min + span / 2, max];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Treasury yield curve">
      {ticks.map((v, i) => (
        <g key={i}>
          <line x1={PAD.l} x2={W - PAD.r} y1={yOf(v)} y2={yOf(v)} stroke="rgb(var(--term-border))" strokeOpacity={0.4} />
          <text x={4} y={yOf(v) + 3} className="fill-term-muted" style={{ fontSize: 9 }}>
            {v.toFixed(2)}
          </text>
        </g>
      ))}
      <path d={path} fill="none" stroke="rgb(var(--term-accent))" strokeWidth={1.6} />
      {pts.map((p, i) => (
        <g key={p.tenor}>
          <circle cx={xOf(i)} cy={yOf(p.value)} r={2.5} fill="rgb(var(--term-accent))" />
          <text x={xOf(i)} y={H - 8} textAnchor="middle" className="fill-term-muted" style={{ fontSize: 9 }}>
            {p.tenor}
          </text>
          <text x={xOf(i)} y={yOf(p.value) - 6} textAnchor="middle" className="fill-term-text" style={{ fontSize: 9 }}>
            {p.value.toFixed(2)}
          </text>
        </g>
      ))}
    </svg>
  );
}

// ── Series explorer ──────────────────────────────────────────────────────────────
function ExplorerTab() {
  const { data: cat } = useQuery({
    queryKey: ["macro-catalog"],
    queryFn: () => api.macroCatalog(),
    staleTime: STALE,
    retry: 1,
  });
  const [sel, setSel] = useState("CPIAUCSL");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["macro-series", sel],
    queryFn: () => api.macroSeries(sel),
    staleTime: STALE,
    retry: 1,
  });
  const colors = seriesColors();
  const series = useMemo<ChartSeries[]>(() => {
    if (!data) return [];
    return [
      {
        title: data.label,
        color: colors.accent,
        kind: "area",
        points: data.observations.filter((o): o is { date: string; value: number } => o.value != null).map((o) => ({ time: o.date, value: o.value })),
      },
    ];
  }, [data, colors]);

  const us = cat?.series.filter((s) => s.group === "us") ?? [];
  const wb = cat?.series.filter((s) => s.group === "cross_country") ?? [];

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      <select
        value={sel}
        onChange={(e) => setSel(e.target.value)}
        className="focus-ring w-full rounded border border-term-border bg-term-bg px-2 py-1 text-xs text-term-text"
      >
        <optgroup label="US indicators">
          {us.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label} ({s.id})
            </option>
          ))}
        </optgroup>
        <optgroup label="World Bank (cross-country)">
          {wb.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </optgroup>
      </select>
      <div className="min-h-0 flex-1">
        {isLoading && <LoadingState rows={6} />}
        {error && <ErrorState message={(error as Error).message} onRetry={() => refetch()} />}
        {data && (series[0]?.points.length ? <LineChart series={series} /> : <EmptyState title="No observations" />)}
      </div>
    </div>
  );
}

// ── Cross-country panel ──────────────────────────────────────────────────────────
const CC_INDICATORS: { id: string; label: string }[] = [
  { id: "gdp_growth", label: "GDP growth" },
  { id: "inflation", label: "Inflation" },
  { id: "unemployment", label: "Unemployment" },
];

function CrossCountryTab() {
  const [indicator, setIndicator] = useState("gdp_growth");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["macro-cross", indicator],
    queryFn: () => api.macroCrossCountry(indicator),
    staleTime: STALE,
    retry: 1,
  });
  const colors = seriesColors();
  const series = useMemo<ChartSeries[]>(() => {
    if (!data) return [];
    return data.countries.map((c, i) => ({
      title: c.country,
      color: colors.priceLines[i % colors.priceLines.length],
      kind: "line" as const,
      points: c.observations.filter((o): o is { date: string; value: number } => o.value != null).map((o) => ({ time: o.date, value: o.value })),
    }));
  }, [data, colors]);

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      <div className="flex gap-1">
        {CC_INDICATORS.map((ind) => (
          <TextButton key={ind.id} active={indicator === ind.id} onClick={() => setIndicator(ind.id)}>
            {ind.label}
          </TextButton>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        {isLoading && <LoadingState rows={6} />}
        {error && <ErrorState message={(error as Error).message} onRetry={() => refetch()} />}
        {data && (series.length ? <LineChart series={series} /> : <EmptyState title="No data for this indicator" />)}
      </div>
    </div>
  );
}

export default function MacroWidget(_props: WidgetProps) {
  const [tab, setTab] = useState<Tab>("grid");
  return (
    <WidgetShell
      badge="eod"
      toolbar={
        <div className="flex items-center gap-1">
          <span className="mr-1 text-[10px] uppercase tracking-wider text-term-muted">Macro</span>
          {TABS.map((tb) => (
            <TextButton key={tb.id} active={tab === tb.id} onClick={() => setTab(tb.id)}>
              {tb.label}
            </TextButton>
          ))}
        </div>
      }
    >
      {tab === "grid" && <IndicatorsTab />}
      {tab === "curve" && <YieldCurveTab />}
      {tab === "explorer" && <ExplorerTab />}
      {tab === "cross" && <CrossCountryTab />}
    </WidgetShell>
  );
}
