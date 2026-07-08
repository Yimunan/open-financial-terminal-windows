/** Number/price formatting helpers shared by every widget. */

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Price with sensible decimals: 2 for >=10, more for small (sub-dollar / crypto). */
export function fmtPrice(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 2 : abs >= 10 ? 2 : abs >= 0.1 ? 4 : 6;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(v: number | null | undefined, signed = true): string {
  if (v == null || !isFinite(v)) return "—";
  const s = signed && v > 0 ? "+" : "";
  return `${s}${v.toFixed(2)}%`;
}

/** Compact volume/notional: 1.2K, 3.4M, 5.6B. */
export function fmtCompact(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

/** Order/trade quantity: compact above 1k, fractional precision below (0.0392 BTC ≠ 0). */
export function fmtQty(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  if (Math.abs(v) >= 1000) return fmtCompact(v);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(4);
}

export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

/** Compact "time ago" from unix seconds: 12s, 5m, 3h, 2d. */
export function fmtAgo(epochSeconds: number | null | undefined, nowMs = Date.now()): string {
  if (!epochSeconds) return "";
  const s = Math.max(0, Math.floor(nowMs / 1000 - epochSeconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

/** Absolute timestamp from unix seconds, e.g. "Jun 19, 14:32" (24h, no year). */
export function fmtStamp(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function upDownClass(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-term-muted";
  return v > 0 ? "text-term-up" : "text-term-down";
}
