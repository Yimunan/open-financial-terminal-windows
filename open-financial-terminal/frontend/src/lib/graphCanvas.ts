/** GraphCanvas — a dependency-free canvas engine for the agent-workflow builder.
 *
 * Renders nodes (rounded rects with input/output ports + status ring) and edges (bezier
 * curves between ports). Interactions: drag a node to reposition, drag from an output port
 * to an input port to create an edge, click a node to select, click an edge to remove,
 * wheel-zoom + background-drag pan. Same conventions as candleChart.ts: one canvas, HiDPI,
 * rAF-batched, colors injected from the theme tokens.
 *
 * State (node positions + status) is owned by the React widget and pushed via setData; the
 * canvas reports edits through the `opts` callbacks (onMove/onSelect/onConnect/onDeleteEdge).
 */

export interface GraphColors {
  up: string;
  down: string;
  accent: string;
  text: string;
  muted: string;
  border: string;
  panel: string;
  bg: string;
}

export type NodeStatus = "idle" | "running" | "done" | "error";

export interface GNode {
  id: string;
  type: string;
  label: string;
  x: number;
  y: number;
  inputs: number; // 0 or 1 input port
  status: NodeStatus;
}

export interface GEdge {
  source: string;
  target: string;
}

export interface GraphData {
  nodes: GNode[];
  edges: GEdge[];
  selected: string | null;
}

export interface GraphOpts {
  onMove?: (id: string, x: number, y: number) => void;
  onSelect?: (id: string | null) => void;
  onConnect?: (source: string, target: string) => void;
  onDeleteEdge?: (source: string, target: string) => void;
}

const NODE_W = 150;
const NODE_H = 52;
const PORT_R = 5;

export class GraphCanvas {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private colors: GraphColors;
  private opts: GraphOpts;
  private ro: ResizeObserver;

  private data: GraphData = { nodes: [], edges: [], selected: null };
  private view = { x: 0, y: 0, scale: 1 }; // pan + zoom
  private dragNode: { id: string; dx: number; dy: number } | null = null;
  private panning: { sx: number; sy: number; ox: number; oy: number } | null = null;
  private linking: { source: string; mx: number; my: number } | null = null;
  private dirty = false;
  private raf = 0;
  private animRaf = 0; // continuous loop, alive only while a node is "running"
  private destroyed = false;

  constructor(container: HTMLElement, colors: GraphColors, opts: GraphOpts = {}) {
    this.container = container;
    this.colors = colors;
    this.opts = opts;
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "display:block;width:100%;height:100%;cursor:default";
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;
    this.ro = new ResizeObserver(() => this.invalidate());
    this.ro.observe(container);
    this.canvas.addEventListener("wheel", this.onWheel, { passive: false });
    this.canvas.addEventListener("pointerdown", this.onPointerDown);
    this.canvas.addEventListener("pointermove", this.onPointerMove);
    this.canvas.addEventListener("pointerup", this.onPointerUp);
    this.canvas.addEventListener("dblclick", this.onDblClick);
  }

  setData(data: GraphData): void {
    this.data = data;
    // Drive a live animation loop while any step is running (pulsing node + flowing edges);
    // otherwise a single one-shot render is enough.
    if (this.needsAnim()) this.startAnim();
    else {
      this.stopAnim();
      this.invalidate();
    }
  }

  private needsAnim(): boolean {
    return this.data.nodes.some((n) => n.status === "running");
  }
  private startAnim(): void {
    if (this.animRaf || this.destroyed) return;
    this.animRaf = requestAnimationFrame(this.tick);
  }
  private stopAnim(): void {
    if (!this.animRaf) return;
    cancelAnimationFrame(this.animRaf);
    this.animRaf = 0;
  }
  private tick = (): void => {
    if (this.destroyed) return;
    this.render();
    if (this.needsAnim()) this.animRaf = requestAnimationFrame(this.tick);
    else {
      this.animRaf = 0;
      this.render(); // final frame with the loop's animated overlays cleared
    }
  };

  setColors(colors: GraphColors): void {
    this.colors = colors;
    this.invalidate();
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    cancelAnimationFrame(this.animRaf);
    this.ro.disconnect();
    this.canvas.remove();
  }

  /* ── coordinate transforms ────────────────────────────────────────────────── */
  private toWorld(cx: number, cy: number): { x: number; y: number } {
    return { x: (cx - this.view.x) / this.view.scale, y: (cy - this.view.y) / this.view.scale };
  }

  private nodeAt(wx: number, wy: number): GNode | null {
    // topmost last; iterate reverse
    for (let i = this.data.nodes.length - 1; i >= 0; i--) {
      const n = this.data.nodes[i];
      if (wx >= n.x && wx <= n.x + NODE_W && wy >= n.y && wy <= n.y + NODE_H) return n;
    }
    return null;
  }

  private outPortAt(wx: number, wy: number): GNode | null {
    for (const n of this.data.nodes) {
      const px = n.x + NODE_W;
      const py = n.y + NODE_H / 2;
      if (Math.hypot(wx - px, wy - py) <= PORT_R + 4) return n;
    }
    return null;
  }

