import { useEffect, useRef } from "react";
import type { LinePoint } from "../api/types";
import { chartColors } from "../lib/chartTheme";
import { SeriesChart as SeriesChartEngine } from "../lib/seriesChart";
import { usePalette } from "../state/settings";

export interface SeriesSpec {
  points: LinePoint[];
  color: string;
  title?: string;
  kind?: "line" | "area" | "histogram" | "scatter";
  /** Marker fill for `kind:"scatter"` — filled dot (default) or hollow ring. */
  marker?: "filled" | "hollow";
}

export interface SeriesAxisOpts {
  xMode?: "time" | "value";
  xTicks?: { x: number; label: string }[];
  xUnit?: string;
}

/** Generic multi-series chart on the from-scratch canvas engine (line / area / histogram / scatter).
 * x-axis defaults to time; pass `xMode="value"` (+ optional xTicks/xUnit) for a numeric axis
 * such as a yield curve's tenor-in-years. One engine per palette so theme changes recolor. */
export default function SeriesChart({
  series,
  title,
  axis,
}: {
  series: SeriesSpec[];
  title?: string;
  axis?: SeriesAxisOpts;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const engineRef = useRef<SeriesChartEngine | null>(null);
  const palette = usePalette();

  useEffect(() => {
    if (!ref.current) return;
    const engine = new SeriesChartEngine(ref.current, chartColors());
    engineRef.current = engine;
    return () => {
      engineRef.current = null;
      engine.destroy();
    };
  }, [palette]);

  useEffect(() => {
    engineRef.current?.setData(series, axis);
  }, [series, axis]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {title && <div className="px-1 pb-1 text-[9px] uppercase tracking-wider text-term-muted">{title}</div>}
      <div ref={ref} className="min-h-0 flex-1" />
    </div>
  );
}
