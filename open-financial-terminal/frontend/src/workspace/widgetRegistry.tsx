/** Single source of truth for widget types: Dockview component map, titles, default
 * link channels, and what the command bar offers. Adding a widget = one entry here
 * plus its component file.
 */

import type { FunctionComponent } from "react";
import type { IDockviewPanelProps } from "dockview";
import ErrorBoundary from "../components/ErrorBoundary";
import type { Asset, SandboxMode, SandboxTrust, Timeframe } from "../api/types";
import type { Channel } from "../state/linking";
import { INTENT_KINDS, type AcceptMap, type IntentKind } from "../state/intents";
import { withResizeGrip } from "./ResizeGrip";

import WatchlistWidget from "../widgets/WatchlistWidget";
import MarketBoardWidget from "../widgets/MarketBoardWidget";
import ChartWidget from "../widgets/ChartWidget";
import QuoteWidget from "../widgets/QuoteWidget";
import OrderBookWidget from "../widgets/OrderBookWidget";
import OptionChainWidget from "../widgets/OptionChainWidget";
import OptionSurfaceWidget from "../widgets/OptionSurfaceWidget";
import TimeSalesWidget from "../widgets/TimeSalesWidget";
import NewsWidget from "../widgets/NewsWidget";
import TopicNewsWidget from "../widgets/TopicNewsWidget";
import ScreenerWidget from "../widgets/ScreenerWidget";
import BacktestWidget from "../widgets/BacktestWidget";
import PortfolioWidget from "../widgets/PortfolioWidget";
import AssistantWidget from "../widgets/AssistantWidget";
import PaperTradingWidget from "../widgets/PaperTradingWidget";
import AgentGraphWidget from "../widgets/AgentGraphWidget";
import FactorLibrary from "../widgets/FactorLibrary";
import StrategyLibrary from "../widgets/StrategyLibrary";
import ModelRepository from "../widgets/ModelRepository";
import PortfolioBuilder from "../widgets/PortfolioBuilder";
import SandboxWidget from "../widgets/SandboxWidget";
import FactorMonitorWidget from "../widgets/FactorMonitorWidget";
import PublicFilingsWidget from "../widgets/PublicFilingsWidget";
import NewListingsWidget from "../widgets/NewListingsWidget";
import MacroWidget from "../widgets/MacroWidget";
import FiccWidget from "../widgets/FiccWidget";
import CommitteeWidget from "../widgets/CommitteeWidget";
import MetricsWidget from "../widgets/MetricsWidget";
import ChartStudioWidget from "../widgets/ChartStudioWidget";
import MarketMakingWidget from "../widgets/MarketMakingWidget";
import RiskAttributionWidget from "../widgets/RiskAttributionWidget";
import AlgoTradingWidget from "../widgets/AlgoTradingWidget";
import ResearchLoopWidget from "../widgets/ResearchLoopWidget";
import MapWidget from "../widgets/MapWidget";

export interface WidgetParams {
  channel?: Channel;
  symbol?: string;
  asset?: Asset;
  timeframe?: Timeframe;
  chartType?: "candles" | "heikin" | "area";
  indicators?: string[];
  initialQuery?: string;
  category?: string; // topicnews: which news topic (built-in key or user topic key)
  label?: string; // topicnews: display label / panel title
  expiry?: string; // options chain: selected expiration (YYYY-MM-DD)
  strike?: number; // options: strike
  right?: import("../api/types").OptionRight; // options: call/put
  // sandbox: prefill when opened from a library ("Open in Sandbox" / "+ New")
  initialMode?: SandboxMode;
  initialTrust?: SandboxTrust;
  initialCode?: string;
  initialName?: string;
  initialMeta?: Record<string, unknown>;
  // Cross-module `send` hand-off payloads (written by dispatchIntent, consumed once on mount by the
  // target widget — same lifecycle as initialQuery). See state/intents.ts SendPayload.
  symbols?: string[];
  incomingUniverse?: string;
  incomingSymbols?: string[];
  incomingFactor?: string;
  incomingWeights?: Record<string, number>;
  incomingStrategy?: string;
  incomingParams?: Record<string, unknown>;
  // A precomputed result streamed in by the Agent Workflow's `backtest` node — rendered directly
  // (no re-run) by BacktestWidget, then cleared. See widgets/AgentGraphWidget.tsx reveal logic.
  incomingResult?: import("../api/types").BacktestResponse;
  // Likewise from the `screen` node — a precomputed screen result rendered directly by ScreenerWidget
  // (no /api/ask), so a stopped/paused workflow leaves nothing running in the Screener.
  incomingScreen?: import("../api/types").ScreenResponse;
  // From the Agent Workflow's `execution` node — planned (dry-run or armed) orders shown once as a
  // read-only banner in PaperTradingWidget, then cleared. Same consume-once lifecycle as incomingResult.
  incomingOrders?: { orders: string[]; armed: boolean };
  // From the `research` node — its best strategy + scorecard/target outcome, shown once as a summary
  // banner in ResearchLoopWidget, then cleared.
  incomingResearch?: {
    best?: { label?: string; metrics?: { sharpe?: number; calmar?: number }; n_checks_passed?: number; passed?: boolean };
    n_iterations?: number;
    target_sharpe?: number;
    target_met?: boolean;
    best_sharpe?: number;
  };
  // From the `portfolio` node — the accumulated pipeline spec (universe/factor/mode/…), shown once as
  // a config banner in PortfolioBuilder, then cleared. Non-destructive (does not touch an open draft).
  incomingSpec?: { universe?: string; factor?: string; mode?: string; top_pct?: number; years?: number; initial?: number };
  // Append-only stream of live ResearchFrames forwarded from a running `research` node — the Research
  // Loop module mirrors the agent's loop iteration-by-iteration. See AgentGraphWidget run() forwarding.
  incomingResearchFrames?: import("../api/types").ResearchFrame[];
  [key: string]: unknown;
}

