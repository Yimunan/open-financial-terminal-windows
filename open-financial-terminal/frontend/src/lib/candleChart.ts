/** CandleChart — a dependency-free canvas chart engine for the Chart widget.
 *
 * One canvas, two panes: price (candles/area + volume strip + overlay lines) and an
 * optional lower pane (RSI/MACD lines) sharing the time axis. Interactions: wheel-zoom
 * anchored at the cursor, drag-pan, double-click to fit, crosshair with OHLC readout.
 *
 * Design notes:
 * - The x domain is candle INDEX space (floats), not time — bars stay evenly spaced
 *   across weekends/market closes, which is what terminals do.
 * - All drawing happens in one rAF-batched `render()`; data updates and interactions
 *   only mutate state and mark dirty. ~1k candles renders in well under a frame.
 * - HiDPI: backing store scaled by devicePixelRatio, drawing in CSS pixels.
 * - Colors are injected (resolved from the CSS theme tokens by the caller), so the
 *   widget rebuilds the chart on theme/scheme/accent changes like before.
 */

export interface ChartColors {
  up: string;
  down: string;
  accent: string;
  text: string;
  muted: string;
  border: string;
  panel: string;
}

export interface ChartCandle {
  time: string | number; // "YYYY-MM-DD" (daily) or unix seconds (intraday)
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface SeriesPoint {
  time: string | number;
  value: number;
}

export interface LineSpec {
  label: string;
  color: string;
  points: SeriesPoint[];
}

/** Strategy-lab trade marker drawn on the price pane. */
export interface ChartMarker {
  time: string | number;
  price: number;
  kind: "longEntry" | "shortEntry" | "exit";
  win?: boolean;
}

/** Draggable horizontal price line (what-if stop/target). */
export interface DragLine {
  id: string;
  price: number;
  color: string;
  label: string;
}

export interface CandleChartData {
  candles: ChartCandle[];
  volume: SeriesPoint[];
  overlays: LineSpec[]; // price-pane lines (SMA/EMA/Bollinger)
  lower: LineSpec[]; // lower-pane lines (RSI/MACD)
  mode: "candles" | "area";
  intraday: boolean;
  markers?: ChartMarker[]; // trade entries/exits (strategy lab)
  equity?: SeriesPoint[]; // equity curve drawn in the lower pane (strategy lab)
  highlight?: string | number | null; // emphasize the marker/bar at this time
  dragLines?: DragLine[]; // draggable stop/target lines (what-if)
  whatif?: { time: string | number; price: number; win: boolean } | null; // recomputed exit
}

export interface CandleChartOpts {
  onDragLine?: (id: string, price: number) => void;
}

const AXIS_W = 58; // right price axis
const AXIS_H = 20; // bottom time axis
const PAD_TOP = 8;
const MIN_BARS = 6; // zoom-in floor
const VOLUME_SHARE = 0.16; // of price pane height
const LOWER_SHARE = 0.26; // of plot height when a lower pane exists

interface View {
  v0: number; // first visible candle index (float)
  v1: number; // last visible candle index (float, exclusive-ish)
}

/** 1-2-5 tick step covering roughly `span / target`. */
function niceStep(span: number, target: number): number {
  const raw = span / Math.max(target, 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) {
    if (raw <= m * mag) return m * mag;
  }
  return 10 * mag;
}

function fmtPrice(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 10000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (abs >= 100) return v.toFixed(1);
  if (abs >= 1) return v.toFixed(2);
  return v.toPrecision(3);
}

function fmtCompact(v: number): string {
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

function toDate(time: string | number): Date {
  return typeof time === "number" ? new Date(time * 1000) : new Date(`${time}T00:00:00`);
}

export class CandleChart {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private colors: ChartColors;
  private ro: ResizeObserver;

  private data: CandleChartData = {
    candles: [],
    volume: [],
    overlays: [],
    lower: [],
    mode: "candles",
    intraday: false,
  };
  private timeIndex = new Map<string | number, number>();
  private view: View = { v0: 0, v1: 1 };
  private cursor: { x: number; y: number } | null = null;
  private dragging: { startX: number; startV0: number; startV1: number } | null = null;
  private draggingLine: string | null = null;
  private scale: { lo: number; hi: number; yTop: number; ySpan: number; plotW: number } | null = null;
  private opts: CandleChartOpts;
  private dirty = false;
  private raf = 0;
  private destroyed = false;

  constructor(container: HTMLElement, colors: ChartColors, opts: CandleChartOpts = {}) {
    this.container = container;
    this.colors = colors;
    this.opts = opts;
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "display:block;width:100%;height:100%;cursor:crosshair";
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;

    this.ro = new ResizeObserver(() => this.invalidate());
    this.ro.observe(container);

    this.canvas.addEventListener("wheel", this.onWheel, { passive: false });
    this.canvas.addEventListener("pointerdown", this.onPointerDown);
    this.canvas.addEventListener("pointermove", this.onPointerMove);
    this.canvas.addEventListener("pointerup", this.onPointerUp);
    this.canvas.addEventListener("pointerleave", this.onPointerLeave);
    this.canvas.addEventListener("dblclick", this.onDblClick);
  }

  setData(data: CandleChartData): void {
    const sameLength = data.candles.length === this.data.candles.length;
    const sameFirst =
      sameLength &&
      data.candles.length > 0 &&
      data.candles[0].time === this.data.candles[0]?.time;
    this.data = data;
    this.timeIndex.clear();
    data.candles.forEach((c, i) => this.timeIndex.set(c.time, i));
    // Keep the user's pan/zoom across refetches of the same series; refit otherwise.
    if (!(sameLength && sameFirst)) this.fit();
    this.invalidate();
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.canvas.removeEventListener("wheel", this.onWheel);
    this.canvas.remove();
  }

  fit(): void {
    const n = this.data.candles.length;
    this.view = n ? { v0: -0.5, v1: n - 0.5 } : { v0: 0, v1: 1 };
    this.invalidate();
  }

  /* ── interactions ──────────────────────────────────────────────────────────── */

  private onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const n = this.data.candles.length;
    if (!n) return;
    const rect = this.canvas.getBoundingClientRect();
    const plotW = rect.width - AXIS_W;
    const mx = Math.min(Math.max(e.clientX - rect.left, 0), plotW);
    const { v0, v1 } = this.view;
    const span = v1 - v0;
    const anchor = v0 + (mx / plotW) * span;
    const factor = Math.exp(e.deltaY * 0.0012);
    const newSpan = Math.min(Math.max(span * factor, MIN_BARS), n * 1.5);
    let nv0 = anchor - ((anchor - v0) / span) * newSpan;
    nv0 = Math.min(Math.max(nv0, -newSpan * 0.6), n - newSpan * 0.4);
    this.view = { v0: nv0, v1: nv0 + newSpan };
    this.invalidate();
  };

  /** id of the drag line within `hitPx` of a y position, if any. */
  private dragLineAt(y: number, x: number): string | null {
    if (!this.scale || !this.data.dragLines?.length || x > this.scale.plotW) return null;
    for (const line of this.data.dragLines) {
      if (Math.abs(this.yOfPrice(line.price) - y) <= 5) return line.id;
    }
    return null;
  }

  private yOfPrice(p: number): number {
    const s = this.scale!;
    return s.yTop + ((s.hi - p) / (s.hi - s.lo)) * s.ySpan;
  }

  private priceOfY(y: number): number {
    const s = this.scale!;
    return s.hi - ((y - s.yTop) / s.ySpan) * (s.hi - s.lo);
  }

  private onPointerDown = (e: PointerEvent) => {
    try {
      this.canvas.setPointerCapture(e.pointerId);
    } catch {
      /* capture can fail for non-primary/synthetic pointers — drag still works */
    }
    const rect = this.canvas.getBoundingClientRect();
    const hit = this.dragLineAt(e.clientY - rect.top, e.clientX - rect.left);
    if (hit) {
      this.draggingLine = hit; // grab the stop/target line instead of panning
      return;
    }
    this.dragging = { startX: e.clientX, startV0: this.view.v0, startV1: this.view.v1 };
  };

  private onPointerMove = (e: PointerEvent) => {
    const rect = this.canvas.getBoundingClientRect();
    this.cursor = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    if (this.draggingLine && this.scale) {
      this.opts.onDragLine?.(this.draggingLine, this.priceOfY(this.cursor.y));
      this.canvas.style.cursor = "ns-resize";
      return; // chart redraws when the caller pushes new dragLines via setData
    }
    if (this.dragging) {
      const n = this.data.candles.length;
      const plotW = rect.width - AXIS_W;
      const span = this.dragging.startV1 - this.dragging.startV0;
      const di = ((e.clientX - this.dragging.startX) / plotW) * span;
      let nv0 = this.dragging.startV0 - di;
      nv0 = Math.min(Math.max(nv0, -span * 0.6), n - span * 0.4);
      this.view = { v0: nv0, v1: nv0 + span };
    } else {
      this.canvas.style.cursor = this.dragLineAt(this.cursor.y, this.cursor.x) ? "ns-resize" : "crosshair";
    }
    this.invalidate();
  };

  private onPointerUp = (e: PointerEvent) => {
    try {
      this.canvas.releasePointerCapture(e.pointerId);
    } catch {
      /* no-op */
    }
    this.dragging = null;
    this.draggingLine = null;
  };

  private onPointerLeave = () => {
    this.cursor = null;
    this.dragging = null;
    this.draggingLine = null;
    this.invalidate();
  };

  private onDblClick = () => this.fit();

  /* ── rendering ─────────────────────────────────────────────────────────────── */

  private invalidate(): void {
    if (this.dirty || this.destroyed) return;
    this.dirty = true;
    this.raf = requestAnimationFrame(() => {
      this.dirty = false;
      this.render();
    });
  }

  private render(): void {
    if (this.destroyed) return;
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW < 50 || cssH < 50) return;
    const dpr = window.devicePixelRatio || 1;
    if (this.canvas.width !== Math.round(cssW * dpr) || this.canvas.height !== Math.round(cssH * dpr)) {
      this.canvas.width = Math.round(cssW * dpr);
      this.canvas.height = Math.round(cssH * dpr);
    }
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.font = "10px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, monospace";

    const { candles, lower, equity } = this.data;
    if (!candles.length) return;

    const plotW = cssW - AXIS_W;
    const plotH = cssH - AXIS_H;
    const hasLower = lower.length > 0 || (equity?.length ?? 0) > 0;
    const lowerH = hasLower ? Math.round(plotH * LOWER_SHARE) : 0;
    const priceH = plotH - lowerH;
    const lowerTop = priceH;

    const { v0, v1 } = this.view;
    const span = v1 - v0;
    const barSpace = plotW / span;
    const xOf = (i: number) => (i - v0) * barSpace;

    const iFrom = Math.max(0, Math.floor(v0));
    const iTo = Math.min(candles.length - 1, Math.ceil(v1));

    // price scale over visible candles + visible overlay points
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = iFrom; i <= iTo; i++) {
      const c = candles[i];
      if (c.low < lo) lo = c.low;
      if (c.high > hi) hi = c.high;
    }
    for (const ov of this.data.overlays) {
      for (const p of ov.points) {
        const i = this.timeIndex.get(p.time);
        if (i !== undefined && i >= iFrom && i <= iTo) {
          if (p.value < lo) lo = p.value;
          if (p.value > hi) hi = p.value;
        }
      }
    }
    // keep draggable stop/target lines in view by widening the price range to include them
    for (const dl of this.data.dragLines ?? []) {
      if (dl.price < lo) lo = dl.price;
      if (dl.price > hi) hi = dl.price;
    }
    if (!isFinite(lo) || !isFinite(hi)) return;
    if (hi === lo) {
      hi += 1;
      lo -= 1;
    }
    const padPx = 10;
    const yTop = PAD_TOP + padPx;
    const ySpan = priceH - PAD_TOP - 2 * padPx - VOLUME_SHARE * priceH * 0.4;
    const yOf = (p: number) => yTop + ((hi - p) / (hi - lo)) * ySpan;
    this.scale = { lo, hi, yTop, ySpan, plotW };

