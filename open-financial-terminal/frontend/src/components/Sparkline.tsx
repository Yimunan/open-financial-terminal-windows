import { useMemo } from "react";

/** Tiny inline trend line (SVG — cheap at watchlist sizes; charts proper use canvas). */
export default function Sparkline({
  points,
  width = 64,
  height = 18,
}: {
  points: number[] | undefined;
  width?: number;
  height?: number;
}) {
  const { path, rising } = useMemo(() => {
    if (!points || points.length < 2) return { path: "", rising: true };
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = max - min || 1;
    const step = width / (points.length - 1);
    const d = points
      .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(height - ((v - min) / span) * (height - 2) - 1).toFixed(1)}`)
      .join(" ");
    return { path: d, rising: points[points.length - 1] >= points[0] };
  }, [points, width, height]);

  if (!path) return <span className="inline-block" style={{ width, height }} />;
  return (
    <svg width={width} height={height} className="inline-block align-middle">
      <path
        d={path}
        fill="none"
        strokeWidth={1.2}
        stroke={rising ? "rgb(var(--term-up))" : "rgb(var(--term-down))"}
      />
    </svg>
  );
}