export type WidgetProps = IDockviewPanelProps<WidgetParams>;

export type WidgetType =
  | "watchlist"
  | "market_board"
  | "chart"
  | "quote"
  | "orderbook"
  | "options_chain"
  | "options_surface"
  | "timesales"
  | "news"
  | "topicnews"
  | "screener"
  | "backtest"
  | "portfolio"
  | "assistant"
  | "paper"
  | "agent"
  | "factors"
  | "strategies"
  | "models"
  | "portfolios"
  | "sandbox"
  | "factor_monitor"
  | "filings"
  | "listings"
  | "macro"
  | "ficc"
  | "committee"
  | "metrics"
  | "chart_studio"
  | "market_making"
  | "risk_attribution"
  | "algo_trading"
  | "research_loop"
  | "map";

/** Optional metadata that makes a widget reachable by the Assistant's control loop. `description`
 * tells the agent what the module is for; `params` lists the params it can set (key → human hint).
 * Adding this field to a registry entry is all it takes to let the Assistant open/configure it. */
export interface AssistantMeta {
  description: string;
  params?: Record<string, string>;
}

interface WidgetMeta {
  title: string;
  component: FunctionComponent<WidgetProps>;
  defaultChannel: Channel;
  assistant?: AssistantMeta;
  /** Which cross-module `send` payload kinds this widget accepts, and how each maps to its params.
   * Declaring a kind here automatically makes the widget a target in every source's "Send to…"
   * menu (sendTargets() derives the menu from this — no hardcoded lists). */
  accepts?: AcceptMap;
}

