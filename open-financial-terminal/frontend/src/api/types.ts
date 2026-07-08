export type Asset = "equity" | "crypto" | "rates" | "fx" | "commodity";
export type Timeframe = "1m" | "5m" | "15m" | "1h" | "1d";

export interface SearchHit {
  symbol: string;
  asset: Asset;
  sector: string | null;
  universe: string;
  name?: string; // company name (set for EDGAR new-listing hits, so name search matches)
}

export interface Quote {
  price: number | null;
  change: number | null;
  change_pct: number | null;
  high?: number;
  low?: number;
  volume?: number;
  asof: string | null;
  spark?: number[];
}

export interface Candle {
  time: string | number; // date string (daily) or unix seconds (intraday)
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface LinePoint {
  time: string | number;
  value: number;
}

export interface IndicatorPayload {
  name: string;
  pane: "price" | "lower";
  series: Record<string, LinePoint[]>;
}

export interface BarsResponse {
  symbol: string;
  asset: Asset;
  timeframe: Timeframe;
  candles: Candle[];
  volume: LinePoint[];
  quote: Quote;
  indicators: IndicatorPayload[];
}

export interface WatchItem {
  symbol: string;
  asset: Asset;
}

export interface BoardItem {
  symbol: string;
  name: string;
  asset: Asset;
}

export interface BoardSection {
  key: string;
  label: string;
  items: BoardItem[];
}

export interface BoardResponse {
  sections: BoardSection[];
}

export interface FactorMeta {
  key: string;
  label: string;
  direction: string;
}

export interface ScreenRow {
  symbol: string;
  asset: Asset;
  sector: string | null;
  score: number;
  ret_20d: number | null;
  price: number | null;
}

export interface ScreenResponse {
  factor: string;
  universe: string;
  coverage: number;
  results: ScreenRow[];
  rationale?: string;
}

export interface RiskMetric {
  symbol: string;
  ann_vol: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  cagr: number;
}

export interface RiskResponse {
  symbols: string[];
  correlation: (number | null)[][];
  metrics: RiskMetric[];
}

/** Portfolio-level (weighted-book) risk: aggregate stats over the active position source. */
export interface PortfolioRisk {
  as_of?: string | null;
  n: number;
  insufficient?: boolean;
  total_value: number;
  ann_vol: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  cagr: number;
  var_95: number;
  cvar_95: number;
  var_95_usd: number;
  cvar_95_usd: number;
  gross: number;
  net: number;
  long: number;
  short: number;
  n_long: number;
  n_short: number;
  concentration: number;
  beta: number | null;
  benchmark: string;
}

export interface RiskFactorContribution {
  factor: string;
  kind: "style" | "industry";
  exposure: number;       // portfolio net loading on the factor (Xᵀw)
  var_contribution: number;
  pct_total: number;      // fraction of TOTAL variance (factor pcts sum to pct_factor)
}

export interface RiskPositionContribution {
  symbol: string;
  weight: number;         // signed, gross-normalized
  mctr: number;           // marginal contribution to risk, %
  cctr: number;           // component contribution to risk, % (Σ = total_vol)
  pct: number;            // share of total risk (Σ = 1)
}

export interface RiskAttributionResponse {
  as_of?: string;
  source?: "holdings" | "paper";
  base_universe?: string;
  window_days?: number;
  n: number;
  insufficient?: boolean;
  reason?: string;
  skipped?: { symbol: string; reason: string }[];
  total_vol?: number;     // annualized %, factor + specific combined
  factor_vol?: number;
  specific_vol?: number;
  pct_factor?: number;    // systematic fraction of variance (0..1)
  factors?: RiskFactorContribution[];
  positions?: RiskPositionContribution[];
}

export interface ReturnFactorContribution {
  factor: string;
  kind: "style" | "industry";
  contribution: number;   // cumulative realized P&L contribution over the window, %
}

export interface ReturnAttributionResponse {
  as_of?: string;
  source?: "holdings" | "paper";
  base_universe?: string;
  window_days?: number;
  n: number;
  insufficient?: boolean;
  reason?: string;
  skipped?: { symbol: string; reason: string }[];
  total_return?: number;     // cumulative realized return over the window, %
  factor_return?: number;    // portion from factor bets
  specific_return?: number;  // portion from stock selection (residual)
  contributions?: ReturnFactorContribution[];
  series?: { times: string[]; total: number[]; factor: number[]; specific: number[] };
}

export interface BrinsonSectorRow {
  sector: string;
  w_port: number;      // %
  w_bench: number;     // %
  r_port: number;      // % (window-cumulative sector return, portfolio names)
  r_bench: number;     // %
  allocation: number;  // %
  selection: number;   // %
  interaction: number; // %
  total: number;       // % (allocation + selection + interaction)
}

export interface BrinsonAttributionResponse {
  as_of?: string;
  source?: "holdings" | "paper";
  base_universe?: string;
  window_days?: number;
  benchmark?: string;
  n: number;
  insufficient?: boolean;
  reason?: string;
  skipped?: { symbol: string; reason: string }[];
  active_return?: number;  // % vs benchmark = allocation + selection + interaction
  allocation?: number;
  selection?: number;
  interaction?: number;
  sectors?: BrinsonSectorRow[];
}

export interface HoldingRow {
  symbol: string;
  asset: Asset;
  quantity: number;
  cost_basis: number;
  price: number | null;
  value: number | null;
  pnl: number | null;
  pnl_pct: number | null;
}

export interface HoldingsResponse {
  holdings: HoldingRow[];
  total_value: number;
  total_cost: number;
  total_pnl: number;
  total_pnl_pct: number | null;
}

export interface PortfolioBook {
  id: number;
  name: string;
  created: string;
}

export interface PortfolioBooksResponse {
  books: PortfolioBook[];
  active: number;
}

export interface CompositionSeries {
  symbol: string;
  weights: number[];       // per-date share in percent (0–100), aligned with `times`
  start_weight: number;
  end_weight: number;
  end_value: number;
}

export interface CompositionResponse {
  n: number;
  insufficient?: boolean;
  error?: string;
  start?: string;
  end?: string;
  times?: string[];
  symbols?: string[];
  series?: CompositionSeries[];
  total_value?: number[];
}

export interface Fundamentals {
  snapshot: Record<string, string | number | null>;
  financials: { periods: string[]; rows: Record<string, (number | null)[]> };
}

/* ── Metrics (per-symbol, asset-class-tailored tearsheet) ─────────────────────── */

/** How the client should render `value`. `pct` = signed/colored percent; `pctp` = neutral
 * percent (e.g. volatility); `price` = price-precision number; `usd` = compact dollars;
 * `x` = multiple (28.5×); `num`/`ratio` = plain 2-dp; `int` = integer. */
export type MetricFmt = "pct" | "pctp" | "price" | "usd" | "x" | "num" | "ratio" | "int";

export interface MetricRow {
  label: string;
  value: number | null; // pct/pctp values are already in percent units (4.3 == 4.3%)
  fmt: MetricFmt;
  hint?: string;
}

export interface MetricSection {
  key: string;
  label: string;
  rows: MetricRow[];
}

/** One metric (e.g. Return) evaluated over each trailing window — values align to
 * `PeriodMetrics.windows`. Drives the Chart tab's per-period comparison. */
export interface PeriodMetricSeries {
  key: string;
  label: string;
  fmt: MetricFmt;
  values: (number | null)[];
}

export interface PeriodMetrics {
  windows: string[]; // e.g. ["1M","3M","6M","1Y","2Y","3Y"]
  metrics: PeriodMetricSeries[];
}

/** Rolling-window time series for the Chart tab's Rolling view. Series keys:
 * return / ann_vol / sharpe (trailing `window`-day) + drawdown (underwater curve). */
export interface MetricsRolling {
  symbol: string;
  asset: Asset;
  window: number;
  series: Record<string, LinePoint[]>;
}

export interface MetricsResponse {
  symbol: string;
  asset: Asset;
  as_of: string | null;
  price: number | null;
  change_pct: number | null; // percent units
  currency: string | null;
  name: string | null;
  sector: string | null;
  note: string | null;
  sections: MetricSection[];
  period_metrics: PeriodMetrics | null;
}

/* ── Chart Studio (chat-driven chart creation agent) ─────────────────────────── */

/** Semantic color key resolved to a theme token client-side (keeps charts theme-aware). */
export type ChartColorKey = "accent" | "up" | "down" | "series1" | "series2" | "series3" | "series4";
export type ChartEngine = "price" | "series" | "heatmap";

export interface ChartPricePayload {
  symbol: string;
  asset: Asset;
  timeframe: Timeframe;
  style: "candles" | "area";
  candles: Candle[];
  volume: LinePoint[];
  indicators: IndicatorPayload[];
}

export interface ChartSeriesSpec {
  points: LinePoint[];
  colorKey: ChartColorKey;
  kind: "line" | "area" | "histogram";
  title: string;
}

export interface ChartSeriesPayload {
  title: string;
  specs: ChartSeriesSpec[];
  // Optional numeric x-axis (e.g. a yield curve plotted against tenor-in-years instead of time).
  xMode?: "time" | "value";
  xTicks?: { x: number; label: string }[];
  xUnit?: string;
}

export interface ChartHeatmapPayload {
  labels: string[]; // square (correlation): used for both axes
  matrix: (number | null)[][];
  // Rectangular heatmaps (e.g. monthly-return calendar) set distinct row/col labels. `vmax` scales
  // the diverging color (|v| ≥ vmax = full intensity; default 1, for correlation). `fmt`: cell text.
  rows?: string[];
  cols?: string[];
  vmax?: number;
  fmt?: "ratio" | "pct";
  title?: string;
}

export interface ChartAction {
  tool: string;
  args: Record<string, unknown>;
}

/** Streamed frames from the chart-studio agent (`/api/chart/agent`). A `chart` frame carries
 * one of the three engine payloads plus the action (for refinement) and optional pin params. */
export type ChartAgentFrame =
  | { type: "thought"; text: string }
  | {
      type: "chart";
      id: string;
      title: string;
      engine: ChartEngine;
      price?: ChartPricePayload;
      series?: ChartSeriesPayload;
      heatmap?: ChartHeatmapPayload;
      action: ChartAction;
      open_params?: Record<string, unknown> | null;
    }
  | { type: "obs"; text: string; ok?: boolean }
  | { type: "done"; message: string }
  | { type: "error"; detail: string };

export interface NewsItem {
  title: string;
  publisher: string | null;
  link: string | null;
  published?: number | null; // unix seconds
  sentiment?: "bullish" | "neutral" | "bearish" | null;
  score?: number | null;
  relevance?: number | null; // 0..1 importance to the symbol (LLM)
  rank_score?: number | null; // composite router rank (set when rank=true)
  source?: string | null; // routing source key/name that surfaced it
  source_weight?: number | null; // that source's priority weight (0..100)
}

export interface NewsResponse {
  symbol: string;
  items: NewsItem[];
}

/** Symbol-agnostic topic news (Topic News widget). Reuses NewsItem. */
export interface TopicNewsResponse {
  category: string;
  items: NewsItem[];
}

/** A user-defined news topic ("interest subscription"): a keyword query → Google News feed.
 * Can carry several labels/aliases, all feeding the one query. */
export interface NewsTopic {
  key: string; // stable slug (server-assigned on save, from the first label)
  labels: string[]; // one or more display labels/aliases
  query: string; // keyword interest
  enabled: boolean;
}

/** A selectable topic in the launcher (built-in Market/Macro or an enabled user topic). */
export interface AvailableTopic {
  key: string;
  label: string;
  builtin: boolean;
}

/* ── Public filings (SEC EDGAR) ──────────────────────────────────────────────── */

export type FilingCategory =
  | "all"
  | "financials"
  | "events"
  | "insider"
  | "ownership"
  | "governance"
  | "offerings"
  | "other";

export interface FilingItem {
  form: string;
  label: string;
  category: FilingCategory;
  filing_date: string;
  report_date: string | null;
  filed: number | null; // unix seconds
  accession: string;
  url: string;
}

export interface FilingsResponse {
  symbol: string;
  coverage: "live" | "cached" | "unavailable";
  items: FilingItem[];
}

export interface InsiderTxn {
  insider: string;
  role: string;
  date: string;
  filed: number | null;
  code: string;
  acq_disp: string; // "A" acquired | "D" disposed
  shares: number;
  price: number;
  value: number;
  shares_after: number;
  security: string;
  derivative: boolean;
}

export interface InsiderWindow {
  buy_shares: number;
  sell_shares: number;
  buy_value: number;
  sell_value: number;
  net_shares: number;
  net_value: number;
  n_buys: number;
  n_sells: number;
}

export interface InsiderResponse {
  symbol: string;
  coverage: "lake" | "live" | "unavailable";
  summary: { d90: InsiderWindow; m6: InsiderWindow };
  items: InsiderTxn[];
}

export interface InstitutionalHolder {
  manager: string;
  shares: number;
  value_usd: number;
  pct_of_book: number;
  change_shares: number;
  change_pct: number;
}

export interface HoldersResponse {
  symbol: string;
  coverage: "lake" | "none" | "unavailable";
  period: string | null;
  items: InstitutionalHolder[];
}

export interface LlmSettings {
  custom: boolean;
  base_url: string;
  model: string;
  model_pinned: boolean;
  has_key: boolean;
}

export interface LlmTestResult {
  ok: boolean;
  detail: string;
  models: string[];
  current?: string;
}

export interface NewsSource {
  name: string;
  url: string;
  enabled: boolean;
  weight: number; // ranking priority 0..100 (50 = neutral)
}

/** Composite-rank formula parameters (Settings → News Sources → Ranking). */
export interface RankingParams {
  recency: number; // weight on recency (half-life decay)
  source: number; // weight on per-source priority
  relevance: number; // weight on LLM relevance-to-symbol
  sentiment: number; // weight on sentiment conviction
  match: number; // weight on the title mentioning the ticker
  halflife_h: number; // hours for the recency term to halve
}

export interface NewsSourceSettings {
  builtin: Record<string, boolean>;
  builtin_weights: Record<string, number>;
  builtin_meta: { key: string; label: string }[];
  custom: NewsSource[];
  max_items: number;
  ranking: RankingParams;
  ranking_default: RankingParams;
}

export interface NewsSourceTest {
  ok: boolean;
  detail: string;
  sample: string[];
}

export interface NewsFeedCandidate {
  title: string;
  url: string;
}

export interface NewsDiscoverResult {
  ok: boolean;
  detail: string;
  candidates: NewsFeedCandidate[];
}

/* ── External MCP servers (the grounded assistant calls their tools) ─────────── */

export interface McpServer {
  name: string;
  transport: "stdio" | "http";
  command?: string; // stdio: executable to spawn
  args?: string[]; // stdio: command arguments
  env?: Record<string, string>; // stdio: extra environment
  url?: string; // http: streamable-HTTP endpoint
  headers?: Record<string, string>; // http: request headers
  enabled: boolean;
}

export interface McpSettings {
  servers: McpServer[];
}

export interface McpTestResult {
  ok: boolean;
  detail: string;
  tools: { name: string; description: string }[];
}

/* ── New listings (SEC EDGAR detection) ─────────────────────────────────────── */

export interface NewListing {
  form: string; // e.g. "8-A12B", "424B4"
  kind: string; // human label: "Exchange listing" | "IPO prospectus"
  company: string;
  tickers: string[];
  cik: string;
  filing_date: string | null;
  filed: number | null; // unix seconds
  accession: string;
  url: string; // link to the filing on sec.gov
}

export interface NewListingsResponse {
  days: number;
  coverage: "live" | "cached" | "unavailable";
  count: number;
  items: NewListing[];
}

/* ── Market data (Settings → Market Data) ───────────────────────────────────── */

export interface EquityStreamStatus {
  enabled: boolean; // equity real-time streaming available (Alpaca creds configured)
  feed: string; // active feed: "iex" | "sip"
}

/** Per-asset-class category records (each carries its own data source + caching/history/default). */
export interface EquityCategory {
  bars_source: string; // historical-bars source ("yfinance")
  realtime_source: string; // realtime source: "alpaca" | "none"
  realtime_feed: string; // Alpaca equity feed: "iex" | "sip"
  depth_source: string; // order-book (L2) producer: "sim" | "none"
  intraday_ttl: number;
  history_years: number;
  default_symbol: string;
}
export interface CryptoCategory {
  source: string; // ccxt exchange — drives BOTH bars and realtime
  realtime: boolean; // live ticker/book/trades on; off keeps bars/charts working
  depth_source: string; // order-book source: "exchange" (real ccxt L2) | "sim" | "none"
  intraday_ttl: number;
  history_years: number;
  default_symbol: string;
}
/** FICC classes (rates futures / spot FX / commodity futures): yfinance bars only — no exchange or
 * realtime knobs, just the bars history window, intraday cache TTL, the seed symbol, and a
 * selectable order-book depth source (like equity). */
export interface FiccCategory {
  bars_source: string; // "yfinance"
  depth_source: string; // order-book (L2) producer: "sim" | "none"
  intraday_ttl: number;
  history_years: number;
  default_symbol: string;
}
/** Options is a standalone chain subsystem (not an OHLCV asset class): a chain source + knobs. */
export interface OptionsCategory {
  source: string; // "yfinance" | "tradier" | "polygon" | "ibkr" | "none"
  default_underlying: string;
  expiry_window: number; // days forward to list expirations
  chain_ttl: number; // seconds
  greeks: string; // "auto" (compute Black-Scholes) | "passthrough" | "off"
}
export interface MarketDataCategories {
  equity: EquityCategory;
  crypto: CryptoCategory;
  rates: FiccCategory;
  fx: FiccCategory;
  commodity: FiccCategory;
  options?: OptionsCategory;
}
/** Selectable options per category, so the Settings UI can render the dropdowns. */
export interface MarketDataCategoryMeta {
  categories: string[];
  equity: { bars_sources: string[]; realtime_sources: string[]; feeds: string[]; depth_sources: string[] };
  crypto: { sources: string[]; depth_sources: string[] };
  rates: { depth_sources: string[] };
  fx: { depth_sources: string[] };
  commodity: { depth_sources: string[] };
  options?: { sources: string[]; capabilities: Record<string, OptionsCaps> };
}
/** What an options source provides (drives the Settings capability note + greeks-column visibility). */
export interface OptionsCaps {
  chains: boolean;
  iv: boolean;
  greeks: boolean;
  realtime: boolean;
}
/** Options-chain status from /api/health + /api/settings/market-data. */
export interface OptionsStatus {
  source: string;
  enabled: boolean;
  capabilities: OptionsCaps;
  default_underlying: string;
  expiry_window: number;
}
export type OptionRight = "call" | "put";
/** One option contract row (calls/puts), normalized by the backend (NaN/0 → null, iv decimal). */
export interface OptionQuote {
  strike: number;
  right: OptionRight;
  bid: number | null;
  ask: number | null;
  last: number | null;
  volume: number | null;
  open_interest: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  rho: number | null;
  in_the_money: boolean | null;
  contract_symbol: string | null;
}
export interface OptionExpiration {
  date: string; // ISO YYYY-MM-DD
  dte: number; // days to expiry
  monthly: boolean;
}
export interface OptionExpirationsResponse {
  underlying: string;
  source: string;
  expirations: OptionExpiration[];
  note: string | null;
}
export interface OptionChainResponse {
  underlying: string;
  expiry: string;
  dte: number;
  monthly: boolean;
  source: string;
  spot: number | null;
  risk_free_rate: number | null;
  dividend_yield: number | null; // continuous div yield used in BS greeks (null when source-provided)
  greeks_computed: boolean; // true = BS-derived locally; false = source passthrough
  atm_strike: number | null;
  strikes: number[];
  calls: OptionQuote[];
  puts: OptionQuote[];
  note: string | null;
}
/** One leg of a multi-leg (combo) paper option order — an OCC id, or the chain coordinates. */
export interface ComboLeg {
  occ?: string;
  underlying?: string;
  expiry?: string;
  strike?: number;
  right?: OptionRight;
  side: "buy" | "sell";
  ratio?: number; // contracts of this leg per 1 spread unit (default 1)
}
export interface ComboOrderRequest {
  legs: ComboLeg[];
  quantity?: number; // number of spread units
  account?: number;
}
export interface ComboOrderResult {
  ok: boolean;
  book: string;
  net_debit: number; // >0 = net debit (paid), <0 = net credit (received)
  legs: { occ: string; order_id: string; side: string; quantity: number }[];
}
/** Per-asset-class order-book status. `token` is the hub topic segment to subscribe on
 * (book:<token>:<symbol>); empty when depth is off. Backend-computed; the UI never builds it. */
export interface DepthStatus {
  source: string; // the ACTIVE producer ("auto" already resolved): "sim" | "exchange" | "none" | vendor id
  configured?: string; // the configured value — "auto" when auto-picking (so the UI can show "auto → sim")
  token: string; // hub topic token, "" when depth is off
  enabled: boolean; // depth available right now
}
export type DepthMap = Record<Asset, DepthStatus>;

/** Per-asset-class time-&-sales (tape) status. Same shape as DepthStatus: `token` is the hub topic
 * segment to subscribe on (trades:<token>:<symbol>), empty when no tape is available. Backend-
 * computed (crypto/equity real feeds + the simulated FICC tape); the UI never builds it. */
export interface TradesStatus {
  source: string; // "exchange" | "alpaca" | "sim" | "none" | vendor id
  token: string; // hub topic token, "" when the tape is unavailable
  enabled: boolean; // tape available right now
}
export type TradesMap = Record<Asset, TradesStatus>;

export interface MarketDataSettings {
  categories: MarketDataCategories; // canonical per-asset-class config
  category_meta: MarketDataCategoryMeta; // selectable options per category
  depth: DepthMap; // per-class order-book source/token/availability
  options?: OptionsStatus; // options-chain source status
  exchange: string; // legacy mirror: active crypto exchange (= categories.crypto.source)
  supported: string[]; // selectable exchanges
  intraday_ttl: number; // legacy mirror (= equity category)
  history_years: number; // legacy mirror (= equity category)
  equity_feed: string; // legacy mirror (= categories.equity.realtime_feed)
  supported_equity_feeds: string[]; // selectable equity feeds
  equity_stream: EquityStreamStatus; // equity streaming capability + active feed
  has_alpaca_key: boolean; // an Alpaca key is saved (the secret is never returned)
  alpaca_paper: boolean; // route Alpaca to the paper environment
  broker: "alpaca" | "sim"; // active paper broker
  data_dir: string; // terminal data cache dir (read-only status)
  lake_dir: string; // qhfi parquet lake dir (read-only status)
  cached_symbols: number; // cached daily-bars parquet files under data_dir/market
  realtime: { topics: string[]; subscribers: Record<string, number>; exchanges: string[] };
}

/** Generic {ok, detail} result for market-data test / clear-cache actions. */
export interface OkResult {
  ok: boolean;
  detail: string;
}

/** Non-secret status for one market-data vendor provider (Settings → Data Providers). The saved
 * secret is never returned — only whether one exists (`has_key`) and whether an env var supplies one. */
export interface ProviderStatus {
  has_key?: boolean; // a credential is saved (secret never returned)
  from_env?: boolean; // a value is currently supplied by an env var
  env?: string; // tradier: "live" | "sandbox"
  host?: string; // ibkr
  port?: string; // ibkr
  configured?: boolean; // ibkr: host or port set
}

/** All vendor-provider statuses, keyed by provider id (databento/polygon/tradier/dxfeed/ibkr). */
export interface ProviderSettings {
  providers: Record<string, ProviderStatus>;
}

/** PUT body for one provider's credentials/settings — a blank secret keeps the saved value. */
export interface ProviderIn {
  name: string; // databento | polygon | tradier | dxfeed | ibkr
  api_key?: string; // databento / polygon
  token?: string; // tradier
  address?: string; // dxfeed
  env?: string; // tradier: "live" | "sandbox"
  host?: string; // ibkr
  port?: string; // ibkr
}

/** One directory entry from the folder-picker fs-list endpoint. */
export interface FsEntry {
  name: string;
  is_dir: boolean;
}

/** Response of GET /api/settings/fs/list — the sub-directories of a path, for the folder picker. */
export interface FsList {
  path: string; // the resolved absolute path listed
  parent: string; // parent path, "" at the filesystem root
  sep: string; // OS path separator
  entries: FsEntry[]; // sub-directories only (never files)
  roots: string[]; // quick-jump roots: home, cwd, data dir
  error: string; // non-empty when the requested path was bad/denied
}

/** One automatic-refresh job's live state (Settings → Data Refresh). */
export interface DataRefreshJob {
  label: string;
  enabled: boolean;
  interval_s: number;
  interval_minutes: number;
  last_run: string | null; // ISO timestamp of the last completed pass
  next_run: string | null;
  running: boolean;
  last_result: Record<string, unknown> | null; // {status, refreshed/rows/..., error?}
}

/** Full data-refresh status: master toggle, market-hours gate, active counts, per-job state. */
export interface DataRefreshStatus {
  running: boolean; // the scheduler loop is alive
  master_enabled: boolean;
  market_hours_only: boolean;
  active_by_asset: Record<string, number>; // {equity, crypto, fx, rates} → active-set count
  active_equities: number; // legacy mirror
  active_crypto: number; // legacy mirror
  jobs: Record<string, DataRefreshJob>;
}

/** PUT body to change refresh config (only set fields are applied). */
export interface DataRefreshConfigIn {
  master_enabled?: boolean;
  market_hours_only?: boolean;
  jobs?: Record<string, { enabled?: boolean; interval_minutes?: number }>;
}

/** Result of a manual "Run now" trigger. */
export interface DataRefreshJobResult {
  status: string; // "ok" | "error" | "busy"
  [k: string]: unknown;
}

/** A designed, runnable backtest the "Ideas" panel surfaces. `kind:"factor"` carries an NL `prompt`
 *  for the agent; `kind:"model"` names a saved research-bundle model run via /api/backtest/model. */
export interface BacktestProposal {
  id: string;
  kind: "factor" | "model";
  label: string;
  rationale: string;
  source: string;
  generated: "llm" | "template";
  // factor proposals
  prompt?: string;
  factor?: string;
  universe?: string;
  mode?: string;
  years?: number;
  // model proposals
  model?: string;
}

export interface BacktestProposalsResponse {
  proposals: BacktestProposal[];
  inventory: {
    factors: { name: string; label: string }[];
    models: { name: string; kind: "research" | "trained" }[];
  };
  counts: { factors: number; research_models: number; trained_models: number; universes: number };
}

export interface BacktestResponse {
  universe: string;
  factor: string;
  strategy?: string;
  mode: string;
  n_instruments: number;
  window_start: string;
  window_end: string;
  pnl: number;
  pnl_pct: number;
  metrics: {
    cagr: number;
    ann_vol: number;
    sharpe: number;
    sortino: number;
    max_drawdown: number;
    calmar: number;
  };
  robustness: {
    psr: number | null;
    dsr: number | null;
    n_trials: number;
  };
  benchmark: {
    beta: number;
    alpha: number; // annualized Jensen's alpha, %
    information_ratio: number | null;
    tracking_error: number; // annualized, %
    correlation: number;
    r_squared: number;
    bench_cagr: number;
    bench_sharpe: number;
    excess_cagr: number; // strategy − benchmark CAGR, %
  } | null;
  benchmark_label: string; // investable index symbol (e.g. SPY/BTC) or "equal-weight"
  rebalance?: string; // rebalance cadence: "monthly" | "quarterly" | "annual"
  /** Present only when a market-timing overlay was applied — drives the "Timing" dashboard tab. */
  timing?: {
    kind: "trend" | "regime";
    params: Record<string, number>;
    exposure_curve: LinePoint[]; // % invested over time (0–100+)
    baseline_equity_curve: LinePoint[]; // the same backtest WITHOUT the timing overlay
    delta: { sharpe: number; cagr: number; max_drawdown: number }; // timed − baseline
    labels?: { time: string; value: number }[]; // regime id per date (regime only)
    policy?: { regime: number; ann_mean: number; ann_vol: number; risky_fraction: number }[]; // regime only
  } | null;
  rolling_beta: LinePoint[]; // trailing-window beta to the benchmark over time
  sharpe_ci: {
    p5: number;
    p50: number;
    p95: number;
    prob_positive: number; // % of bootstrap Sharpes > 0
    n_boot: number;
    block: number;
  } | null;
  ic: {
    mean_ic: number;
    ic_std: number;
    icir: number | null; // annualized
    hit_rate: number; // % of months with IC > 0
    n_periods: number;
    series: LinePoint[];
  } | null;
  ic_decay: {
    horizon: number; // forward months
    mean_ic: number;
    icir: number | null;
    n: number;
  }[] | null;
  quantile_spread: {
    n_buckets: number;
    buckets: (number | null)[]; // annualized %, low-score → high-score
    spread: number | null; // top − bottom bucket, annualized %
    monotonicity: number | null; // Spearman(bucket rank, return), 1 = perfectly monotone
    n_periods: number;
  } | null;
  cost_sensitivity: {
    bps: number; // slippage assumption
    sharpe: number;
    cagr: number;
    total_costs: number;
  }[] | null;
  stability: {
    periods: {
      label: string;
      start: string;
      end: string;
      sharpe: number;
      ret: number; // total return over the sub-period, %
      max_drawdown: number;
    }[];
    n_periods: number;
    positive_periods: number;
    consistency: number; // share of sub-periods with positive return, 0–1
    sharpe_min: number;
    sharpe_max: number;
  } | null;
  avg_turnover: number;
  total_costs: number;
  final_equity: number;
  equity_curve: LinePoint[];
  benchmark_curve: LinePoint[];
  pnl_curve: LinePoint[];
  drawdown_curve: LinePoint[];
  drawdowns: {
    rank: number;
    depth: number; // trough/peak − 1, %
    peak_date: string;
    trough_date: string;
    recovery_date: string | null;
    decline_days: number;
    recovery_days: number | null;
    underwater_days: number;
    ongoing: boolean;
  }[];
  distribution: {
    skew: number;
    kurtosis: number; // excess kurtosis (normal = 0)
    var95: number; // 95% daily VaR, %
    cvar95: number | null; // expected shortfall, %
    best_day: number;
    worst_day: number;
    pct_positive: number;
    tail_ratio: number | null;
    win_loss: number | null;
  } | null;
  rolling_sharpe: LinePoint[];
  rolling_window: number;
  monthly_returns: { year: number; month: number; ret: number }[];
  turnover_monthly: LinePoint[];
  costs_cum: LinePoint[];
  gross_exposure: LinePoint[];
  net_exposure: LinePoint[];
  top_weights: { symbol: string; weight: number }[];
  sector_exposure: { sector: string; net: number }[] | null; // net long/short weight by sector, %
}

/* ── Strategy Lab ───────────────────────────────────────────────────────────── */

export interface LabStrategyParam {
  key: string;
  label: string;
  default: number;
  min: number;
  max: number;
  step: number;
}

export interface LabStrategy {
  key: string;
  label: string;
  params: LabStrategyParam[];
  sweepable: string[];
}

export interface LabTrade {
  side: "long" | "short";
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_price: number;
  pnl: number;
  ret_pct: number;
  bars: number;
  reason: "signal" | "stop" | "target" | "eod";
}

export interface LabMarker {
  time: string | number;
  price: number;
  kind: "longEntry" | "shortEntry" | "exit";
  win?: boolean;
}

export interface LabStats {
  net_pnl: number;
  net_pnl_pct: number;
  buy_hold_pct: number; // return of simply holding the symbol over the window, %
  vs_buy_hold: number; // strategy − buy & hold, percentage points
  final_equity: number;
  profit_factor: number | null;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  avg_bars: number;
  expectancy: number;
  avg_win: number;
  avg_loss: number;
  sharpe: number;
  gross_win: number;
  gross_loss: number;
}

export interface LabResult {
  symbol: string;
  timeframe: string;
  trades: LabTrade[];
  equity_curve: LinePoint[];
  benchmark_curve: LinePoint[];
  markers: LabMarker[];
  stats: LabStats;
  histogram: { lo: number; hi: number; n: number }[];
  candles: Candle[];
}

export interface SweepResult {
  x_key: string;
  x_vals: number[];
  y_key: string | null;
  y_vals: number[];
  metric: string;
  grid: (number | null)[][];
}

/* ── Market making (Avellaneda–Stoikov quoting backtest) ────────────────────── */

export type MMStrategyKey = "symmetric" | "linear" | "avellaneda" | "alpha" | "alpha_taker";

export interface MMRequest {
  symbol: string;
  timeframe: string;
  strategy: MMStrategyKey;
  gamma: number;
  kappa: number;
  half_spread_bps: number;
  skew_bps: number;
  q_max: number;
  obi_alpha: number;
  quote_size: number;
  sigma_window: number;
  spread_bps: number;
  levels: number;
  depth: number;
  imbalance_gain: number;
  maker_bps: number;
  initial_equity: number;
  max_snapshots: number;
}

export interface MMStats {
  final_equity: number;
  net_pnl: number;
  net_pnl_pct: number;
  spread_captured_bps: number;
  adv_sel_bps: number;
  net_edge_bps: number;
  fill_ratio: number;
  n_fills: number;
  inv_max_abs: number;
  inv_half_life: number | null;
}

export interface MMStrategyRun {
  key: MMStrategyKey;
  name: string;
  equity_curve: LinePoint[];
  inventory_curve: LinePoint[];
  markout: { h: number; bps: number }[];
  stats: MMStats;
}

interface MMMeta {
  synthetic_depth: boolean;
  note: string;
  snapshots: number;
  spread_bps: number;
  levels: number;
}

export interface MMResult extends MMStrategyRun {
  symbol: string;
  timeframe: string;
  strategy: MMStrategyKey;
  strategy_name: string;
  initial_equity: number;
  benchmark_curve: LinePoint[];
  mid_curve: LinePoint[];
  alpha_bps: number;
  alpha_r2: number;
  meta: MMMeta;
}

export interface MMCompareResult {
  symbol: string;
  timeframe: string;
  initial_equity: number;
  benchmark_curve: LinePoint[];
  strategies: MMStrategyRun[];
  alpha_bps: number;
  alpha_r2: number;
  meta: MMMeta;
}

export interface LabRequest {
  symbol: string;
  asset: Asset;
  timeframe: Timeframe;
  strategy: string;
  params: Record<string, number>;
  direction: "long_only" | "short_only" | "both";
  sl_pct: number;
  tp_pct: number;
  initial: number;
  commission_bps: number;
  size_pct: number;
  leverage: number;
  years: number;
}

/* ── Agent workflow (LangGraph) ─────────────────────────────────────────────── */

export interface AgentParam {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "code";
  default: string | number;
  options?: string[];
}

export interface AgentNodeType {
  key: string;
  label: string;
  category: "io" | "data" | "llm" | "pipeline" | "code";
  inputs: number;
  params: AgentParam[];
}

export interface AgentNode {
  id: string;
  type: string;
  x: number;
  y: number;
  config: Record<string, string | number>;
}

export interface AgentEdge {
  source: string;
  target: string;
}

export interface AgentGraphSpec {
  nodes: AgentNode[];
  edges: AgentEdge[];
}

export interface AgentGraphMeta {
  name: string;
  updated: string | null;
}

export interface ScenarioShocks {
  equity_pct: number;
  crypto_pct: number;
  vol_mult: number;
}

/** A named workflow scenario: variable preset (overrides node configs) + market shocks. */
export interface AgentScenario {
  name: string;
  description?: string;
  variables: Record<string, string | number>;
  shocks: ScenarioShocks;
  updated?: string | null;
}

/* ── Registries (factors / strategies / models) ─────────────────────────────── */

export interface RegFactor {
  name: string;
  label?: string;
  kind: string;
  direction: string;
  description?: string;
  code?: string;
  builtin: boolean;
  updated?: string | null;
}

export interface RegStrategyParam {
  key: string;
  label: string;
  default: number;
  min: number;
  max: number;
  step: number;
}

export interface RegStrategy {
  name: string;
  key?: string;
  label: string;
  description?: string;
  params: RegStrategyParam[];
  sweepable?: string[];
  code?: string;
  builtin: boolean;
  updated?: string | null;
}

export interface RegModel {
  name: string;
  description?: string;
  factor?: string;
  strategy?: string;
  universe?: string;
  mode?: string;
  params?: Record<string, unknown>;
  tags?: string[];
  notes?: string;
  updated?: string | null;
}

/* ── Linked qhfi directories + live engine registries ───────────────────────── */

export interface RegPaths {
  factors_dir: string;
  strategies_dir: string;
  models_dir: string;
}

export interface ScanItem {
  name: string;
  file: string;
  path: string;
  doc: string;
  symbols: string[];
}

export interface ScanDir {
  path: string;
  exists: boolean;
  items: ScanItem[];
}

/** A factor from qhfi's live factor registry (built-in or registered from the linked dir). */
export interface EngineFactor {
  name: string;
  label: string;
  direction: string;
  doc: string;
  source: "builtin" | "linked";
}

export interface EngineStrategyParam {
  key: string;
  default: number | string | boolean | null;
  type: string;
}

/** A strategy from qhfi's live strategy registry, runnable through the portfolio engine. */
export interface EngineStrategy {
  name: string;
  label: string;
  doc: string;
  params: EngineStrategyParam[];
  source: "builtin" | "linked";
}

export interface EngineRunRequest {
  strategy_key: string;
  universe_name: string;
  years: number;
  initial_equity?: number;
  mode?: string;
  params?: Record<string, number | string | boolean>;
  start?: string | null;
  end?: string | null;
}

/* ── Trained-model repository (linked qhfi ModelRepository) ──────────────────── */

export interface RepoModelVersion {
  version: number;
  stage: "draft" | "backtest" | "paper" | "production" | "archived";
  framework: string;
  domain: string | null;
  asset_class: string | null;
  created_at: string | null;
  metrics: Record<string, number>;
  features: string[];
  train_span: [string, string] | null;
  tags: string[];
}

export interface RepoModel {
  name: string;
  versions: RepoModelVersion[];
  latest: number | null;
  production_version: number | null;
}

export interface RepoModelsResponse {
  models: RepoModel[];
  root: string;
  exists: boolean;
  error?: string;
}

/* ── Portfolios (saved weight + allocation lists) ────────────────────────────── */

export interface PortfolioAllocation {
  symbol: string;
  asset: string;
  weight: number; // fraction (0.05 = 5%)
}

export interface Portfolio {
  name: string;
  description?: string;
  mode: string; // long_only | long_short
  allocations: PortfolioAllocation[];
  tags?: string[];
  notes?: string;
  updated?: string | null;
  builtin?: boolean;
}

export interface PortfolioExposure {
  gross: number;
  net: number;
  long: number;
  short: number;
  n_long: number;
  n_short: number;
  n: number;
}

export interface NormalizeResult {
  allocations: PortfolioAllocation[];
  exposures: PortfolioExposure;
}

export interface AllocationRow {
  symbol: string;
  asset: string;
  weight: number;
  price: number | null;
  notional: number;
  shares: number | null;
  shares_int: number | null;
  side: "long" | "short" | "flat";
}

export interface AllocateResult {
  capital: number;
  rows: AllocationRow[];
  exposures: PortfolioExposure;
  gross_notional: number;
  net_notional: number;
  priced: number;
}

/* ── Factor performance monitoring ───────────────────────────────────────────── */

export interface FactorScoreRow {
  factor: string;
  label: string;
  mean_ic: number;
  ic_ir: number;
  t_stat: number;
  hit_rate: number;
  q_spread: number;
  autocorr: number;
  n: number;
}

export interface FactorScorecard {
  universe: string;
  horizon: number;
  q: number;
  n_instruments: number;
  window_start: string;
  window_end: string;
  rows: FactorScoreRow[];
  errors: { factor: string; error: string }[];
  monitor?: string;
  snapshot_id?: number;
}

export interface FactorDetail {
  universe: string;
  factor: string;
  label: string;
  horizon: number;
  q: number;
  n_instruments: number;
  window_start: string;
  window_end: string;
  roll_window: number;
  metrics: { mean_ic: number; ic_ir: number; t_stat: number; hit_rate: number; autocorr: number; n: number };
  ic_series: LinePoint[];
  ic_decay: { horizon: number; value: number }[];
  quantile_returns: { bucket: number; value: number }[];
  turnover_series: LinePoint[];
  // 3-layer diagnostics (optional: older cached results may omit them)
  returns?: {
    ls_curve: LinePoint[];
    ls_drawdown: LinePoint[];
    ls_total_return: number;
    ls_sharpe: number;
    quantile_monotonicity: number | null;
  };
  risk?: {
    factor_correlations: { factor: string; label: string; corr: number }[];
    beta: number | null;
    market_corr: number | null;
    alpha_annual: number | null;
    benchmark: string;
  };
  health?: {
    regimes: { regime: string; ic: number | null; ls_return: number | null; n: number }[];
  };
}

export interface FactorMonitor {
  name: string;
  universe: string;
  factors: string[];
  horizon: number;
  q: number;
  lookback_days: number;
  notes?: string;
  updated?: string | null;
}

export interface MonitorHistory {
  monitor: string;
  n_snapshots: number;
  factors: Record<string, { label: string; mean_ic: LinePoint[]; ic_ir: LinePoint[] }>;
}

export interface FactorCorrelationMatrix {
  universe: string;
  method: string;
  n_instruments: number;
  window_start: string;
  window_end: string;
  factors: string[];
  labels: string[];
  matrix: (number | null)[][];
  errors: { factor: string; error: string }[];
}

export type FactorMonitorResultKind = "board" | "detail" | "history" | "heatmap";

/** Streamed frames from the chat-driven factor agent (`/api/factor-monitor/agent`).
 * Mirrors `BacktestAgentFrame`; a "result" carries a leaderboard / drill-down / history payload. */
export type FactorMonitorAgentFrame =
  | { type: "thought"; text: string }
  | {
      type: "result";
      id: string;
      kind: FactorMonitorResultKind;
      label: string;
      params: Record<string, string | number>;
      data: FactorScorecard | FactorDetail | MonitorHistory | FactorCorrelationMatrix;
    }
  | { type: "obs"; text: string; ok?: boolean }
  | { type: "done"; message: string; best_id?: string | null }
  | { type: "error"; detail: string };

/* ── Sandbox (author + run + save module code) ───────────────────────────────── */

export type SandboxMode = "factor" | "strategy" | "portfolio";
export type SandboxTrust = "sandboxed" | "trusted";

export interface SandboxTemplates {
  modes: SandboxMode[];
  trusts: SandboxTrust[];
  starters: Record<SandboxMode, Record<SandboxTrust, string>>;
}

export interface SandboxRunResult {
  kind: SandboxMode;
  ok: boolean;
  trust: SandboxTrust;
  // factor
  universe?: string;
  name?: string;
  ranking?: { symbol: string; score: number }[];
  n_scored?: number;
  n_universe?: number;
  errors?: number;
  truncated?: boolean;
  // strategy
  engine?: "lab" | "portfolio";
  preview?: BacktestResponse | LabResult;
  // portfolio
  mode?: string;
  allocations?: PortfolioAllocation[];
  exposures?: PortfolioExposure;
  allocation?: AllocateResult;
}

export interface SandboxSaveResult {
  ok: boolean;
  saved: string;
  name?: string;
  path?: string;
}

export type BacktestAgentFrame =
  | { type: "thought"; text: string }
  | {
      type: "run";
      id: string;
      label: string;
      params: Record<string, string | number>;
      metrics: Record<string, number | null>;
      result: BacktestResponse | LabResult;
    }
  | { type: "obs"; text: string; ok?: boolean }
  | { type: "done"; message: string; best_id?: string | null }
  | { type: "error"; detail: string };

export type AgentCoderFrame =
  | { type: "step"; tool?: string; args?: Record<string, unknown>; thought?: string }
  | { type: "obs"; text: string; ok?: boolean }
  | { type: "spec"; spec: AgentGraphSpec }
  | { type: "node"; id: string; status: "running" | "done" | "error" }
  | { type: "done"; message: string; spec?: AgentGraphSpec }
  | { type: "error"; detail: string };

export type AgentFrame =
  // `progress` (research node only) carries a live ResearchFrame so the Agent Workflow can mirror the
  // research loop into the Research Loop module while the node runs; clients reading only `status` ignore it.
  | { type: "node"; id: string; status: "running" | "done" | "error"; summary?: string; value?: unknown; progress?: ResearchFrame }
  | { type: "token"; id: string; text: string }
  | { type: "done" }
  | { type: "error"; detail: string };

/* ── Paper trading ──────────────────────────────────────────────────────────── */

export type BrokerKind = "sim" | "alpaca";

export interface PaperPosition {
  symbol: string;
  asset: Asset;
  quantity: number;
  avg_price: number;
  last: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pct: number;
}

export interface PaperExposure {
  gross: number;
  net: number;
  long: number;
  short: number;
  gross_pct: number;
  net_pct: number;
  long_count: number;
  short_count: number;
  largest_pct: number;
  concentration_hhi: number;
  by_asset: Record<string, { market_value: number; pct: number }>;
}

export interface PaperAccount {
  broker: BrokerKind;
  equity: number;
  cash: number;
  buying_power: number;
  realized_pnl: number | null;
  exposure: PaperExposure;
  positions: PaperPosition[];
}

export interface PaperConfig {
  broker: BrokerKind;
  /** When true, Alpaca is the primary broker and a local sim sandbox runs alongside it. */
  alpaca_active: boolean;
  commission_bps: number;
  slippage_bps: number;
}

/** Which paper book a request targets: the primary broker, the default sim sandbox (`sim`), or a
 * specific sim account (`sim:<id>`). The default book maps to sim account 1. */
export type PaperBook = "primary" | "sim" | `sim:${number}`;

/** A local sim paper account (one independent book). Mirrors the backend `/api/paper/accounts` row. */
export interface PaperSimAccount {
  id: number;
  name: string;
  cash: number;
  realized_total: number;
  initial_cash: number;
  commission_bps: number;
  slippage_bps: number;
  created: string;
  archived: boolean;
}

export interface PaperMetrics {
  cagr: number | null;
  ann_vol: number | null;
  sharpe: number | null;
  sortino: number | null;
  max_drawdown: number | null;
  calmar: number | null;
}

export interface PaperEquityPoint {
  ts: string;
  equity: number;
}

export interface PaperClosedTrade {
  id: number;
  ts: string;
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  fill_price: number | null;
  realized_pnl: number;
}

/** Trade-level analytics over the closed-P&L ledger (qhfi.evaluation.metrics.trade_stats). */
export interface PaperTradeStats {
  n_trades: number;
  n_wins: number;
  n_losses: number;
  win_rate: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  payoff_ratio: number;
  expectancy: number;
  largest_win: number;
  largest_loss: number;
  total_realized: number;
}

/** Tail-risk + rolling-Sharpe over the equity-curve return series. */
export interface PaperRisk {
  var_95: number | null;
  cvar_95: number | null;
  rolling_sharpe: number[];
}

/** Benchmark-relative stats (daily-resampled vs the benchmark, default SPY); null below 2 days. */
export interface PaperBenchmark {
  symbol: string;
  beta: number;
  alpha: number;
  tracking_error: number;
  information_ratio: number;
  up_capture: number;
  down_capture: number;
  correlation: number;
}

export interface PaperExposurePoint {
  ts: string;
  gross: number;
  net: number;
}

export interface PaperPerformance {
  broker: BrokerKind;
  equity_curve: PaperEquityPoint[];
  exposure_curve: PaperExposurePoint[];
  metrics: PaperMetrics;
  risk: PaperRisk;
  trade_stats: PaperTradeStats | null;
  pnl_by_symbol: { symbol: string; realized_pnl: number }[];
  benchmark: PaperBenchmark | null;
  realized_total: number | null;
  closed_trades: PaperClosedTrade[];
}

/** Operational/observability metrics for the sim book (GET /api/paper/ops). */
export interface PaperOps {
  broker: BrokerKind;
  applicable: boolean;
  counts: { filled?: number; open?: number; cancelled?: number };
  total: number;
  fill_rate: number | null;
  latency: { avg_s: number; p50_s: number; max_s: number; n: number } | null;
}

export type PaperOrderType = "market" | "limit" | "stop" | "stop_limit" | "trailing_stop";

export interface PaperOrder {
  id: number | string; // sim: int rowid; alpaca: order UUID
  ts: string;
  symbol: string;
  asset: Asset;
  side: "buy" | "sell";
  quantity: number;
  type: PaperOrderType;
  limit_price: number | null;
  status: "filled" | "open" | "cancelled";
  fill_price: number | null;
  broker_order_id: string | null;
  stop_price?: number | null;
  trail_pct?: number | null;
}

export interface PaperOrderIn {
  symbol: string;
  asset: Asset;
  side: "buy" | "sell";
  quantity: number;
  type: PaperOrderType;
  limit_price?: number | null;
  stop_price?: number | null;
  trail_pct?: number | null;
}

export interface PaperPreview {
  applicable: boolean;
  est_price?: number | null;
  est_cost?: number | null;
  buying_power?: number;
  buying_power_ok?: boolean;
  warnings?: string[];
}

export interface RebalanceOrder {
  symbol: string;
  asset: Asset;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  notional: number;
  target_weight: number;
  current_weight: number;
}

export interface RebalancePlan {
  applicable: boolean;
  executed: boolean;
  equity: number;
  orders: RebalanceOrder[];
  gate: { approved: boolean; reason: string };
  skipped: string[];
  order_ids?: string[];
  results?: { symbol: string; ok: boolean; order_id?: string; error?: string }[];
}

export interface RebalanceIn {
  weights: Record<string, number>;
  asset?: Asset;
  gross?: number | null;
  execute?: boolean;
}

export interface Health {
  status: string;
  qhfi: { ok: boolean; llm_model: string };
  llm: { ok: boolean; detail: string; base_url: string };
  data_dir: string;
  crypto_exchange: string;
  equity_stream: EquityStreamStatus;
  crypto_stream: { enabled: boolean }; // crypto live streaming on/off (bars/charts work regardless)
  depth: DepthMap; // per-class order-book source/token/availability
  trades: TradesMap; // per-class time-&-sales (tape) source/token/availability
  options?: OptionsStatus; // options-chain source status
  universes: string[];
  /** First-run data bootstrap progress (see backend app/services/bootstrap.py). */
  bootstrap?: {
    state: "idle" | "running" | "done" | "skipped" | "error";
    universe: string;
    total: number;
    done: number;
    failed: number;
    detail: string;
  };
}

/** One mounted backend route, as reported by GET /api/wiring (live FastAPI introspection). */
export interface WiringRoute {
  path: string;
  methods: string[];
  tags: string[];
  kind: "rest" | "ws";
  name: string;
}

/** Live backend module inventory the Map widget scans to reconcile its data layer. */
export interface WiringResponse {
  routes: WiringRoute[];
  services: { tag: string; count: number }[];
}

export interface WorkspaceMeta {
  name: string;
  updated: string | null;
}

/* ── /api/ws/stream frames ──────────────────────────────────────────────────── */

export interface TickerFrame {
  last: number | null;
  bid: number | null;
  ask: number | null;
  bid_size: number | null; // NBBO size-at-touch (shares for equities; base-asset vol for crypto)
  ask_size: number | null;
  change_pct: number | null;
  base_volume: number | null;
  ts: number | null;
}

export interface BookFrame {
  bids: [number, number][];
  asks: [number, number][];
  ts: number | null;
}

export interface TradeFrame {
  price: number;
  amount: number;
  side: "buy" | "sell" | null;
  ts: number | null;
}

export type StreamFrame =
  | { topic: string; type: "ticker"; data: TickerFrame }
  | { topic: string; type: "book"; data: BookFrame }
  | { topic: string; type: "trades"; data: TradeFrame[] }
  | { topic: string; type: "status"; data: { state: string; error?: string } }
  | { topic: string; type: "error"; data: { message: string } };

// ── Macro module ─────────────────────────────────────────────────────────────────
export interface MacroObs {
  date: string;
  value: number | null;
}

export interface MacroCatalogItem {
  id: string;
  label: string;
  group: "us" | "cross_country";
  obs: number;
  start: string | null;
  end: string | null;
}

export interface MacroCatalog {
  series: MacroCatalogItem[];
}

export interface MacroLatest {
  value: number | null;
  date: string | null;
  prev: number | null;
  change: number | null;
  change_pct: number | null;
}

export interface MacroCard {
  id: string;
  label: string;
  frequency_hint: string;
  latest: MacroLatest;
  spark: number[];
}

export interface MacroGrid {
  cards: MacroCard[];
}

export interface MacroSeries {
  series_id: string;
  label: string;
  frequency_hint: string;
  observations: MacroObs[];
}

export interface CurvePoint {
  tenor: string;
  years: number | null;
  value: number | null;
}

export interface RatesCurve {
  tenors: string[];
  latest: { date: string | null; points: CurvePoint[] };
  rows: { date: string; rates: Record<string, number | null> }[];
}

// Rates module — CME Treasury futures complex (qhfi lake market/rates/*).
export interface RatesFuture {
  symbol: string;
  name: string;
  tenor: string;
  contract_multiplier: number;
  modified_duration: number;
  quote: Quote;
  spark: number[];
}

export interface RatesFutures {
  futures: RatesFuture[];
}

export interface RatesFutureBars {
  symbol: string;
  name: string;
  tenor: string;
  candles: Candle[];
  volume: { time: string | number; value: number }[];
  quote: Quote;
}

export interface CrossCountrySeries {
  country: string;
  name: string;
  observations: MacroObs[];
}

export interface CrossCountry {
  indicator: string;
  label: string;
  countries: CrossCountrySeries[];
}

// Investment Committee (CrewAI multi-agent module)
export interface CommitteeMember {
  role: string;
  goal: string;
  backstory: string;
  instructions?: string; // optional per-agent focus appended to its task prompt
}

export interface KnowledgeFile {
  name: string;
  size: number;
  type: string;
  embeddable: boolean;
}

// Knowledge is stored on disk as Directory -> Committee -> Agent -> files.
export interface KnowledgeDir {
  dir: string;
  default: string;
  supported: string[];
}

export interface KnowledgeAgentNode {
  name: string;
  files: KnowledgeFile[];
}

export interface KnowledgeCommitteeNode {
  name: string;
  agents: KnowledgeAgentNode[];
}

export interface KnowledgeTree {
  dir: string;
  committees: KnowledgeCommitteeNode[];
  supported: string[];
}

export interface CommitteeVerdict {
  recommendation: string;
  conviction: string;
  sizing: string;
  key_risks: string[];
  dissent: string;
}

export interface CommitteeTemplate {
  name: string;
  members: CommitteeMember[];
  updated?: string;
}

export interface CommitteePreset {
  name: string;
  members: CommitteeMember[];
}

// ── Algo trading (always-on runner) ──────────────────────────────────────────
export type AlgoKind = "template" | "xsection";

export interface AlgoCadence {
  kind: "daily" | "interval";
  seconds?: number;
  at?: string; // daily: fire only after this local time, "HH:MM"
  tz?: string; // daily: timezone for `at`
}

export interface AlgoRisk {
  max_gross?: number | null;
  max_net?: number | null;
  max_position?: number | null;
  max_drawdown_kill?: number | null;
}

/** The create/update payload; also the shape stored per algo (plus id/last_run on reads). */
export interface AlgoIn {
  name: string;
  kind: AlgoKind;
  // template
  symbol?: string;
  asset?: Asset;
  timeframe?: Timeframe;
  strategy?: string;
  params?: Record<string, number>;
  direction?: "long_only" | "short_only" | "both";
  // xsection
  universe?: string;
  factor?: string;
  mode?: "long_short" | "long_only";
  top_pct?: number;
  // shared
  size_pct?: number;
  cadence?: AlgoCadence;
  risk?: AlgoRisk;
  armed?: boolean;
  book?: PaperBook; // which paper book to trade: 'primary' (default) | 'sim' sandbox
}

export interface Algo extends AlgoIn {
  id: string;
  last_run: string | null;
  updated?: string;
}

export interface AlgoStrategies {
  templates: LabStrategy[];
  factors: { key: string; label: string; direction?: string }[];
  universes: string[];
  broker: BrokerKind;
}

export interface AlgoRunnerStatus {
  paused: boolean;
  broker: BrokerKind;
  alpaca_active: boolean;
  running: boolean;
  armed_count: number;
  algo_count: number;
}

export interface AlgoSignal {
  kind: AlgoKind;
  symbol?: string;
  strategy?: string;
  signal?: number;            // template: -1 | 0 | 1
  last_price?: number;
  target_weight?: number;
  universe?: string;
  factor?: string;
  mode?: string;
  n_names?: number;
  top_weights?: { symbol: string; weight: number }[];
}

export interface AlgoIntendedOrder {
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  asset?: Asset;
}

export interface AlgoSubmittedOrder {
  order_id?: string;
  symbol: string;
  side?: "buy" | "sell";
  quantity?: number;
  error?: string;
}

/** One cycle result — returned by run/preview and stored in the run log (with id/ts on reads). */
export interface AlgoRun {
  id?: number;
  ts?: string;
  status: "ok" | "preview" | "rejected" | "killed" | "error" | "no_data" | "no_weights";
  broker?: BrokerKind;
  reason?: string;
  error?: string;
  signal?: AlgoSignal;
  target_weights?: Record<string, number>;
  orders?: AlgoIntendedOrder[];
  submitted?: AlgoSubmittedOrder[];
  equity?: number;
}

/* ── Research loop (autonomous design→generate→evaluate→reflect) ─────────────────── */

export interface ResearchExperiment {
  factors: string[];
  universe: string;
  mode: string;
  top_pct: number;
  gross: number;
  years: number;
  rebalance: string;
  rationale: string;
}

export interface ResearchScored {
  metrics: Record<string, number>;
  passed: boolean;
  checks: Record<string, boolean>;
  n_checks_passed: number;
  oos_sharpe: number | null;
  oos_sharpe_ratio: number | null;
  notes: string[];
}

/** Compact per-iteration record carried in `iteration`/`done`/`best` frames. */
export interface ResearchRecord {
  i: number;
  label: string;
  experiment: ResearchExperiment;
  objective: number | null;
  passed: boolean;
  n_checks_passed: number;
  checks: Record<string, boolean>;
  metrics: Record<string, number | null>;
  oos_sharpe_ratio: number | null;
  error: string | null;
}

/** Full persisted iteration (from `GET /api/research/runs/{id}`), carries the dashboard payload. */
export interface ResearchIteration {
  ts: string;
  i: number;
  experiment: ResearchExperiment;
  scored: ResearchScored | null;
  objective: number | null;
  error: string | null;
  result_payload: BacktestResponse | null;
}

export interface ResearchRunSummary {
  id: string;
  goal: string;
  status: string;
  best: ResearchRecord | null;
  created: string;
  updated: string;
}

export type ResearchFrame =
  | { type: "started"; run_id: string; goal: string }
  | { type: "analyze"; iteration: number; inventory: { factors: { key: string; label: string; kind: string }[]; factor_keys: string[]; universes: string[]; models: string[] } }
  | { type: "phase"; iteration: number; phase: "design" | "generate" | "evaluate" | "reflect"; status: string }
  | { type: "design"; iteration: number; experiment: ResearchExperiment }
  | { type: "evaluate"; iteration: number; scored: ResearchScored }
  | { type: "result"; iteration: number; payload: BacktestResponse }
  | { type: "iteration"; iteration: number; record: ResearchRecord; best: ResearchRecord | null }
  | { type: "reflect"; iteration: number; text: string; next_change: string }
  | { type: "error"; iteration?: number; phase?: string; detail: string }
  | { type: "done"; run_id: string; best_iteration: number | null; best: ResearchRecord | null; n_iterations: number };
