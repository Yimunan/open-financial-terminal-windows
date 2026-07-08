/** DonutChart — a dependency-free circular (donut) allocation chart.
 *
 * Same hand-rolled canvas approach as the other engines (HiDPI backing store, rAF-batched render,
 * ResizeObserver, injected theme colors). Renders one ring of value-proportional segments with a
 * hover highlight, a center readout (total, or the hovered segment's share), and leader labels for
 * the larger slices. Used to visualize the current portfolio holdings snapshot.
 */

import type { ChartColors } from "./candleChart";

export interface DonutSegment {
  label: string;
  value: number; // segment magnitude (e.g. market value); negatives are treated as |value|
  color: string;
}

const FONT = "11px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, ui-monospace, monospace";
const FONT_SM = "10px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, ui-monospace, monospace";
const TAU = Math.PI * 2;

function fmtCompact(v: number): string {
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

export class DonutChart {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private colors: ChartColors;
  private ro: ResizeObserver;
  private segments: DonutSegment[] = [];
  private hover = -1;
  private dirty = false;
  private raf = 0;
  private destroyed = false;
  // geometry cached each render for hit-testing
  private geo = { cx: 0, cy: 0, rOuter: 0, rInner: 0 };

  constructor(container: HTMLElement, colors: ChartColors) {
    this.container = container;
    this.colors = colors;
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "display:block;width:100%;height:100%;cursor:pointer";
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;
    this.ro = new ResizeObserver(() => this.invalidate());
    this.ro.observe(container);
    this.canvas.addEventListener("pointermove", this.onMove);
    this.canvas.addEventListener("pointerleave", this.onLeave);
  }

  setData(segments: DonutSegment[]): void {
    this.segments = segments.map((s) => ({ ...s, value: Math.abs(s.value) })).filter((s) => s.value > 0);
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
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const { cx, cy, rOuter, rInner } = this.geo;
    const dx = x - cx;
    const dy = y - cy;
    const r = Math.hypot(dx, dy);
    let next = -1;
    if (r >= rInner && r <= rOuter && this.segments.length) {
      // angle measured from 12 o'clock, clockwise (matches the render start at -PI/2)
      let a = Math.atan2(dy, dx) + Math.PI / 2;
      if (a < 0) a += TAU;
      const total = this.segments.reduce((s, g) => s + g.value, 0);
      let acc = 0;
      for (let i = 0; i < this.segments.length; i++) {
        const frac = this.segments[i].value / total;
        if (a >= acc * TAU && a < (acc + frac) * TAU) { next = i; break; }
        acc += frac;
      }
    }
    if (next !== this.hover) { this.hover = next; this.invalidate(); }
  };

  private onLeave = () => {
    if (this.hover !== -1) { this.hover = -1; this.invalidate(); }
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
    const { ctx, canvas, container, colors, segments } = this;
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

    if (segments.length === 0) {
      ctx.fillStyle = colors.muted;
      ctx.font = FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("no holdings", w / 2, h / 2);
      return;
    }

    const total = segments.reduce((s, g) => s + g.value, 0);
    const cx = w / 2;
    const cy = h / 2;
    const rOuter = Math.max(20, Math.min(w, h) / 2 - 14);
    const rInner = rOuter * 0.58;
    this.geo = { cx, cy, rOuter, rInner };

    // ── segments ──
    let a0 = -Math.PI / 2;
    segments.forEach((seg, i) => {
      const frac = seg.value / total;
      const a1 = a0 + frac * TAU;
      const grow = i === this.hover ? 3 : 0;
      ctx.beginPath();
      ctx.arc(cx, cy, rOuter + grow, a0, a1);
      ctx.arc(cx, cy, rInner, a1, a0, true);
      ctx.closePath();
      ctx.fillStyle = seg.color;
      ctx.globalAlpha = this.hover === -1 || this.hover === i ? 1 : 0.4;
      ctx.fill();
      ctx.globalAlpha = 1;
      // thin separator
      ctx.strokeStyle = colors.panel;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      a0 = a1;
    });

    // ── leader labels for slices >= 7% ──
    ctx.font = FONT_SM;
    ctx.textBaseline = "middle";
    a0 = -Math.PI / 2;
    segments.forEach((seg) => {
      const frac = seg.value / total;
      const mid = a0 + (frac * TAU) / 2;
      a0 += frac * TAU;
      if (frac < 0.07) return;
      const lr = rOuter + 6;
      const lx = cx + Math.cos(mid) * lr;
      const ly = cy + Math.sin(mid) * lr;
      ctx.fillStyle = colors.muted;
      ctx.textAlign = Math.cos(mid) >= 0 ? "left" : "right";
      ctx.fillText(`${seg.label} ${(frac * 100).toFixed(0)}%`, lx, ly);
    });

    // ── center readout ──
    ctx.textAlign = "center";
    const hov = this.hover >= 0 ? segments[this.hover] : null;
    if (hov) {
      ctx.fillStyle = hov.color;
      ctx.font = FONT;
      ctx.fillText(hov.label, cx, cy - 8);
      ctx.fillStyle = colors.text;
      ctx.fillText(`${((hov.value / total) * 100).toFixed(1)}%`, cx, cy + 7);
      ctx.fillStyle = colors.muted;
      ctx.font = FONT_SM;
      ctx.fillText(`$${fmtCompact(hov.value)}`, cx, cy + 21);
    } else {
      ctx.fillStyle = colors.muted;
      ctx.font = FONT_SM;
      ctx.fillText("TOTAL", cx, cy - 9);
      ctx.fillStyle = colors.text;
      ctx.font = FONT;
      ctx.fillText(`$${fmtCompact(total)}`, cx, cy + 6);
      ctx.fillStyle = colors.muted;
      ctx.font = FONT_SM;
      ctx.fillText(`${segments.length} names`, cx, cy + 20);
    }
  }
}