export const WIDGETS: Record<WidgetType, WidgetMeta> = {
  watchlist: {
    title: "Watchlist", component: WatchlistWidget, defaultChannel: "red",
    assistant: { description: "Live watchlist of symbols with sparklines", params: { channel: "red/blue/green/none" } },
    accepts: {
      screen_result: (p) => ({ symbols: p.symbols, asset: p.asset }),
      symbols: (p) => ({ symbols: p.symbols, asset: p.asset }),
    },
  },
  market_board: { title: "Market Board", component: MarketBoardWidget, defaultChannel: "none" },
  chart: {
    title: "Chart", component: ChartWidget, defaultChannel: "red",
    assistant: {
      description: "Price chart (candles/heikin/area) with indicators",
      params: {
        symbol: "ticker e.g. AAPL or BTC/USDT", asset: "equity/crypto",
        timeframe: "1m/5m/15m/1h/1d", chartType: "candles/heikin/area",
        indicators: "array e.g. [\"sma:50\",\"ema:20\",\"rsi:14\",\"macd\",\"bollinger:20\"]",
        channel: "red/blue/green/none",
      },
    },
  },
  quote: {
    title: "Quote", component: QuoteWidget, defaultChannel: "red",
    assistant: { description: "Detailed quote panel for one symbol", params: { symbol: "ticker", asset: "equity/crypto", channel: "red/blue/green/none" } },
  },
  orderbook: {
    // Default to the primary selection channel (red) so a palette-opened book follows the symbol
    // your watchlist / command bar pick — same channel as chart/quote/watchlist.
    title: "Order Book", component: OrderBookWidget, defaultChannel: "red",
    assistant: { description: "Live L2 depth / order book for any asset class", params: { symbol: "ticker or pair e.g. AAPL or BTC/USDT", channel: "red/blue/green/none" } },
  },
  timesales: {
    // Follows the primary selection channel (red), like the order book, so the tape tracks your pick.
    title: "Time & Sales", component: TimeSalesWidget, defaultChannel: "red",
    assistant: { description: "Live trade prints / time & sales for any asset class", params: { symbol: "ticker or pair e.g. AAPL or BTC/USDT", channel: "red/blue/green/none" } },
  },
  options_chain: {
    title: "Option Chain", component: OptionChainWidget, defaultChannel: "red",
    assistant: { description: "Equity options chain: calls|strike|puts with bid/ask/IV/greeks for a selected expiry", params: { symbol: "underlying ticker e.g. AAPL", expiry: "expiration YYYY-MM-DD", channel: "red/blue/green/none" } },
    accepts: {
      symbols: (p) => ({ symbol: p.symbols?.[0], asset: "equity" }),
    },
  },
  options_surface: {
    title: "Options Surface", component: OptionSurfaceWidget, defaultChannel: "red",
    assistant: { description: "Implied-volatility smile (per expiry) and expiry×strike IV surface heatmap for an equity", params: { symbol: "underlying ticker e.g. AAPL", channel: "red/blue/green/none" } },
    accepts: {
      symbols: (p) => ({ symbol: p.symbols?.[0], asset: "equity" }),
    },
  },
  news: {
    title: "News", component: NewsWidget, defaultChannel: "red",
    assistant: { description: "Headlines with sentiment for a symbol", params: { symbol: "ticker", channel: "red/blue/green/none" } },
  },
  topicnews: { title: "Topic News", component: TopicNewsWidget, defaultChannel: "none" },
  screener: {
    title: "Screener", component: ScreenerWidget, defaultChannel: "none",
    assistant: { description: "Factor/NL screener; pass initialQuery to auto-run a natural-language screen", params: { initialQuery: "natural-language screen e.g. 'top momentum in the dow'" } },
  },
  backtest: {
    title: "Backtest", component: BacktestWidget, defaultChannel: "none",
    assistant: { description: "Factor or single-strategy backtester (chat-driven)", params: { btMode: "factor/lab" } },
    accepts: {
      screen_result: (p) => ({
        btMode: "factor",
        incomingUniverse: p.universe,
        incomingFactor: p.factor,
        incomingSymbols: p.symbols,
        incomingWeights: p.weights,
      }),
    },
  },
  portfolio: {
    title: "Portfolio", component: PortfolioWidget, defaultChannel: "none",
    assistant: { description: "Holdings, P&L and correlation for the tracked portfolio" },
  },
  assistant: { title: "Assistant", component: AssistantWidget, defaultChannel: "red" },
  paper: {
    title: "Paper Trading", component: PaperTradingWidget, defaultChannel: "red",
    accepts: {
      backtest_result: (p) => ({ incomingStrategy: p.strategyKey, incomingParams: p.params }),
    },
  },
  agent: { title: "Agent Workflow", component: AgentGraphWidget, defaultChannel: "red" },
  factors: { title: "Factors", component: FactorLibrary, defaultChannel: "none" },
  strategies: {
    title: "Strategies", component: StrategyLibrary, defaultChannel: "red",
    accepts: {
      backtest_result: (p) => ({ incomingStrategy: p.strategyKey, incomingParams: p.params }),
    },
  },
  models: { title: "Models", component: ModelRepository, defaultChannel: "none" },
  portfolios: { title: "Portfolio Builder", component: PortfolioBuilder, defaultChannel: "none" },
  sandbox: { title: "Sandbox", component: SandboxWidget, defaultChannel: "red" },
  factor_monitor: { title: "Factor Performance", component: FactorMonitorWidget, defaultChannel: "none" },
  filings: { title: "Public Filings", component: PublicFilingsWidget, defaultChannel: "red" },
  listings: { title: "New Listings", component: NewListingsWidget, defaultChannel: "red" },
  macro: {
    title: "Macro", component: MacroWidget, defaultChannel: "none",
    assistant: { description: "Macro/FRED series and the Treasury yield curve" },
  },
  ficc: {
    title: "FICC", component: FiccWidget, defaultChannel: "red",
    assistant: { description: "Unified FICC board: the Treasury yield curve and CME Treasury futures complex (ZT/ZF/ZN/ZB/UB/ZQ), G10 spot FX, and the commodity futures complex (metals/energy/agriculture) with EOD quotes" },
  },
  committee: { title: "Committees", component: CommitteeWidget, defaultChannel: "red" },
  metrics: {
    title: "Profile", component: MetricsWidget, defaultChannel: "red",
    assistant: { description: "Profile: metrics, chart, and fundamentals for a symbol", params: { symbol: "ticker", channel: "red/blue/green/none", tab: "metrics/chart/fundamentals" } },
  },
  chart_studio: { title: "Chart Studio", component: ChartStudioWidget, defaultChannel: "none" },
  market_making: { title: "Market Making", component: MarketMakingWidget, defaultChannel: "blue" },
  risk_attribution: { title: "Risk Attribution", component: RiskAttributionWidget, defaultChannel: "none" },
  algo_trading: { title: "Algo Trading", component: AlgoTradingWidget, defaultChannel: "red" },
  research_loop: {
    title: "Research Loop", component: ResearchLoopWidget, defaultChannel: "none",
    assistant: {
      description: "Autonomous research loop: designs a factor experiment, backtests it, grades it against the promotion scorecard, reflects, and redoes up to 5 iterations",
      params: { initialQuery: "research goal e.g. 'find a robust long-short that passes the scorecard'" },
    },
  },
  map: {
    title: "Map", component: MapWidget, defaultChannel: "none",
    assistant: {
      description: "Module wiring map: how widgets link via channels, hand off via sends, and which backend routes/services each uses",
      params: { mode: "live/catalog" },
    },
  },
};

