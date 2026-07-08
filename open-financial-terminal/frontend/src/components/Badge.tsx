/** Small status pill used for sentiment, data-source, and provider labels.
 * Always pairs a glyph with color so meaning is never conveyed by color alone. */

import { type CSSProperties, type ReactNode } from "react";
import { cx } from "../lib/format";

export type BadgeTone = "up" | "down" | "accent" | "muted";

const TONE_STYLE: Record<BadgeTone, string> = {
  up: "text-term-up border-term-up/50",
  down: "text-term-down border-term-down/50",
  accent: "text-term-accent border-term-accent/50",
  muted: "text-term-muted border-term-border",
};

export function Badge({
  tone = "muted",
  icon,
  children,
  title,
  style,
  className,
}: {
  tone?: BadgeTone;
  icon?: ReactNode;
  children: ReactNode;
  title?: string;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <span
      title={title}
      style={style}
      className={cx(
        "inline-flex shrink-0 items-center gap-0.5 rounded border px-1 py-px text-[9px] font-semibold uppercase tracking-wider",
        TONE_STYLE[tone],
        className,
      )}
    >
      {icon && <span aria-hidden>{icon}</span>}
      {children}
    </span>
  );
}

/** Maps a sentiment string to a Badge tone + directional glyph. */
export function sentimentBadge(sentiment: string): { tone: BadgeTone; icon: string } {
  if (sentiment === "bullish") return { tone: "up", icon: "▲" };
  if (sentiment === "bearish") return { tone: "down", icon: "▼" };
  return { tone: "muted", icon: "●" };
}
