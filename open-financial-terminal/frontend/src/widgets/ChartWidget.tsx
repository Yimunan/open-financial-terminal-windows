import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Candle, Timeframe } from "../api/types";
import { CandleChart, type ChartColors, type LineSpec } from "../lib/candleChart";
import { cx, fmtPct, fmtPrice, upDownClass } from "../lib/format";
import { useT, type I18nKey } from "../lib/i18n";
import { themeColor, usePalette } from "../state/settings";
import { equityTopics, subscribeStream, topics } from "../lib/wsClient";
import { useCryptoStreamEnabled, useEquityStreamEnabled } from "../lib/useEquityStream";
import QuoteStrip from "../components/QuoteStrip";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { SkeletonRows, WidgetShell, useWidgetSymbol, type BadgeKind } from "./shell";

interface LiveQuote { last: number | null; bid: number | null; ask: number | null; bid_size: number | null; ask_size: number | null }

const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h", "1d"];
type ChartType = "candles" | "heikin" | "area";
const CHART_TYPES: { key: ChartType; labelKey: I18nKey }[] = [
  { key: "candles", labelKey: "chart.candles" },
  { key: "heikin", labelKey: "chart.heikin" },
  { key: "area", labelKey: "chart.area" },
];
const INDICATOR_CHOICES = [
  { spec: "sma:20", label: "SMA 20" },
  { spec: "ema:50", label: "EMA 50" },
  { spec: "bollinger:20", label: "BB 20" },
  { spec: "rsi:14", label: "RSI 14" },
  { spec: "macd", label: "MACD" },
];
// Fallback overlay colors if the --term-series-* tokens are ever missing (e.g. a custom
// theme that didn't define them) — keeps lines visible instead of producing rgb().
const LINE_COLORS = ["#4f9cf9", "#a78bfa", "#f472b6", "#34d399", "#fb923c", "#e3a008"];

/** Chart overlay line colors, read from the theme tokens so they recolor on theme/CN
 * switches (matching how chartColors() reads up/down/accent). Degrades to LINE_COLORS. */
function seriesColors(): string[] {
  return LINE_COLORS.map((fallback, i) => {
    const c = themeColor(`--term-series-${i + 1}`);
    return c === "rgb()" ? fallback : c;
  });
}

/** Heikin Ashi is a pure presentation transform of OHLC — computed client-side. */
function heikinAshi(candles: Candle[]): Candle[] {
  const out: Candle[] = [];
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i];
    const close = (c.open + c.high + c.low + c.close) / 4;
    const open = i === 0 ? (c.open + c.close) / 2 : (out[i - 1].open + out[i - 1].close) / 2;
    out.push({
      time: c.time,
      open,
      close,
      high: Math.max(c.high, open, close),
      low: Math.min(c.low, open, close),
    });
  }
  return out;
}

/** US equity trading date (America/New_York) as a YYYY-MM-DD string — matches the daily
 * bar's date key, so we can tell today's still-forming bar from a prior completed EOD bar.
 * en-CA formats as YYYY-MM-DD. */
function exchangeToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}

function chartColors(): ChartColors {
  return {
    up: themeColor("--term-up"),
    down: themeColor("--term-down"),
    accent: themeColor("--term-accent"),
    text: themeColor("--term-text"),
    muted: themeColor("--term-muted"),
    border: themeColor("--term-border"),
    panel: themeColor("--term-panel"),
  };
}

