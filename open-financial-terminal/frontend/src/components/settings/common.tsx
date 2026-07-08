/** Shared building blocks for the Settings sections (extracted from the old monolithic
 * SettingsDialog). Each section under this folder is a self-contained component that fetches its own
 * data on mount; these primitives keep them visually consistent. */
import { cx } from "../../lib/format";

/** Full-width mono input (LLM base-url / model / registry-dir fields, etc.). */
export const llmInputCls =
  "focus-ring w-full rounded border border-term-border bg-term-sunken px-2 py-1 font-mono text-xs text-term-text focus:border-term-accent";

// Same look as llmInputCls but WITHOUT `w-full`, so it can be flex-sized inside a row without
// overflowing (used by the News Topics rows, where width must yield to the Add/Remove buttons).
export const topicInputCls =
  "focus-ring rounded border border-term-border bg-term-sunken px-2 py-1 font-mono text-xs text-term-text focus:border-term-accent";

/** Compact numeric input (for inline weight/parameter fields next to a slider). */
export const numCls =
  "focus-ring w-14 rounded border border-term-border bg-term-sunken px-1 py-0.5 text-right text-[10px] tabular-nums text-term-text focus:border-term-accent";

export const clampNum = (v: number, lo: number, hi: number) =>
  Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : lo));

/** A short {ok, detail} status message, or null when there's nothing to show. */
export type Msg = { ok: boolean; detail: string } | null;

/** Relative "x ago" / "in x" for refresh timestamps; null → "never". */
export function relTime(iso: string | null): string {
  if (!iso) return "never";
  const ms = new Date(iso).getTime() - Date.now();
  const abs = Math.abs(ms);
  const m = Math.round(abs / 60000);
  const unit = m < 60 ? `${m}m` : m < 1440 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d`;
  if (m < 1) return ms >= 0 ? "soon" : "just now";
  return ms >= 0 ? `in ${unit}` : `${unit} ago`;
}

/** Left-nav entry (grouped section list in the dialog shell). */
export function NavItem({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cx(
        "focus-ring block w-full rounded px-2.5 py-1.5 text-left text-xs transition-colors",
        active
          ? "bg-term-accent/15 text-term-accent"
          : "text-term-muted hover:bg-term-border/40 hover:text-term-text",
      )}
    >
      {children}
    </button>
  );
}

/** A label + controls row (the common two-column settings row). */
export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 py-2.5">
      <span className="text-xs text-term-muted">{label}</span>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  );
}

/** A segmented-choice button (theme/on-off/feed toggles). */
export function Choice({
  active,
  onClick,
  children,
  disabled,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cx(
        "focus-ring rounded border px-2.5 py-1 text-xs transition-colors disabled:opacity-40",
        active
          ? "border-term-accent bg-term-accent/15 text-term-accent"
          : "border-term-border text-term-muted hover:text-term-text",
      )}
    >
      {children}
    </button>
  );
}

/** Compact ranking-priority control for a news source (0–100; 50 = neutral). */
export function WeightSlider({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <span className="flex shrink-0 items-center gap-1" title="Ranking priority (0–100)">
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1 w-16 accent-term-accent"
        aria-label="Ranking priority"
      />
      <input
        type="number"
        min={0}
        max={100}
        step={1}
        value={value}
        onChange={(e) => onChange(clampNum(Number(e.target.value), 0, 100))}
        className={cx(numCls, "w-11")}
        aria-label="Ranking priority value"
      />
    </span>
  );
}

/** The shared "Working… / ✓ saved / ✕ error" line every section shows under its actions. */
export function Status({
  busy,
  msg,
  working = "Working…",
}: {
  busy: boolean;
  msg: Msg;
  working?: string;
}) {
  if (!busy && !msg) return null;
  return (
    <div
      className={cx(
        "mt-1.5 truncate text-[10px]",
        busy ? "text-term-muted" : msg?.ok ? "text-term-up" : "text-term-down",
      )}
      title={msg?.detail}
    >
      {busy ? working : `${msg?.ok ? "✓" : "✕"} ${msg?.detail}`}
    </div>
  );
}

/** A small header + optional right-aligned status chip, used at the top of most sections. */
export function SectionHeader({
  title,
  chip,
  chipActive,
}: {
  title: string;
  chip?: React.ReactNode;
  chipActive?: boolean;
}) {
  return (
    <div className="mb-1.5 flex items-center justify-between">
      <span className="text-xs font-semibold text-term-text">{title}</span>
      {chip != null && (
        <span
          className={cx(
            "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider",
            chipActive ? "bg-term-accent/15 text-term-accent" : "bg-term-border/40 text-term-muted",
          )}
        >
          {chip}
        </span>
      )}
    </div>
  );
}
