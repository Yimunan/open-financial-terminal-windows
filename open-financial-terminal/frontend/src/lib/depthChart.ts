/** DepthChart — dependency-free canvas market-depth chart for the Order Book widget.
 *
 * The classic cumulative-depth profile: from the mid price, bid liquidity accumulates
 * to the left (price falling) and ask liquidity to the right (price rising), each drawn
 * as a stepped area whose height is the running sum of size. A dashed mid line splits
 * the two; hovering reads out price + cumulative depth on that side.
 *
 * Same conventions as candleChart.ts: one canvas, HiDPI-aware, rAF-batched, colors
 * injected from the live theme tokens so the widget rebuilds on theme/scheme changes.
 */

export interface DepthColors {
  up: string;
  down: string;
  text: string;
  muted: string;
  border: string;
  panel: string;
}

export interface DepthData {
  bids: [number, number][]; // descending price
  asks: [number, number][]; // ascending price
}

const AXIS_H = 16;
const PAD = 8;

function fmtPrice(v: number): string {
  const a = Math.abs(v);
  if (a >= 10000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(3);
}

function fmtCompact(v: number): string {
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v);
}

interface Cum {
  price: number;
  cum: number;
}

export class DepthChart {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private colors: DepthColors;
  private ro: ResizeObserver;

  private data: DepthData = { bids: [], asks: [] };
  private cursor: { x: number; y: number } | null = null;
  private dirty = false;
  private raf = 0;
  private destroyed = false;

  // cached geometry for crosshair hit-testing (set each render)
  private geo: { pMin: number; pMax: number; maxCum: number; plotW: number; plotH: number } | null =
    null;

  constructor(container: HTMLElement, colors: DepthColors) {
    this.container = container;
    this.colors = colors;
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "display:block;width:100%;height:100%;cursor:crosshair";
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;
    this.ro = new ResizeObserver(() => this.invalidate());
    this.ro.observe(container);
    this.canvas.addEventListener("pointermove", this.onMove);
    this.canvas.addEventListener("pointerleave", this.onLeave);
  }

  setData(data: DepthData): void {
    this.data = data;
    this.invalidate();
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.canvas.remove();
  }

  private onMove = (e: PointerEvent) => {
    const rect = this.canvas.getBoundingClientRect();
    this.cursor = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    this.invalidate();
  };

  private onLeave = () => {
    this.cursor = null;
    this.invalidate();
  };

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
    if (cssW < 40 || cssH < 40) return;
    const dpr = window.devicePixelRatio || 1;
    if (this.canvas.width !== Math.round(cssW * dpr) || this.canvas.height !== Math.round(cssH * dpr)) {
      this.canvas.width = Math.round(cssW * dpr);
      this.canvas.height = Math.round(cssH * dpr);
    }
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.font = "10px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, monospace";

    const { bids, asks } = this.data;
    if (!bids.length || !asks.length) return;

    // cumulative from the mid outward
    const bidCum: Cum[] = [];
    let cb = 0;
    for (const [p, s] of bids) bidCum.push({ price: p, cum: (cb += s) });
    const askCum: Cum[] = [];
    let ca = 0;
    for (const [p, s] of asks) askCum.push({ price: p, cum: (ca += s) });

    const mid = (bids[0][0] + asks[0][0]) / 2;
    const pMin = bids[bids.length - 1][0];
    const pMax = asks[asks.length - 1][0];
    const maxCum = Math.max(cb, ca) * 1.08 || 1;

    const plotW = cssW;
    const plotH = cssH - AXIS_H;
    const xOf = (p: number) => ((p - pMin) / (pMax - pMin || 1)) * plotW;
    const yOf = (c: number) => PAD + (1 - c / maxCum) * (plotH - 2 * PAD);
    this.geo = { pMin, pMax, maxCum, plotW, plotH };

    // y grid + labels
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (let k = 0; k <= 3; k++) {
      const c = (maxCum / 1.08) * (k / 3);
      const y = Math.round(yOf(c)) + 0.5;
      ctx.globalAlpha = 0.4;
      ctx.strokeStyle = this.colors.border;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.globalAlpha = 1;
      if (k > 0) {
        ctx.fillStyle = this.colors.muted;
        ctx.fillText(fmtCompact(c), 3, y - 6);
      }
    }

