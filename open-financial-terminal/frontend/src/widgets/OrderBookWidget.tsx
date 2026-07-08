import { useEffect, useMemo, useRef, useState } from "react";
import type { BookFrame } from "../api/types";
import { cx, fmtPrice, fmtQty } from "../lib/format";
import { useT } from "../lib/i18n";
import { DepthChart, type DepthColors } from "../lib/depthChart";
import { subscribeStream, depthBookTopic } from "../lib/wsClient";
import { useDepthStatus } from "../lib/useEquityStream";
import { themeColor, usePalette } from "../state/settings";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { IconButton, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState } from "../components/States";

const LEVELS = 15;
type BookView = "ladder" | "depth";

function depthColors(): DepthColors {
  return {
    up: themeColor("--term-up"),
    down: themeColor("--term-down"),
    text: themeColor("--term-text"),
    muted: themeColor("--term-muted"),
    border: themeColor("--term-border"),
    panel: themeColor("--term-panel"),
  };
}

/** Depth heatmap ladder: per-row background width = cumulative size, opacity ∝ level size.
 * Liquidity walls (size ≥ 3× the median level) get a brighter band.
 */
function Side({ levels, side }: { levels: [number, number][]; side: "bid" | "ask" }) {
  const { rows, maxCum, median } = useMemo(() => {
    const slice = levels.slice(0, LEVELS);
    let cum = 0;
    const rows = slice.map(([price, size]) => ({ price, size, cum: (cum += size) }));
    const sizes = slice.map(([, s]) => s).sort((a, b) => a - b);
    return { rows, maxCum: cum || 1, median: sizes[Math.floor(sizes.length / 2)] || 1 };
  }, [levels]);

  const color = side === "bid" ? "--term-up" : "--term-down";

  return (
    <div className="flex-1">
      {rows.map((r, i) => {
        const wall = r.size >= 3 * median;
        const depthPct = (r.cum / maxCum) * 100;
        const intensity = Math.min(0.12 + (r.size / (median * 4)) * 0.5, 0.65);
        return (
          <div key={`${r.price}-${i}`} className="relative flex justify-between px-2 py-px font-mono text-[11px] leading-5">
            <div
              className="absolute inset-y-0 right-0"
              style={{
                width: `${depthPct}%`,
                backgroundColor: `rgb(var(${color}) / ${wall ? Math.max(intensity, 0.45) : intensity})`,
                ...(side === "bid" ? { right: 0 } : { left: 0 }),
              }}
            />
            <span className="relative z-10" style={{ color: `rgb(var(${color}))` }}>
              {fmtPrice(r.price)}
            </span>
            <span className={`relative z-10 ${wall ? "font-bold text-term-text" : "text-term-muted"}`}>
              {fmtQty(r.size)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** Cumulative bid/ask depth profile on a custom canvas (lib/depthChart.ts). */
function DepthView({ book }: { book: BookFrame }) {
  const palette = usePalette();
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<DepthChart | null>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const chart = new DepthChart(hostRef.current, depthColors());
    chartRef.current = chart;
    return () => {
      chartRef.current = null;
      chart.destroy();
    };
  }, [palette]);

  useEffect(() => {
    chartRef.current?.setData({ bids: book.bids, asks: book.asks });
  }, [book, palette]);

  return <div ref={hostRef} className="h-full w-full" />;
}

const ICON = "h-3.5 w-3.5";

export default function OrderBookWidget(props: WidgetProps) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const t = useT();
  const [book, setBook] = useState<BookFrame | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const { token, source, enabled } = useDepthStatus(asset);
  const streamed = enabled && !!token;
  const synthetic = source === "sim"; // modelled depth around a real mid — tag it honestly

  const view = (props.params.bookView ?? "ladder") as BookView;
  const setView = (v: BookView) => props.api.updateParameters({ bookView: v });

  useEffect(() => {
    if (!streamed) return;
    setBook(null);
    setStatus(null);
    const topic = depthBookTopic(symbol, token);
    if (!topic) return;
    return subscribeStream(topic, (frame) => {
      if (frame.type === "book") {
        setBook(frame.data);
        setStatus(null);
      } else if (frame.type === "status") {
        setStatus(frame.data.state);
      } else if (frame.type === "error") {
        setStatus(frame.data.message);
      }
    });
  }, [symbol, asset, streamed, token]);

  const mid =
    book && book.bids.length && book.asks.length ? (book.bids[0][0] + book.asks[0][0]) / 2 : null;
  const spread = book && book.bids.length && book.asks.length ? book.asks[0][0] - book.bids[0][0] : null;

  const toggleBtn = (v: BookView, title: string, icon: JSX.Element) => (
    <IconButton
      label={title}
      title={title}
      onClick={() => setView(v)}
      className={cx(
        "flex h-5 w-6 items-center justify-center",
        view === v && "bg-term-accent/20 text-term-accent",
      )}
    >
      {icon}
    </IconButton>
  );

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={streamed ? "live" : "eod"}
      toolbar={
        <>
          <span className="font-mono text-sm font-bold">{symbol}</span>
          {streamed && synthetic && (
            <span
              className="ml-2 rounded border border-term-border px-1 text-[10px] uppercase tracking-wider text-term-muted"
              title={t("book.syntheticHint")}
            >
              {t("book.synthetic")}
            </span>
          )}
          {streamed && (
            <div className="ml-2 flex items-center gap-px rounded border border-term-border">
              {toggleBtn(
                "ladder",
                "Ladder",
                <svg viewBox="0 0 16 16" className={ICON} fill="none" stroke="currentColor" strokeWidth="1.5">
                  <line x1="2" y1="4" x2="14" y2="4" />
                  <line x1="2" y1="8" x2="14" y2="8" />
                  <line x1="2" y1="12" x2="14" y2="12" />
                </svg>,
              )}
              {toggleBtn(
                "depth",
                "Depth",
                <svg viewBox="0 0 16 16" className={ICON} fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M1 13 L6 13 L6 7 L8 7 L8 4" strokeLinejoin="round" />
                  <path d="M15 13 L10 13 L10 7 L8 7 L8 4" strokeLinejoin="round" />
                </svg>,
              )}
            </div>
          )}
        </>
      }
    >
      {!enabled ? (
        <EmptyState title={t("book.depthOff")} />
      ) : !book ? (
        <EmptyState title={status ? t("book.stream", { x: status }) : t("book.connecting", { x: symbol })} />
      ) : (
        <div className="flex h-full flex-col">
          <div className="border-b border-term-border px-2 py-1 text-center font-mono text-xs">
            <span className="text-term-text">{fmtPrice(mid)}</span>
            <span className="ml-2 text-term-muted">{t("book.spread")} {fmtPrice(spread)}</span>
          </div>
          {view === "ladder" ? (
            <>
              <div className="flex border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
                <div className="flex-1 px-2 py-1">{t("book.bidSize")}</div>
                <div className="flex-1 px-2 py-1 text-right">{t("book.askSize")}</div>
              </div>
              <div className="flex min-h-0 flex-1 overflow-auto">
                <Side levels={book.bids} side="bid" />
                <div className="w-px bg-term-border" />
                <Side levels={book.asks} side="ask" />
              </div>
            </>
          ) : (
            <div className="min-h-0 flex-1 p-1">
              <DepthView book={book} />
            </div>
          )}
        </div>
      )}
    </WidgetShell>
  );
}
