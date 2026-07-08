/** Shared KPI card for the Backtest / Strategy Lab / Factor Performance dashboards.
 * Elevated surface + tone coloring; `emphasis` makes a primary KPI larger with an accent rule. */

import { cx } from "../lib/format";

export type MetricTone = "up" | "down" | null;

export function MetricCard({
  label,
  value,
  tone = null,
  emphasis = false,
  className,
}: {
  label: string;
  value: string;
  tone?: MetricTone;
  emphasis?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "rounded border border-term-border bg-term-elev px-2.5 py-2 shadow-elev-1",
        emphasis && "shadow-[inset_2px_0_0_rgb(var(--term-accent)),0_1px_2px_rgb(var(--term-shadow)/0.3)]",
        className,
      )}
    >
      <div className="mb-0.5 text-[11px] uppercase tracking-wider text-term-muted">{label}</div>
      <div
        className={cx(
          "font-mono font-semibold leading-tight",
          emphasis ? "text-[18px]" : "text-[14px]",
          tone === "up" ? "text-term-up" : tone === "down" ? "text-term-down" : "text-term-text",
        )}
      >
        {value}
      </div>
    </div>
  );
}

/** Shared table styling so the runs / leaderboard / regime / correlation tables match.
 * Header is an elevated sticky strip; rows get a clear hover and an accent active state. */
export const TABLE = {
  /** <thead> cell: elevated, sticky, uppercase micro-label. Add text-right for numerics. */
  th: "sticky top-0 z-10 bg-term-elev px-2 py-1.5 text-[11px] font-medium uppercase tracking-wider text-term-muted",
  /** <tbody> row: bottom divider + hover. */
  row: "border-b border-term-border/30 transition-colors hover:bg-term-border/40",
  /** active/selected row marker (compose with `row`). */
  rowActive: "bg-term-accent/15 shadow-[inset_2px_0_0_rgb(var(--term-accent))]",
} as const;
