import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset, BoardItem, BoardResponse } from "../api/types";
import { cx } from "../lib/format";
import { useT } from "../lib/i18n";
import { useMarketPool } from "../state/marketPool";
import { SkeletonRows, TextButton } from "./shell";
import { EmptyState, ErrorState } from "../components/States";

/** Which pool store backs the panel and which catalog feeds the "add from board" dropdown +
 * suggestions. Defaults to the Market Board pool/catalog, so existing callers are unchanged. */
interface CorrelationPanelProps {
  usePool?: typeof useMarketPool;
  catalog?: { key: unknown[]; fetch: () => Promise<BoardResponse> };
}

/** Trailing windows offered for the return series feeding the correlation. */
const WINDOWS: { days: number; label: string }[] = [
  { days: 90, label: "3M" },
  { days: 180, label: "6M" },
  { days: 365, label: "1Y" },
  { days: 730, label: "2Y" },
];

/** -1 → red, 0 → transparent, +1 → green (same scale as the Portfolio risk heatmap). */
function corrColor(v: number | null): string {
  if (v == null) return "transparent";
  const a = Math.min(Math.abs(v), 1) * 0.55;
  return v >= 0 ? `rgb(var(--term-up) / ${a})` : `rgb(var(--term-down) / ${a})`;
}

/** Compact axis tick from a fetch symbol: ^GSPC→GSPC, GC=F→GC, EURUSD=X→EURUSD, BTC/USD→BTC. */
function tick(sym: string): string {
  return sym
    .replace(/^\^/, "")
    .replace(/=[FX]$/i, "")
    .replace(/-Y\.NYB$/i, "")
    .split("/")[0];
}