export default function ChartWidget(props: WidgetProps) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const t = useT();
  const palette = usePalette();

  const timeframe = (props.params.timeframe ?? "1d") as Timeframe;
  const chartType = (props.params.chartType ?? "candles") as ChartType;
  const indicators = (props.params.indicators ?? []) as string[];
  const setParam = (patch: object) => props.api.updateParameters(patch);

  const [indOpen, setIndOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["bars", symbol, asset, timeframe, indicators],
    queryFn: () => api.bars(symbol, asset, timeframe, indicators),
    refetchInterval: timeframe === "1d" ? 120_000 : 30_000,
    retry: 1,
  });

  // Live top-of-book (NBBO) for the header quote strip: crypto rides the ccxt ticker; equities
  // ride the Alpaca stream when it's enabled (keys set), else there's no realtime quote.
  const equityStream = useEquityStreamEnabled();
  const cryptoStream = useCryptoStreamEnabled();
  const streamed = (asset === "crypto" && cryptoStream) || (asset === "equity" && equityStream);
  const [live, setLive] = useState<LiveQuote | null>(null);
  useEffect(() => {
    setLive(null);
    if (!streamed) return;
    const topic = asset === "crypto" ? topics.ticker(symbol) : equityTopics.ticker(symbol);
    return subscribeStream(topic, (frame) => {
      if (frame.type === "ticker") {
        const { last, bid, ask, bid_size, ask_size } = frame.data;
        setLive({ last, bid, ask, bid_size, ask_size });
      }
    });
  }, [symbol, asset, streamed]);

  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<CandleChart | null>(null);

  // One engine instance per palette: colors are baked into the canvas, so a theme /
  // scheme / accent change tears down and recreates (same policy as before).
  useEffect(() => {
    if (!hostRef.current) return;
    const chart = new CandleChart(hostRef.current, chartColors());
    chartRef.current = chart;
    return () => {
      chartRef.current = null;
      chart.destroy();
    };
  }, [palette]);

  // The 1D series ends on the EOD bar (or the last refetch); fold the live last-traded
  // price into that final candle so it tracks the current price between refetches.
  const liveLast =
    timeframe === "1d" && live?.last != null && Number.isFinite(live.last) ? live.last : null;

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !data) return;
    let candles = data.candles;
    if (liveLast != null && candles.length) {
      const last = candles[candles.length - 1];
      candles = candles.slice();
      candles[candles.length - 1] = {
        ...last,
        close: liveLast,
        high: Math.max(last.high, liveLast),
        low: Math.min(last.low, liveLast),
      };
    }
    const overlays: LineSpec[] = [];
    const lower: LineSpec[] = [];
    const colors = seriesColors();
    let li = 0;
    for (const ind of data.indicators) {
      for (const [label, points] of Object.entries(ind.series)) {
        const spec = { label, points, color: colors[li++ % colors.length] };
        (ind.pane === "price" ? overlays : lower).push(spec);
      }
    }
    chart.setData({
      candles: chartType === "heikin" ? heikinAshi(candles) : candles,
      volume: data.volume,
      overlays: chartType === "area" ? [] : overlays,
      lower,
      mode: chartType === "area" ? "area" : "candles",
      intraday: timeframe !== "1d",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, chartType, palette, liveLast]);

  const q = data?.quote;
  // Match the toolbar price/change to the live-patched last bar when streaming, so the
  // header number and the chart's last candle agree. Change is vs the prior bar's close.
  const prevClose =
    data && data.candles.length >= 2 ? data.candles[data.candles.length - 2].close : null;
  const displayPrice = liveLast ?? q?.price ?? null;
  const displayPct =
    liveLast != null && prevClose ? (liveLast / prevClose - 1) * 100 : (q?.change_pct ?? null);
  // The 1d series is daily bars, but its final bar is today's still-forming bar during the
  // session (polled every 120s) — that's "delayed", not "eod". Only a prior completed trading
  // day reads "eod". Crypto is 24/7 so it's always "delayed". A live stream still wins → "live".
  const lastBarDate = data && data.candles.length ? data.candles[data.candles.length - 1].time : null;
  const lastBarIsToday = typeof lastBarDate === "string" && lastBarDate === exchangeToday();
  const badge: BadgeKind =
    liveLast != null
      ? "live"
      : timeframe === "1d"
        ? asset === "crypto" || lastBarIsToday
          ? "delayed"
          : "eod"
        : "delayed";

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={badge}
      toolbar={
        <>
          <span className="font-mono text-sm font-bold">{symbol}</span>
          {displayPrice != null && (
            <span className={cx("font-mono text-xs", upDownClass(displayPct))}>
              {fmtPrice(displayPrice)} {fmtPct(displayPct)}
            </span>
          )}
          {live && (
            <QuoteStrip bid={live.bid} ask={live.ask} bidSize={live.bid_size} askSize={live.ask_size} showSizes />
          )}
          <div className="ml-2 flex items-center gap-px rounded border border-term-border">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setParam({ timeframe: tf })}
                className={cx(
                  "px-1.5 py-0.5 text-[10px] uppercase",
                  tf === timeframe ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
                )}
              >
                {tf}
              </button>
            ))}
          </div>
          <select
            value={chartType}
            onChange={(e) => setParam({ chartType: e.target.value })}
            aria-label={t("chart.candles")}
            className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-muted"
          >
            {CHART_TYPES.map((c) => (
              <option key={c.key} value={c.key}>
                {t(c.labelKey)}
              </option>
            ))}
          </select>
          <div className="relative">
            <button
              onClick={() => setIndOpen((v) => !v)}
              className={cx(
                "rounded border border-term-border px-1.5 py-0.5 text-[10px]",
                indicators.length ? "text-term-accent" : "text-term-muted hover:text-term-text",
              )}
            >
              {t("chart.indicators")}{indicators.length ? ` (${indicators.length})` : ""}
            </button>
            {indOpen && (
              <div className="absolute left-0 top-6 z-40 min-w-[140px] rounded border border-term-border bg-term-panel p-1 shadow-xl">
                {INDICATOR_CHOICES.map((c) => {
                  const on = indicators.includes(c.spec);
                  return (
                    <button
                      key={c.spec}
                      onClick={() =>
                        setParam({
                          indicators: on ? indicators.filter((i) => i !== c.spec) : [...indicators, c.spec],
                        })
                      }
                      className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-term-border/50"
                    >
                      <span className={cx("h-2 w-2 rounded-sm border border-term-muted", on && "bg-term-accent")} />
                      {c.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </>
      }
    >
      {isLoading && <SkeletonRows rows={8} />}
      {error && (
        <div className="p-4 text-xs text-term-down">
          {(error as Error).message}
          {timeframe !== "1d" && asset === "equity" && (
            <div className="mt-1 text-term-muted">{t("chart.intradayShallow")}</div>
          )}
        </div>
      )}
      <div
        ref={hostRef}
        onClick={() => indOpen && setIndOpen(false)}
        className={cx("h-full w-full", (isLoading || error || !data) && "hidden")}
      />
    </WidgetShell>
  );
}
