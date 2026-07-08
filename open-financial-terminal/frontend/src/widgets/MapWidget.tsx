/** Module Map: an SVG view of how the terminal's modules wire together. Two modes:
 *   • Catalog — the static architecture (all widget types, channel groups, send routes, data layer)
 *   • Live    — the open workspace (panels grouped by channel, their data sources, pending sends)
 * Edge-type chips (Links/Sends/Data) declutter; nodes pan/zoom; click opens (catalog) or focuses
 * (live). All wiring data + layout come from workspace/moduleGraph — this file is just the renderer.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { WidgetShell, TextButton } from "./shell";
import type { WidgetProps } from "../workspace/widgetRegistry";
import {
  buildCatalogGraph,
  buildLiveGraph,
  buildScanContext,
  layoutGraph,
  type EdgeKind,
  type GraphNode,
  type ModuleGraph,
  type NodeLayout,
  type ScanContext,
} from "../workspace/moduleGraph";
import { useWorkspace } from "../state/workspace";
import { useLinking, CHANNEL_DOT, type Channel } from "../state/linking";
import { snapshotWorkspace, type WorkspaceSnapshot } from "../lib/terminalState";
import { dispatchIntent } from "../state/intents";
import { api as http } from "../api/client";

type Mode = "live" | "catalog";
interface ViewBox { x: number; y: number; w: number; h: number; }

const SEND_COLOR = "#f5a623";
const DATA_COLOR = "rgb(var(--term-muted))";
const STALE_COLOR = "rgb(var(--term-down))";

interface ScanInfo {
  routes: number;
  services: number;
  stale: number;
  unused: number;
}

function channelColor(c?: Channel): string {
  return c && c !== "none" ? CHANNEL_DOT[c] : "rgb(var(--term-border))";
}

function truncate(s: string, w: number): string {
  const max = Math.max(4, Math.floor((w - 14) / 6.2));
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function fitBox(layout: Map<string, NodeLayout>): ViewBox {
  if (layout.size === 0) return { x: 0, y: 0, w: 1200, h: 600 };
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const b of layout.values()) {
    minX = Math.min(minX, b.x);
    minY = Math.min(minY, b.y);
    maxX = Math.max(maxX, b.x + b.w);
    maxY = Math.max(maxY, b.y + b.h);
  }
  const pad = 48;
  return { x: minX - pad, y: minY - pad, w: maxX - minX + pad * 2, h: maxY - minY + pad * 2 };
}

/** Cubic-bezier path between two node boxes; bulges to the side for same-column (send) edges. */
function edgePath(a: NodeLayout, b: NodeLayout): string {
  const sameCol = Math.abs(a.x - b.x) < 1;
  if (sameCol) {
    const sx = a.x + a.w, sy = a.y + a.h / 2;
    const tx = b.x + b.w, ty = b.y + b.h / 2;
    const bulge = 70;
    return `M${sx},${sy} C${sx + bulge},${sy} ${tx + bulge},${ty} ${tx},${ty}`;
  }
  if (b.x > a.x) {
    const sx = a.x + a.w, sy = a.y + a.h / 2, tx = b.x, ty = b.y + b.h / 2;
    const dx = (tx - sx) / 2;
    return `M${sx},${sy} C${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`;
  }
  const sx = a.x, sy = a.y + a.h / 2, tx = b.x + b.w, ty = b.y + b.h / 2;
  const dx = (sx - tx) / 2;
  return `M${sx},${sy} C${sx - dx},${sy} ${tx + dx},${ty} ${tx},${ty}`;
}

