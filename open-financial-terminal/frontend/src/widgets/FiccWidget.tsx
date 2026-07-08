/** FICC board — a unified Futures/FICC overview over the rates, FX and commodity complexes the
 * DataManager serves natively. One tile, a tab per section (Rates · FX · Metals · Energy ·
 * Agriculture) plus a cross-asset Correlation tab. All rows are EOD (daily bars from the
 * qhfi/yfinance lake) — polled, not streamed. Clicking a row selects it for the chart below and
 * pushes its symbol to the link channel, retargeting a linked Chart/Metrics.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BoardItem, CurvePoint, RatesFuture } from "../api/types";
import FlashCell from "../components/FlashCell";
import Sparkline from "../components/Sparkline";
import { cx, fmtPct, fmtPrice, upDownClass } from "../lib/format";
import { useT } from "../lib/i18n";
import { chartColors, seriesColors } from "../lib/chartTheme";
import { SeriesChart as SeriesChartEngine, type ChartSeries } from "../lib/seriesChart";
import { usePalette } from "../state/settings";
import { useFiccPool } from "../state/marketPool";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import CorrelationPanel from "./CorrelationPanel";

/** Sentinel tab keys for views that aren't board asset-class sections. */
const CORR = "__corr__";
const CURVE = "__curve__"; // Treasury yield curve (merged-in Rates module)
const RATESFUT = "__ratesfut__"; // CME Treasury futures complex (merged-in Rates module)

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

/** Daily close chart for one FICC instrument (3y), via the generic /api/bars (native asset class). */
function InstrumentChart({ item }: { item: BoardItem }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["bars", item.symbol, item.asset, "1d"],
    queryFn: () => api.bars(item.symbol, item.asset, "1d", []),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  const colors = seriesColors();
  const series = useMemo<ChartSeries[]>(() => {
    if (!data) return [];
    const cutoff = isoDaysAgo(3 * 365);
    return [
      {
        title: `${item.symbol} close`,
        color: colors.accent,
        kind: "area",
        points: data.candles
          .filter((c) => typeof c.time === "string" && c.time >= cutoff)
          .map((c) => ({ time: c.time, value: c.close })),
      },
    ];
  }, [data, colors, item.symbol]);

  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wider text-term-muted">
        {item.symbol} · {item.name} — daily close (3y)
      </div>
      <div className="h-[260px]">
        {isLoading && <LoadingState rows={6} />}
        {error && <ErrorState message={(error as Error).message} onRetry={() => refetch()} />}
        {data && (series[0]?.points.length ? <LineChart series={series} /> : <EmptyState title="No bars" />)}
      </div>
    </div>
  );
}

/** One FICC row — EOD quote polled every 60s (rates/fx/commodity have no live stream). */
function Row({ item, active, onPick }: { item: BoardItem; active: boolean; onPick: () => void }) {
  const { data: q } = useQuery({
    queryKey: ["quote", item.symbol, item.asset],
    queryFn: () => api.quote(item.symbol, item.asset, 30),
    refetchInterval: 60_000,
    retry: 1,
  });
  return (
    <tr
      onClick={onPick}
      className={cx(
        "group cursor-pointer border-b border-term-border/40 hover:bg-term-border/30",
        active && "bg-term-border/40",
      )}
    >
      <td className="px-2 py-1">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-xs font-semibold">{item.symbol}</span>
          <span className="truncate text-[10px] text-term-muted">{item.name}</span>
        </div>
      </td>
      <td className="px-1 py-1">
        <Sparkline points={q?.spark} />
      </td>
      <td className="px-2 py-1 text-right font-mono text-xs">
        <FlashCell value={q?.price ?? null}>{fmtPrice(q?.price ?? null)}</FlashCell>
      </td>
      <td className={cx("px-2 py-1 text-right font-mono text-xs", upDownClass(q?.change_pct ?? null))}>
        <FlashCell value={q?.change_pct ?? null}>{fmtPct(q?.change_pct ?? null)}</FlashCell>
      </td>
    </tr>
  );
}

