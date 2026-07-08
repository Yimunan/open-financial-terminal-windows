/** The data model behind the Map widget: a single, pure source of truth for how the terminal's
 * modules wire together. Three kinds of wiring are made explicit here:
 *   • link  — widgets sharing a color channel (red/blue/green) track the same symbol
 *   • send  — one widget hands a typed payload to another (registry `accepts` + emit sources below)
 *   • data  — a widget reads REST routes / WS topics served by a backend service
 *
 * `buildCatalogGraph()` derives the static architecture from the registry; `buildLiveGraph()`
 * introspects the open workspace. Both return a `ModuleGraph` the SVG renderer lays out via
 * `layoutGraph()`. No React here — keep it testable.
 */

import { WIDGETS, WIDGET_TYPES, type WidgetType } from "./widgetRegistry";
import { sendTargets, type SendPayloadKind } from "../state/intents";
import { LINK_CHANNELS, type Channel } from "../state/linking";
import type { WorkspaceSnapshot } from "../lib/terminalState";
import type { WiringResponse, WiringRoute } from "../api/types";

export type NodeKind = "widget" | "channel" | "route" | "service";
export type EdgeKind = "link" | "send" | "data";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  /** widget/channel coloring */
  channel?: Channel;
  /** catalog widget nodes: open this widget on click */
  widgetType?: WidgetType;
  /** live panel nodes: focus this Dockview panel on click */
  panelId?: string;
  /** secondary line (channel's active symbol, route method, service detail) */
  sub?: string;
  /** live: panel currently holds an unconsumed send hand-off */
  pending?: boolean;
  /** scan: a referenced route that no live backend route matches (drift) */
  stale?: boolean;
  /** scan: a live backend service nothing in the UI is wired to */
  unused?: boolean;
}

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  kind: EdgeKind;
  label?: string;
}

export interface ModuleGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ── backend wiring descriptor — the source of truth for the data layer ─────────────────────────────
// (The widget→data-source mapping otherwise lives only implicitly in each widget's imports.)

/** Backend service key → human label. */
export const SERVICES: Record<string, string> = {
  realtime: "Realtime Hub (CCXT + Alpaca)",
  market: "Market data (qhfi)",
  screener: "Factor screener",
  backtest: "Backtest engine",
  lab: "Strategy Lab",
  portfolio: "Portfolio & risk",
  assistant: "Assistant (LLM)",
  news: "News & sentiment",
  registry: "Factor/strategy/model registry",
  macro: "Macro / rates (FRED)",
  ficc: "FICC board",
  paper: "Paper broker",
  algo: "Algo runner",
  fundamentals: "Fundamentals & filings",
  agent: "Agent / research loops",
  sandbox: "Quant sandbox",
};

interface DataSource {
  rest?: string[];
  ws?: string[];
  service: string[];
}

/** Per-widget data dependencies (verified against each widget's `import { api }` / `subscribeStream`
 * usage). REST routes and WS topics are deduped into shared `route` nodes so converging
 * dependencies are visible. */
