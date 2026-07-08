import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset, CompositionResponse, PortfolioRisk, RiskResponse } from "../api/types";
import FlashCell from "../components/FlashCell";
import { CompositionChart, type CompositionBand } from "../lib/compositionChart";
import { DonutChart, type DonutSegment } from "../lib/donutChart";
import { chartColors } from "../lib/chartTheme";
import { cx, fmtCompact, fmtPct, fmtPrice, upDownClass } from "../lib/format";
import { useT, type I18nKey } from "../lib/i18n";
import { themeColor, usePalette } from "../state/settings";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { IconButton, SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState, ErrorState } from "../components/States";
import SymbolSearch from "../components/SymbolSearch";
import AttributionView from "./risk/AttributionView";
import { simBook, useActiveAccountId } from "../state/accounts";

/** Distinct per-component colors from the theme's chart tokens (cycles if there are more names). */
const BAND_TOKENS = [
  "--term-accent", "--term-series-1", "--term-series-2", "--term-series-3",
  "--term-series-4", "--term-series-5", "--term-series-6", "--term-up", "--term-down",
];
function bandColor(i: number): string {
  return themeColor(BAND_TOKENS[i % BAND_TOKENS.length]);
}

function corrColor(v: number | null): string {
  if (v == null) return "transparent";
  // -1 → down-red, 0 → transparent, +1 → up-green
  const a = Math.min(Math.abs(v), 1) * 0.55;
  return v >= 0 ? `rgb(var(--term-up) / ${a})` : `rgb(var(--term-down) / ${a})`;
}

function Stat({ label, value, cls }: { label: ReactNode; value: ReactNode; cls?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-wider text-term-muted">{label}</span>
      <span className={cx("font-mono text-xs", cls)}>{value}</span>
    </div>
  );
}

/** Portfolio-level aggregate risk over the weighted book (vs the per-symbol RiskPanel below). */
function AggregateRiskCard({ pr }: { pr: PortfolioRisk }) {
  const t = useT();
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-term-border bg-term-sunken/40 p-3 sm:grid-cols-3 lg:grid-cols-4">
      <Stat label={t("pf.totalValue")} value={`$${fmtCompact(pr.total_value)}`} />
      <Stat label={t("bt.vol")} value={fmtPct(pr.ann_vol, false)} />
      <Stat
        label={t("pf.var")}
        value={`${fmtPct(pr.var_95, false)} · $${fmtCompact(pr.var_95_usd)}`}
        cls="text-term-down"
      />
      <Stat label={t("pf.cvar")} value={fmtPct(pr.cvar_95, false)} cls="text-term-down" />
      <Stat label={t("bt.maxdd")} value={fmtPct(pr.max_drawdown, false)} cls="text-term-down" />
      <Stat label="Sharpe" value={pr.sharpe.toFixed(2)} />
      <Stat label="Sortino" value={pr.sortino.toFixed(2)} />
      <Stat
        label={`${t("pf.beta")} · ${pr.benchmark}`}
        value={pr.beta == null ? "—" : pr.beta.toFixed(2)}
      />
      <Stat label={t("pf.gross")} value={fmtPct(pr.gross * 100, false)} />
      <Stat label={t("pf.net")} value={fmtPct(pr.net * 100)} cls={upDownClass(pr.net)} />
      <Stat label={t("pf.conc")} value={pr.concentration.toFixed(2)} />
    </div>
  );
}