export const WIDGET_TYPES = Object.keys(WIDGETS) as WidgetType[];

function withBoundary(type: WidgetType): FunctionComponent<WidgetProps> {
  const Inner = WIDGETS[type].component;
  return function BoundedWidget(props: WidgetProps) {
    return (
      <ErrorBoundary label={WIDGETS[type].title}>
        <Inner {...props} />
      </ErrorBoundary>
    );
  };
}

/** Legacy widget-type ids that were merged into another module. Saved layouts / templates that
 * still reference them render the target module (on a sensible default tab) rather than failing to
 * resolve a component — and are retitled to the target module's current name on load.
 * Single source of truth, consumed by `dockviewComponents` and `retitlePanels`. */
export const LEGACY_ALIASES: Record<string, WidgetType> = {
  research: "metrics", // merged into Profile (Fundamentals tab)
  rates: "ficc", // merged into FICC (Yield Curve / Treasury Futures tabs)
};
const LEGACY_DEFAULT_TAB: Record<string, string> = { research: "fundamentals", rates: "__curve__" };

function legacyAlias(legacyType: string): FunctionComponent<WidgetProps> {
  const target = LEGACY_ALIASES[legacyType];
  const Inner = WIDGETS[target].component;
  const tab = LEGACY_DEFAULT_TAB[legacyType];
  return function LegacyAliased(props: WidgetProps) {
    return (
      <ErrorBoundary label={WIDGETS[target].title}>
        <Inner {...props} params={{ ...props.params, tab: props.params.tab ?? tab }} />
      </ErrorBoundary>
    );
  };
}

/** Dockview's `components` prop — every widget wrapped so one crash stays one tile, plus the
 * legacy-alias components so pre-merge saved layouts still resolve. */
export const dockviewComponents: Record<string, FunctionComponent<WidgetProps>> = {
  ...Object.fromEntries(WIDGET_TYPES.map((t) => [t, withResizeGrip(withBoundary(t))])),
  ...Object.fromEntries(Object.keys(LEGACY_ALIASES).map((t) => [t, withResizeGrip(legacyAlias(t))])),
};

/** The navigate/configure verbs the Assistant control loop may drive. Mirrors the backend
 * allowlist (assistant_agent.CLIENT_ACTIONS) and the terminalActions dispatcher. */
export const ASSISTANT_ACTION_VERBS = [
  "open_widget",
  "set_symbol",
  "configure_widget",
  "switch_workspace",
  "apply_template",
  "read_workspace",
] as const;

/** Widget types the Assistant is allowed to open/configure (those declaring an `assistant` field). */
export const ASSISTANT_WIDGET_TYPES = WIDGET_TYPES.filter((t) => WIDGETS[t].assistant);

/** Capability catalog sent to the agent per session: the action verbs + the registry-derived
 * widget catalog (type, purpose, settable params). Adding a widget with an `assistant` field
 * extends the Assistant's reach automatically — no agent edits. */
export function buildAssistantCapabilities(): {
  actions: string[];
  intents: IntentKind[];
  widgets: { type: WidgetType; description: string; params?: Record<string, string> }[];
} {
  return {
    // Legacy verb array the backend ReAct prompt consumes (unchanged).
    actions: [...ASSISTANT_ACTION_VERBS],
    // The unified intent vocabulary — same source of truth as dispatchIntent. Additive; lets the
    // backend/agent evolve toward intents without breaking the existing `actions` contract.
    intents: INTENT_KINDS,
    widgets: ASSISTANT_WIDGET_TYPES.map((type) => ({
      type,
      description: WIDGETS[type].assistant!.description,
      params: WIDGETS[type].assistant!.params,
    })),
  };
}