    this.drawSide(ctx, bidCum, mid, xOf, yOf, plotH, this.colors.up, "bid");
    this.drawSide(ctx, askCum, mid, xOf, yOf, plotH, this.colors.down, "ask");

    // mid line + label
    const xMid = xOf(mid);
    ctx.save();
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = this.colors.muted;
    ctx.beginPath();
    ctx.moveTo(xMid, 0);
    ctx.lineTo(xMid, plotH);
    ctx.stroke();
    ctx.restore();
    ctx.fillStyle = this.colors.text;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(fmtPrice(mid), Math.min(Math.max(xMid, 20), plotW - 20), plotH + 3);

    // price axis ends
    ctx.fillStyle = this.colors.muted;
    ctx.textAlign = "left";
    ctx.fillText(fmtPrice(pMin), 2, plotH + 3);
    ctx.textAlign = "right";
    ctx.fillText(fmtPrice(pMax), plotW - 2, plotH + 3);

    if (this.cursor) this.drawCrosshair(ctx, bidCum, askCum, mid, plotW, plotH);
  }

  private drawSide(
    ctx: CanvasRenderingContext2D,
    cum: Cum[],
    mid: number,
    xOf: (p: number) => number,
    yOf: (c: number) => number,
    plotH: number,
    color: string,
    side: "bid" | "ask",
  ): void {
    if (!cum.length) return;
    // step polyline from the mid outward; baseline back to mid for the fill
    ctx.beginPath();
    const x0 = xOf(mid);
    ctx.moveTo(x0, yOf(0));
    ctx.lineTo(x0, yOf(cum[0].cum));
    let prevY = yOf(cum[0].cum);
    for (let i = 0; i < cum.length; i++) {
      const x = xOf(cum[i].price);
      ctx.lineTo(x, prevY); // horizontal hold
      const y = yOf(cum[i].cum);
      ctx.lineTo(x, y); // vertical step
      prevY = y;
    }
    // close down to baseline
    const lastX = xOf(cum[cum.length - 1].price);
    ctx.lineTo(lastX, yOf(0));
    ctx.closePath();
    ctx.fillStyle = color.replace("rgb(", "rgba(").replace(")", ", 0.18)");
    ctx.fill();

    // stroke the staircase edge
    ctx.beginPath();
    ctx.moveTo(x0, yOf(cum[0].cum));
    prevY = yOf(cum[0].cum);
    for (let i = 0; i < cum.length; i++) {
      const x = xOf(cum[i].price);
      ctx.lineTo(x, prevY);
      const y = yOf(cum[i].cum);
      ctx.lineTo(x, y);
      prevY = y;
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    void plotH;
    void side;
  }

  private drawCrosshair(
    ctx: CanvasRenderingContext2D,
    bidCum: Cum[],
    askCum: Cum[],
    mid: number,
    plotW: number,
    plotH: number,
  ): void {
    if (!this.geo) return;
    const { pMin, pMax } = this.geo;
    const cur = this.cursor!;
    if (cur.x < 0 || cur.x > plotW || cur.y > plotH) return;
    const price = pMin + (cur.x / plotW) * (pMax - pMin);
    const isBid = price <= mid;
    // cumulative depth at this price on the relevant side
    let depth = 0;
    if (isBid) {
      for (const c of bidCum) {
        if (c.price >= price) depth = c.cum;
        else break;
      }
    } else {
      for (const c of askCum) {
        if (c.price <= price) depth = c.cum;
        else break;
      }
    }

    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = this.colors.muted;
    ctx.beginPath();
    ctx.moveTo(Math.round(cur.x) + 0.5, 0);
    ctx.lineTo(Math.round(cur.x) + 0.5, plotH);
    ctx.stroke();
    ctx.restore();

    const label = `${fmtPrice(price)}  ·  ${fmtCompact(depth)}`;
    const w = ctx.measureText(label).width + 10;
    const tx = Math.min(Math.max(cur.x - w / 2, 0), plotW - w);
    ctx.fillStyle = this.colors.panel;
    ctx.strokeStyle = isBid ? this.colors.up : this.colors.down;
    ctx.fillRect(tx, 2, w, 16);
    ctx.strokeRect(tx, 2, w, 16);
    ctx.fillStyle = this.colors.text;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, tx + w / 2, 10);
  }
}