  private inPortAt(wx: number, wy: number): GNode | null {
    for (const n of this.data.nodes) {
      if (n.inputs < 1) continue;
      const px = n.x;
      const py = n.y + NODE_H / 2;
      if (Math.hypot(wx - px, wy - py) <= PORT_R + 4) return n;
    }
    return null;
  }

  private edgeAt(wx: number, wy: number): GEdge | null {
    for (const e of this.data.edges) {
      const s = this.byId(e.source);
      const t = this.byId(e.target);
      if (!s || !t) continue;
      const x1 = s.x + NODE_W, y1 = s.y + NODE_H / 2;
      const x2 = t.x, y2 = t.y + NODE_H / 2;
      // sample the bezier and test distance
      for (let k = 0; k <= 20; k++) {
        const p = this.bezier(x1, y1, x2, y2, k / 20);
        if (Math.hypot(wx - p.x, wy - p.y) < 6) return e;
      }
    }
    return null;
  }

  private byId(id: string): GNode | undefined {
    return this.data.nodes.find((n) => n.id === id);
  }

  private bezier(x1: number, y1: number, x2: number, y2: number, t: number) {
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
    const cx1 = x1 + dx, cx2 = x2 - dx;
    const mt = 1 - t;
    const x = mt * mt * mt * x1 + 3 * mt * mt * t * cx1 + 3 * mt * t * t * cx2 + t * t * t * x2;
    const y = mt * mt * mt * y1 + 3 * mt * mt * t * cy(y1) + 3 * mt * t * t * cy(y2) + t * t * t * y2;
    return { x, y };
    function cy(v: number) {
      return v;
    }
  }

