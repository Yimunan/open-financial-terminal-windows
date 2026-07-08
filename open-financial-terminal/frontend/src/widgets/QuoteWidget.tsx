import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { TickerFrame } from "../api/types";
import FlashCell from "../components/FlashCell";
import Sparkline from "../components/Sparkline";
import { cx, fmtCompact, fmtPct, fmtPrice, fmtQty, upDownClass } from "../lib/format";
import { equityTopics, subscribeStream, topics } from "../lib/wsClient";
import { useCryptoStreamEnabled, useEquityStreamEnabled } from "../lib/useEquityStream";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState, ErrorState } from "../components/States";

/** One label/value stat cell. */
function Stat({ label, children, tone }: { label: string; children: React.ReactNode; tone?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 px-2 py-1">
      <span className="text-[10px] uppercase tracking-wider text-term-muted">{label}</span>
      <span className={cx("font-mono text-xs tabular-nums text-term-text", tone)}>{children}</span>
    </div>
  );
}

/** Level-1 price quote for the linked symbol: last / change / bid·ask / spread / OHLC / volume.
 * Streams live (last + NBBO bid/ask) for crypto and — with Alpaca keys — equities; otherwise the
 * quote is polled (delayed/EOD) and bid/ask are unavailable, shown honestly as "—". */
export default function QuoteWidget(props: WidgetProps) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const equityStream = useEquityStreamEnabled();
  const cryptoStream = useCryptoStreamEnabled();
  const streamed = (asset === "crypto" && cryptoStream) || (asset === "equity" && equityStream);

  const { data: q, isLoading, error } = useQuery({
    queryKey: ["quote", symbol, asset, "full"],
    queryFn: () => api.quote(symbol, asset, 40),
    refetchInterval: 60_000,
    retry: 1,
  });

  const [live, setLive] = useState<TickerFrame | null>(null);
  useEffect(() => {
    setLive(null);
    if (!streamed) return;
    const topic = asset === "crypto" ? topics.ticker(symbol) : equityTopics.ticker(symbol);
    return subscribeStream(topic, (frame) => {
      if (frame.type === "ticker") setLive(frame.data);
    });
  }, [symbol, asset, streamed]);

  // Live last/bid/ask/pct override the polled quote; absolute change + OHLC come from /api/quote.
  const price = live?.last ?? q?.price ?? null;
  const pct = live?.change_pct ?? q?.change_pct ?? null;
  const change = q?.change ?? null;
  const bid = live?.bid ?? null;
  const ask = live?.ask ?? null;
  const spread = bid != null && ask != null ? ask - bid : null;
  const spreadBps = spread != null && price ? (spread / price) * 10_000 : null;
  const volume = live?.base_volume ?? q?.volume ?? null;
  // crypto is always streamed here, so the non-streamed branch is an equity on polled EOD data.
  const badge = streamed ? "live" : "eod";

  const bidAsk = (p: number | null, size: number | null | undefined) =>
    p == null ? "—" : size != null ? `${fmtPrice(p)} × ${fmtQty(size)}` : fmtPrice(p);

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={badge}
      toolbar={
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-sm font-bold">{symbol}</span>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">{asset}</span>
        </div>
      }
    >
      {isLoading && !q && <SkeletonRows />}
      {error && <ErrorState message={(error as Error).message} />}
      {!isLoading && !error && q && price == null && <EmptyState title={`No quote for ${symbol}`} />}
      {q && price != null && (
        <div className="flex flex-col gap-2 p-2">
          {/* headline: last + change */}
          <div className="flex items-end justify-between gap-3">
            <FlashCell value={price}>
              <span className="font-mono text-2xl font-bold tabular-nums text-term-text">{fmtPrice(price)}</span>
            </FlashCell>
            <div className={cx("text-right font-mono text-sm leading-tight tabular-nums", upDownClass(pct))}>
              <div>{change != null ? fmtPrice(change) : "—"}</div>
              <div>{fmtPct(pct)}</div>
            </div>
          </div>

          {q.spark && q.spark.length > 1 && <Sparkline points={q.spark} />}

          {/* level-1 grid */}
          <div className="grid grid-cols-2 gap-x-4 rounded border border-term-border/50 bg-term-sunken/40 py-0.5">
            <Stat label="Bid" tone="text-term-up">{bidAsk(bid, live?.bid_size)}</Stat>
            <Stat label="Ask" tone="text-term-down">{bidAsk(ask, live?.ask_size)}</Stat>
            <Stat label="Spread">
              {spread != null ? `${fmtPrice(spread)}${spreadBps != null ? ` · ${spreadBps.toFixed(1)}bp` : ""}` : "—"}
            </Stat>
            <Stat label="Volume">{fmtCompact(volume)}</Stat>
            <Stat label="High">{fmtPrice(q.high ?? null)}</Stat>
            <Stat label="Low">{fmtPrice(q.low ?? null)}</Stat>
          </div>

          <div className="px-1 text-[10px] text-term-muted">
            {streamed ? "Live" : `As of ${q.asof ?? "—"}`}
            {!streamed && " · EOD (yfinance) — no bid/ask; enter Alpaca keys for live"}
          </div>
        </div>
      )}
    </WidgetShell>
  );
}
