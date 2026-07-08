import type React from "react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset, MetricFmt, MetricRow, PeriodMetrics, PeriodMetricSeries } from "../api/types";
import { cx, fmtCompact, fmtPct, fmtPrice, upDownClass } from "../lib/format";
import { useT, type I18nKey } from "../lib/i18n";
import { themeColor, usePalette } from "../state/settings";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { ErrorState } from "../components/States";
import SeriesChart, { type SeriesSpec } from "../components/SeriesChart";

/** Format one metric value by its `fmt` tag, returning the display text + a color class.
 * pct/pctp values arrive already in percent units (4.3 == 4.3%). */
function fmtMetric(row: MetricRow): { text: string; cls: string } {
  const v = row.value;
  if (v == null) return { text: "—", cls: "text-term-muted" };
  switch (row.fmt) {
    case "pct":
      return { text: fmtPct(v), cls: upDownClass(v) };
    case "pctp":
      return { text: fmtPct(v, false), cls: "text-term-text" };
    case "price":
      return { text: fmtPrice(v), cls: "text-term-text" };
    case "usd":
      return { text: `$${fmtCompact(v)}`, cls: "text-term-text" };
    case "x":
      return { text: `${v.toFixed(1)}×`, cls: "text-term-text" };
    case "int":
      return { text: String(Math.round(v)), cls: "text-term-text" };
    default: // num | ratio
      return { text: v.toFixed(2), cls: "text-term-text" };
  }
}

/** Plain-text format for a period value (no color class), used by the bar-chart labels. */
function fmtPeriodValue(v: number | null, fmt: MetricFmt): string {
  if (v == null) return "—";
  if (fmt === "pct") return fmtPct(v);
  if (fmt === "pctp") return fmtPct(v, false);
  return v.toFixed(2);
}

/** Color for a bar: returns/Sharpe by sign, drawdown always negative-toned, vol neutral-accent. */
function barColor(key: string, v: number): string {
  if (key === "ann_vol") return themeColor("--term-accent");
  if (key === "max_drawdown") return themeColor("--term-down");
  if (v > 0) return themeColor("--term-up");
  if (v < 0) return themeColor("--term-down");
  return themeColor("--term-muted");
}

/** Signed bar chart comparing one metric across trailing windows (1M…3Y). CSS-positioned
 * around a zero baseline so positive/negative bars read at a glance. */
function PeriodBarChart({ windows, series }: { windows: string[]; series: PeriodMetricSeries }) {
  usePalette(); // re-render (and re-read theme colors) on theme/scheme/accent change
  const nums = series.values.filter((v): v is number => v != null);
  const domainMax = Math.max(0, ...nums);
  const domainMin = Math.min(0, ...nums);
  const total = domainMax - domainMin || 1;
  const zeroTop = (domainMax / total) * 100; // % from top where value 0 sits

  return (
    <div className="px-2 pt-2">
      <div className="relative" style={{ height: 188 }}>
        {/* value labels row, pinned to the top so bars never overlap text */}
        <div className="absolute inset-x-0 top-0 flex gap-1">
          {series.values.map((v, i) => (
            <div
              key={windows[i]}
              className={cx(
                "flex-1 text-center font-mono text-[9px]",
                v == null ? "text-term-muted" : "text-term-text",
              )}
            >
              {fmtPeriodValue(v, series.fmt)}
            </div>
          ))}
        </div>
        {/* zero baseline */}
        <div className="absolute inset-x-0 border-t border-term-border/70" style={{ top: `${zeroTop}%` }} />
        <div className="flex h-full items-stretch gap-1">
          {windows.map((w, i) => {
            const v = series.values[i];
            if (v == null) return <div key={w} className="relative flex-1" />;
            const h = (Math.abs(v) / total) * 100;
            const top = v >= 0 ? zeroTop - h : zeroTop;
            return (
              <div key={w} className="relative flex-1">
                <div
                  className="absolute rounded-sm transition-[top,height]"
                  style={{ left: "20%", right: "20%", top: `${top}%`, height: `${h}%`, background: barColor(series.key, v) }}
                  title={`${w}: ${fmtPeriodValue(v, series.fmt)}`}
                />
              </div>
            );
          })}
        </div>
      </div>
      {/* x-axis window labels */}
      <div className="flex gap-1 pt-1">
        {windows.map((w) => (
          <div key={w} className="flex-1 text-center text-[10px] font-semibold uppercase tracking-wide text-term-muted">
            {w}
          </div>
        ))}
      </div>
    </div>
  );
}