  /* ── interactions ─────────────────────────────────────────────────────────── */
  private onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const rect = this.canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const before = this.toWorld(cx, cy);
    const factor = Math.exp(-e.deltaY * 0.0012);
    this.view.scale = Math.min(Math.max(this.view.scale * factor, 0.3), 2.5);
    const after = this.toWorld(cx, cy);
    this.view.x += (after.x - before.x) * this.view.scale;
    this.view.y += (after.y - before.y) * this.view.scale;
    this.invalidate();
  };

  private onPointerDown = (e: PointerEvent) => {
    try {
      this.canvas.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic pointer */
    }
    const rect = this.canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    const w = this.toWorld(cx, cy);

    const outPort = this.outPortAt(w.x, w.y);
    if (outPort) {
      this.linking = { source: outPort.id, mx: w.x, my: w.y };
      return;
    }
    const node = this.nodeAt(w.x, w.y);
    if (node) {
      this.dragNode = { id: node.id, dx: w.x - node.x, dy: w.y - node.y };
      this.opts.onSelect?.(node.id);
      return;
    }
    const edge = this.edgeAt(w.x, w.y);
    if (edge) {
      this.opts.onDeleteEdge?.(edge.source, edge.target);
      return;
    }
    this.opts.onSelect?.(null);
    this.panning = { sx: cx, sy: cy, ox: this.view.x, oy: this.view.y };
  };

  private onPointerMove = (e: PointerEvent) => {
    const rect = this.canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    const w = this.toWorld(cx, cy);
    if (this.dragNode) {
      this.opts.onMove?.(this.dragNode.id, w.x - this.dragNode.dx, w.y - this.dragNode.dy);
      return;
    }
    if (this.linking) {
      this.linking.mx = w.x;
      this.linking.my = w.y;
      this.invalidate();
      return;
    }
    if (this.panning) {
      this.view.x = this.panning.ox + (cx - this.panning.sx);
      this.view.y = this.panning.oy + (cy - this.panning.sy);
      this.invalidate();
      return;
    }
    // hover cursor hint
    this.canvas.style.cursor =
      this.outPortAt(w.x, w.y) || this.inPortAt(w.x, w.y) ? "crosshair" : this.nodeAt(w.x, w.y) ? "grab" : "default";
  };

  private onPointerUp = (e: PointerEvent) => {
    try {
      this.canvas.releasePointerCapture(e.pointerId);
    } catch {
      /* no-op */
    }
    if (this.linking) {
      const rect = this.canvas.getBoundingClientRect();
      const w = this.toWorld(e.clientX - rect.left, e.clientY - rect.top);
      const target = this.inPortAt(w.x, w.y) ?? this.nodeAt(w.x, w.y);
      if (target && target.id !== this.linking.source && target.inputs >= 1) {
        this.opts.onConnect?.(this.linking.source, target.id);
      }
    }
    this.dragNode = null;
    this.panning = null;
    this.linking = null;
    this.invalidate();
  };

  private onDblClick = () => {
    this.view = { x: 0, y: 0, scale: 1 };
    this.invalidate();
  };

  /* ── rendering ────────────────────────────────────────────────────────────── */
  private invalidate(): void {
    // While the animation loop is alive it already renders every frame — no one-shot needed.
    if (this.dirty || this.destroyed || this.animRaf) return;
    this.dirty = true;
    this.raf = requestAnimationFrame(() => {
      this.dirty = false;
      this.render();
    });
  }

  private statusColor(s: NodeStatus): string {
    return s === "running" ? this.colors.accent : s === "done" ? this.colors.up : s === "error" ? this.colors.down : this.colors.border;
  }

  private render(): void {
    if (this.destroyed) return;
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW < 40 || cssH < 40) return;
    const dpr = window.devicePixelRatio || 1;
    if (this.canvas.width !== Math.round(cssW * dpr) || this.canvas.height !== Math.round(cssH * dpr)) {
      this.canvas.width = Math.round(cssW * dpr);
      this.canvas.height = Math.round(cssH * dpr);
    }
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.fillStyle = this.colors.bg;
    ctx.fillRect(0, 0, cssW, cssH);

    ctx.save();
    ctx.translate(this.view.x, this.view.y);
    ctx.scale(this.view.scale, this.view.scale);
    ctx.font = "12px 'Inter Variable', Inter, system-ui, sans-serif";

    const now = performance.now();
    // edges first
    for (const e of this.data.edges) {
      const s = this.byId(e.source), t = this.byId(e.target);
      if (!s || !t) continue;
      const x1 = s.x + NODE_W, y1 = s.y + NODE_H / 2, x2 = t.x, y2 = t.y + NODE_H / 2;
      // an edge is "active" while its target step is running — show data flowing into it
      const active = t.status === "running";
      this.drawEdge(ctx, x1, y1, x2, y2, active ? this.colors.accent : this.colors.muted);
      if (active) {
        const dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
        // marching-ants accent overlay
        ctx.save();
        ctx.setLineDash([3, 7]);
        ctx.lineDashOffset = -(now / 30);
        ctx.globalAlpha = 0.9;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.bezierCurveTo(x1 + dx, y1, x2 - dx, y2, x2, y2);
        ctx.strokeStyle = this.colors.accent;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.restore();
        // a packet traveling source → target
        const u = (now % 900) / 900;
        const p = this.bezier(x1, y1, x2, y2, u);
        ctx.fillStyle = this.colors.accent;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    // in-progress link
    if (this.linking) {
      const s = this.byId(this.linking.source)!;
      this.drawEdge(ctx, s.x + NODE_W, s.y + NODE_H / 2, this.linking.mx, this.linking.my, this.colors.accent);
    }

    // nodes
    for (const n of this.data.nodes) {
      const sel = n.id === this.data.selected;
      const ring = this.statusColor(n.status);
      // running step: an expanding "radar" halo behind the body
      if (n.status === "running") {
        const ph = (now % 1200) / 1200; // 0..1
        const pad = ph * 9;
        ctx.save();
        ctx.globalAlpha = (1 - ph) * 0.55;
        this.roundRect(ctx, n.x - pad, n.y - pad, NODE_W + pad * 2, NODE_H + pad * 2, 6 + pad);
        ctx.strokeStyle = this.colors.accent;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.restore();
      }
      // body
      this.roundRect(ctx, n.x, n.y, NODE_W, NODE_H, 6);
      ctx.fillStyle = this.colors.panel;
      ctx.fill();
      ctx.lineWidth = sel ? 2 : 1.5;
      ctx.strokeStyle = sel ? this.colors.accent : ring;
      ctx.stroke();
      // status indicator: a rotating spinner arc while running, otherwise a solid dot
      if (n.status === "running") {
        const a0 = (now / 1000) * Math.PI * 2;
        ctx.strokeStyle = ring;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(n.x + 12, n.y + 14, 5, a0, a0 + Math.PI * 1.35);
        ctx.stroke();
      } else {
        ctx.fillStyle = ring;
        ctx.beginPath();
        ctx.arc(n.x + 12, n.y + 14, 4, 0, Math.PI * 2);
        ctx.fill();
      }
      // labels
      ctx.fillStyle = this.colors.text;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(n.label, n.x + 24, n.y + 15);
      ctx.fillStyle = this.colors.muted;
      ctx.font = "10px 'JetBrains Mono Variable', 'JetBrains Mono', monospace";
      ctx.fillText(n.type, n.x + 12, n.y + 36);
      ctx.font = "12px 'Inter Variable', Inter, system-ui, sans-serif";
      // ports
      if (n.inputs >= 1) {
        ctx.fillStyle = this.colors.muted;
        ctx.beginPath();
        ctx.arc(n.x, n.y + NODE_H / 2, PORT_R, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.fillStyle = this.colors.accent;
      ctx.beginPath();
      ctx.arc(n.x + NODE_W, n.y + NODE_H / 2, PORT_R, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();

    if (this.data.nodes.length === 0) {
      ctx.fillStyle = this.colors.muted;
      ctx.font = "12px 'Inter Variable', Inter, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Add nodes from the palette, then drag output→input ports to wire them.", cssW / 2, cssH / 2);
    }
  }

  private drawEdge(ctx: CanvasRenderingContext2D, x1: number, y1: number, x2: number, y2: number, color: string): void {
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.bezierCurveTo(x1 + dx, y1, x2 - dx, y2, x2, y2);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    // arrow head
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x2, y2, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }

  private roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
}
