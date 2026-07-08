import type {
  Asset,
  BacktestResponse,
  BarsResponse,
  BoardResponse,
  FactorMeta,
  FilingCategory,
  FilingsResponse,
  Fundamentals,
  Health,
  HoldersResponse,
  HoldingsResponse,
  InsiderResponse,
  NewsResponse,
  Quote,
  RiskResponse,
  ScreenResponse,
  SearchHit,
  Timeframe,
  WatchItem,
  WiringResponse,
  WorkspaceMeta,
} from "./types";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<Health>("/api/health"),
  // Live backend module inventory for the Map widget's "Scan" action.
  wiring: () => get<WiringResponse>("/api/wiring"),
  search: (q: string) => get<{ results: SearchHit[] }>(`/api/search?q=${encodeURIComponent(q)}`),
  bars: (symbol: string, asset: Asset, timeframe: Timeframe, indicators: string[]) => {
    const ind = indicators.length ? `&indicators=${encodeURIComponent(indicators.join(","))}` : "";
    return get<BarsResponse>(
      `/api/bars?symbol=${encodeURIComponent(symbol)}&asset=${asset}&timeframe=${timeframe}${ind}`,
    );
  },
  quote: (symbol: string, asset: Asset, spark = 0) =>
    get<Quote & { symbol: string }>(
      `/api/quote?symbol=${encodeURIComponent(symbol)}&asset=${asset}${spark ? `&spark=${spark}` : ""}`,
    ),
  universes: () => get<{ universes: string[] }>("/api/universes"),

  // Market Board (curated multi-asset symbol sections — quotes fetched per-row via /api/quote)
  board: () => get<BoardResponse>("/api/board"),

  // FICC board (rates / FX / commodity complexes by native asset class — quotes per-row via /api/quote)
  ficcBoard: () => get<BoardResponse>("/api/ficc/board"),

  // Watchlist (server-side so it survives reloads and is shared across widgets)
  watchlist: () => get<{ items: WatchItem[] }>("/api/watchlist"),
  addWatch: (symbol: string, asset: Asset) =>
    send<{ items: WatchItem[] }>("POST", "/api/watchlist", { symbol, asset }),
  removeWatch: (symbol: string) =>
    send<{ items: WatchItem[] }>("DELETE", `/api/watchlist/${encodeURIComponent(symbol)}`),
  reorderWatch: (order: string[]) =>
    send<{ items: WatchItem[] }>("POST", "/api/watchlist/reorder", { order }),

  // Screener
  screenFactors: () => get<{ factors: FactorMeta[] }>("/api/screen/factors"),
  screen: (universe: string, factor: string, limit = 25) =>
    send<ScreenResponse>("POST", "/api/screen", { universe, factor, limit }),

  // Portfolio & risk
  risk: (items: WatchItem[], days?: number) =>
    send<RiskResponse>("POST", "/api/risk", days ? { items, days } : { items }),
  portfolioRisk: (
    positions: { symbol: string; asset: Asset; quantity: number }[],
    opts?: { days?: number; benchmark?: string },
  ) =>
    send<import("./types").PortfolioRisk>("POST", "/api/risk/portfolio", { positions, ...opts }),
  riskAttribution: (
    source: "holdings" | "paper" = "holdings",
    opts?: { window_days?: number; base_universe?: string; account?: number },
  ) =>
    send<import("./types").RiskAttributionResponse>("POST", "/api/risk/attribution", {
      source,
      ...opts,
    }),
  returnAttribution: (
    source: "holdings" | "paper" = "holdings",
    opts?: { window_days?: number; base_universe?: string; account?: number },
  ) =>
    send<import("./types").ReturnAttributionResponse>("POST", "/api/risk/return-attribution", {
      source,
      ...opts,
    }),
  brinsonAttribution: (
    source: "holdings" | "paper" = "holdings",
    opts?: { window_days?: number; base_universe?: string; account?: number },
  ) =>
    send<import("./types").BrinsonAttributionResponse>("POST", "/api/risk/brinson", {
      source,
      ...opts,
    }),
  holdings: () => get<HoldingsResponse>("/api/holdings"),
  // Portfolio books (named multi-book holdings; the holdings endpoints act on the active book)
  portfolioBooks: () =>
    get<import("./types").PortfolioBooksResponse>("/api/portfolio-books"),
  createPortfolioBook: (name: string) =>
    send<import("./types").PortfolioBooksResponse & { created: number }>(
      "POST", "/api/portfolio-books", { name }),
  // Export a target-weight allocation into a NEW book, valued into shares at current prices.
  createBookFromAllocations: (
    name: string,
    allocations: import("./types").PortfolioAllocation[],
    capital: number,
  ) =>
    send<import("./types").PortfolioBooksResponse & { created: number; seeded: number; priced: number; rows: number }>(
      "POST", "/api/portfolio-books/from-allocations", { name, allocations, capital }),
  renamePortfolioBook: (id: number, name: string) =>
    send<import("./types").PortfolioBooksResponse>("PUT", `/api/portfolio-books/${id}`, { name }),
  deletePortfolioBook: (id: number) =>
    send<import("./types").PortfolioBooksResponse>("DELETE", `/api/portfolio-books/${id}`),
  setActivePortfolioBook: (id: number) =>
    send<import("./types").PortfolioBooksResponse>("PUT", "/api/portfolio-books/active", { id }),
  portfolioComposition: (opts?: { start?: string; end?: string; years?: number }) =>
    send<import("./types").CompositionResponse>("POST", "/api/portfolio/composition", opts ?? {}),
  putHolding: (h: { symbol: string; asset: Asset; quantity: number; cost_basis: number }) =>
    send<{ ok: boolean }>("PUT", "/api/holdings", h),
  deleteHolding: (symbol: string) =>
    send<{ ok: boolean }>("DELETE", `/api/holdings/${encodeURIComponent(symbol)}`),

  // Research
  fundamentals: (symbol: string) =>
    get<Fundamentals>(`/api/fundamentals?symbol=${encodeURIComponent(symbol)}`),

  // Metrics (per-symbol, asset-class-tailored analytics tearsheet)
  metrics: (symbol: string, asset: Asset) =>
    get<import("./types").MetricsResponse>(
      `/api/metrics?symbol=${encodeURIComponent(symbol)}&asset=${asset}`,
    ),
  metricsRolling: (symbol: string, asset: Asset, window: number) =>
    get<import("./types").MetricsRolling>(
      `/api/metrics/rolling?symbol=${encodeURIComponent(symbol)}&asset=${asset}&window=${window}`,
    ),
  news: (symbol: string, sentiment = true, rank = true) =>
    get<NewsResponse>(
      `/api/news?symbol=${encodeURIComponent(symbol)}&sentiment=${sentiment}&rank=${rank}`,
    ),
  // Symbol-agnostic topic feeds (Topic News widget: built-in Market/Macro + user "interest" topics)
  newsTopic: (category: string, sentiment = true, rank = true) =>
    get<import("./types").TopicNewsResponse>(
      `/api/news/topic?category=${encodeURIComponent(category)}&sentiment=${sentiment}&rank=${rank}`,
    ),
  // Selectable topics for the launcher (built-ins + enabled user topics)
  newsTopics: () =>
    get<{ topics: import("./types").AvailableTopic[] }>("/api/news/topics"),
  // User-topic CRUD (Settings → News → Topics) + keyword preview
  newsTopicsConfig: () =>
    get<{ topics: import("./types").NewsTopic[] }>("/api/settings/news/topics"),
  saveNewsTopics: (topics: import("./types").NewsTopic[]) =>
    send<{ topics: import("./types").NewsTopic[] }>("PUT", "/api/settings/news/topics", { topics }),
  previewNewsTopic: (query: string) =>
    send<import("./types").NewsSourceTest>("POST", "/api/settings/news/topics/preview", { query }),

  // Macro module (FRED + World Bank series, Treasury yield curve — all from the qhfi lake)
  macroCatalog: () => get<import("./types").MacroCatalog>("/api/macro/catalog"),
  macroGrid: () => get<import("./types").MacroGrid>("/api/macro/grid"),
  macroSeries: (id: string, start?: string, end?: string) => {
    const qs = [start ? `start=${start}` : "", end ? `end=${end}` : ""].filter(Boolean).join("&");
    return get<import("./types").MacroSeries>(
      `/api/macro/series/${encodeURIComponent(id)}${qs ? `?${qs}` : ""}`,
    );
  },
  macroRatesCurve: (start?: string, end?: string) => {
    const qs = [start ? `start=${start}` : "", end ? `end=${end}` : ""].filter(Boolean).join("&");
    return get<import("./types").RatesCurve>(`/api/macro/rates/curve${qs ? `?${qs}` : ""}`);
  },
  macroCrossCountry: (indicator: string, start?: string, end?: string) => {
    const qs = [`indicator=${encodeURIComponent(indicator)}`, start ? `start=${start}` : "", end ? `end=${end}` : ""].filter(Boolean).join("&");
    return get<import("./types").CrossCountry>(`/api/macro/cross-country?${qs}`);
  },

  // Rates module (Treasury yield curve + CME Treasury futures complex — from the qhfi lake)
  ratesCurve: (start?: string, end?: string) => {
    const qs = [start ? `start=${start}` : "", end ? `end=${end}` : ""].filter(Boolean).join("&");
    return get<import("./types").RatesCurve>(`/api/rates/curve${qs ? `?${qs}` : ""}`);
  },
  ratesFutures: () => get<import("./types").RatesFutures>("/api/rates/futures"),
  ratesFutureBars: (symbol: string, start?: string, end?: string) => {
    const qs = [start ? `start=${start}` : "", end ? `end=${end}` : ""].filter(Boolean).join("&");
    return get<import("./types").RatesFutureBars>(
      `/api/rates/futures/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ""}`,
    );
  },

  // New listings (SEC EDGAR detection: 8-A12B exchange listings + 424B4 IPO prospectuses)
  newListings: (days = 14, withTickerOnly = true) =>
    get<import("./types").NewListingsResponse>(
      `/api/listings/new?days=${days}&with_ticker_only=${withTickerOnly}`,
    ),

  // Public filings (SEC EDGAR: feed + insider transactions + institutional holders)
  filings: (symbol: string, category: FilingCategory = "all") =>
    get<FilingsResponse>(
      `/api/filings?symbol=${encodeURIComponent(symbol)}&category=${category}`,
    ),
  filingsInsider: (symbol: string) =>
    get<InsiderResponse>(`/api/filings/insider?symbol=${encodeURIComponent(symbol)}`),
  filingsHolders: (symbol: string) =>
    get<HoldersResponse>(`/api/filings/holders?symbol=${encodeURIComponent(symbol)}`),

  // LLM assistant
  ask: (query: string) => send<ScreenResponse>("POST", "/api/ask", { query }),
  summarize: (symbol: string, asset: Asset) =>
    send<{ symbol: string; summary: string }>("POST", "/api/summarize", { symbol, asset }),
  summarizeRegistry: (kind: "factors" | "strategies" | "models") =>
    send<{ kind: string; summary: string }>("POST", "/api/registry/summarize", { kind }),

  // Backtest
  backtest: (body: {
    universe: string;
    factor: string;
    mode: string;
    top_pct: number;
    years: number;
    deflate: boolean;
    start?: string | null;
    end?: string | null;
  }) => send<BacktestResponse>("POST", "/api/backtest", body),

  // Strategy Lab
  labStrategies: () => get<{ strategies: import("./types").LabStrategy[] }>("/api/lab/strategies"),
  labRun: (body: Partial<import("./types").LabRequest>) =>
    send<import("./types").LabResult>("POST", "/api/lab/run", body),
  labSweep: (body: Record<string, unknown>) =>
    send<import("./types").SweepResult>("POST", "/api/lab/sweep", body),

  // Market making — qhfi quoting-strategy backtests over real bars + synthetic depth
  mmBacktest: (body: Partial<import("./types").MMRequest>) =>
    send<import("./types").MMResult>("POST", "/api/mm/backtest", body),
  mmCompare: (body: Partial<import("./types").MMRequest>) =>
    send<import("./types").MMCompareResult>("POST", "/api/mm/compare", body),

  // Paper trading
  paperConfig: (account = 1) =>
    get<import("./types").PaperConfig>(`/api/paper/config?account=${account}`),
  setPaperConfig: (body: { commission_bps: number; slippage_bps: number }, account = 1) =>
    send<import("./types").PaperConfig>("POST", `/api/paper/config?account=${account}`, body),
  // Sim accounts (multi-book CRUD)
  paperAccounts: () =>
    get<{ accounts: import("./types").PaperSimAccount[] }>("/api/paper/accounts"),
  createPaperAccount: (body: {
    name: string;
    initial_cash?: number;
    commission_bps?: number;
    slippage_bps?: number;
  }) =>
    send<{ ok: boolean; account: import("./types").PaperSimAccount }>(
      "POST", "/api/paper/accounts", body),
  updatePaperAccount: (
    id: number,
    body: { name?: string; initial_cash?: number; commission_bps?: number; slippage_bps?: number },
  ) =>
    send<{ ok: boolean; account: import("./types").PaperSimAccount }>(
      "PATCH", `/api/paper/accounts/${id}`, body),
  resetPaperAccount: (id: number) =>
    send<{ ok: boolean; account: import("./types").PaperSimAccount }>(
      "POST", `/api/paper/accounts/${id}/reset`),
  deletePaperAccount: (id: number) =>
    send<{ ok: boolean }>("DELETE", `/api/paper/accounts/${id}`),
  paperAccount: (book?: import("./types").PaperBook) =>
    get<import("./types").PaperAccount>(`/api/paper/account${book ? `?book=${book}` : ""}`),
  paperPerformance: (book?: import("./types").PaperBook) =>
    get<import("./types").PaperPerformance>(`/api/paper/performance${book ? `?book=${book}` : ""}`),
  paperOps: (book?: import("./types").PaperBook) =>
    get<import("./types").PaperOps>(`/api/paper/ops${book ? `?book=${book}` : ""}`),
  paperOrders: (book?: import("./types").PaperBook) =>
    get<{ orders: import("./types").PaperOrder[] }>(`/api/paper/orders${book ? `?book=${book}` : ""}`),
  submitPaperOrder: (body: import("./types").PaperOrderIn, book?: import("./types").PaperBook) =>
    send<{ order_id: string; ok: boolean; book: string }>(
      "POST",
      `/api/paper/orders${book ? `?book=${book}` : ""}`,
      body,
    ),
  previewPaperOrder: (body: import("./types").PaperOrderIn, account = 1) =>
    send<import("./types").PaperPreview>("POST", `/api/paper/preview?account=${account}`, body),
  cancelPaperOrder: (id: number | string, book?: import("./types").PaperBook) =>
    send<{ ok: boolean }>("DELETE", `/api/paper/orders/${encodeURIComponent(id)}${book ? `?book=${book}` : ""}`),
  closePaperPosition: (symbol: string, asset: import("./types").Asset, book?: import("./types").PaperBook) =>
    send<{ order_id: string; ok: boolean }>("POST", `/api/paper/close${book ? `?book=${book}` : ""}`, { symbol, asset }),
  flattenPaper: (book?: import("./types").PaperBook) =>
    send<{ order_ids: string[]; ok: boolean; closed: number }>("POST", `/api/paper/flatten${book ? `?book=${book}` : ""}`),
  rebalancePaper: (body: import("./types").RebalanceIn, book?: import("./types").PaperBook) =>
    send<import("./types").RebalancePlan>("POST", `/api/paper/rebalance${book ? `?book=${book}` : ""}`, body),
  resetPaper: () => send<{ ok: boolean }>("POST", "/api/paper/reset"),

  // Algo trading (always-on runner: scheduled strategies → paper broker)
  algoStrategies: () => get<import("./types").AlgoStrategies>("/api/algo/strategies"),
  algoStatus: () => get<import("./types").AlgoRunnerStatus>("/api/algo/status"),
  algoPause: () => send<import("./types").AlgoRunnerStatus>("POST", "/api/algo/pause"),
  algoResume: () => send<import("./types").AlgoRunnerStatus>("POST", "/api/algo/resume"),
  algos: () => get<{ algos: import("./types").Algo[]; broker: string }>("/api/algo/algos"),
  createAlgo: (body: import("./types").AlgoIn) =>
    send<import("./types").Algo>("POST", "/api/algo/algos", body),
  updateAlgo: (id: string, body: import("./types").AlgoIn) =>
    send<import("./types").Algo>("PUT", `/api/algo/algos/${encodeURIComponent(id)}`, body),
  deleteAlgo: (id: string) =>
    send<{ ok: boolean }>("DELETE", `/api/algo/algos/${encodeURIComponent(id)}`),
  armAlgo: (id: string) =>
    send<import("./types").Algo>("POST", `/api/algo/algos/${encodeURIComponent(id)}/arm`),
  disarmAlgo: (id: string) =>
    send<import("./types").Algo>("POST", `/api/algo/algos/${encodeURIComponent(id)}/disarm`),
  runAlgo: (id: string) =>
    send<import("./types").AlgoRun>("POST", `/api/algo/algos/${encodeURIComponent(id)}/run`),
  previewAlgo: (body: import("./types").AlgoIn) =>
    send<import("./types").AlgoRun>("POST", "/api/algo/preview", body),
  algoRuns: (id: string, limit = 50) =>
    get<{ runs: import("./types").AlgoRun[] }>(
      `/api/algo/algos/${encodeURIComponent(id)}/runs?limit=${limit}`,
    ),

  // Agent workflow (LangGraph)
  agentNodeTypes: () =>
    get<{ node_types: import("./types").AgentNodeType[] }>("/api/agent/node-types"),
  agentGraphs: () => get<{ graphs: import("./types").AgentGraphMeta[] }>("/api/agent/graphs"),
  loadAgentGraph: (name: string) =>
    get<{ name: string; spec: import("./types").AgentGraphSpec }>(
      `/api/agent/graphs/${encodeURIComponent(name)}`,
    ),
  saveAgentGraph: (name: string, spec: import("./types").AgentGraphSpec) =>
    send<{ ok: boolean }>("PUT", `/api/agent/graphs/${encodeURIComponent(name)}`, { spec }),
  deleteAgentGraph: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/agent/graphs/${encodeURIComponent(name)}`),
  researchRuns: () =>
    get<{ runs: import("./types").ResearchRunSummary[] }>("/api/research/runs"),
  researchRun: (id: string) =>
    get<{ run: import("./types").ResearchRunSummary; iterations: import("./types").ResearchIteration[] }>(
      `/api/research/runs/${encodeURIComponent(id)}`,
    ),
  deleteResearchRun: (id: string) =>
    send<{ ok: boolean }>("DELETE", `/api/research/runs/${encodeURIComponent(id)}`),
  assistAgent: (spec: import("./types").AgentGraphSpec, message: string) =>
    send<{ ok: boolean; spec: import("./types").AgentGraphSpec; message: string }>(
      "POST",
      "/api/agent/assist",
      { spec, message },
    ),

  // Agent workflow scenarios (named variable presets + market shocks)
  agentScenarios: () =>
    get<{ scenarios: import("./types").AgentScenario[] }>("/api/agent/scenarios"),
  saveScenario: (name: string, body: Partial<import("./types").AgentScenario>) =>
    send<{ ok: boolean }>("PUT", `/api/agent/scenarios/${encodeURIComponent(name)}`, body),
  deleteScenario: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/agent/scenarios/${encodeURIComponent(name)}`),

  // Registries (factors / strategies / models)
  registryFactors: () =>
    get<{
      builtin: import("./types").RegFactor[];
      custom: import("./types").RegFactor[];
      engine: import("./types").EngineFactor[];
      linked: import("./types").ScanDir;
    }>("/api/registry/factors"),
  saveFactor: (name: string, body: Partial<import("./types").RegFactor>) =>
    send<{ ok: boolean }>("PUT", `/api/registry/factors/${encodeURIComponent(name)}`, body),
  deleteFactor: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/registry/factors/${encodeURIComponent(name)}`),
  registryStrategies: () =>
    get<{
      builtin: import("./types").RegStrategy[];
      custom: import("./types").RegStrategy[];
      engine: import("./types").EngineStrategy[];
      linked: import("./types").ScanDir;
    }>("/api/registry/strategies"),
  saveStrategy: (name: string, body: Partial<import("./types").RegStrategy>) =>
    send<{ ok: boolean }>("PUT", `/api/registry/strategies/${encodeURIComponent(name)}`, body),
  deleteStrategy: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/registry/strategies/${encodeURIComponent(name)}`),
  runEngineStrategy: (body: import("./types").EngineRunRequest) =>
    send<import("./types").BacktestResponse>("POST", "/api/registry/strategies/run", body),
  registryModels: (q = "") =>
    get<{ models: import("./types").RegModel[] }>(`/api/registry/models?q=${encodeURIComponent(q)}`),
  saveModel: (name: string, body: Partial<import("./types").RegModel>) =>
    send<{ ok: boolean }>("PUT", `/api/registry/models/${encodeURIComponent(name)}`, body),
  deleteModel: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/registry/models/${encodeURIComponent(name)}`),

  // Linked qhfi directories (factors / strategies / models)
  registryPaths: () => get<import("./types").RegPaths>("/api/registry/paths"),
  savePaths: (body: Partial<import("./types").RegPaths>) =>
    send<import("./types").RegPaths>("PUT", "/api/registry/paths", body),

  // Backtest "Ideas": scan saved factors/models → design runnable backtest proposals
  backtestProposals: (params?: {
    n?: number;
    universe?: string;
    factor?: string;
    factors?: string[];
    models?: string[];
  }) => {
    const q = new URLSearchParams();
    if (params?.n) q.set("n", String(params.n));
    if (params?.universe) q.set("universe", params.universe);
    if (params?.factor) q.set("factor", params.factor);
    if (params?.factors?.length) q.set("factors", params.factors.join(","));
    if (params?.models?.length) q.set("models", params.models.join(","));
    const qs = q.toString();
    return get<import("./types").BacktestProposalsResponse>(`/api/backtest/proposals${qs ? `?${qs}` : ""}`);
  },
  // Back-test a saved research-model bundle directly → dashboard payload
  backtestModel: (body: { model: string; years?: number | null; mode?: string | null }) =>
    send<BacktestResponse>("POST", "/api/backtest/model", body),

  // Trained-model repository (linked qhfi ModelRepository)
  repoModels: () => get<import("./types").RepoModelsResponse>("/api/registry/repo-models"),
  promoteRepoModel: (name: string, body: { version: number; stage: string }) =>
    send<import("./types").RepoModelVersion & { name: string }>(
      "POST",
      `/api/registry/repo-models/${encodeURIComponent(name)}/promote`,
      body,
    ),

  // Factor performance monitoring (IC / decay / quantile / turnover + saved monitors)
  factorScorecard: (body: { universe: string; factors?: string[] | null; horizon?: number; q?: number; lookback_days?: number }) =>
    send<import("./types").FactorScorecard>("POST", "/api/factor-monitor/scorecard", body),
  factorDetail: (body: { universe: string; factor: string; horizon?: number; q?: number; lookback_days?: number; roll_window?: number }) =>
    send<import("./types").FactorDetail>("POST", "/api/factor-monitor/detail", body),
  factorMonitors: () =>
    get<{ monitors: import("./types").FactorMonitor[] }>("/api/factor-monitor/monitors"),
  saveFactorMonitor: (name: string, body: Partial<import("./types").FactorMonitor>) =>
    send<{ ok: boolean }>("PUT", `/api/factor-monitor/monitors/${encodeURIComponent(name)}`, body),
  deleteFactorMonitor: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/factor-monitor/monitors/${encodeURIComponent(name)}`),
  runFactorMonitor: (name: string) =>
    send<import("./types").FactorScorecard>("POST", `/api/factor-monitor/monitors/${encodeURIComponent(name)}/run`),
  factorMonitorHistory: (name: string) =>
    get<import("./types").MonitorHistory>(`/api/factor-monitor/monitors/${encodeURIComponent(name)}/history`),

  // Sandbox (author + run + save factor/strategy/portfolio code, sandboxed or trusted)
  sandboxTemplates: () => get<import("./types").SandboxTemplates>("/api/sandbox/templates"),
  sandboxRun: (body: {
    mode: import("./types").SandboxMode;
    trust: import("./types").SandboxTrust;
    code: string;
    context: Record<string, unknown>;
  }) => send<import("./types").SandboxRunResult>("POST", "/api/sandbox/run", body),
  sandboxSave: (body: {
    mode: import("./types").SandboxMode;
    trust: import("./types").SandboxTrust;
    name: string;
    code: string;
    meta?: Record<string, unknown>;
    allocations?: import("./types").PortfolioAllocation[] | null;
  }) => send<import("./types").SandboxSaveResult>("POST", "/api/sandbox/save", body),

  // Portfolios (saved weight + allocation lists)
  registryPortfolios: (q = "") =>
    get<{ portfolios: import("./types").Portfolio[] }>(`/api/registry/portfolios?q=${encodeURIComponent(q)}`),
  savePortfolio: (name: string, body: Partial<import("./types").Portfolio>) =>
    send<{ ok: boolean }>("PUT", `/api/registry/portfolios/${encodeURIComponent(name)}`, body),
  deletePortfolio: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/registry/portfolios/${encodeURIComponent(name)}`),
  normalizePortfolio: (body: { allocations: import("./types").PortfolioAllocation[]; mode: string }) =>
    send<import("./types").NormalizeResult>("POST", "/api/registry/portfolios/normalize", body),
  allocatePortfolio: (body: { allocations: import("./types").PortfolioAllocation[]; capital: number }) =>
    send<import("./types").AllocateResult>("POST", "/api/registry/portfolios/allocate", body),

  // Workspaces
  workspaces: () => get<{ workspaces: WorkspaceMeta[] }>("/api/workspaces"),
  loadWorkspace: (name: string) =>
    get<{ name: string; layout: object }>(`/api/workspaces/${encodeURIComponent(name)}`),
  saveWorkspace: (name: string, layout: object) =>
    send<{ ok: boolean }>("PUT", `/api/workspaces/${encodeURIComponent(name)}`, { layout }),
  deleteWorkspace: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/workspaces/${encodeURIComponent(name)}`),

  // Workspace templates (reusable layout snapshots)
  templates: () => get<{ templates: WorkspaceMeta[] }>("/api/templates"),
  loadTemplate: (name: string) =>
    get<{ name: string; layout: object }>(`/api/templates/${encodeURIComponent(name)}`),
  saveTemplate: (name: string, layout: object) =>
    send<{ ok: boolean }>("PUT", `/api/templates/${encodeURIComponent(name)}`, { layout }),
  deleteTemplate: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/templates/${encodeURIComponent(name)}`),

  // LLM provider (local proxy vs an online OpenAI-compatible API)
  llmSettings: () => get<import("./types").LlmSettings>("/api/settings/llm"),
  llmModels: () => get<import("./types").LlmTestResult>("/api/settings/llm/models"),
  saveLlmSettings: (body: { base_url: string; api_key?: string; model?: string }) =>
    send<import("./types").LlmSettings>("PUT", "/api/settings/llm", body),

  // News sources (which feeds the News widget pulls from)
  newsSettings: () => get<import("./types").NewsSourceSettings>("/api/settings/news"),
  saveNewsSettings: (body: {
    builtin: Record<string, boolean>;
    builtin_weights: Record<string, number>;
    custom: import("./types").NewsSource[];
    max_items: number;
    ranking: import("./types").RankingParams;
  }) => send<import("./types").NewsSourceSettings>("PUT", "/api/settings/news", body),
  testNewsSource: (body: { url: string; name?: string }) =>
    send<import("./types").NewsSourceTest>("POST", "/api/settings/news/test", body),
  discoverNewsSources: (query: string) =>
    send<import("./types").NewsDiscoverResult>("POST", "/api/settings/news/discover", { query }),

  // External MCP servers (whose tools the grounded assistant can call)
  mcpSettings: () => get<import("./types").McpSettings>("/api/settings/mcp"),
  saveMcpSettings: (body: { servers: import("./types").McpServer[] }) =>
    send<import("./types").McpSettings>("PUT", "/api/settings/mcp", body),
  testMcpServer: (body: import("./types").McpServer) =>
    send<import("./types").McpTestResult>("POST", "/api/settings/mcp/test", body),

  // Market data (crypto exchange, cache/history, Alpaca creds, data-store + realtime status)
  marketDataSettings: () => get<import("./types").MarketDataSettings>("/api/settings/market-data"),
  saveMarketDataSettings: (body: {
    categories: import("./types").MarketDataCategories; // canonical per-asset-class config
    alpaca_api_key?: string;
    alpaca_api_secret?: string;
    alpaca_paper: boolean;
  }) => send<import("./types").MarketDataSettings>("PUT", "/api/settings/market-data", body),
  testMarketDataExchange: (exchange: string) =>
    send<import("./types").OkResult>("POST", "/api/settings/market-data/test", { exchange }),
  // Probe Alpaca market-data creds for the equity realtime source (blank key/secret → saved creds)
  testMarketDataEquity: (body: { api_key?: string; api_secret?: string; feed?: string }) =>
    send<import("./types").OkResult>("POST", "/api/settings/market-data/test-equity", body),
  // Probe an order-book depth source for an asset class (blank source → the saved depth source)
  testMarketDataDepth: (body: { asset: string; source?: string }) =>
    send<import("./types").OkResult>("POST", "/api/settings/market-data/test-depth", body),
  // Probe the options-chain source (blank → saved source/underlying)
  testMarketDataOptions: (body: { source?: string; underlying?: string }) =>
    send<import("./types").OkResult>("POST", "/api/settings/market-data/test-options", body),

  // Options chains (standalone chain subsystem)
  optionExpirations: (underlying: string) =>
    get<import("./types").OptionExpirationsResponse>(
      `/api/options/expirations?underlying=${encodeURIComponent(underlying)}`,
    ),
  optionChain: (underlying: string, expiry: string) =>
    get<import("./types").OptionChainResponse>(
      `/api/options/chain?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}`,
    ),
  // Single-leg option paper order (local sim book). Premium is per-contract (not ×100).
  submitOptionOrder: (body: {
    underlying?: string; expiry?: string; strike?: number; right?: "call" | "put"; occ?: string;
    side: "buy" | "sell"; quantity: number; type?: string; limit_price?: number;
  }) => send<{ order_id: string; ok: boolean; book: string; occ: string }>(
    "POST", "/api/paper/option-order", body,
  ),
  // Multi-leg (combo) option paper order — 2–4 legs, market, local sim book.
  submitComboOrder: (body: import("./types").ComboOrderRequest) =>
    send<import("./types").ComboOrderResult>("POST", "/api/paper/combo-order", body),
  // Remove the saved Alpaca credentials entirely (broker falls back to the local sim)
  removeAlpacaCreds: () =>
    send<import("./types").MarketDataSettings>("DELETE", "/api/settings/market-data/alpaca"),
  clearMarketDataCache: () =>
    send<import("./types").OkResult>("POST", "/api/settings/market-data/clear-cache"),

  // Market-data vendor providers (Databento / Polygon / Tradier / dxFeed / IBKR credentials).
  // Stored encrypted server-side; the status view never returns a saved secret.
  providerSettings: () => get<import("./types").ProviderSettings>("/api/settings/providers"),
  saveProvider: (body: import("./types").ProviderIn) =>
    send<import("./types").ProviderSettings>("PUT", "/api/settings/providers", body),
  removeProvider: (name: string) =>
    send<import("./types").ProviderSettings>(
      "DELETE",
      `/api/settings/providers/${encodeURIComponent(name)}`,
    ),

  // Filesystem browse — sub-directories of a path, for the in-app folder picker (dir settings).
  fsList: (path = "") =>
    get<import("./types").FsList>(
      `/api/settings/fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`,
    ),

  // Automatic background data refresh (per-job enable/interval, status, manual trigger)
  dataRefreshStatus: () =>
    get<import("./types").DataRefreshStatus>("/api/settings/data-refresh/status"),
  saveDataRefreshConfig: (body: import("./types").DataRefreshConfigIn) =>
    send<import("./types").DataRefreshStatus>("PUT", "/api/settings/data-refresh/config", body),
  runDataRefreshJob: (job: string) =>
    send<import("./types").DataRefreshJobResult>(
      "POST",
      `/api/settings/data-refresh/${encodeURIComponent(job)}/run`,
    ),

  // Investment Committee (CrewAI) — default roster + knowledge-base management
  committeeRoster: () =>
    get<{ members: import("./types").CommitteeMember[] }>("/api/committee/roster"),
  committeePresets: () =>
    get<{ presets: import("./types").CommitteePreset[] }>("/api/committee/presets"),

  // Knowledge: configurable base directory + Directory/Committee/Agent file store
  committeeKnowledgeDir: () => get<import("./types").KnowledgeDir>("/api/committee/knowledge-dir"),
  setCommitteeKnowledgeDir: (dir: string) =>
    send<import("./types").KnowledgeDir>("PUT", "/api/committee/knowledge-dir", { dir }),
  committeeKnowledgeTree: () => get<import("./types").KnowledgeTree>("/api/committee/knowledge/tree"),
  agentFiles: (committee: string, agent: string) =>
    get<{ committee: string; agent: string; files: import("./types").KnowledgeFile[] }>(
      `/api/committee/knowledge/files?committee=${encodeURIComponent(committee)}&agent=${encodeURIComponent(agent)}`,
    ),
  uploadAgentFile: async (committee: string, agent: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const qs = `committee=${encodeURIComponent(committee)}&agent=${encodeURIComponent(agent)}`;
    const res = await fetch(`/api/committee/knowledge/files?${qs}`, { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<{ ok: boolean; file: import("./types").KnowledgeFile; files: import("./types").KnowledgeFile[] }>;
  },
  deleteAgentFile: (committee: string, agent: string, name: string) =>
    send<{ ok: boolean; files: import("./types").KnowledgeFile[] }>(
      "DELETE",
      `/api/committee/knowledge/files?committee=${encodeURIComponent(committee)}&agent=${encodeURIComponent(agent)}&name=${encodeURIComponent(name)}`,
    ),

  // Committee templates (reusable named rosters)
  committeeTemplates: () =>
    get<{ templates: import("./types").CommitteeTemplate[] }>("/api/committee/templates"),
  saveCommitteeTemplate: (name: string, members: import("./types").CommitteeMember[]) =>
    send<{ ok: boolean; name: string }>(
      "PUT",
      `/api/committee/templates/${encodeURIComponent(name)}`,
      { members },
    ),
  deleteCommitteeTemplate: (name: string) =>
    send<{ ok: boolean }>("DELETE", `/api/committee/templates/${encodeURIComponent(name)}`),
};

/** Frames the chat/control socket streams to the client. `action` frames (control loop only)
 * require the client to reply `{op:"observation", id, ok, result}` so the agent can continue. */
export type ChatFrame =
  | { type: "thought"; text: string }
  | { type: "tool"; name: string; args: Record<string, unknown>; summary?: unknown }
  | { type: "action"; id: string; name: string; args: Record<string, unknown> }
  | { type: "obs"; text: string }
  | { type: "token"; text: string }
  | { type: "done" }
  | { type: "error"; detail: string };

/** Open the streaming chat/control WebSocket. Returns the socket; caller wires onmessage. */
export function openChatSocket(): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${location.host}/api/ws/chat`);
}

/** Open the Investment Committee convene WebSocket (relays the CrewAI deliberation SSE). */
export function openCommitteeSocket(): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${location.host}/api/committee/ws`);
}

/** Open the agent-workflow run WebSocket. */
export function openAgentSocket(): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${location.host}/api/agent/run`);
}

/** Open the agentic workflow-coder WebSocket (edit→run→fix loop). */
export function openAgentCoderSocket(): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${location.host}/api/agent/code`);
}

/** Open the chat-driven backtest agent WebSocket (factor / lab autonomous loop). */
export function openBacktestAgentSocket(): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${location.host}/api/backtest/agent`);
}

/** Open the chat-driven factor-performance agent WebSocket (rank / drill / monitor loop). */
export function openFactorMonitorAgentSocket(): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${location.host}/api/factor-monitor/agent`);
}

/** Open the chat-driven Chart Studio agent WebSocket (NL → rendered chart). */
export function openChartAgentSocket(): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${location.host}/api/chart/agent`);
}

/** Open the autonomous research-loop WebSocket (design→generate→evaluate→reflect, ≤5 iterations). */
export function openResearchLoopSocket(): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${location.host}/api/research/run`);
}
