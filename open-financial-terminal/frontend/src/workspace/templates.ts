/** Built-in workspace templates: ready-made bento layouts for common desks.
 *
 * Each template is a `build(api)` function that arranges panels with `api.addPanel`,
 * exactly like `seedDefaultLayout` in `layoutUtil.ts` — authored programmatically rather
 * than as serialized Dockview JSON so they stay robust to Dockview's internal format.
 * Widgets sharing a link `channel` (red/blue/green) track the same symbol; templates use
 * this to wire a coherent, pre-linked desk. Applied via `useWorkspace.applyBuiltinTemplate`.
 */

import type { DockviewApi } from "dockview";
import { widgetTitle } from "../lib/i18n";
import { useSettings } from "../state/settings";
import { WIDGETS, type WidgetParams, type WidgetType } from "./widgetRegistry";

export interface BuiltinTemplate {
  id: string;
  name: string;
  description: string;
  build: (api: DockviewApi) => void;
}

/** Mints a builder bound to a fresh, collision-free panel-id counter so every panel within a
 * template (and across rapid re-applies) gets a unique id, matching `openWidget`'s scheme. */
function builder(steps: (add: AddPanel) => void): (api: DockviewApi) => void {
  return (api) => {
    let seq = 0;
    const stamp = Date.now();
    const add: AddPanel = (type, opts = {}) => {
      seq += 1;
      const lang = useSettings.getState().language;
      return api.addPanel({
        id: `${type}-${stamp}-${seq}`,
        component: type,
        title: opts.title ?? widgetTitle(type, lang),
        // Persist an explicit custom title as a `label` param so it survives module-name
        // retitling on reload; panels without one recompute from the registry title (and so
        // track module renames automatically).
        params: { channel: WIDGETS[type].defaultChannel, ...(opts.title ? { label: opts.title } : {}), ...opts.params },
        ...(opts.position ? { position: opts.position } : {}),
      });
    };
    steps(add);
  };
}

type AddOpts = {
  title?: string;
  params?: WidgetParams;
  position?: { referencePanel: string; direction: "left" | "right" | "above" | "below" | "within" };
};
type AddPanel = (type: WidgetType, opts?: AddOpts) => ReturnType<DockviewApi["addPanel"]>;

// Space budget for templates. Widgets aren't equal: charts/analytics are the "hero" and should own
// the middle; watchlists/boards/libraries are narrow rails; and time & sales, quotes and order books
// are compact strips that only need a slice of height (a full-height tape wastes the desk — the
// hero chart should tower over it). We only constrain the small ones (rail width, side width, strip
// heights); the hero fills whatever remains, so it stays the biggest panel at any window size.
const COL_RAIL = 300;   // narrow left rail: watchlists, market boards, libraries
const COL_WIDE_RAIL = 360; // a rail that carries tables (profile fundamentals, portfolio holdings)
const COL_SIDE = 440;   // secondary right column; the hero column fills the middle
const H_STRIP = 220;    // compact strip: time & sales, quote, a secondary feed under a taller panel
const H_BOOK = 300;     // order book / depth ladder — a touch taller than a strip