// ── Rates module (merged in): Treasury yield curve + CME Treasury futures complex ──────
/** 3-decimal rate price (distinct from the imported equity `fmtPrice`). */
function fmtRate(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(3);
}

/** US Treasury yield-curve tab: current term structure (SVG) + per-tenor history (line chart). */
function YieldCurveTab() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["rates-curve"],
    queryFn: () => api.ratesCurve(isoDaysAgo(5 * 365)),
    staleTime: 5 * 60_000,
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

  const pts = data.latest.points.filter((p) => p.value != null);
  const short = pts[0];
  const ten = pts.find((p) => p.tenor === "10Y");
  const spread = short && ten && short.value != null && ten.value != null ? ten.value - short.value : null;

  return (
    <div className="flex flex-col gap-3 p-3">
      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <span className="text-[10px] uppercase tracking-wider text-term-muted">
            Current curve · {data.latest.date ?? "—"}
          </span>
          {spread != null && short && (
            <span className="text-[10px] text-term-muted">
              term spread (10Y − {short.tenor}):{" "}
              <span className={cx("font-mono", spread < 0 ? "text-term-down" : "text-term-up")}>
                {spread >= 0 ? "+" : ""}
                {spread.toFixed(2)}% {spread < 0 ? "· inverted" : "· normal"}
              </span>
            </span>
          )}
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

/** CME Treasury futures complex (ZT/ZF/ZN/ZB/UB/ZQ) — cards + a selected contract's chart. */
function TreasuryFuturesTab() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["rates-futures"],
    queryFn: () => api.ratesFutures(),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  const [sel, setSel] = useState<string | null>(null);
  const selected = sel ?? data?.futures.find((f) => f.symbol === "ZN")?.symbol ?? data?.futures[0]?.symbol ?? null;

  if (isLoading) return <LoadingState rows={6} />;
  if (error) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;
  if (!data?.futures.length) return <EmptyState title="No Treasury futures in the lake" />;

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
        {data.futures.map((f) => (
          <FutureCard key={f.symbol} fut={f} active={f.symbol === selected} onClick={() => setSel(f.symbol)} />
        ))}
      </div>
      {selected && <FutureChart symbol={selected} />}
    </div>
  );
}

function FutureCard({ fut, active, onClick }: { fut: RatesFuture; active: boolean; onClick: () => void }) {
  const { quote } = fut;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "focus-ring flex flex-col gap-1 rounded border p-2.5 text-left transition-colors",
        active ? "border-term-accent bg-term-accent/10" : "border-term-border/50 bg-term-bg/40 hover:border-term-border",
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-xs font-semibold">{fut.symbol}</span>
        <span className="truncate text-[10px] text-term-muted" title={fut.name}>{fut.name}</span>
      </div>
      <div className="flex items-end justify-between gap-2">
        <span className="font-mono text-lg leading-none">{fmtRate(quote.price)}</span>
        <Sparkline points={fut.spark} width={56} height={20} />
      </div>
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-term-muted">{quote.asof ?? "—"}</span>
        <span className={cx("font-mono", upDownClass(quote.change))}>
          {quote.change == null ? "—" : `${quote.change >= 0 ? "+" : ""}${quote.change.toFixed(3)}`}
          {quote.change_pct != null && ` (${quote.change_pct >= 0 ? "+" : ""}${quote.change_pct.toFixed(2)}%)`}
        </span>
      </div>
      <div className="flex items-center justify-between text-[9px] text-term-muted">
        <span>mod dur {fut.modified_duration}</span>
        <span>${fut.contract_multiplier.toLocaleString()}/pt</span>
      </div>
    </button>
  );
}

