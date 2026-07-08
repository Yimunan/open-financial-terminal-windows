/** Top-of-book (NBBO) quote: best bid × best ask, optional size-at-touch, and the spread in bps.
 *
 * Data rides the live `ticker` stream (TickerFrame.bid/ask/bid_size/ask_size) — equities get it
 * from Alpaca NBBO quotes (with creds), crypto from the ccxt exchange. Renders nothing until both
 * sides are present. Sizes are share counts for equities and base-asset volume for crypto. */

import { cx, fmtPrice } from "../lib/format";

function fmtSize(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  if (n >= 10) return `${Math.round(n)}`;
  return n.toFixed(2); // fractional crypto base-asset volume
}

export interface QuoteStripProps {
  bid: number | null;
  ask: number | null;
  bidSize?: number | null;
  askSize?: number | null;
  showSizes?: boolean;
  className?: string;
}

export default function QuoteStrip({ bid, ask, bidSize, askSize, showSizes, className }: QuoteStripProps) {
  if (bid == null || ask == null) return null;
  const spread = ask - bid;
  const mid = (ask + bid) / 2;
  const bps = mid > 0 ? (spread / mid) * 10000 : 0;
  const title = `Bid ${fmtPrice(bid)}${bidSize != null ? ` × ${fmtSize(bidSize)}` : ""} · Ask ${fmtPrice(ask)}${askSize != null ? ` × ${fmtSize(askSize)}` : ""} · spread ${fmtPrice(spread)} (${bps.toFixed(1)} bps)`;
  return (
    <span className={cx("flex items-center gap-1 font-mono text-[10px] tabular-nums text-term-muted", className)} title={title}>
      <span className="text-term-up">{fmtPrice(bid)}</span>
      {showSizes && bidSize != null && <span className="text-term-muted/70">×{fmtSize(bidSize)}</span>}
      <span className="text-term-muted/50">/</span>
      <span className="text-term-down">{fmtPrice(ask)}</span>
      {showSizes && askSize != null && <span className="text-term-muted/70">×{fmtSize(askSize)}</span>}
      <span className="text-term-muted/60">({bps.toFixed(1)}bps)</span>
    </span>
  );
}
