import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset, SearchHit } from "../api/types";
import { cx } from "../lib/format";

/** Predictive symbol search input: as the user types, queries /api/search and shows a ranked
 * dropdown of matches (symbol + asset + sector/name). Keyboard: ↑/↓ to move, Enter to pick,
 * Esc to close. `onChange` keeps the raw text in the parent (so manual entry still works);
 * `onSelect` fires when a prediction is chosen. `placement` flips the list above the input for
 * forms anchored near the bottom of a panel. */
export default function SymbolSearch({
  value,
  onChange,
  onSelect,
  placeholder,
  ariaLabel,
  inputClassName,
  placement = "bottom",
}: {
  value: string;
  onChange: (v: string) => void;
  onSelect: (hit: { symbol: string; asset: Asset }) => void;
  placeholder?: string;
  ariaLabel?: string;
  inputClassName?: string;
  placement?: "top" | "bottom";
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const blurTimer = useRef<number | undefined>(undefined);

  const q = value.trim();
  const { data } = useQuery({
    queryKey: ["search", q],
    queryFn: () => api.search(q),
    enabled: q.length >= 1 && open,
    staleTime: 60_000,
  });
  const hits: SearchHit[] = data?.results ?? [];
  const show = open && q.length >= 1 && hits.length > 0;

  const choose = (h: SearchHit) => {
    onSelect({ symbol: h.symbol, asset: h.asset });
    setOpen(false);
    setActive(0);
  };

  return (
    <div className="relative">
      <input
        value={value}
        onChange={(e) => { onChange(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)}
        onBlur={() => { blurTimer.current = window.setTimeout(() => setOpen(false), 120); }}
        onKeyDown={(e) => {
          if (!show) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter") { e.preventDefault(); choose(hits[active]); }
          else if (e.key === "Escape") { setOpen(false); }
        }}
        placeholder={placeholder}
        aria-label={ariaLabel}
        autoComplete="off"
        role="combobox"
        aria-expanded={show}
        aria-controls="symbol-search-list"
        className={inputClassName}
      />
      {show && (
        <div
          id="symbol-search-list"
          role="listbox"
          className={cx(
            "absolute left-0 z-40 max-h-56 min-w-[220px] overflow-auto rounded border border-term-border bg-term-elev shadow-elev-2",
            placement === "top" ? "bottom-full mb-1" : "top-full mt-1",
          )}
        >
          {hits.map((h, i) => (
            <button
              key={`${h.symbol}-${h.universe}`}
              type="button"
              role="option"
              aria-selected={i === active}
              // mousedown fires before the input's blur — preventDefault keeps focus so the click lands
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(h)}
              onMouseEnter={() => setActive(i)}
              className={cx(
                "flex w-full items-center justify-between px-2 py-1.5 text-xs",
                i === active ? "bg-term-border/50" : "hover:bg-term-border/50",
              )}
            >
              <span className="font-mono font-semibold">{h.symbol}</span>
              <span className="truncate pl-2 text-term-muted">{h.asset} · {h.sector ?? h.name ?? h.universe}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
