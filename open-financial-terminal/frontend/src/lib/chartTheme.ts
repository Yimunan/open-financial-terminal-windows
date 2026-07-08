import { themeColor } from "../state/settings";
import type { ChartColors } from "./candleChart";
import type { GraphColors } from "./graphCanvas";

/** Theme tokens packaged for the custom canvas chart engine (candleChart / depthChart). */
export function chartColors(): ChartColors {
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

/** Theme tokens for the agent-workflow GraphCanvas (adds the page bg). */
export function graphColors(): GraphColors {
  return { ...chartColors(), bg: themeColor("--term-bg") };
}

export function seriesColors() {
  return {
    up: themeColor("--term-up"),
    down: themeColor("--term-down"),
    accent: themeColor("--term-accent"),
    accentSoft: themeColor("--term-accent", 0.3),
    volume: themeColor("--term-border"),
    priceLines: [
      themeColor("--term-accent"),
      themeColor("--term-series-1"),
      themeColor("--term-series-2"),
      themeColor("--term-series-3"),
      themeColor("--term-series-4"),
    ],
    lowerLines: [themeColor("--term-accent"), themeColor("--term-series-1"), themeColor("--term-down")],
  };
}
