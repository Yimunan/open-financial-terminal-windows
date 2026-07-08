/** SeriesChart — a dependency-free multi-series canvas chart for the backtest dashboard.
 *
 * Replaces the lightweight-charts dependency with the same hand-rolled approach as
 * `candleChart.ts`: one canvas, injected theme colors, HiDPI backing store, rAF-batched
 * render, ResizeObserver. Renders any mix of line / area / histogram series on a shared
 * linear time x-axis + value y-axis, with a 1-2-5 tick grid, a legend, and a crosshair
 * tooltip that reads each series' value at the hovered date.
 */

import type { ChartColors } from "./candleChart";

export interface SeriesPoint {
  time: string | number; // "YYYY-MM-DD" (daily) or unix seconds (intraday)
  value: number;
}

export interface ChartSeries {
  points: SeriesPoint[];
  color: string;
  title?: string;
  kind?: "line" | "area" | "histogram" | "scatter";
  /** Marker fill for `kind:"scatter"` — filled dot (default) or hollow ring. */
  marker?: "filled" | "hollow";
}

/** Options that change how the x-axis is interpreted/labelled.
 * Default ("time"): `point.time` is a date string / unix-seconds timestamp (the original behavior).
 * "value": `point.time` is a raw numeric x (e.g. tenor in years for a yield curve); axis ticks come
 * from `xTicks` when supplied, else a numeric 1-2-5 grid; `xUnit` suffixes numeric labels. */
export interface SeriesOpts {
  xMode?: "time" | "value";
  xTicks?: { x: number; label: string }[];
  xUnit?: string;
}

const AXIS_W = 54; // right value axis
const AXIS_H = 18; // bottom time axis
const PAD_TOP = 18; // legend strip
const PAD_LEFT = 6;

function niceStep(span: number, target: number): number {
  const raw = span / Math.max(target, 1);
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  for (const m of [1, 2, 5, 10]) if (raw <= m * mag) return m * mag;
  return 10 * mag;
}

function toMs(time: string | number): number {
  return typeof time === "number" ? time * 1000 : new Date(`${time}T00:00:00`).getTime();
}

function fmtValue(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1000) return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
  if (abs >= 10) return v.toFixed(1);
  if (abs >= 0.01 || v === 0) return v.toFixed(2);
  return v.toPrecision(2);
}

export class SeriesChart {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private colors: ChartColors;
  private ro: ResizeObserver;
  private series: ChartSeries[] = [];
  private xMode: "time" | "value" = "time";
  private xTicks: { x: number; label: string }[] = [];
  private xUnit = "";
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

  setData(series: ChartSeries[], opts?: SeriesOpts): void {
    this.series = series;
    this.xMode = opts?.xMode ?? "time";
    this.xTicks = opts?.xTicks ?? [];
    this.xUnit = opts?.xUnit ?? "";
    this.invalidate();
  }