export const WIDGET_DATA_SOURCES: Partial<Record<WidgetType, DataSource>> = {
  watchlist: { rest: ["/api/watchlist", "/api/quote"], ws: ["ticker:*"], service: ["portfolio", "market", "realtime"] },
  market_board: { rest: ["/api/board", "/api/ficc/board", "/api/quote"], ws: ["ticker:*"], service: ["market", "ficc", "realtime"] },
  chart: { rest: ["/api/bars", "/api/quote", "/api/metrics"], ws: ["ticker:*", "trades:*"], service: ["market", "realtime"] },
  quote: { rest: ["/api/quote"], ws: ["ticker:*"], service: ["market", "realtime"] },
  orderbook: { rest: ["/api/quote"], ws: ["book:*"], service: ["realtime"] },
  timesales: { rest: ["/api/quote"], ws: ["trades:*"], service: ["realtime"] },
  news: { rest: ["/api/news"], service: ["news"] },
  topicnews: { rest: ["/api/news/topics"], service: ["news"] },
  screener: { rest: ["/api/screen/factors", "/api/ask", "/api/universes"], service: ["screener", "assistant"] },
  backtest: { rest: ["/api/backtest", "/api/backtest/proposals", "/api/lab/strategies"], service: ["backtest", "lab"] },
  portfolio: { rest: ["/api/holdings", "/api/watchlist", "/api/risk"], service: ["portfolio"] },
  assistant: { rest: ["/api/ask", "/api/assistant/tools/*", "/api/summarize"], service: ["assistant"] },
  paper: { rest: ["/api/paper/account", "/api/paper/orders", "/api/paper/performance"], service: ["paper"] },
  agent: { rest: ["/api/agent/graphs", "/api/agent/node-types"], service: ["agent"] },
  factors: { rest: ["/api/registry/factors"], service: ["registry"] },
  strategies: { rest: ["/api/registry/strategies"], service: ["registry"] },
  models: { rest: ["/api/registry/models"], service: ["registry"] },
  portfolios: { rest: ["/api/holdings", "/api/portfolio-books", "/api/portfolio-books/from-allocations", "/api/risk"], service: ["portfolio"] },
  sandbox: { rest: ["/api/sandbox"], service: ["sandbox"] },
  factor_monitor: { rest: ["/api/factor-monitor"], service: ["registry"] },
  filings: { rest: ["/api/filings", "/api/filings/insider"], service: ["fundamentals"] },
  listings: { rest: ["/api/listings/new"], service: ["market"] },
  macro: { rest: ["/api/macro/catalog", "/api/macro/series", "/api/macro/grid"], service: ["macro"] },
  ficc: { rest: ["/api/ficc/board", "/api/rates/curve", "/api/rates/futures"], service: ["ficc", "macro"] },
  committee: { rest: ["/api/committee"], service: ["agent"] },
  metrics: { rest: ["/api/metrics", "/api/metrics/rolling", "/api/fundamentals"], service: ["market", "fundamentals"] },
  chart_studio: { rest: ["/api/chart/*", "/api/bars"], service: ["market"] },
  market_making: { rest: ["/api/mm/backtest", "/api/mm/compare"], service: ["lab"] },
  risk_attribution: { rest: ["/api/risk/attribution"], service: ["portfolio"] },
  algo_trading: { rest: ["/api/algo/algos", "/api/algo/strategies", "/api/algo/status"], service: ["algo", "paper"] },
  research_loop: { rest: ["/api/research/runs"], service: ["agent"] },
};

/** The emit side of send routes (the registry only declares the accept side, via `accepts`). */
export const SEND_SOURCES: Record<SendPayloadKind, WidgetType[]> = {
  screen_result: ["screener"],
  backtest_result: ["backtest"],
  symbols: ["watchlist", "screener"],
};

// ── live backend scan (reconcile the static data layer against what's actually mounted) ────────────

/** Lookups derived from a live `GET /api/wiring` response. When passed to the graph builders, each
 * referenced REST route is re-assigned its true service (router tag), unmatched routes are flagged
 * `stale`, and backend services nothing references are surfaced as `unused`. */
export interface ScanContext {
  /** route ref (as written in WIDGET_DATA_SOURCES) → live router tag (the real service) */
  routeService: Map<string, string>;
  /** route refs that a live route matched */
  known: Set<string>;
  /** every live router tag (one per backend module) */
  liveServices: string[];
  /** total live routes seen — shown in the scan summary */
  routeCount: number;
}

function prefixBeforeParam(p: string): string {
  const i = p.indexOf("/{");
  return i === -1 ? p : p.slice(0, i);
}

/** Find the live route a (possibly wildcard / param-less) frontend ref points at. */
function liveMatch(ref: string, routes: WiringRoute[]): WiringRoute | undefined {
  const exact = routes.find((r) => r.path === ref);
  if (exact) return exact;
  if (ref.endsWith("*")) {
    const base = ref.slice(0, -1);
    return routes.find((r) => r.path.startsWith(base));
  }
  return routes.find((r) => r.path.startsWith(ref + "/") || prefixBeforeParam(r.path) === ref);
}