export const BUILTIN_TEMPLATES: BuiltinTemplate[] = [
  {
    id: "equities-day-trading",
    name: "Equities Day-Trading",
    description: "Watchlist, intraday chart, time & sales, quote and news — linked on the red channel.",
    build: builder((add) => {
      const watchlist = add("watchlist", { params: { channel: "red" } });
      const chart = add("chart", {
        title: "Chart · 1m",
        params: { channel: "red", timeframe: "1m", chartType: "candles" },
        position: { referencePanel: watchlist.id, direction: "right" },
      });
      const timesales = add("timesales", {
        params: { channel: "red" },
        position: { referencePanel: chart.id, direction: "below" },
      });
      const quote = add("quote", {
        params: { channel: "red" },
        position: { referencePanel: chart.id, direction: "right" },
      });
      add("news", {
        params: { channel: "red" },
        position: { referencePanel: quote.id, direction: "below" },
      });
      watchlist.api.setSize({ width: COL_RAIL });
      quote.api.setSize({ width: COL_SIDE, height: H_STRIP }); // quote is a compact strip; news fills below
      timesales.api.setSize({ height: H_STRIP });              // tape strip under a towering chart
    }),
  },
  {
    id: "crypto-trading",
    name: "Crypto Trading",
    description: "Crypto watchlist, 1m chart, order book, time & sales and market-making — linked on the blue channel.",
    build: builder((add) => {
      const watchlist = add("watchlist", { title: "Crypto Watchlist", params: { channel: "blue" } });
      const chart = add("chart", {
        title: "Chart · 1m",
        params: { channel: "blue", asset: "crypto", timeframe: "1m", chartType: "candles" },
        position: { referencePanel: watchlist.id, direction: "right" },
      });
      const timesales = add("timesales", {
        params: { channel: "blue" },
        position: { referencePanel: chart.id, direction: "below" },
      });
      const orderbook = add("orderbook", {
        params: { channel: "blue" },
        position: { referencePanel: chart.id, direction: "right" },
      });
      const mm = add("market_making", {
        params: { channel: "blue" },
        position: { referencePanel: orderbook.id, direction: "below" },
      });
      watchlist.api.setSize({ width: COL_RAIL });
      orderbook.api.setSize({ width: COL_SIDE, height: H_BOOK }); // book gets the ladder height it needs
      timesales.api.setSize({ height: H_STRIP });                 // tape strip; chart towers above
      mm.api.setSize({ height: H_STRIP });                        // market-making tucks under the book
    }),
  },
  {
    id: "research-due-diligence",
    name: "Research & Due Diligence",
    description: "Research, daily chart, public filings, news, committees and assistant for deep dives.",
    build: builder((add) => {
      const profile = add("metrics", { params: { channel: "red", tab: "fundamentals" } });
      const chart = add("chart", {
        title: "Chart · 1d",
        params: { channel: "red", timeframe: "1d", chartType: "candles" },
        position: { referencePanel: profile.id, direction: "right" },
      });
      const filings = add("filings", {
        params: { channel: "red" },
        position: { referencePanel: chart.id, direction: "below" },
      });
      const news = add("news", {
        params: { channel: "red" },
        position: { referencePanel: chart.id, direction: "right" },
      });
      const committee = add("committee", {
        params: { channel: "red" },
        position: { referencePanel: news.id, direction: "below" },
      });
      add("assistant", {
        params: { channel: "red" },
        position: { referencePanel: news.id, direction: "below" },
      });
      profile.api.setSize({ width: COL_WIDE_RAIL });           // fundamentals tables need room
      news.api.setSize({ width: COL_SIDE, height: H_STRIP });  // news + committee are strips…
      committee.api.setSize({ height: H_STRIP });              // …so the assistant chat fills the rest
      filings.api.setSize({ height: H_STRIP });                // filings strip; the 1d chart dominates
    }),
  },
  {
    id: "quant-research",
    name: "Quant Research",
    description: "Factor library, factor performance, backtest, sandbox and strategies — the alpha-dev loop.",
    build: builder((add) => {
      const factors = add("factors");
      const monitor = add("factor_monitor", {
        position: { referencePanel: factors.id, direction: "right" },
      });
      add("backtest", {
        position: { referencePanel: monitor.id, direction: "below" },
      });
      const sandbox = add("sandbox", {
        params: { channel: "red" },
        position: { referencePanel: monitor.id, direction: "right" },
      });
      const strategies = add("strategies", {
        params: { channel: "red" },
        position: { referencePanel: sandbox.id, direction: "below" },
      });
      factors.api.setSize({ width: COL_RAIL });
      sandbox.api.setSize({ width: COL_SIDE });     // sandbox editor gets a proper column
      monitor.api.setSize({ height: H_STRIP });     // perf strip; the backtest run fills below it
      strategies.api.setSize({ height: H_STRIP });  // strategies list tucks under the sandbox
    }),
  },
  {
    id: "portfolio-risk",
    name: "Portfolio & Risk",
    description: "Portfolio, risk attribution, metrics, portfolio builder and paper trading for book monitoring.",
    build: builder((add) => {
      const portfolio = add("portfolio");
      const risk = add("risk_attribution", {
        position: { referencePanel: portfolio.id, direction: "right" },
      });
      const metrics = add("metrics", {
        params: { channel: "red" },
        position: { referencePanel: risk.id, direction: "below" },
      });
      const portfolios = add("portfolios", {
        position: { referencePanel: risk.id, direction: "right" },
      });
      const paper = add("paper", {
        params: { channel: "red" },
        position: { referencePanel: portfolios.id, direction: "below" },
      });
      portfolio.api.setSize({ width: COL_WIDE_RAIL });         // holdings table needs room
      portfolios.api.setSize({ width: COL_SIDE });
      metrics.api.setSize({ height: H_STRIP });                // metrics strip; risk charts dominate
      paper.api.setSize({ height: H_STRIP });                  // paper ticket tucks under the builder
    }),
  },
  {
    id: "macro-markets",
    name: "Macro & Markets Overview",
    description: "Market board, macro, market & macro topic news, new listings and news — a top-down view.",
    build: builder((add) => {
      const board = add("market_board");
      const macro = add("macro", {
        position: { referencePanel: board.id, direction: "right" },
      });
      const macronews = add("topicnews", {
        title: "Macro News",
        params: { channel: "none", category: "macro", label: "Macro" },
        position: { referencePanel: macro.id, direction: "below" },
      });
      const listings = add("listings", {
        params: { channel: "red" },
        position: { referencePanel: macro.id, direction: "right" },
      });
      const marketnews = add("topicnews", {
        title: "Market News",
        params: { channel: "none", category: "market", label: "Market" },
        position: { referencePanel: listings.id, direction: "below" },
      });
      board.api.setSize({ width: COL_RAIL });
      listings.api.setSize({ width: COL_SIDE });
      macronews.api.setSize({ height: H_STRIP });   // news strip; macro charts dominate the middle
      marketnews.api.setSize({ height: H_STRIP });  // news strip; new-listings feed sits above it
    }),
  },
  {
    id: "algo-execution",
    name: "Algo / Execution Desk",
    description: "Algo trading, 5m chart, order book, paper trading and agent workflow for automated execution.",
    build: builder((add) => {
      const algo = add("algo_trading", { params: { channel: "red" } });
      const chart = add("chart", {
        title: "Chart · 5m",
        params: { channel: "red", timeframe: "5m", chartType: "candles" },
        position: { referencePanel: algo.id, direction: "right" },
      });
      const orderbook = add("orderbook", {
        // Red like the rest of this desk, so the book follows the algo/chart symbol (was an
        // orphaned blue book that tracked nothing selected in this layout).
        params: { channel: "red" },
        position: { referencePanel: chart.id, direction: "below" },
      });
      const paper = add("paper", {
        params: { channel: "red" },
        position: { referencePanel: chart.id, direction: "right" },
      });
      add("agent", {
        params: { channel: "red" },
        position: { referencePanel: paper.id, direction: "below" },
      });
      algo.api.setSize({ width: COL_WIDE_RAIL });   // algo config + status column
      paper.api.setSize({ width: COL_SIDE });
      orderbook.api.setSize({ height: H_BOOK });    // book strip under the 5m chart, which dominates
    }),
  },
];