export default function CorrelationPanel({
  usePool = useMarketPool,
  catalog = { key: ["board"], fetch: api.board },
}: CorrelationPanelProps = {}) {
  const t = useT();
  const { pool, add, remove, reset } = usePool();
  const [days, setDays] = useState(365);
  const [draft, setDraft] = useState("");

  const { data: board } = useQuery({ queryKey: catalog.key, queryFn: catalog.fetch, staleTime: Infinity });

  // Symbol prediction: search the equities/crypto universe as the user types (board catalog
  // matches are merged in client-side below — /api/search doesn't index index/FX/commodity tickers).
  const q = draft.trim();
  const { data: hits } = useQuery({
    queryKey: ["search", q],
    queryFn: () => api.search(q),
    enabled: q.length >= 1,
    staleTime: 60_000,
  });

  const poolKey = pool.map((p) => `${p.symbol}:${p.asset}`).join(",");
  const { data, isFetching, error } = useQuery({
    queryKey: ["risk", poolKey, days],
    queryFn: () => api.risk(pool.map((p) => ({ symbol: p.symbol, asset: p.asset })), days),
    enabled: pool.length >= 2,
    staleTime: 5 * 60_000,
  });

  // symbol (upper) → friendly name, for axis tooltips
  const nameBy = useMemo(() => {
    const m = new Map<string, string>();
    pool.forEach((p) => m.set(p.symbol.toUpperCase(), p.name));
    return m;
  }, [pool]);

  const inPool = (sym: string) => pool.some((p) => p.symbol.toUpperCase() === sym.toUpperCase());

  const addFree = () => {
    const sym = draft.trim().toUpperCase();
    if (!sym || inPool(sym)) return setDraft("");
    const asset: Asset = sym.includes("/") ? "crypto" : "equity";
    add({ symbol: sym, name: sym, asset });
    setDraft("");
  };

  const addCatalog = (symbol: string) => {
    const item: BoardItem | undefined = board?.sections.flatMap((s) => s.items).find((i) => i.symbol === symbol);
    if (item) add(item);
  };

  // Predicted matches for the typed query: board catalog (cross-asset, named) first, then the
  // universe search — deduped by symbol, excluding what's already pooled, capped for the dropdown.
  const suggestions = useMemo(() => {
    const ql = q.toLowerCase();
    if (!ql) return [] as { symbol: string; asset: Asset; name: string; label: string }[];
    const out: { symbol: string; asset: Asset; name: string; label: string }[] = [];
    const seen = new Set<string>();
    const push = (symbol: string, asset: Asset, name: string, label: string) => {
      const k = symbol.toUpperCase();
      if (seen.has(k) || inPool(symbol)) return;
      seen.add(k);
      out.push({ symbol, asset, name, label });
    };
    board?.sections.forEach((s) =>
      s.items.forEach((i) => {
        if (i.symbol.toLowerCase().includes(ql) || i.name.toLowerCase().includes(ql))
          push(i.symbol, i.asset, i.name, `${i.name} · ${s.label}`);
      }),
    );
    hits?.results.forEach((h) =>
      push(h.symbol, h.asset, h.symbol, `${h.asset}${h.sector ? ` · ${h.sector}` : ""}`),
    );
    return out.slice(0, 8);
  }, [q, board, hits, pool]); // eslint-disable-line react-hooks/exhaustive-deps

  const addSuggestion = (s: { symbol: string; asset: Asset; name: string }) => {
    add({ symbol: s.symbol, name: s.name, asset: s.asset });
    setDraft("");
  };

  const onDraftEnter = () => {
    if (suggestions.length) addSuggestion(suggestions[0]);
    else addFree();
  };

  const symbols = data?.symbols ?? [];
  const matrix = data?.correlation ?? [];

  return (
    <div className="flex h-full flex-col">
      {/* Pool builder */}
      <div className="space-y-2 border-b border-term-border p-2">
        <div className="flex flex-wrap items-center gap-1">
          {pool.map((p) => (
            <span
              key={p.symbol}
              className="group inline-flex items-center gap-1 rounded border border-term-border bg-term-sunken px-1.5 py-0.5 text-[11px]"
              title={`${p.name} · ${p.asset}`}
            >
              <span className="font-mono">{tick(p.symbol)}</span>
              <button
                type="button"
                aria-label={`${t("common.remove")} ${p.symbol}`}
                onClick={() => remove(p.symbol)}
                className="text-term-muted opacity-50 transition-opacity hover:text-term-down group-hover:opacity-100"
              >
                ×
              </button>
            </span>
          ))}
          {pool.length === 0 && <span className="text-[11px] text-term-muted">{t("board.poolEmpty")}</span>}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <select
            value=""
            onChange={(e) => {
              if (e.target.value) addCatalog(e.target.value);
              e.target.value = "";
            }}
            aria-label={t("board.addFromBoard")}
            className="focus-ring max-w-[160px] rounded border border-term-border bg-term-sunken px-2 py-1 text-xs focus:border-term-accent"
          >
            <option value="">{t("board.addFromBoard")}</option>
            {board?.sections.map((s) => (
              <optgroup key={s.key} label={s.label}>
                {s.items
                  .filter((i) => !inPool(i.symbol))
                  .map((i) => (
                    <option key={i.symbol} value={i.symbol}>
                      {i.name}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>

          <div className="relative">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onDraftEnter();
                else if (e.key === "Escape") setDraft("");
              }}
              placeholder={t("board.addSymbol")}
              aria-label={t("board.addSymbol")}
              autoComplete="off"
              className="focus-ring w-[140px] rounded border border-term-border bg-term-sunken px-2 py-1 text-xs placeholder:text-term-muted focus:border-term-accent"
            />
            {q && suggestions.length > 0 && (
              <div className="absolute left-0 top-7 z-40 max-h-56 min-w-[220px] overflow-auto rounded border border-term-border bg-term-elev shadow-elev-2">
                {suggestions.map((sug) => (
                  <button
                    key={sug.symbol}
                    type="button"
                    onClick={() => addSuggestion(sug)}
                    className="focus-ring flex w-full items-center justify-between gap-3 px-2 py-1.5 text-xs hover:bg-term-border/50"
                  >
                    <span className="font-mono font-semibold">{sug.symbol}</span>
                    <span className="truncate text-term-muted">{sug.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="ml-auto flex items-center gap-1">
            {WINDOWS.map((w) => (
              <TextButton key={w.days} active={days === w.days} onClick={() => setDays(w.days)}>
                {w.label}
              </TextButton>
            ))}
            <TextButton onClick={reset} title={t("board.resetPool")}>
              {t("common.reset")}
            </TextButton>
          </div>
        </div>
      </div>

      {/* Heatmap */}
      <div className="min-h-0 flex-1 overflow-auto p-2">
        {pool.length < 2 && <EmptyState title={t("board.corrEmpty")} />}
        {pool.length >= 2 && isFetching && !data && <SkeletonRows rows={5} />}
        {error && <ErrorState message={(error as Error).message} />}
        {pool.length >= 2 && data && symbols.length > 0 && (
          <table className="border-collapse font-mono text-[10px]">
            <thead>
              <tr>
                <th className="sticky left-0 bg-term-panel" />
                {symbols.map((s) => (
                  <th key={s} className="px-1 pb-1 text-term-muted" title={nameBy.get(s.toUpperCase()) ?? s}>
                    {tick(s)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {symbols.map((row, i) => (
                <tr key={row}>
                  <td
                    className="sticky left-0 bg-term-panel pr-2 text-right text-term-muted"
                    title={nameBy.get(row.toUpperCase()) ?? row}
                  >
                    {tick(row)}
                  </td>
                  {symbols.map((col, j) => {
                    const v = matrix[i]?.[j] ?? null;
                    return (
                      <td
                        key={col}
                        title={`${tick(row)} × ${tick(col)}: ${v == null ? "—" : v.toFixed(2)}`}
                        className={cx(
                          "h-7 w-10 border border-term-border/40 text-center",
                          i === j && "font-semibold text-term-text",
                        )}
                        style={{ backgroundColor: corrColor(v) }}
                      >
                        {v == null ? "" : v.toFixed(2)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {pool.length >= 2 && data && symbols.length === 0 && <EmptyState title={t("board.corrNoData")} />}
      </div>
    </div>
  );
}