/** Reconcile every REST route the data layer declares against the live route table. */
export function buildScanContext(wiring: WiringResponse): ScanContext {
  const refs = new Set<string>();
  for (const ds of Object.values(WIDGET_DATA_SOURCES)) {
    for (const r of ds?.rest ?? []) refs.add(r);
  }
  const routeService = new Map<string, string>();
  const known = new Set<string>();
  for (const ref of refs) {
    const m = liveMatch(ref, wiring.routes);
    if (!m) continue;
    known.add(ref);
    if (m.tags[0]) routeService.set(ref, m.tags[0]);
  }
  return { routeService, known, liveServices: wiring.services.map((s) => s.tag), routeCount: wiring.routes.length };
}

// ── ids ────────────────────────────────────────────────────────────────────────────────────────
const channelId = (c: string) => `chan:${c}`;
const routeId = (r: string) => `route:${r}`;
const serviceId = (s: string) => `svc:${s}`;

// ── builders ─────────────────────────────────────────────────────────────────────────────────────

function ensureService(svc: string, nodes: Map<string, GraphNode>): string {
  const sid = serviceId(svc);
  if (!nodes.has(sid)) nodes.set(sid, { id: sid, kind: "service", label: SERVICES[svc] ?? svc });
  return sid;
}

/** Add the widget → route → service data edges for one widget node, creating route/service nodes on
 * demand (deduped via the shared `nodes` map). With a `scan`, each REST route is bound to its live
 * service (router tag) and flagged `stale` when no live route matches; without one, the route maps
 * to the widget's statically-declared services. */
function addDataLayer(
  widgetNodeId: string,
  type: WidgetType,
  nodes: Map<string, GraphNode>,
  edges: GraphEdge[],
  scan?: ScanContext,
) {
  const ds = WIDGET_DATA_SOURCES[type];
  if (!ds) return;
  const routes = [
    ...(ds.rest ?? []).map((r) => ({ r, rest: true })),
    ...(ds.ws ?? []).map((r) => ({ r, rest: false })),
  ];
  for (const { r, rest } of routes) {
    const rid = routeId(r);
    const stale = scan && rest ? !scan.known.has(r) : false;
    let sub = rest ? "REST" : "WS";
    if (stale) sub = "REST · missing";
    if (!nodes.has(rid)) nodes.set(rid, { id: rid, kind: "route", label: r, sub, stale });
    edges.push({ id: `data:${widgetNodeId}->${rid}`, from: widgetNodeId, to: rid, kind: "data" });

    // route → service: live router tag when scanned & matched, else the widget's static services.
    const live = scan && rest ? scan.routeService.get(r) : undefined;
    const services = live ? [live] : ds.service;
    for (const svc of services) {
      const sid = ensureService(svc, nodes);
      const eid = `data:${rid}->${sid}`;
      if (!edges.some((e) => e.id === eid)) edges.push({ id: eid, from: rid, to: sid, kind: "data" });
    }
  }
}

/** After a scan, surface every live backend service — including ones nothing in the UI references
 * (flagged `unused`) — so scanning visibly grows the graph with the real backend module set. */
function addUnusedServices(nodes: Map<string, GraphNode>, scan: ScanContext) {
  for (const tag of scan.liveServices) {
    const sid = serviceId(tag);
    if (!nodes.has(sid)) nodes.set(sid, { id: sid, kind: "service", label: SERVICES[tag] ?? tag, unused: true });
  }
}

/** Static architecture: every widget type, every channel group, every send route, full data layer.
 * Pass a `scan` to bind routes to their live services, flag stale refs, and reveal unused services. */
export function buildCatalogGraph(scan?: ScanContext): ModuleGraph {
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];

  // channel hubs
  for (const c of LINK_CHANNELS) {
    nodes.set(channelId(c), { id: channelId(c), kind: "channel", label: `${c} channel`, channel: c });
  }

  // widget nodes + channel link edges
  for (const type of WIDGET_TYPES) {
    const meta = WIDGETS[type];
    const wid = `widget:${type}`;
    nodes.set(wid, { id: wid, kind: "widget", label: meta.title, channel: meta.defaultChannel, widgetType: type });
    if (meta.defaultChannel !== "none") {
      edges.push({ id: `link:${wid}`, from: wid, to: channelId(meta.defaultChannel), kind: "link" });
    }
    addDataLayer(wid, type, nodes, edges, scan);
  }

  if (scan) addUnusedServices(nodes, scan);

  // send routes: every emit source → every accepting target, per payload kind
  for (const kind of Object.keys(SEND_SOURCES) as SendPayloadKind[]) {
    for (const src of SEND_SOURCES[kind]) {
      for (const tgt of sendTargets(kind)) {
        if (src === tgt) continue;
        edges.push({
          id: `send:${kind}:${src}->${tgt}`,
          from: `widget:${src}`,
          to: `widget:${tgt}`,
          kind: "send",
          label: kind,
        });
      }
    }
  }

  return { nodes: [...nodes.values()], edges };
}

