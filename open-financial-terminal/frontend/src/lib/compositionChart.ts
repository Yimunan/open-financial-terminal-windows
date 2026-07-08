/** CompositionChart — a dependency-free 100%-stacked-area canvas engine.
 *
 * Same hand-rolled approach as candleChart / seriesChart (one canvas, injected theme colors,
 * HiDPI backing store, rAF-batched render, ResizeObserver). Renders portfolio composition over
 * time: each band is one component's share (%), stacked bottom-to-top on a date x-axis with a
 * 0–100% y-axis, a legend, and a crosshair tooltip reading every band's weight at the hovered date.
 */

import type { ChartColors } from "./candleChart";

export interface CompositionBand {
  label: string;
  color: string;
  /** Per-date weight in percent (0–100), aligned 1:1 with `times`. */
  weights: number[];
}

const AXIS_W = 44; // right value axis
const AXIS_H = 18; // bottom time axis
const PAD_TOP = 18; // legend strip
const PAD_LEFT = 6;
const FONT = "10px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, ui-monospace, monospace";

function toMs(time: string): number {
  return new Date(`${time}T00:00:00`).getTime();
}

export class CompositionChart {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private colors: ChartColors;
  private ro: ResizeObserver;
  private times: number[] = [];
  private bands: CompositionBand[] = [];
  private cursor: { x: number; y: number } | null = null;
  private dirty = false;
  private raf = 0;
  private destroyed = false;

  constructor(container: HTMLElement, colors: ChartColors) {
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

  setData(times: string[], bands: CompositionBand[]): void {
    this.times = times.map(toMs);
    this.bands = bands;
    this.invalidate();
  }

  setColors(colors: ChartColors): void {
    this.colors = colors;
    this.invalidate();
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.canvas.removeEventListener("pointermove", this.onMove);
    this.canvas.removeEventListener("pointerleave", this.onLeave);
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
    const { ctx, canvas, container, colors, times, bands } = this;
    const dpr = window.devicePixelRatio || 1;
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (w === 0 || h === 0) return;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = colors.panel;
    ctx.fillRect(0, 0, w, h);

    if (times.length === 0 || bands.length === 0) {
      ctx.fillStyle = colors.muted;
      ctx.font = FONT;
      ctx.textAlign = "center";
      ctx.fillText("no data", w / 2, h / 2);
      return;
    }

    const pl = PAD_LEFT;
    const pr = w - AXIS_W;
    const pt = PAD_TOP;
    const pb = h - AXIS_H;
    const plotW = pr - pl;
    const plotH = pb - pt;

    const tMin = times[0];
    const tMax = times[times.length - 1];
    const tSpan = tMax - tMin || 1;
    const xOf = (t: number) => pl + ((t - tMin) / tSpan) * plotW;
    const yOf = (v: number) => pt + ((100 - v) / 100) * plotH; // 0–100%, 100 at top

    // ── y grid + labels (0/25/50/75/100%) ──
    ctx.font = FONT;
    ctx.textBaseline = "middle";
    ctx.strokeStyle = colors.border;
    for (let v = 0; v <= 100; v += 25) {
      const y = yOf(v);
      ctx.globalAlpha = 0.35;
      ctx.beginPath();
      ctx.moveTo(pl, y);
      ctx.lineTo(pr, y);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = colors.muted;
      ctx.textAlign = "left";
      ctx.fillText(`${v}%`, pr + 4, y);
    }

    // ── x grid + labels ──
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const spanDays = tSpan / 86400000;
    const dateFmt: Intl.DateTimeFormatOptions =
      spanDays > 540 ? { year: "numeric" } : spanDays > 60 ? { month: "short", year: "2-digit" } : { month: "short", day: "numeric" };
    let lastLabel = "";
    for (let i = 0; i <= 5; i++) {
      const t = tMin + (tSpan * i) / 5;
      const x = xOf(t);
      ctx.strokeStyle = colors.border;
      ctx.globalAlpha = 0.2;
      ctx.beginPath();
      ctx.moveTo(x, pt);
      ctx.lineTo(x, pb);
      ctx.stroke();
      ctx.globalAlpha = 1;
      const label = new Date(t).toLocaleDateString("en-US", dateFmt);
      if (label !== lastLabel) {
        ctx.fillStyle = colors.muted;
        ctx.fillText(label, Math.min(Math.max(x, pl + 14), pr - 14), pb + 3);
        lastLabel = label;
      }
    }

    // ── stacked bands (cumulative from the bottom, in array order) ──
    ctx.save();
    ctx.beginPath();
    ctx.rect(pl, pt, plotW, plotH);
    ctx.clip();
    const lower = new Array(times.length).fill(0); // running cumulative base per date
    for (const band of bands) {
      const upper = band.weights.map((v, i) => lower[i] + Math.max(v, 0));
      ctx.beginPath();
      // top edge left→right
      times.forEach((t, i) => {
        const x = xOf(t);
        const y = yOf(upper[i]);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      // bottom edge right→left
      for (let i = times.length - 1; i >= 0; i--) ctx.lineTo(xOf(times[i]), yOf(lower[i]));
      ctx.closePath();
      ctx.globalAlpha = 0.78;
      ctx.fillStyle = band.color;
      ctx.fill();
      ctx.globalAlpha = 1;
      for (let i = 0; i < lower.length; i++) lower[i] = upper[i];
    }
    ctx.restore();

    // ── legend (heaviest first, clipped to the strip) ──
    let lx = pl + 2;
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    for (const band of bands) {
      if (lx > pr - 40) break;
      ctx.fillStyle = band.color;
      ctx.fillRect(lx, PAD_TOP / 2 - 4, 8, 8);
      ctx.fillStyle = colors.muted;
      ctx.fillText(band.label, lx + 11, PAD_TOP / 2 + 0.5);
      lx += 20 + ctx.measureText(band.label).width;
    }

    // ── crosshair + tooltip ──
    if (this.cursor && this.cursor.x >= pl && this.cursor.x <= pr) {
      const t = tMin + ((this.cursor.x - pl) / plotW) * tSpan;
      // nearest date index
      let idx = 0;
      let bestD = Infinity;
      times.forEach((tt, i) => {
        const d = Math.abs(tt - t);
        if (d < bestD) { bestD = d; idx = i; }
      });
      const cx = xOf(times[idx]);
      ctx.strokeStyle = colors.muted;
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(cx, pt);
      ctx.lineTo(cx, pb);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      const head = new Date(times[idx]).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
      const rows = bands
        .map((b) => ({ color: b.color, title: b.label, value: b.weights[idx] ?? 0 }))
        .filter((r) => r.value >= 0.05);
      const lines = [head, ...rows.map((r) => `${r.title} ${r.value.toFixed(1)}%`)];
      const tw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 16;
      const th = lines.length * 13 + 6;
      let bx = this.cursor.x + 10;
      if (bx + tw > pr) bx = this.cursor.x - tw - 10;
      const by = Math.min(pt + 4, pb - th);
      ctx.fillStyle = colors.panel;
      ctx.globalAlpha = 0.94;
      ctx.fillRect(bx, by, tw, th);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = colors.border;
      ctx.strokeRect(bx, by, tw, th);
      ctx.textBaseline = "middle";
      ctx.textAlign = "left";
      lines.forEach((l, i) => {
        ctx.fillStyle = i === 0 ? colors.text : rows[i - 1].color;
        ctx.fillText(l, bx + 8, by + 9 + i * 13);
      });
    }
  }
}