function RiskPanel({ risk }: { risk: RiskResponse }) {
  const t = useT();
  return (
    <div className="space-y-3 p-2">
      <table className="border-collapse font-mono text-[10px]">
        <thead>
          <tr>
            <th />
            {risk.symbols.map((s) => (
              <th key={s} className="px-1 pb-1 text-term-muted">{s.split("/")[0]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {risk.symbols.map((row, i) => (
            <tr key={row}>
              <td className="pr-1 text-term-muted">{row.split("/")[0]}</td>
              {risk.symbols.map((col, j) => (
                <td
                  key={col}
                  title={`${row}×${col}: ${risk.correlation[i][j]?.toFixed(2) ?? "—"}`}
                  className="h-6 w-9 border border-term-border/40 text-center"
                  style={{ backgroundColor: corrColor(risk.correlation[i][j]) }}
                >
                  {risk.correlation[i][j]?.toFixed(2) ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
            <th className="px-2 py-1 text-left font-medium">{t("common.symbol")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("bt.vol")}</th>
            <th className="px-2 py-1 text-right font-medium">Sharpe</th>
            <th className="px-2 py-1 text-right font-medium">Sortino</th>
            <th className="px-2 py-1 text-right font-medium">{t("bt.maxdd")}</th>
            <th className="px-2 py-1 text-right font-medium">CAGR</th>
          </tr>
        </thead>
        <tbody>
          {risk.metrics.map((m) => (
            <tr key={m.symbol} className="border-b border-term-border/30">
              {/* backend risk metrics arrive pre-scaled to percent (services/portfolio.py) */}
              <td className="px-2 py-1 font-mono font-semibold">{m.symbol}</td>
              <td className="px-2 py-1 text-right font-mono">{fmtPct(m.ann_vol, false)}</td>
              <td className="px-2 py-1 text-right font-mono">{m.sharpe.toFixed(2)}</td>
              <td className="px-2 py-1 text-right font-mono">{m.sortino.toFixed(2)}</td>
              <td className="px-2 py-1 text-right font-mono text-term-down">{fmtPct(m.max_drawdown, false)}</td>
              <td className={cx("px-2 py-1 text-right font-mono", upDownClass(m.cagr))}>{fmtPct(m.cagr)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 100%-stacked-area canvas of composition over time. */
function CompositionCanvas({ times, bands }: { times: string[]; bands: CompositionBand[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const engineRef = useRef<CompositionChart | null>(null);
  const palette = usePalette();
  useEffect(() => {
    if (!ref.current) return;
    const engine = new CompositionChart(ref.current, chartColors());
    engineRef.current = engine;
    return () => { engineRef.current = null; engine.destroy(); };
  }, [palette]);
  useEffect(() => { engineRef.current?.setData(times, bands); }, [times, bands]);
  return <div ref={ref} className="min-h-0 flex-1" />;
}

/** Donut (cirque) of the current holdings allocation by market value. */
function DonutCanvas({ segments }: { segments: DonutSegment[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const engineRef = useRef<DonutChart | null>(null);
  const palette = usePalette();
  useEffect(() => {
    if (!ref.current) return;
    const engine = new DonutChart(ref.current, chartColors());
    engineRef.current = engine;
    return () => { engineRef.current = null; engine.destroy(); };
  }, [palette]);
  useEffect(() => { engineRef.current?.setData(segments); }, [segments]);
  return <div ref={ref} className="h-full w-full" />;
}

const PERIODS: { key: string; years: number }[] = [
  { key: "3M", years: 0 }, { key: "6M", years: 0 }, { key: "1Y", years: 1 },
  { key: "3Y", years: 3 }, { key: "Max", years: 10 },
];
const PERIOD_DAYS: Record<string, number> = { "3M": 91, "6M": 182, "1Y": 365, "3Y": 1096, Max: 3650 };

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

/** Composition tab — user picks a window (presets or explicit From/To) and sees how each holding's
 *  share of the book drifts over it, plus a start→end weight-drift table. */
function CompositionView() {
  const t = useT();
  const [preset, setPreset] = useState<string>("1Y");
  const [start, setStart] = useState<string>(() => isoDaysAgo(PERIOD_DAYS["1Y"]));
  const [end, setEnd] = useState<string>(() => new Date().toISOString().slice(0, 10));

  const choosePreset = (key: string) => {
    setPreset(key);
    setStart(isoDaysAgo(PERIOD_DAYS[key]));
    setEnd(new Date().toISOString().slice(0, 10));
  };

  const q = useQuery({
    queryKey: ["pf-composition", start, end],
    queryFn: () => api.portfolioComposition({ start, end }),
  });
  const data = q.data as CompositionResponse | undefined;

  const bands = useMemo<CompositionBand[]>(
    () => (data?.series ?? []).map((s, i) => ({ label: s.symbol, color: bandColor(i), weights: s.weights })),
    [data],
  );

  const dateInput =
    "focus-ring rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-[10px] text-term-text focus:border-term-accent";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-term-border px-2 py-1">
        <div className="flex items-center gap-0.5">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              onClick={() => choosePreset(p.key)}
              className={cx(
                "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
                preset === p.key ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
              )}
            >
              {p.key}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <label className="text-[9px] uppercase tracking-wider text-term-muted">{t("pf.from")}</label>
          <input type="date" value={start} max={end} aria-label={t("pf.from")} className={dateInput}
            onChange={(e) => { setStart(e.target.value); setPreset(""); }} />
          <label className="text-[9px] uppercase tracking-wider text-term-muted">{t("pf.to")}</label>
          <input type="date" value={end} min={start} aria-label={t("pf.to")} className={dateInput}
            onChange={(e) => { setEnd(e.target.value); setPreset(""); }} />
        </div>
      </div>

      {q.isLoading && <SkeletonRows />}
      {q.error && <ErrorState message={(q.error as Error).message} />}
      {!q.isLoading && !q.error && data?.insufficient && (
        <EmptyState title={data.error || t("pf.needOne")} />
      )}
      {!q.isLoading && !q.error && !data?.insufficient && data?.times && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="px-2 pt-1 text-[11px] font-medium uppercase tracking-wider text-term-text/80">
            {t("pf.compTitle")}
            <span className="ml-2 font-mono text-[10px] normal-case text-term-muted">{data.start} → {data.end}</span>
          </div>
          <div className="flex min-h-[160px] flex-[3] flex-col px-1">
            <CompositionCanvas times={data.times} bands={bands} />
          </div>
          <p className="px-2 text-[10px] text-term-muted">{t("pf.compHint")}</p>

          <div className="mt-1 max-h-[40%] overflow-auto border-t border-term-border">
            <div className="px-2 py-1 text-[10px] uppercase tracking-wider text-term-muted">{t("pf.compDrift")}</div>
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
                  <th className="px-2 py-1 text-left font-medium">{t("common.symbol")}</th>
                  <th className="px-2 py-1 text-right font-medium">{t("pf.startW")}</th>
                  <th className="px-2 py-1 text-right font-medium">{t("pf.endW")}</th>
                  <th className="px-2 py-1 text-right font-medium">{t("pf.delta")}</th>
                  <th className="px-2 py-1 text-right font-medium">{t("pf.value")}</th>
                </tr>
              </thead>
              <tbody>
                {(data.series ?? []).map((s, i) => {
                  const d = s.end_weight - s.start_weight;
                  return (
                    <tr key={s.symbol} className="border-b border-term-border/30">
                      <td className="px-2 py-1 font-mono font-semibold">
                        <span className="mr-1.5 inline-block h-2 w-2 rounded-sm align-middle" style={{ backgroundColor: bandColor(i) }} />
                        {s.symbol}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-term-muted">{s.start_weight.toFixed(1)}%</td>
                      <td className="px-2 py-1 text-right font-mono">{s.end_weight.toFixed(1)}%</td>
                      <td className={cx("px-2 py-1 text-right font-mono", upDownClass(d))}>{(d >= 0 ? "+" : "") + d.toFixed(1)}pp</td>
                      <td className="px-2 py-1 text-right font-mono">${fmtCompact(s.end_value)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default function PortfolioWidget(props: WidgetProps) {
  const { channel, setChannel } = useWidgetSymbol(props);
  const t = useT();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"holdings" | "composition" | "risk" | "attribution">("holdings");
  const [riskSource, setRiskSource] = useState<"holdings" | "paper">("holdings");
  const [form, setForm] = useState({ symbol: "", asset: "equity" as Asset, quantity: "", cost_basis: "" });

  // which sim account the risk/attribution tabs analyze (defaults to the global active account)
  const activeAccountId = useActiveAccountId();
  const [riskAccountId, setRiskAccountId] = useState(activeAccountId);
  const accountsQ = useQuery({ queryKey: ["paper-accounts"], queryFn: api.paperAccounts });
  const simAccounts = accountsQ.data?.accounts ?? [];

  const { data, isLoading } = useQuery({ queryKey: ["holdings"], queryFn: api.holdings });
  const paperQ = useQuery({
    queryKey: ["paper-account", simBook(riskAccountId)],
    queryFn: () => api.paperAccount(simBook(riskAccountId)),
    enabled: (tab === "risk" || tab === "attribution") && riskSource === "paper",
  });

  const put = useMutation({
    mutationFn: () =>
      api.putHolding({
        symbol: form.symbol.toUpperCase(),
        asset: form.asset,
        quantity: Number(form.quantity),
        cost_basis: Number(form.cost_basis),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["holdings"] });
      setForm({ symbol: "", asset: "equity", quantity: "", cost_basis: "" });
    },
  });
  const del = useMutation({
    mutationFn: (symbol: string) => api.deleteHolding(symbol),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["holdings"] }),
  });

  // ── named portfolio books (multi-book holdings) ──────────────────────────────
  const booksQ = useQuery({ queryKey: ["portfolio-books"], queryFn: api.portfolioBooks });
  const books = booksQ.data?.books ?? [];
  const activeBook = booksQ.data?.active ?? 1;
  const [renaming, setRenaming] = useState(false);
  const [bookName, setBookName] = useState("");
  const [bookFilter, setBookFilter] = useState("");
  const [bookSelectorOpen, setBookSelectorOpen] = useState(false);
  // Books shown in the search dropdown — filtered by the query (the tabs always show every book).
  const shownBooks = bookFilter.trim()
    ? books.filter((b) => b.name.toLowerCase().includes(bookFilter.trim().toLowerCase()))
    : books;
  // Wheel-scroll the book tab strip horizontally (vertical wheel → horizontal), like Committees.
  const bookTabsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = bookTabsRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth) return;
      const delta = Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
      if (!delta) return;
      e.preventDefault();
      el.scrollLeft += delta;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);
  type Books = NonNullable<typeof booksQ.data>;
  // Switching the active book re-points the server-side holdings/risk/attribution views, so
  // invalidate everything that reads them.
  const onBooks = (r: Books) => {
    qc.setQueryData(["portfolio-books"], r);
    qc.invalidateQueries({ queryKey: ["holdings"] });
    qc.invalidateQueries({ queryKey: ["pf-composition"] });
    qc.invalidateQueries({ queryKey: ["risk-corr"] });
    qc.invalidateQueries({ queryKey: ["risk-agg"] });
    qc.invalidateQueries({ queryKey: ["risk-attribution"] });
    qc.invalidateQueries({ queryKey: ["risk-return-attribution"] });
    qc.invalidateQueries({ queryKey: ["risk-brinson"] });
  };
  const switchBook = useMutation({ mutationFn: (id: number) => api.setActivePortfolioBook(id), onSuccess: onBooks });
  const createBook = useMutation({ mutationFn: (name: string) => api.createPortfolioBook(name), onSuccess: onBooks });
  const renameBook = useMutation({
    mutationFn: (p: { id: number; name: string }) => api.renamePortfolioBook(p.id, p.name),
    onSuccess: (r) => { onBooks(r); setRenaming(false); },
  });
  const deleteBook = useMutation({ mutationFn: (id: number) => api.deletePortfolioBook(id), onSuccess: onBooks });

  // Active position book for the risk tab, driven by the source toggle.
  const positions =
    riskSource === "paper"
      ? (paperQ.data?.positions ?? []).map((p) => ({ symbol: p.symbol, asset: p.asset, quantity: p.quantity }))
      : (data?.holdings ?? []).map((h) => ({ symbol: h.symbol, asset: h.asset, quantity: h.quantity }));
  const positionsKey = positions.map((p) => `${p.symbol}:${p.quantity}`).join(",");
  const riskEnabled = tab === "risk" && positions.length >= 2;
  const srcLoading = riskSource === "paper" ? paperQ.isLoading : isLoading;

  const corrQ = useQuery({
    queryKey: ["risk-corr", riskSource, positionsKey],
    queryFn: () => api.risk(positions.map((p) => ({ symbol: p.symbol, asset: p.asset }))),
    enabled: riskEnabled,
  });
  const aggQ = useQuery({
    queryKey: ["risk-agg", riskSource, positionsKey],
    queryFn: () => api.portfolioRisk(positions),
    enabled: riskEnabled,
  });

  const riskLoading = srcLoading || corrQ.isLoading || aggQ.isLoading;
  const riskError = (corrQ.error || aggQ.error) as Error | null;

  // Current-allocation donut: one segment per holding with a known market value.
  const donutSegments = useMemo<DonutSegment[]>(
    () =>
      (data?.holdings ?? [])
        .filter((h) => h.value != null && h.value > 0)
        .map((h, i) => ({ label: h.symbol, value: h.value as number, color: bandColor(i) })),
    [data],
  );

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge="eod"
      toolbar={
        <>
          {(["holdings", "composition", "risk"] as const).map((tabKey) => (
            <button
              key={tabKey}
              onClick={() => setTab(tabKey)}
              className={cx(
                "rounded px-2 py-0.5 text-[10px] uppercase tracking-wide",
                tab === tabKey ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
              )}
            >
              {t(`pf.${tabKey}` as I18nKey)}
            </button>
          ))}
          <button
            onClick={() => setTab("attribution")}
            className={cx(
              "rounded px-2 py-0.5 text-[10px] uppercase tracking-wide",
              tab === "attribution" ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
            )}
          >
            Attribution
          </button>
          {data && (
            <span className={cx("ml-auto font-mono text-xs", upDownClass(data.total_pnl))}>
              ${fmtCompact(data.total_value)} · P&L {fmtPct(data.total_pnl_pct)}
            </span>
          )}
        </>
      }
    >
      {/* Portfolio books — Committees-style: wheel-scrollable tabs + a searchable jump dropdown + a
          new-book button. Click a tab to switch; double-click to rename; ✕ removes. */}
      <div className="flex items-center gap-1 border-b border-term-border bg-term-elev px-1.5 py-1">
        {/* scrollable tabs (mouse wheel scrolls horizontally) */}
        <div
          ref={bookTabsRef}
          className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:thin]"
        >
          {books.map((b) => {
            const active = b.id === activeBook;
            if (renaming && active) {
              return (
                <input
                  key={b.id}
                  autoFocus
                  value={bookName}
                  onChange={(e) => setBookName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && bookName.trim()) renameBook.mutate({ id: activeBook, name: bookName.trim() });
                    if (e.key === "Escape") setRenaming(false);
                  }}
                  onBlur={() => setRenaming(false)}
                  aria-label={t("pf.bookName")}
                  className="focus-ring w-36 shrink-0 rounded border border-term-accent bg-term-sunken px-1.5 py-0.5 font-mono text-[11px] text-term-text"
                />
              );
            }
            return (
              <span
                key={b.id}
                className={cx(
                  "group flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs",
                  active ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:text-term-text",
                )}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => !active && switchBook.mutate(b.id)}
                  onDoubleClick={() => { setBookName(b.name); setRenaming(true); }}
                  className="focus-ring max-w-[140px] truncate font-mono"
                  title={active ? t("pf.renameBook") : b.name}
                >
                  {b.name}
                </button>
                {books.length > 1 && (
                  <IconButton
                    label={t("pf.deleteBook")}
                    danger
                    className="opacity-40 group-hover:opacity-100"
                    onClick={() => { if (window.confirm(t("pf.deleteBookConfirm"))) deleteBook.mutate(b.id); }}
                  >
                    ✕
                  </IconButton>
                )}
              </span>
            );
          })}
        </div>

        {/* searchable dropdown of all books (jump to any, even when tabs overflow) */}
        <div className="relative shrink-0">
          <button
            type="button"
            onClick={() => { setBookSelectorOpen((v) => !v); setBookFilter(""); }}
            aria-haspopup="listbox"
            aria-expanded={bookSelectorOpen}
            title={t("pf.portfolio")}
            className="focus-ring rounded border border-term-border px-1.5 py-1 text-xs text-term-muted hover:text-term-text"
          >
            Search ▾
          </button>
          {bookSelectorOpen && (
            <div
              role="listbox"
              className="absolute right-0 z-20 mt-1 w-60 rounded border border-term-border bg-term-elev p-1 shadow-elev-2"
            >
              <input
                autoFocus
                value={bookFilter}
                onChange={(e) => setBookFilter(e.target.value)}
                placeholder="Search portfolios…"
                aria-label="Search portfolios"
                className="focus-ring mb-1 w-full rounded border border-term-border bg-term-sunken px-2 py-1 text-xs"
              />
              <div className="max-h-56 overflow-auto">
                {shownBooks.map((b) => (
                  <div
                    key={b.id}
                    className={cx(
                      "group flex items-center gap-1 rounded px-1",
                      b.id === activeBook ? "bg-term-accent/15" : "hover:bg-term-border/50",
                    )}
                  >
                    <button
                      type="button"
                      role="option"
                      aria-selected={b.id === activeBook}
                      onClick={() => { if (b.id !== activeBook) switchBook.mutate(b.id); setBookSelectorOpen(false); }}
                      className={cx(
                        "focus-ring min-w-0 flex-1 truncate px-1 py-1 text-left text-xs",
                        b.id === activeBook ? "text-term-accent" : "text-term-text",
                      )}
                      title={b.name}
                    >
                      {b.name}
                    </button>
                    {books.length > 1 && (
                      <IconButton
                        label={t("pf.deleteBook")}
                        danger
                        className="opacity-40 group-hover:opacity-100"
                        onClick={() => { if (window.confirm(t("pf.deleteBookConfirm"))) deleteBook.mutate(b.id); }}
                      >
                        ✕
                      </IconButton>
                    )}
                  </div>
                ))}
                {shownBooks.length === 0 && (
                  <div className="px-2 py-2 text-[11px] text-term-muted">No portfolios match.</div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* new book */}
        <button
          type="button"
          onClick={() => createBook.mutate(`Portfolio ${books.length + 1}`)}
          title={t("pf.newBook")}
          aria-label={t("pf.newBook")}
          className="focus-ring shrink-0 rounded border border-dashed border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent"
        >
          ＋
        </button>
      </div>
      {tab === "holdings" && isLoading && <SkeletonRows />}
      {tab === "holdings" && data && (
        <div>
          {donutSegments.length > 0 && (
            <div className="h-44 border-b border-term-border">
              <DonutCanvas segments={donutSegments} />
            </div>
          )}
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
                <th className="px-2 py-1 text-left font-medium">{t("common.symbol")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("pf.qty")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("pf.cost")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("common.last")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("pf.value")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("pf.pnl")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.holdings.map((h) => (
                <tr key={h.symbol} className="border-b border-term-border/30">
                  <td className="px-2 py-1 font-mono font-semibold">{h.symbol}</td>
                  <td className="px-2 py-1 text-right font-mono">{h.quantity}</td>
                  <td className="px-2 py-1 text-right font-mono">{fmtPrice(h.cost_basis)}</td>
                  <td className="px-2 py-1 text-right font-mono">
                    <FlashCell value={h.price}>{fmtPrice(h.price)}</FlashCell>
                  </td>
                  <td className="px-2 py-1 text-right font-mono">{h.value != null ? `$${fmtCompact(h.value)}` : "—"}</td>
                  <td className={cx("px-2 py-1 text-right font-mono", upDownClass(h.pnl_pct))}>{fmtPct(h.pnl_pct)}</td>
                  <td className="px-1 py-1 text-right">
                    <IconButton label={`Remove ${h.symbol}`} danger onClick={() => del.mutate(h.symbol)}>×</IconButton>
                  </td>
                </tr>
              ))}
              {data.holdings.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-4 text-center text-term-muted">{t("pf.empty")}</td>
                </tr>
              )}
            </tbody>
          </table>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (form.symbol && form.quantity && form.cost_basis) put.mutate();
            }}
            className="flex items-center gap-1.5 border-t border-term-border p-2"
          >
            <SymbolSearch
              value={form.symbol}
              onChange={(v) => setForm({ ...form, symbol: v })}
              onSelect={(h) => setForm({ ...form, symbol: h.symbol, asset: h.asset })}
              placeholder={t("common.symbol")}
              ariaLabel={t("common.symbol")}
              placement="top"
              inputClassName="focus-ring w-24 rounded border border-term-border bg-term-sunken px-2 py-0.5 font-mono text-xs uppercase focus:border-term-accent"
            />
            <select value={form.asset} onChange={(e) => setForm({ ...form, asset: e.target.value as Asset })} aria-label="Asset" className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-muted">
              <option value="equity">equity</option>
              <option value="crypto">crypto</option>
            </select>
            <input value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} placeholder={t("pf.qty")} aria-label={t("pf.qty")} type="number" step="any" className="focus-ring w-20 rounded border border-term-border bg-term-sunken px-2 py-0.5 font-mono text-xs focus:border-term-accent" />
            <input value={form.cost_basis} onChange={(e) => setForm({ ...form, cost_basis: e.target.value })} placeholder={t("pf.costBasis")} aria-label={t("pf.costBasis")} type="number" step="any" className="focus-ring w-24 rounded border border-term-border bg-term-sunken px-2 py-0.5 font-mono text-xs focus:border-term-accent" />
            <button type="submit" disabled={put.isPending} className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10 disabled:opacity-50">
              {t("common.save")}
            </button>
          </form>
        </div>
      )}
      {tab === "composition" && <CompositionView />}
      {tab === "attribution" && (
        <AttributionView
          source={riskSource}
          onSourceChange={setRiskSource}
          account={riskAccountId}
          onAccountChange={setRiskAccountId}
          accounts={simAccounts}
        />
      )}
      {tab === "risk" && (
        <div>
          <div className="flex items-center gap-2 border-b border-term-border px-2 py-1">
            <span className="text-[10px] uppercase tracking-wider text-term-muted">{t("pf.portfolioRisk")}</span>
            <div className="ml-auto flex items-center gap-1">
              {(["holdings", "paper"] as const).map((src) => (
                <button
                  key={src}
                  onClick={() => setRiskSource(src)}
                  className={cx(
                    "rounded px-2 py-0.5 text-[10px] uppercase tracking-wide",
                    riskSource === src ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
                  )}
                >
                  {t(`pf.src.${src}` as I18nKey)}
                </button>
              ))}
              {riskSource === "paper" && simAccounts.length > 0 && (
                <select
                  value={riskAccountId}
                  onChange={(e) => setRiskAccountId(Number(e.target.value))}
                  aria-label="Sim account"
                  className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-text"
                  title="Which sim account to analyze"
                >
                  {simAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
          {riskLoading && <SkeletonRows />}
          {riskError && <ErrorState message={riskError.message} />}
          {!riskLoading && !riskError && (
            <>
              {aggQ.data && !aggQ.data.insufficient && <AggregateRiskCard pr={aggQ.data} />}
              {corrQ.data && <RiskPanel risk={corrQ.data} />}
              {!riskEnabled && <EmptyState title={t("pf.needTwo")} />}
            </>
          )}
        </div>
      )}
    </WidgetShell>
  );
}