type ChartView = "period" | "rolling";
const ROLL_WINDOWS = [30, 60, 90, 180];
const ROLLING_METRICS = [
  { key: "return", label: "Return" },
  { key: "ann_vol", label: "Ann. Volatility" },
  { key: "sharpe", label: "Sharpe" },
  { key: "drawdown", label: "Drawdown" },
];

function Pill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "px-1.5 py-0.5 text-[10px] uppercase",
        active ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
      )}
    >
      {children}
    </button>
  );
}

/** Rolling view: a metric's trailing-window value (or the underwater drawdown) over time. */
function RollingView({ symbol, asset, metricKey, label }: { symbol: string; asset: Asset; metricKey: string; label: string }) {
  usePalette();
  const [window, setWindow] = useState(90);
  const { data, isLoading, error } = useQuery({
    queryKey: ["metrics-rolling", symbol, asset, window],
    queryFn: () => api.metricsRolling(symbol, asset, window),
    staleTime: 60_000,
    retry: 1,
  });

  const points = data?.series?.[metricKey] ?? [];
  const isDrawdown = metricKey === "drawdown";
  const series: SeriesSpec[] = points.length
    ? [{
        points,
        color: isDrawdown ? themeColor("--term-down") : themeColor("--term-accent"),
        kind: isDrawdown ? "area" : "line",
        title: label,
      }]
    : [];

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-term-muted">Window</span>
        <div className="flex items-center gap-px rounded border border-term-border">
          {ROLL_WINDOWS.map((w) => (
            <Pill key={w} active={w === window} onClick={() => setWindow(w)}>{w}d</Pill>
          ))}
        </div>
        <span className="text-[10px] uppercase tracking-wider text-term-muted">
          {label}{isDrawdown ? " · underwater" : ` · rolling ${window}d`}
        </span>
      </div>
      <div className="min-h-0 flex-1" style={{ minHeight: 220 }}>
        {isLoading && <SkeletonRows rows={8} />}
        {error && <ErrorState message={(error as Error).message} />}
        {!isLoading && !error && (series.length ? (
          <SeriesChart series={series} />
        ) : (
          <p className="p-4 text-xs text-term-muted">Not enough history for a rolling {window}-day series.</p>
        ))}
      </div>
    </div>
  );
}

