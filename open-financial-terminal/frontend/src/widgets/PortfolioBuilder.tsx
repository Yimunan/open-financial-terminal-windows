import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AllocateResult, Portfolio, PortfolioAllocation } from "../api/types";
import { cx, fmtPct } from "../lib/format";
import type { WidgetParams, WidgetProps } from "../workspace/widgetRegistry";
import { EmptyState } from "../components/States";
import { useWorkspace } from "../state/workspace";
import { IconButton, WidgetShell, useWidgetSymbol } from "./shell";

const inputCls =
  "focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs focus:border-term-accent";
const selCls = "focus-ring w-full rounded border border-term-border bg-term-sunken px-1 py-0.5 text-xs text-term-muted";

// Draft rows hold weights as PERCENT (what the user types); the API uses fractions.
type Row = { symbol: string; asset: string; weight: number };
type Draft = { name: string; description: string; mode: string; tags: string; notes: string; rows: Row[] };

const EMPTY: Draft = { name: "", description: "", mode: "long_short", tags: "", notes: "", rows: [{ symbol: "", asset: "equity", weight: 0 }] };

const toRows = (a: PortfolioAllocation[]): Row[] =>
  a.length ? a.map((x) => ({ symbol: x.symbol, asset: x.asset, weight: +(x.weight * 100).toFixed(4) })) : [{ symbol: "", asset: "equity", weight: 0 }];
const toAllocations = (rows: Row[]): PortfolioAllocation[] =>
  rows.filter((r) => r.symbol.trim()).map((r) => ({ symbol: r.symbol.trim().toUpperCase(), asset: r.asset, weight: r.weight / 100 }));

// Render the draft's manual rows as portfolio/sandboxed starter code (weights are percent → fractions).
const draftToCode = (rows: Row[]): string => {
  const entries = rows
    .filter((r) => r.symbol.trim())
    .map((r) => `    "${r.symbol.trim().toUpperCase()}": ${(r.weight / 100).toFixed(4)},`);
  return `# Portfolio weights generated from the manual book — edit freely.\nresult = {\n${entries.join("\n")}\n}\n`;
};