const HANDOFF_KEYS = ["symbols", "incomingUniverse", "incomingSymbols", "incomingFactor", "incomingStrategy"];

/** Live workspace: open panels grouped by their link channel, plus each panel's data layer, plus a
 * `pending` flag on any panel still holding an unconsumed send hand-off. Pass a `scan` to reconcile
 * the open panels' data sources against the live backend. */
export function buildLiveGraph(snapshot: WorkspaceSnapshot, scan?: ScanContext): ModuleGraph {
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  const usedChannels = new Set<Channel>();

  for (const panel of snapshot.panels) {
    const type = panel.type as WidgetType;
    const meta = WIDGETS[type];
    if (!meta) continue; // unknown panel type — skip defensively
    const channel = ((panel.params.channel as Channel) ?? meta.defaultChannel) ?? "none";
    const pending = HANDOFF_KEYS.some((k) => panel.params[k] != null);
    const wid = `panel:${panel.id}`;
    nodes.set(wid, {
      id: wid,
      kind: "widget",
      label: meta.title,
      channel,
      panelId: panel.id,
      pending,
    });
    if (channel !== "none") {
      usedChannels.add(channel);
      edges.push({ id: `link:${wid}`, from: wid, to: channelId(channel), kind: "link" });
    }
    addDataLayer(wid, type, nodes, edges, scan);
  }

  if (scan) addUnusedServices(nodes, scan);

  // channel hubs (only those with ≥1 open panel), labeled with the channel's active symbol
  for (const c of LINK_CHANNELS) {
    if (!usedChannels.has(c)) continue;
    const sym = snapshot.channels[c]?.symbol;
    nodes.set(channelId(c), { id: channelId(c), kind: "channel", label: `${c} channel`, channel: c, sub: sym });
  }

  return { nodes: [...nodes.values()], edges };
}

// ── deterministic layered layout (no physics) ──────────────────────────────────────────────────────

export interface NodeLayout {
  x: number;
  y: number;
  w: number;
  h: number;
}

const COLUMN_OF: Record<NodeKind, number> = { service: 0, route: 1, widget: 2, channel: 3 };
const COL_X = [40, 320, 640, 980]; // left edge of each column
const NODE_W: Record<NodeKind, number> = { service: 200, route: 220, widget: 180, channel: 150 };
const NODE_H = 34;
const ROW_GAP = 16;
const CHANNEL_ORDER: Record<string, number> = { red: 0, blue: 1, green: 2, none: 3 };

/** Assign each node an absolute box. Columns left→right: service | route | widget | channel.
 * Widgets are grouped by channel so link edges stay short. */
export function layoutGraph(graph: ModuleGraph): Map<string, NodeLayout> {
  const byColumn = new Map<number, GraphNode[]>();
  for (const n of graph.nodes) {
    const col = COLUMN_OF[n.kind];
    if (!byColumn.has(col)) byColumn.set(col, []);
    byColumn.get(col)!.push(n);
  }

  const out = new Map<string, NodeLayout>();
  for (const [col, list] of byColumn) {
    // widgets: sort by channel then label; others: stable by label
    list.sort((a, b) => {
      if (col === COLUMN_OF.widget) {
        const ca = CHANNEL_ORDER[a.channel ?? "none"] ?? 3;
        const cb = CHANNEL_ORDER[b.channel ?? "none"] ?? 3;
        if (ca !== cb) return ca - cb;
      }
      return a.label.localeCompare(b.label);
    });
    list.forEach((n, i) => {
      out.set(n.id, {
        x: COL_X[col],
        y: 24 + i * (NODE_H + ROW_GAP),
        w: NODE_W[n.kind],
        h: NODE_H,
      });
    });
  }
  return out;
}