export default function MapWidget(props: WidgetProps) {
  const [mode, setMode] = useState<Mode>(props.params.mode === "catalog" ? "catalog" : "live");
  const [show, setShow] = useState<Record<EdgeKind, boolean>>({ link: true, send: true, data: true });
  const [hovered, setHovered] = useState<string | null>(null);
  const [scan, setScan] = useState<ScanContext | null>(null);
  const [scanInfo, setScanInfo] = useState<ScanInfo | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  // Scan the live backend (GET /api/wiring) and reconcile the data layer against what's mounted.
  const runScan = async () => {
    setScanning(true);
    setScanError(null);
    try {
      const wiring = await http.wiring();
      setScan(buildScanContext(wiring));
    } catch (e) {
      setScanError((e as Error).message);
      setScan(null);
    } finally {
      setScanning(false);
    }
  };

  const api = useWorkspace((s) => s.api);
  const channelSymbols = useLinking((s) => s.symbols);
  const [snap, setSnap] = useState<WorkspaceSnapshot>(() => snapshotWorkspace());

  // Live reactivity: re-snapshot on layout / active-panel changes and whenever channel symbols move.
  useEffect(() => {
    if (mode !== "live" || !api) return;
    const refresh = () => setSnap(snapshotWorkspace());
    refresh();
    const d1 = api.onDidLayoutChange(refresh);
    const d2 = api.onDidActivePanelChange(refresh);
    return () => {
      d1.dispose();
      d2.dispose();
    };
  }, [mode, api, channelSymbols]);

  const graph: ModuleGraph = useMemo(
    () => (mode === "catalog" ? buildCatalogGraph(scan ?? undefined) : buildLiveGraph(snap, scan ?? undefined)),
    [mode, snap, scan],
  );
  const layout = useMemo(() => layoutGraph(graph), [graph]);
  const nodeById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);

  // Scan summary (recomputed from the graph so it reflects the active mode).
  useEffect(() => {
    if (!scan) {
      setScanInfo(null);
      return;
    }
    const services = graph.nodes.filter((n) => n.kind === "service");
    const routes = graph.nodes.filter((n) => n.kind === "route");
    setScanInfo({
      routes: scan.routeCount,
      services: services.length,
      stale: routes.filter((n) => n.stale).length,
      unused: services.filter((n) => n.unused).length,
    });
  }, [scan, graph]);

  // neighbors of the hovered node (for dim-the-rest highlighting)
  const focus = useMemo(() => {
    if (!hovered) return null;
    const keep = new Set<string>([hovered]);
    for (const e of graph.edges) {
      if (e.from === hovered) keep.add(e.to);
      if (e.to === hovered) keep.add(e.from);
    }
    return keep;
  }, [hovered, graph]);

  // viewBox: fit on mount + whenever mode changes; manual pan/zoom otherwise.
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const [vb, setVb] = useState<ViewBox>(() => fitBox(layout));
  useEffect(() => {
    setVb(fitBox(layoutRef.current));
  }, [mode]);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; vb: ViewBox } | null>(null);

  // wheel zoom (native listener so we can preventDefault)
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = (e.clientX - rect.left) / rect.width;
      const py = (e.clientY - rect.top) / rect.height;
      setVb((cur) => {
        const factor = e.deltaY > 0 ? 1.12 : 1 / 1.12;
        const w = Math.min(6000, Math.max(200, cur.w * factor));
        const h = Math.min(6000, Math.max(120, cur.h * factor));
        return { x: cur.x + (cur.w - w) * px, y: cur.y + (cur.h - h) * py, w, h };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const onBackgroundDown = (e: React.MouseEvent) => {
    drag.current = { x: e.clientX, y: e.clientY, vb };
  };
  const onMove = (e: React.MouseEvent) => {
    const d = drag.current;
    const el = svgRef.current;
    if (!d || !el) return;
    const scale = d.vb.w / el.clientWidth;
    setVb({ x: d.vb.x - (e.clientX - d.x) * scale, y: d.vb.y - (e.clientY - d.y) * scale, w: d.vb.w, h: d.vb.h });
  };
  const endDrag = () => {
    drag.current = null;
  };

  const onNodeClick = (n: GraphNode) => {
    if (n.panelId) {
      api?.getPanel(n.panelId)?.api.setActive();
    } else if (n.widgetType) {
      dispatchIntent({ kind: "open", widget: n.widgetType });
    }
  };

  const chip = (kind: EdgeKind, label: string) => (
    <TextButton active={show[kind]} onClick={() => setShow((s) => ({ ...s, [kind]: !s[kind] }))}>
      {label}
    </TextButton>
  );

  return (
    <WidgetShell
      toolbar={
        <div className="flex items-center gap-2">
          <TextButton active={mode === "live"} onClick={() => setMode("live")}>Live</TextButton>
          <TextButton active={mode === "catalog"} onClick={() => setMode("catalog")}>Catalog</TextButton>
          <span className="mx-1 h-4 w-px bg-term-border" />
          {chip("link", "Links")}
          {chip("send", "Sends")}
          {chip("data", "Data")}
          <span className="mx-1 h-4 w-px bg-term-border" />
          <TextButton onClick={() => setVb(fitBox(layout))}>Fit</TextButton>
          <TextButton
            active={!!scan}
            onClick={() => (scan ? setScan(null) : runScan())}
            title="Scan the live backend (/api/wiring) and reconcile the data layer against the mounted routes/services"
          >
            {scanning ? "Scanning…" : scan ? "Scanned ✓" : "Scan"}
          </TextButton>
          {scanInfo && (
            <span className="text-[10px] text-term-muted">
              {scanInfo.routes} routes · {scanInfo.services} services
              {scanInfo.stale > 0 && <span className="text-term-down"> · {scanInfo.stale} stale</span>}
              {scanInfo.unused > 0 && <span> · {scanInfo.unused} unused</span>}
            </span>
          )}
          {scanError && <span className="text-[10px] text-term-down">scan failed: {scanError}</span>}
        </div>
      }
    >
      <div className="relative h-full w-full">
        <svg
          ref={svgRef}
          className="h-full w-full cursor-grab active:cursor-grabbing select-none"
          viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
          onMouseDown={onBackgroundDown}
          onMouseMove={onMove}
          onMouseUp={endDrag}
          onMouseLeave={endDrag}
        >
          <defs>
            <marker id="map-arrow-send" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill={SEND_COLOR} />
            </marker>
          </defs>

          {/* edges */}
          <g fill="none">
            {graph.edges.map((e) => {
              if (!show[e.kind]) return null;
              const a = layout.get(e.from);
              const b = layout.get(e.to);
              if (!a || !b) return null;
              const dim = focus && !(focus.has(e.from) && focus.has(e.to));
              const color =
                e.kind === "send" ? SEND_COLOR : e.kind === "data" ? DATA_COLOR : channelColor(nodeById.get(e.to)?.channel);
              return (
                <path
                  key={e.id}
                  d={edgePath(a, b)}
                  stroke={color}
                  strokeWidth={e.kind === "send" ? 1.6 : 1.1}
                  strokeOpacity={dim ? 0.06 : e.kind === "data" ? 0.32 : 0.7}
                  strokeDasharray={e.kind === "data" ? "3 3" : undefined}
                  markerEnd={e.kind === "send" ? "url(#map-arrow-send)" : undefined}
                />
              );
            })}
          </g>

          {/* nodes */}
          <g>
            {graph.nodes.map((n) => {
              const b = layout.get(n.id);
              if (!b) return null;
              const dim = focus && !focus.has(n.id);
              const border = n.stale
                ? STALE_COLOR
                : n.kind === "channel" || n.kind === "widget"
                  ? channelColor(n.channel)
                  : n.kind === "service"
                    ? "rgb(var(--term-accent))"
                    : "rgb(var(--term-border))";
              const clickable = !!(n.widgetType || n.panelId);
              return (
                <g
                  key={n.id}
                  transform={`translate(${b.x},${b.y})`}
                  opacity={dim ? 0.18 : n.unused ? 0.5 : 1}
                  style={{ cursor: clickable ? "pointer" : "default" }}
                  onMouseEnter={() => setHovered(n.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => onNodeClick(n)}
                >
                  {n.pending && (
                    <rect className="animate-pulse" x={-3} y={-3} width={b.w + 6} height={b.h + 6} rx={7} fill="none" stroke={SEND_COLOR} strokeWidth={1.5} />
                  )}
                  <rect
                    width={b.w}
                    height={b.h}
                    rx={5}
                    fill={n.kind === "channel" ? channelColor(n.channel) : "rgb(var(--term-panel))"}
                    fillOpacity={n.kind === "channel" ? 0.18 : 1}
                    stroke={border}
                    strokeWidth={n.kind === "service" || n.kind === "channel" || n.stale ? 1.6 : 1}
                    strokeDasharray={n.kind === "route" || n.unused ? "4 2" : undefined}
                  />
                  <text x={10} y={n.sub ? 15 : b.h / 2 + 4} fontSize={11} fill="rgb(var(--term-text))" className="font-medium">
                    {truncate(n.label, b.w)}
                  </text>
                  {n.sub && (
                    <text x={10} y={27} fontSize={9} fill="rgb(var(--term-muted))">
                      {truncate(n.sub, b.w)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>

        {/* legend */}
        <div className="pointer-events-none absolute bottom-2 left-2 flex flex-col gap-1 rounded border border-term-border bg-term-panel/85 px-2 py-1.5 text-[10px] text-term-muted">
          <div className="flex items-center gap-3">
            <Legend swatch={<span className="inline-block h-2 w-3 rounded" style={{ background: CHANNEL_DOT.red }} />} label="channel link" />
            <Legend swatch={<span className="inline-block h-0.5 w-4" style={{ background: SEND_COLOR }} />} label="send route" />
            <Legend swatch={<span className="inline-block h-0.5 w-4 border-t border-dashed border-term-muted" />} label="data dep" />
          </div>
          <div className="flex items-center gap-3">
            <Legend swatch={<span className="inline-block h-2.5 w-2.5 rounded border" style={{ borderColor: "rgb(var(--term-accent))" }} />} label="service" />
            <Legend swatch={<span className="inline-block h-2.5 w-2.5 rounded border border-dashed border-term-muted" />} label="route" />
            <span>{mode === "live" ? `${snap.panels.length} open` : `${graph.nodes.filter((n) => n.kind === "widget").length} widgets`}</span>
          </div>
        </div>
      </div>
    </WidgetShell>
  );
}

function Legend({ swatch, label }: { swatch: React.ReactNode; label: string }) {
  return (
    <span className="flex items-center gap-1">
      {swatch}
      {label}
    </span>
  );
}
