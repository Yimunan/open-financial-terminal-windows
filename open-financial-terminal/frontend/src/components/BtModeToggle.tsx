import { cx } from "../lib/format";
import { useT } from "../lib/i18n";

/** The three tools that share the Backtest widget: cross-sectional Factor, single-symbol
 * Strategy Lab, and the Market-Making quoting comparison. Mode persists in the panel param. */
export type BtMode = "factor" | "lab" | "mm";

export default function BtModeToggle({ mode, onMode }: { mode: BtMode; onMode: (m: BtMode) => void }) {
  const t = useT();
  const items: [BtMode, string][] = [["factor", t("bt.factor")], ["lab", t("bt.lab")], ["mm", "MM"]];
  return (
    <div className="flex items-center gap-px rounded border border-term-border">
      {items.map(([k, label]) => (
        <button
          key={k}
          onClick={() => onMode(k)}
          className={cx(
            "px-2 py-0.5 text-[11px] uppercase",
            mode === k ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