function FutureChart({ symbol }: { symbol: string }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["rates-future-bars", symbol],
    queryFn: () => api.ratesFutureBars(symbol, isoDaysAgo(3 * 365)),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  const colors = seriesColors();
  const series = useMemo<ChartSeries[]>(() => {
    if (!data) return [];
    return [{ title: `${data.symbol} close`, color: colors.accent, kind: "area", points: data.candles.map((c) => ({ time: c.time, value: c.close })) }];
  }, [data, colors]);

  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wider text-term-muted">
        {data ? `${data.symbol} · ${data.name} — front-month close (3y)` : "Loading…"}
      </div>
      <div className="h-[280px]">
        {isLoading && <LoadingState rows={6} />}
        {error && <ErrorState message={(error as Error).message} onRetry={() => refetch()} />}
        {data && (series[0]?.points.length ? <LineChart series={series} /> : <EmptyState title="No bars" />)}
      </div>
    </div>
  );
}

export default function FiccWidget(props: WidgetProps) {
  const { symbol: activeSymbol, channel, setChannel, setSymbol } = useWidgetSymbol(props);
  const t = useT();
  const { data, isLoading, error } = useQuery({
    queryKey: ["ficc-board"],
    queryFn: api.ficcBoard,
    staleTime: Infinity,
  });

  const sections = data?.sections ?? [];
  // Initial tab can be deep-linked via params.tab (e.g. the legacy "rates" alias opens the curve).
  const [tabKey, setTabKey] = useState<string | null>((props.params.tab as string) ?? null);
  const isCorr = tabKey === CORR;
  const isCurve = tabKey === CURVE;
  const isFutures = tabKey === RATESFUT;
  const isSentinel = isCorr || isCurve || isFutures;
  const active = isSentinel ? undefined : sections.find((s) => s.key === tabKey) ?? sections[0];

  // Instrument selected for the chart below the table; defaults to the active section's first row.
  const [selectedSym, setSelectedSym] = useState<string | null>(null);
  const selected =
    active?.items.find((i) => i.symbol === selectedSym) ?? active?.items[0] ?? null;

  const tabClass = (on: boolean) =>
    cx(
      "rounded px-2 py-0.5 text-[10px] uppercase tracking-wide",
      on ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
    );

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge="eod"
      toolbar={
        <div className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-[10px] uppercase tracking-wider text-term-muted">FICC</span>
          {sections.map((s) => (
            <button key={s.key} onClick={() => setTabKey(s.key)} className={tabClass(!isSentinel && active?.key === s.key)}>
              {s.label}
            </button>
          ))}
          <button onClick={() => setTabKey(CURVE)} className={tabClass(isCurve)}>
            {t("ficc.yieldCurve")}
          </button>
          <button onClick={() => setTabKey(RATESFUT)} className={tabClass(isFutures)}>
            {t("ficc.treasuryFutures")}
          </button>
          {sections.length > 0 && (
            <button onClick={() => setTabKey(CORR)} className={tabClass(isCorr)}>
              {t("board.correlation")}
            </button>
          )}
        </div>
      }
    >
      {isCurve && <YieldCurveTab />}
      {isFutures && <TreasuryFuturesTab />}
      {!isCurve && !isFutures && isLoading && <SkeletonRows />}
      {!isCurve && !isFutures && error && <ErrorState message={(error as Error).message} />}
      {isCorr && (
        <CorrelationPanel usePool={useFiccPool} catalog={{ key: ["ficc-board"], fetch: api.ficcBoard }} />
      )}
      {!isCorr && active && active.items.length === 0 && <EmptyState title="No instruments" />}
      {!isCorr && active && active.items.length > 0 && (
        <div className="flex flex-col gap-3 p-1">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
                <th className="px-2 py-1 text-left font-medium">{t("common.symbol")}</th>
                <th className="px-1 py-1 text-left font-medium">30d</th>
                <th className="px-2 py-1 text-right font-medium">{t("common.last")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("common.chg")}</th>
              </tr>
            </thead>
            <tbody>
              {active.items.map((item) => (
                <Row
                  key={`${item.asset}:${item.symbol}`}
                  item={item}
                  active={item.symbol === (selected?.symbol ?? activeSymbol)}
                  onPick={() => {
                    setSelectedSym(item.symbol);
                    setSymbol({ symbol: item.symbol, asset: item.asset });
                  }}
                />
              ))}
            </tbody>
          </table>
          {selected && <InstrumentChart item={selected} />}
        </div>
      )}
    </WidgetShell>
  );
}