  /** x-coordinate value for a point: raw number in "value" mode, epoch-ms in "time" mode. */
  private xv(t: string | number): number {
    return this.xMode === "value" ? Number(t) : toMs(t);
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
    const { ctx, canvas, container, colors } = this;
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

    const pts = this.series.flatMap((s) => s.points);
    if (pts.length === 0) {
      ctx.fillStyle = colors.muted;
      ctx.font = "11px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, ui-monospace, monospace";
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

    // domains
    let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity;
    const hasHist = this.series.some((s) => s.kind === "histogram");
    for (const s of this.series) {
      for (const p of s.points) {
        const t = this.xv(p.time);
        if (t < tMin) tMin = t;
        if (t > tMax) tMax = t;
        if (p.value < vMin) vMin = p.value;
        if (p.value > vMax) vMax = p.value;
      }
    }
    if (hasHist) { vMin = Math.min(vMin, 0); vMax = Math.max(vMax, 0); }
    if (vMin === vMax) { vMin -= 1; vMax += 1; }
    const padV = (vMax - vMin) * 0.06;
    vMin -= padV;
    vMax += padV;
    const tSpan = tMax - tMin || 1;
    const vSpan = vMax - vMin || 1;

    const xOf = (t: number) => pl + ((t - tMin) / tSpan) * plotW;
    const yOf = (v: number) => pt + ((vMax - v) / vSpan) * plotH;

    // ── y grid + labels ──
    ctx.font = "10px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, ui-monospace, monospace";
    ctx.textBaseline = "middle";
    const yStep = niceStep(vMax - vMin, 5);
    ctx.strokeStyle = colors.border;
    ctx.fillStyle = colors.muted;
    for (let v = Math.ceil(vMin / yStep) * yStep; v <= vMax; v += yStep) {
      const y = yOf(v);
      ctx.globalAlpha = 0.4;
      ctx.beginPath();
      ctx.moveTo(pl, y);
      ctx.lineTo(pr, y);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.textAlign = "left";
      ctx.fillText(fmtValue(v), pr + 4, y);
    }

    // ── x grid + labels ──
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const gridLine = (x: number, label: string, last: string): string => {
      ctx.strokeStyle = colors.border;
      ctx.globalAlpha = 0.25;
      ctx.beginPath();
      ctx.moveTo(x, pt);
      ctx.lineTo(x, pb);
      ctx.stroke();
      ctx.globalAlpha = 1;
      if (label && label !== last) {
        ctx.fillStyle = colors.muted;
        ctx.fillText(label, Math.min(Math.max(x, pl + 14), pr - 14), pb + 3);
        return label;
      }
      return last;
    };
    if (this.xMode === "value") {
      // Explicit ticks (e.g. tenor labels) when supplied, else a numeric 1-2-5 grid.
      const marks = this.xTicks.length
        ? this.xTicks.filter((m) => m.x >= tMin && m.x <= tMax)
        : (() => {
            const step = niceStep(tMax - tMin, 5);
            const out: { x: number; label: string }[] = [];
            for (let v = Math.ceil(tMin / step) * step; v <= tMax; v += step)
              out.push({ x: v, label: fmtValue(v) + this.xUnit });
            return out;
          })();
      let lastLabel = "";
      for (const m of marks) lastLabel = gridLine(xOf(m.x), m.label, lastLabel);
    } else {
      const spanDays = tSpan / 86400000;
      const dateFmt: Intl.DateTimeFormatOptions =
        spanDays > 540 ? { year: "numeric" } : spanDays > 60 ? { month: "short", year: "2-digit" } : { month: "short", day: "numeric" };
      const ticks = 5;
      let lastLabel = "";
      for (let i = 0; i <= ticks; i++) {
        const t = tMin + (tSpan * i) / ticks;
        lastLabel = gridLine(xOf(t), new Date(t).toLocaleDateString("en-US", dateFmt), lastLabel);
      }
    }

    // baseline for histograms / zero line
    if (vMin < 0 && vMax > 0) {
      ctx.strokeStyle = colors.muted;
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.moveTo(pl, yOf(0));
      ctx.lineTo(pr, yOf(0));
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // ── series ──
    ctx.save();
    ctx.beginPath();
    ctx.rect(pl, pt, plotW, plotH);
    ctx.clip();
    for (const s of this.series) {
      if (s.points.length === 0) continue;
      if (s.kind === "histogram") {
        const bw = Math.max(1, (plotW / s.points.length) * 0.7);
        ctx.fillStyle = s.color;
        const y0 = yOf(0);
        for (const p of s.points) {
          const x = xOf(this.xv(p.time));
          const y = yOf(p.value);
          ctx.fillRect(x - bw / 2, Math.min(y, y0), bw, Math.abs(y - y0) || 1);
        }
        continue;
      }
      if (s.kind === "scatter") {
        // discrete markers per point: filled dot (default) or hollow ring — no connecting path.
        const hollow = s.marker === "hollow";
        ctx.strokeStyle = s.color;
        ctx.fillStyle = s.color;
        ctx.lineWidth = 1.4;
        for (const p of s.points) {
          const x = xOf(this.xv(p.time));
          const y = yOf(p.value);
          ctx.beginPath();
          ctx.arc(x, y, 3, 0, Math.PI * 2);
          hollow ? ctx.stroke() : ctx.fill();
        }
        continue;
      }
      if (s.kind === "area") {
        ctx.beginPath();
        s.points.forEach((p, i) => {
          const x = xOf(this.xv(p.time));
          const y = yOf(p.value);
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        });
        const lastX = xOf(this.xv(s.points[s.points.length - 1].time));
        const firstX = xOf(this.xv(s.points[0].time));
        ctx.lineTo(lastX, yOf(vMin > 0 ? vMin : 0));
        ctx.lineTo(firstX, yOf(vMin > 0 ? vMin : 0));
        ctx.closePath();
        ctx.globalAlpha = 0.16;
        ctx.fillStyle = s.color;
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      // line stroke (also for area outline)
      ctx.beginPath();
      s.points.forEach((p, i) => {
        const x = xOf(this.xv(p.time));
        const y = yOf(p.value);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.title === "benchmark" ? 1 : 1.6;
      ctx.stroke();
    }
    ctx.restore();

    // ── legend ──
    let lx = pl + 2;
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    ctx.font = "10px 'JetBrains Mono Variable', 'JetBrains Mono', Consolas, ui-monospace, monospace";
    for (const s of this.series) {
      if (!s.title) continue;
      ctx.strokeStyle = s.color;
      ctx.fillStyle = s.color;
      ctx.lineWidth = s.kind === "scatter" ? 1.4 : 2;
      if (s.kind === "scatter") {
        ctx.beginPath();
        ctx.arc(lx + 7, PAD_TOP / 2, 3, 0, Math.PI * 2);
        s.marker === "hollow" ? ctx.stroke() : ctx.fill();
      } else {
        ctx.beginPath();
        ctx.moveTo(lx, PAD_TOP / 2);
        ctx.lineTo(lx + 14, PAD_TOP / 2);
        ctx.stroke();
      }
      ctx.fillStyle = colors.muted;
      ctx.fillText(s.title, lx + 18, PAD_TOP / 2 + 0.5);
      lx += 24 + ctx.measureText(s.title).width;
    }

    // ── crosshair + tooltip ──
    if (this.cursor && this.cursor.x >= pl && this.cursor.x <= pr) {
      const t = tMin + ((this.cursor.x - pl) / plotW) * tSpan;
      ctx.strokeStyle = colors.muted;
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(this.cursor.x, pt);
      ctx.lineTo(this.cursor.x, pb);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      // nearest point per series
      const rows: { color: string; title: string; value: number }[] = [];
      let nearestT = t;
      for (const s of this.series) {
        if (s.points.length === 0) continue;
        let best = s.points[0];
        let bestD = Infinity;
        for (const p of s.points) {
          const d = Math.abs(this.xv(p.time) - t);
          if (d < bestD) { bestD = d; best = p; }
        }
        nearestT = this.xv(best.time);
        const x = xOf(this.xv(best.time));
        const y = yOf(best.value);
        ctx.fillStyle = s.color;
        ctx.beginPath();
        ctx.arc(x, y, 2.5, 0, Math.PI * 2);
        ctx.fill();
        rows.push({ color: s.color, title: s.title ?? "", value: best.value });
      }
      const headStr =
        this.xMode === "value"
          ? (this.xTicks.find((m) => m.x === nearestT)?.label ?? fmtValue(nearestT) + this.xUnit)
          : new Date(nearestT).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
      const lines = [headStr, ...rows.map((r) => `${r.title ? r.title + " " : ""}${fmtValue(r.value)}`)];
      const tw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 14;
      const th = lines.length * 13 + 6;
      let bx = this.cursor.x + 10;
      if (bx + tw > pr) bx = this.cursor.x - tw - 10;
      const by = pt + 4;
      ctx.fillStyle = colors.panel;
      ctx.globalAlpha = 0.92;
      ctx.fillRect(bx, by, tw, th);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = colors.border;
      ctx.strokeRect(bx, by, tw, th);
      ctx.textBaseline = "middle";
      ctx.textAlign = "left";
      lines.forEach((l, i) => {
        ctx.fillStyle = i === 0 ? colors.text : rows[i - 1].color;
        ctx.fillText(l, bx + 7, by + 9 + i * 13);
      });
    }
  }
}