    this.drawGrid(ctx, plotW, priceH, lo, hi, yOf);
    this.drawVolume(ctx, plotW, priceH, iFrom, iTo, xOf, barSpace);
    if (this.data.mode === "area") this.drawArea(ctx, plotW, iFrom, iTo, xOf, yOf);
    else this.drawCandles(ctx, iFrom, iTo, xOf, yOf, barSpace);
    this.drawOverlays(ctx, plotW, iFrom, iTo, xOf, yOf);
    if (this.data.markers?.length) this.drawMarkers(ctx, plotW, priceH, iFrom, iTo, xOf, yOf, barSpace);
    if (this.data.whatif) this.drawWhatif(ctx, iFrom, iTo, xOf, yOf, barSpace);
    if (this.data.dragLines?.length) this.drawDragLines(ctx, plotW, yOf);
    if (lowerH) {
      if (equity?.length) this.drawEquityPane(ctx, plotW, lowerTop, lowerH, iFrom, iTo, xOf, barSpace);
      else this.drawLowerPane(ctx, plotW, lowerTop, lowerH, iFrom, iTo, xOf);
    }
    this.drawTimeAxis(ctx, plotW, plotH, iFrom, iTo, xOf, barSpace);
    this.drawLegend(ctx, iFrom, iTo);
    if (this.cursor && !this.dragging) {
      this.drawCrosshair(ctx, cssW, plotW, plotH, priceH, lo, hi, yOf, barSpace);
    }