/** The Chart tab: visualize the metrics — across fixed windows (bars) or rolling over time (line). */
function ChartTab({ symbol, asset, pm }: { symbol: string; asset: Asset; pm: PeriodMetrics }) {
  const [view, setView] = useState<ChartView>("period");
  const [metricKey, setMetricKey] = useState("return");

  const options =
    view === "period" ? pm.metrics.map((m) => ({ key: m.key, label: m.label })) : ROLLING_METRICS;
  const active = options.find((o) => o.key === metricKey) ?? options[0];
  const periodSeries = pm.metrics.find((m) => m.key === active.key) ?? pm.metrics[0];

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-2">
      <div className="flex items-center gap-px self-start rounded border border-term-border">
        <Pill active={view === "period"} onClick={() => setView("period")}>By Period</Pill>
        <Pill active={view === "rolling"} onClick={() => setView("rolling")}>Rolling</Pill>
      </div>

      <div className="flex flex-wrap items-center gap-1">
        {options.map((o) => (
          <button
            key={o.key}
            type="button"
            onClick={() => setMetricKey(o.key)}
            className={cx(
              "focus-ring rounded border px-2 py-0.5 text-[11px] transition-colors",
              o.key === active.key
                ? "border-term-accent bg-term-accent/15 text-term-accent"
                : "border-term-border text-term-muted hover:text-term-text",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>

      {view === "period" ? (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-term-muted">
            {periodSeries.label} · by trailing window
          </div>
          <PeriodBarChart windows={pm.windows} series={periodSeries} />
        </div>
      ) : (
        <RollingView symbol={symbol} asset={asset} metricKey={active.key} label={active.label} />
      )}
    </div>
  );
}

/** Format one fundamentals snapshot cell (carried over from the merged Research widget). */
function fmtFundamentalCell(v: string | number | null): string {
  if (v == null) return "—";
  if (typeof v === "number") return Math.abs(v) >= 10_000 ? fmtCompact(v) : String(Math.round(v * 100) / 100);
  return v;
}

/** Fundamentals tab — snapshot fields grid + income statement (was the standalone Research widget,
 * now merged into the Profile module). */
function FundamentalsTab({ symbol }: { symbol: string }) {
  const t = useT();
  const { data, isLoading, error } = useQuery({
    queryKey: ["fundamentals", symbol],
    queryFn: () => api.fundamentals(symbol),
    staleTime: 10 * 60_000,
    retry: 1,
  });

  const snapshot = data?.snapshot ?? {};
  const summary = typeof snapshot["summary"] === "string" ? (snapshot["summary"] as string) : null;
  const fields = Object.entries(snapshot).filter(([k]) => k !== "summary");

  if (isLoading) return <SkeletonRows rows={10} />;
  if (error) return <ErrorState message={(error as Error).message} />;
  if (!data) return null;
  return (
    <div className="space-y-3 p-3">
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-3">
        {fields.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-2 border-b border-term-border/30 py-1">
            <span className="text-[10px] uppercase tracking-wide text-term-muted">{k.replaceAll("_", " ")}</span>
            <span className="font-mono text-xs">{fmtFundamentalCell(v)}</span>
          </div>
        ))}
      </div>

      {summary && <p className="text-xs leading-relaxed text-term-muted">{summary}</p>}

      {data.financials.periods.length > 0 && (
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
              <th className="px-1 py-1 text-left font-medium">{t("research.income")}</th>
              {data.financials.periods.map((p) => (
                <th key={p} className="px-1 py-1 text-right font-medium">{p}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.financials.rows).map(([row, vals]) => (
              <tr key={row} className="border-b border-term-border/30">
                <td className="px-1 py-1 text-term-muted">{row}</td>
                {vals.map((v, i) => (
                  <td key={i} className="px-1 py-1 text-right font-mono">{v == null ? "—" : fmtCompact(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

type Tab = "metrics" | "chart" | "fundamentals";
const TABS: { key: Tab; labelKey: I18nKey }[] = [
  { key: "metrics", labelKey: "profile.metrics" },
  { key: "chart", labelKey: "widget.chart" },
  { key: "fundamentals", labelKey: "research.subtitle" },
];

export default function MetricsWidget(props: WidgetProps) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const t = useT();
  const tab = (props.params.tab ?? "metrics") as Tab;

  const { data, isLoading, error } = useQuery({
    queryKey: ["metrics", symbol, asset],
    queryFn: () => api.metrics(symbol, asset),
    staleTime: 60_000,
    retry: 1,
  });

  const pm = data?.period_metrics ?? null;

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={asset === "crypto" ? "delayed" : "eod"}
      toolbar={
        <>
          <span className="font-mono text-sm font-bold">{symbol}</span>
          {data?.name && (
            <span className="truncate text-[10px] uppercase tracking-wider text-term-muted">{data.name}</span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {data?.price != null && (
              <span className="flex items-center gap-1.5">
                <span className="font-mono text-xs">{fmtPrice(data.price)}</span>
                {data.change_pct != null && (
                  <span className={cx("font-mono text-[11px]", upDownClass(data.change_pct))}>
                    {fmtPct(data.change_pct)}
                  </span>
                )}
              </span>
            )}
            <div className="flex items-center gap-px rounded border border-term-border">
              {TABS.map(({ key, labelKey }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => props.api.updateParameters({ tab: key })}
                  className={cx(
                    "px-1.5 py-0.5 text-[10px] uppercase",
                    key === tab ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
                  )}
                >
                  {t(labelKey)}
                </button>
              ))}
            </div>
          </div>
        </>
      }
    >
      {tab === "fundamentals" && <FundamentalsTab symbol={symbol} />}

      {tab !== "fundamentals" && isLoading && <SkeletonRows rows={10} />}
      {tab !== "fundamentals" && error && <ErrorState message={(error as Error).message} />}

      {data && !isLoading && tab === "chart" && (
        pm ? (
          <ChartTab symbol={symbol} asset={asset} pm={pm} />
        ) : (
          <p className="p-4 text-xs text-term-muted">
            Not enough price history to compare metrics across periods.
          </p>
        )
      )}

      {data && !isLoading && tab === "metrics" && (
        <div className="space-y-3 p-3">
          {data.note && <p className="text-[11px] leading-relaxed text-term-muted">{data.note}</p>}

          {data.sections.length === 0 && !data.note && (
            <p className="text-xs text-term-muted">No metrics available for {symbol}.</p>
          )}

          <div className="grid grid-cols-1 gap-x-4 gap-y-3 md:grid-cols-2">
            {data.sections.map((section) => (
              <div key={section.key} className="space-y-0.5">
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-term-accent">
                  {section.label}
                </div>
                {section.rows.map((row) => {
                  const { text, cls } = fmtMetric(row);
                  return (
                    <div
                      key={row.label}
                      className="flex items-baseline justify-between gap-2 border-b border-term-border/30 py-1"
                    >
                      <span className="text-[10px] uppercase tracking-wide text-term-muted" title={row.hint}>
                        {row.label}
                      </span>
                      <span className={cx("font-mono text-xs", cls)}>{text}</span>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </WidgetShell>
  );
}