export default function PortfolioBuilder(props: WidgetProps) {
  const { channel, setChannel } = useWidgetSymbol(props);
  const qc = useQueryClient();
  const openWidget = useWorkspace((s) => s.openWidget);
  const { data } = useQuery({ queryKey: ["portfolios"], queryFn: () => api.registryPortfolios("") });
  const [q, setQ] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [capital, setCapital] = useState(1_000_000);
  const [alloc, setAlloc] = useState<AllocateResult | null>(null);

  // The pipeline spec streamed in by the Agent Workflow's `portfolio` node: show once as a config
  // banner (the universe/factor/mode/capital the pipeline chose), then clear. Non-destructive — it
  // does NOT overwrite an in-progress draft (consume-once, mirrors incomingResult).
  const [incomingSpec, setIncomingSpec] = useState<WidgetParams["incomingSpec"] | null>(null);
  useEffect(() => {
    const s = props.params.incomingSpec;
    if (s) {
      setIncomingSpec(s);
      props.api.updateParameters({ incomingSpec: undefined });
    }
  }, [props.params.incomingSpec, props.api]);

  const portfolios = useMemo(() => {
    const all = data?.portfolios ?? [];
    if (!q) return all;
    const s = q.toLowerCase();
    return all.filter((p) =>
      [p.name, p.description, p.mode, p.notes, (p.tags ?? []).join(" "), (p.allocations ?? []).map((a) => a.symbol).join(" ")]
        .some((x) => (x ?? "").toLowerCase().includes(s)),
    );
  }, [data, q]);

  const save = useMutation({
    mutationFn: (d: Draft) =>
      api.savePortfolio(d.name.trim(), {
        description: d.description, mode: d.mode, notes: d.notes,
        allocations: toAllocations(d.rows),
        tags: d.tags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolios"] }),
  });
  const del = useMutation({
    mutationFn: (name: string) => api.deletePortfolio(name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["portfolios"] }); setDraft(null); setAlloc(null); },
  });
  const normalize = useMutation({
    mutationFn: (d: Draft) => api.normalizePortfolio({ allocations: toAllocations(d.rows), mode: d.mode }),
    onSuccess: (r) => draft && setDraft({ ...draft, rows: toRows(r.allocations) }),
  });
  const allocate = useMutation({
    mutationFn: (d: Draft) => api.allocatePortfolio({ allocations: toAllocations(d.rows), capital }),
    onSuccess: (r) => setAlloc(r),
  });
  // Export the draft into the Portfolio module as a NEW book: weights are valued into shares at
  // current prices server-side, seeded as holdings, and the new book is made active. Then open the
  // Portfolio widget so the result is visible.
  const exportToModule = useMutation({
    mutationFn: (d: Draft) =>
      api.createBookFromAllocations(d.name.trim() || "Imported book", toAllocations(d.rows), capital),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolio-books"] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
      openWidget("portfolio", { channel });
    },
  });

  const open = (p: Portfolio) => {
    setAlloc(null);
    setDraft({
      name: p.name, description: p.description ?? "", mode: p.mode ?? "long_short",
      tags: (p.tags ?? []).join(", "), notes: p.notes ?? "", rows: toRows(p.allocations ?? []),
    });
  };

  // Live exposure of the draft (percent-space), so the user sees gross/net as they type.
  const exp = useMemo(() => {
    const rows = draft?.rows ?? [];
    const w = rows.map((r) => r.weight).filter((x) => !Number.isNaN(x));
    return {
      gross: w.reduce((s, x) => s + Math.abs(x), 0),
      net: w.reduce((s, x) => s + x, 0),
      nLong: w.filter((x) => x > 0).length,
      nShort: w.filter((x) => x < 0).length,
    };
  }, [draft]);

  const setRow = (i: number, patch: Partial<Row>) =>
    draft && setDraft({ ...draft, rows: draft.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const addRow = () => draft && setDraft({ ...draft, rows: [...draft.rows, { symbol: "", asset: "equity", weight: 0 }] });
  const delRow = (i: number) => draft && setDraft({ ...draft, rows: draft.rows.filter((_, j) => j !== i) });

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">Portfolios</span>
          <button
            onClick={() => { setAlloc(null); setDraft({ ...EMPTY, rows: [{ symbol: "", asset: "equity", weight: 0 }] }); }}
            className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10"
          >
            ＋ New portfolio
          </button>
          <button
            onClick={() => openWidget("sandbox", {
              initialMode: "portfolio", initialTrust: "sandboxed",
              ...(draft ? { initialCode: draftToCode(draft.rows), initialMeta: { mode: draft.mode } } : {}),
            })}
            className="rounded border border-term-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-muted hover:border-term-accent hover:text-term-accent"
            title="Generate weights with code in the Sandbox (carries the current book if one is open)"
          >
            Open in Sandbox
          </button>
        </>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        {/* ── spec from the Agent Workflow's portfolio node (read-only) ── */}
        {incomingSpec && (
          <div className="flex shrink-0 items-center gap-2 border-b border-term-border bg-term-accent/5 px-2 py-1.5">
            <span className="shrink-0 text-[9px] font-semibold uppercase tracking-wider text-term-accent">From workflow</span>
            <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-term-text">
              {`${incomingSpec.factor ?? "?"} · ${incomingSpec.mode ?? "?"} · top ${incomingSpec.top_pct ?? "?"} on ${incomingSpec.universe ?? "?"} · $${(incomingSpec.initial ?? 0).toLocaleString()}`}
            </span>
            <button onClick={() => setIncomingSpec(null)} className="shrink-0 text-term-muted hover:text-term-text" title="Dismiss">×</button>
          </div>
        )}
        <div className="flex min-h-0 flex-1">
          {/* list */}
          <div className="flex w-56 shrink-0 flex-col border-r border-term-border">
          <div className="p-1.5">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search portfolios…" aria-label="Search portfolios" className={inputCls} />
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-2">
            {portfolios.length === 0 ? (
              <EmptyState
                title={q ? "No matches." : "No portfolios yet"}
                hint={q ? undefined : "A portfolio is a saved weight + allocation list (symbol → target weight)."}
              />
            ) : (
              portfolios.map((p) => (
                <button
                  key={p.name}
                  onClick={() => open(p)}
                  className={cx(
                    "mb-1 block w-full rounded border border-term-border/60 px-2 py-1.5 text-left hover:border-term-accent",
                    draft?.name === p.name && "border-term-accent bg-term-accent/10",
                  )}
                >
                  <div className="font-mono text-xs font-semibold">{p.name}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[9px] text-term-muted">
                    <span className="rounded bg-term-border/50 px-1">{p.mode}</span>
                    <span>{(p.allocations ?? []).length} names</span>
                    {(p.tags ?? []).map((tg) => <span key={tg} className="rounded bg-term-accent/15 px-1 text-term-accent">#{tg}</span>)}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {/* editor */}
        <div className="min-w-0 flex-1 overflow-auto p-3">
          {draft ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <Fld label="Name"><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={inputCls} placeholder="My target book" aria-label="Name" /></Fld>
                <Fld label="Mode">
                  <select value={draft.mode} onChange={(e) => setDraft({ ...draft, mode: e.target.value })} className={selCls} aria-label="Mode">
                    {["long_short", "long_only"].map((m) => <option key={m}>{m}</option>)}
                  </select>
                </Fld>
              </div>
              <Fld label="Description"><input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className={inputCls} aria-label="Description" /></Fld>

              {/* allocation list */}
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-[9px] uppercase tracking-wider text-term-muted">Allocations (weight %)</span>
                  <button onClick={addRow} className="text-[10px] text-term-accent hover:underline">＋ Add row</button>
                </div>
                <div className="space-y-1">
                  {draft.rows.map((r, i) => (
                    <div key={i} className="flex items-center gap-1">
                      <input
                        value={r.symbol}
                        onChange={(e) => setRow(i, { symbol: e.target.value.toUpperCase() })}
                        placeholder="AAPL"
                        aria-label="Symbol"
                        className={cx(inputCls, "flex-1")}
                      />
                      <select value={r.asset} onChange={(e) => setRow(i, { asset: e.target.value })} className={cx(selCls, "w-20 shrink-0")} aria-label="Asset">
                        {["equity", "crypto"].map((a) => <option key={a}>{a}</option>)}
                      </select>
                      <input
                        type="number" step="any" value={Number.isNaN(r.weight) ? "" : r.weight}
                        onChange={(e) => setRow(i, { weight: parseFloat(e.target.value) })}
                        aria-label="Weight %"
                        className={cx(inputCls, "w-20 shrink-0 text-right")}
                      />
                      <span className="w-3 text-[10px] text-term-muted">%</span>
                      <IconButton label="Remove row" danger onClick={() => delRow(i)} className="shrink-0 px-1" title="Remove">×</IconButton>
                    </div>
                  ))}
                </div>
              </div>

              {/* live exposure + normalize */}
              <div className="flex items-center justify-between rounded border border-term-border bg-term-bg/40 px-2 py-1 font-mono text-[11px]">
                <span className="text-term-muted">Gross <span className="text-term-text">{exp.gross.toFixed(1)}%</span></span>
                <span className="text-term-muted">Net <span className={cx(Math.abs(exp.net) < 0.05 ? "text-term-muted" : exp.net >= 0 ? "text-term-up" : "text-term-down")}>{exp.net.toFixed(1)}%</span></span>
                <span className="text-term-muted">L/S <span className="text-term-text">{exp.nLong}/{exp.nShort}</span></span>
                <button
                  onClick={() => normalize.mutate(draft)}
                  disabled={normalize.isPending}
                  className="rounded border border-term-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                  title="Scale to gross 100% (dollar-neutral for long_short)"
                >
                  {normalize.isPending ? "…" : "Normalize"}
                </button>
              </div>

              <Fld label="Tags (comma-separated)"><input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} className={inputCls} placeholder="momentum, core" aria-label="Tags (comma-separated)" /></Fld>
              <Fld label="Notes"><textarea value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} rows={2} className={cx(inputCls, "resize-none leading-snug")} aria-label="Notes" /></Fld>

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <button
                  onClick={() => draft.name.trim() && save.mutate(draft)}
                  disabled={save.isPending || !draft.name.trim()}
                  className="rounded border border-term-accent px-3 py-1 text-xs uppercase text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
                >
                  {save.isPending ? "Saving…" : "Save"}
                </button>
                <button onClick={() => del.mutate(draft.name.trim())} className="rounded border border-term-down px-3 py-1 text-xs uppercase text-term-down hover:bg-term-down/10">Delete</button>
                <div className="ml-auto flex items-center gap-1">
                  <span className="text-[9px] uppercase tracking-wider text-term-muted">Capital</span>
                  <input
                    type="number" step="any" value={capital}
                    onChange={(e) => setCapital(Math.max(0, parseFloat(e.target.value) || 0))}
                    aria-label="Capital"
                    className={cx(inputCls, "w-28 text-right")}
                  />
                  <button
                    onClick={() => allocate.mutate(draft)}
                    disabled={allocate.isPending}
                    className="rounded border border-term-accent px-2 py-1 text-xs uppercase text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
                    title="Value weights into notionals + shares at current prices"
                  >
                    {allocate.isPending ? "…" : "Allocate ▸"}
                  </button>
                  <button
                    onClick={() => exportToModule.mutate(draft)}
                    disabled={exportToModule.isPending || toAllocations(draft.rows).length === 0}
                    className="rounded border border-term-border px-2 py-1 text-xs uppercase tracking-wide text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                    title="Create a new book in the Portfolio module, valued into shares at current prices"
                  >
                    {exportToModule.isPending ? "Exporting…" : "Export to Portfolio ▸"}
                  </button>
                </div>
              </div>
              {(save.error || allocate.error || normalize.error || exportToModule.error) && (
                <div className="text-[10px] text-term-down">{((save.error || allocate.error || normalize.error || exportToModule.error) as Error).message}</div>
              )}

              {alloc && <AllocTable a={alloc} />}
            </div>
          ) : (
            <EmptyState
              title="Select a portfolio to view/edit, or ＋ New portfolio."
              hint="A portfolio saves a weight + allocation list — symbol → target weight — that you can normalize (gross 100%, dollar-neutral for long/short) and value into share counts against a capital base."
            />
          )}
        </div>
        </div>
      </div>
    </WidgetShell>
  );
}

function AllocTable({ a }: { a: AllocateResult }) {
  return (
    <div className="mt-2 space-y-1">
      <div className="flex flex-wrap gap-3 text-[10px] text-term-muted">
        <span>Capital <span className="text-term-text">${a.capital.toLocaleString()}</span></span>
        <span>Gross <span className="text-term-text">${a.gross_notional.toLocaleString()}</span></span>
        <span>Net <span className="text-term-text">${a.net_notional.toLocaleString()}</span></span>
        <span>Priced <span className="text-term-text">{a.priced}/{a.rows.length}</span></span>
      </div>
      <div className="overflow-auto rounded border border-term-border">
        <table className="w-full font-mono text-[11px]">
          <thead className="text-[9px] uppercase tracking-wider text-term-muted">
            <tr className="border-b border-term-border">
              <th className="px-2 py-1 text-left">Symbol</th>
              <th className="px-2 py-1 text-right">Weight</th>
              <th className="px-2 py-1 text-right">Price</th>
              <th className="px-2 py-1 text-right">Notional</th>
              <th className="px-2 py-1 text-right">Shares</th>
            </tr>
          </thead>
          <tbody>
            {a.rows.map((r) => (
              <tr key={r.symbol} className="border-b border-term-border/30">
                <td className="px-2 py-0.5">
                  <span className={cx(r.side === "long" ? "text-term-up" : r.side === "short" ? "text-term-down" : "text-term-muted")}>
                    {r.side === "short" ? "▽" : r.side === "long" ? "△" : "·"}
                  </span>{" "}
                  {r.symbol}
                </td>
                <td className="px-2 py-0.5 text-right">{fmtPct(r.weight * 100)}</td>
                <td className="px-2 py-0.5 text-right">{r.price == null ? "—" : r.price.toLocaleString()}</td>
                <td className="px-2 py-0.5 text-right">{r.notional.toLocaleString()}</td>
                <td className="px-2 py-0.5 text-right">{r.shares == null ? "—" : `${r.shares_int} (${r.shares.toFixed(2)})`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Fld({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-[9px] uppercase tracking-wider text-term-muted">{label}</span>
      {children}
    </label>
  );
}