    // frame
    ctx.strokeStyle = this.colors.border;
    ctx.lineWidth = 1;
    ctx.strokeRect(0.5, 0.5, plotW, plotH);
  }

  private drawGrid(
    ctx: CanvasRenderingContext2D,
    plotW: number,
    priceH: number,
    lo: number,
    hi: number,
    yOf: (p: number) => number,
  ): void {
    const step = niceStep(hi - lo, 5);
    ctx.strokeStyle = this.colors.border;
    ctx.globalAlpha = 0.45;
    ctx.lineWidth = 1;
    ctx.fillStyle = this.colors.muted;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (let p = Math.ceil(lo / step) * step; p <= hi; p += step) {
      const y = Math.round(yOf(p)) + 0.5;
      if (y < PAD_TOP || y > priceH - 4) continue;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillText(fmtPrice(p), plotW + 5, y);
      ctx.globalAlpha = 0.45;
    }
    ctx.globalAlpha = 1;
  }

  private drawCandles(
    ctx: CanvasRenderingContext2D,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    yOf: (p: number) => number,
    barSpace: number,
  ): void {
    const bodyW = Math.max(1, Math.min(barSpace * 0.7, 21));
    const { candles } = this.data;
    for (let i = iFrom; i <= iTo; i++) {
      const c = candles[i];
      const up = c.close >= c.open;
      const color = up ? this.colors.up : this.colors.down;
      const x = xOf(i) + barSpace / 2;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1;
      // wick
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, yOf(c.high));
      ctx.lineTo(Math.round(x) + 0.5, yOf(c.low));
      ctx.stroke();
      // body
      const yO = yOf(c.open);
      const yC = yOf(c.close);
      const top = Math.min(yO, yC);
      const h = Math.max(Math.abs(yC - yO), 1);
      ctx.fillRect(x - bodyW / 2, top, bodyW, h);
    }
  }

  private drawArea(
    ctx: CanvasRenderingContext2D,
    plotW: number,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    yOf: (p: number) => number,
  ): void {
    const { candles } = this.data;
    const bar = plotW / (this.view.v1 - this.view.v0);
    ctx.beginPath();
    for (let i = iFrom; i <= iTo; i++) {
      const x = xOf(i) + bar / 2;
      const y = yOf(candles[i].close);
      if (i === iFrom) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = this.colors.accent;
    ctx.lineWidth = 2;
    ctx.stroke();
    // gradient fill under the line
    const yBottom = yOf(Number.MIN_SAFE_INTEGER) || 0;
    ctx.lineTo(xOf(iTo) + bar / 2, yBottom + 10_000);
    ctx.lineTo(xOf(iFrom) + bar / 2, yBottom + 10_000);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, this.container.clientHeight);
    grad.addColorStop(0, this.colors.accent.replace("rgb(", "rgba(").replace(")", ", 0.28)"));
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.fill();
  }

  private drawVolume(
    ctx: CanvasRenderingContext2D,
    _plotW: number,
    priceH: number,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    barSpace: number,
  ): void {
    const { candles, volume } = this.data;
    if (!volume.length) return;
    let maxV = 0;
    for (let i = iFrom; i <= iTo; i++) {
      const v = volume[i]?.value ?? 0;
      if (v > maxV) maxV = v;
    }
    if (!maxV) return;
    const volH = priceH * VOLUME_SHARE;
    const base = priceH;
    const bodyW = Math.max(1, Math.min(barSpace * 0.7, 21));
    ctx.globalAlpha = 0.35;
    for (let i = iFrom; i <= iTo; i++) {
      const v = volume[i]?.value ?? 0;
      const c = candles[i];
      ctx.fillStyle = c.close >= c.open ? this.colors.up : this.colors.down;
      const h = (v / maxV) * volH;
      ctx.fillRect(xOf(i) + barSpace / 2 - bodyW / 2, base - h, bodyW, h);
    }
    ctx.globalAlpha = 1;
  }

  private drawLine(
    ctx: CanvasRenderingContext2D,
    spec: LineSpec,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    yOf: (v: number) => number,
    barSpace: number,
  ): void {
    ctx.beginPath();
    let started = false;
    for (const p of spec.points) {
      const i = this.timeIndex.get(p.time);
      if (i === undefined || i < iFrom - 1 || i > iTo + 1) continue;
      const x = xOf(i) + barSpace / 2;
      const y = yOf(p.value);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = spec.color;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  private drawOverlays(
    ctx: CanvasRenderingContext2D,
    plotW: number,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    yOf: (p: number) => number,
  ): void {
    const barSpace = plotW / (this.view.v1 - this.view.v0);
    for (const ov of this.data.overlays) this.drawLine(ctx, ov, iFrom, iTo, xOf, yOf, barSpace);
  }

  private drawLowerPane(
    ctx: CanvasRenderingContext2D,
    plotW: number,
    top: number,
    height: number,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
  ): void {
    // separator
    ctx.strokeStyle = this.colors.border;
    ctx.beginPath();
    ctx.moveTo(0, top + 0.5);
    ctx.lineTo(plotW, top + 0.5);
    ctx.stroke();

    let lo = Infinity;
    let hi = -Infinity;
    for (const s of this.data.lower) {
      for (const p of s.points) {
        const i = this.timeIndex.get(p.time);
        if (i !== undefined && i >= iFrom && i <= iTo) {
          if (p.value < lo) lo = p.value;
          if (p.value > hi) hi = p.value;
        }
      }
    }
    if (!isFinite(lo) || !isFinite(hi)) return;
    if (hi === lo) {
      hi += 1;
      lo -= 1;
    }
    const pad = 8;
    const yOf = (v: number) => top + pad + ((hi - v) / (hi - lo)) * (height - 2 * pad);

    // a couple of guide lines + labels
    ctx.fillStyle = this.colors.muted;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (const v of [hi, lo]) {
      const y = Math.round(yOf(v)) + 0.5;
      ctx.globalAlpha = 0.45;
      ctx.strokeStyle = this.colors.border;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillText(fmtPrice(v), plotW + 5, y);
    }
    if (lo < 0 && hi > 0) {
      const y = Math.round(yOf(0)) + 0.5;
      ctx.globalAlpha = 0.6;
      ctx.strokeStyle = this.colors.muted;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    const barSpace = plotW / (this.view.v1 - this.view.v0);
    for (const s of this.data.lower) this.drawLine(ctx, s, iFrom, iTo, xOf, yOf, barSpace);
  }

  private drawMarkers(
    ctx: CanvasRenderingContext2D,
    plotW: number,
    priceH: number,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    yOf: (p: number) => number,
    barSpace: number,
  ): void {
    const hi = this.data.highlight;
    for (const m of this.data.markers!) {
      const idx = this.timeIndex.get(m.time);
      if (idx === undefined || idx < iFrom - 1 || idx > iTo + 1) continue;
      const x = xOf(idx) + barSpace / 2;
      if (x < -10 || x > plotW + 10) continue;
      const y = yOf(m.price);
      const isHi = hi != null && m.time === hi;
      if (isHi) {
        ctx.save();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = this.colors.accent;
        ctx.beginPath();
        ctx.moveTo(Math.round(x) + 0.5, 0);
        ctx.lineTo(Math.round(x) + 0.5, priceH);
        ctx.stroke();
        ctx.restore();
      }
      const r = 5;
      if (m.kind === "exit") {
        ctx.strokeStyle = m.win ? this.colors.up : this.colors.down;
        ctx.lineWidth = isHi ? 2.2 : 1.5;
        ctx.beginPath();
        ctx.moveTo(x - r, y - r);
        ctx.lineTo(x + r, y + r);
        ctx.moveTo(x + r, y - r);
        ctx.lineTo(x - r, y + r);
        ctx.stroke();
      } else {
        const up = m.kind === "longEntry";
        const yo = up ? y + 16 : y - 16; // below the bar for longs, above for shorts
        ctx.fillStyle = up ? this.colors.up : this.colors.down;
        ctx.beginPath();
        if (up) {
          ctx.moveTo(x, yo - r);
          ctx.lineTo(x - r, yo + r);
          ctx.lineTo(x + r, yo + r);
        } else {
          ctx.moveTo(x, yo + r);
          ctx.lineTo(x - r, yo - r);
          ctx.lineTo(x + r, yo - r);
        }
        ctx.closePath();
        ctx.fill();
        if (isHi) {
          ctx.strokeStyle = this.colors.accent;
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
      }
    }
  }

  private drawDragLines(
    ctx: CanvasRenderingContext2D,
    plotW: number,
    yOf: (p: number) => number,
  ): void {
    for (const line of this.data.dragLines!) {
      const y = Math.round(yOf(line.price)) + 0.5;
      ctx.save();
      ctx.strokeStyle = line.color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 3]);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.restore();
      // label chip on the left + price tag on the right axis
      ctx.font = "9px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, monospace";
      const lbl = `${line.label} ${fmtPrice(line.price)}`;
      const w = ctx.measureText(lbl).width + 8;
      ctx.fillStyle = line.color;
      ctx.fillRect(2, y - 7, w, 14);
      ctx.fillStyle = this.colors.panel;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(lbl, 6, y);
      // grab handle on the right
      ctx.fillStyle = line.color;
      ctx.fillRect(plotW - 10, y - 4, 8, 8);
    }
  }

  private drawWhatif(
    ctx: CanvasRenderingContext2D,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    yOf: (p: number) => number,
    barSpace: number,
  ): void {
    const w = this.data.whatif!;
    const idx = this.timeIndex.get(w.time);
    if (idx === undefined || idx < iFrom - 1 || idx > iTo + 1) return;
    const x = xOf(idx) + barSpace / 2;
    const y = yOf(w.price);
    ctx.strokeStyle = w.win ? this.colors.up : this.colors.down;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, Math.PI * 2); // hollow ring = projected what-if exit
    ctx.stroke();
  }

  private drawEquityPane(
    ctx: CanvasRenderingContext2D,
    plotW: number,
    top: number,
    height: number,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    barSpace: number,
  ): void {
    const eq = this.data.equity!;
    ctx.strokeStyle = this.colors.border;
    ctx.beginPath();
    ctx.moveTo(0, top + 0.5);
    ctx.lineTo(plotW, top + 0.5);
    ctx.stroke();

    let lo = Infinity;
    let hi = -Infinity;
    for (const p of eq) {
      const i = this.timeIndex.get(p.time);
      if (i !== undefined && i >= iFrom && i <= iTo) {
        if (p.value < lo) lo = p.value;
        if (p.value > hi) hi = p.value;
      }
    }
    if (!isFinite(lo) || !isFinite(hi)) return;
    if (hi === lo) {
      hi += 1;
      lo -= 1;
    }
    const pad = 6;
    const yOf = (v: number) => top + pad + ((hi - v) / (hi - lo)) * (height - 2 * pad);
    const last = eq[eq.length - 1]?.value ?? 0;
    const first = eq[0]?.value ?? 0;
    const color = last >= first ? this.colors.up : this.colors.down;

    // line
    ctx.beginPath();
    let started = false;
    let lastX = 0;
    for (const p of eq) {
      const i = this.timeIndex.get(p.time);
      if (i === undefined || i < iFrom - 1 || i > iTo + 1) continue;
      const x = xOf(i) + barSpace / 2;
      const y = yOf(p.value);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else ctx.lineTo(x, y);
      lastX = x;
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    // fill to baseline
    ctx.lineTo(lastX, top + height);
    ctx.lineTo(xOf(iFrom) + barSpace / 2, top + height);
    ctx.closePath();
    ctx.fillStyle = color.replace("rgb(", "rgba(").replace(")", ", 0.16)");
    ctx.fill();

    // labels
    ctx.fillStyle = this.colors.muted;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(fmtCompact(hi), plotW + 5, yOf(hi) + 4);
    ctx.fillText(fmtCompact(lo), plotW + 5, yOf(lo) - 4);
    ctx.fillStyle = this.colors.muted;
    ctx.fillText("equity", 4, top + 9);
  }

  private drawTimeAxis(
    ctx: CanvasRenderingContext2D,
    plotW: number,
    plotH: number,
    iFrom: number,
    iTo: number,
    xOf: (i: number) => number,
    barSpace: number,
  ): void {
    const { candles, intraday } = this.data;
    const labelEvery = Math.max(1, Math.ceil(80 / barSpace)); // ~80px between labels
    ctx.fillStyle = this.colors.muted;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    let lastMonth = -1;
    let lastDay = -1;
    for (let i = iFrom; i <= iTo; i++) {
      if (i % labelEvery !== 0) continue;
      const d = toDate(candles[i].time);
      let label: string;
      if (intraday) {
        const day = d.getDate();
        label =
          day !== lastDay
            ? `${String(d.getMonth() + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`
            : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        lastDay = day;
      } else {
        const m = d.getMonth();
        label =
          m !== lastMonth && m === 0
            ? String(d.getFullYear())
            : m !== lastMonth
              ? d.toLocaleString("en-US", { month: "short" })
              : String(d.getDate());
        lastMonth = m;
      }
      const x = xOf(i) + barSpace / 2;
      if (x < 8 || x > plotW - 8) continue;
      ctx.fillText(label, x, plotH + 5);
      ctx.globalAlpha = 0.35;
      ctx.strokeStyle = this.colors.border;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, 0);
      ctx.lineTo(Math.round(x) + 0.5, plotH);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  private drawLegend(ctx: CanvasRenderingContext2D, _iFrom: number, iTo: number): void {
    // overlay labels top-left, under the OHLC line (drawn by crosshair when active)
    let x = 8;
    const y = this.cursor ? 30 : 16;
    for (const ov of [...this.data.overlays, ...this.data.lower]) {
      ctx.fillStyle = ov.color;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(ov.label, x, y);
      x += ctx.measureText(ov.label).width + 14;
    }
    // last price tag on the right axis
    const last = this.data.candles[Math.min(iTo, this.data.candles.length - 1)];
    if (!last) return;
  }

  private drawCrosshair(
    ctx: CanvasRenderingContext2D,
    cssW: number,
    plotW: number,
    plotH: number,
    priceH: number,
    lo: number,
    hi: number,
    yOf: (p: number) => number,
    barSpace: number,
  ): void {
    const cur = this.cursor!;
    if (cur.x > plotW || cur.y > plotH) return;
    const { candles, volume, intraday } = this.data;
    const idx = Math.round(this.view.v0 + cur.x / barSpace - 0.5);
    if (idx < 0 || idx >= candles.length) return;
    const c = candles[idx];
    const snapX = Math.round((idx - this.view.v0 + 0.5) * barSpace) + 0.5;

    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = this.colors.muted;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(snapX, 0);
    ctx.lineTo(snapX, plotH);
    ctx.stroke();
    const cy = Math.round(cur.y) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, cy);
    ctx.lineTo(plotW, cy);
    ctx.stroke();
    ctx.restore();

    // y-axis price label (only meaningful inside the price pane)
    if (cur.y < priceH) {
      const price = hi - ((cur.y - PAD_TOP - 10) / (yOf(lo) - PAD_TOP - 10)) * (hi - lo);
      ctx.fillStyle = this.colors.panel;
      ctx.strokeStyle = this.colors.muted;
      const label = fmtPrice(price);
      const w = ctx.measureText(label).width + 8;
      ctx.fillRect(plotW + 1, cy - 8, Math.max(w, AXIS_W - 2), 16);
      ctx.strokeRect(plotW + 1, cy - 8, Math.max(w, AXIS_W - 2), 16);
      ctx.fillStyle = this.colors.text;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(label, plotW + 5, cy);
    }

    // x-axis time label
    const d = toDate(c.time);
    const tLabel = intraday
      ? `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
      : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const tw = ctx.measureText(tLabel).width + 10;
    const tx = Math.min(Math.max(snapX - tw / 2, 0), cssW - tw);
    ctx.fillStyle = this.colors.panel;
    ctx.strokeStyle = this.colors.muted;
    ctx.fillRect(tx, plotH + 1, tw, AXIS_H - 2);
    ctx.strokeRect(tx, plotH + 1, tw, AXIS_H - 2);
    ctx.fillStyle = this.colors.text;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(tLabel, tx + tw / 2, plotH + AXIS_H / 2);

    // OHLC readout top-left
    const chg = idx > 0 ? ((c.close / candles[idx - 1].close - 1) * 100).toFixed(2) : "0.00";
    const vol = volume[idx]?.value;
    const up = c.close >= c.open;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    let x = 8;
    const parts: [string, string][] = [
      [`O ${fmtPrice(c.open)}`, up ? this.colors.up : this.colors.down],
      [`H ${fmtPrice(c.high)}`, up ? this.colors.up : this.colors.down],
      [`L ${fmtPrice(c.low)}`, up ? this.colors.up : this.colors.down],
      [`C ${fmtPrice(c.close)}`, up ? this.colors.up : this.colors.down],
      [`${Number(chg) >= 0 ? "+" : ""}${chg}%`, Number(chg) >= 0 ? this.colors.up : this.colors.down],
    ];
    if (vol != null) parts.push([`V ${fmtCompact(vol)}`, this.colors.muted]);
    for (const [text, color] of parts) {
      ctx.fillStyle = color;
      ctx.fillText(text, x, 14);
      x += ctx.measureText(text).width + 12;
    }
  }
}
