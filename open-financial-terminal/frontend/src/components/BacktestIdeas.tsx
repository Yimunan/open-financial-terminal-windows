/** "Ideas" sub-window for the Backtest module: scans the repo's built/saved factors + models and
 * lists designed, runnable backtest proposals. The user can focus the design on specific
 * factors/models, regenerate a fresh set, and run any proposal in one click (factor proposals go
 * through the agent; model proposals run the saved bundle). Content/runs are owned by the widget —
 * this panel just fetches proposals and calls `onRun`. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BacktestProposal } from "../api/types";
import { cx } from "../lib/format";
import { useSettings } from "../state/settings";
import { EmptyState } from "./States";

function chipCls(active: boolean, dim = false): string {
  return cx(
    "focus-ring rounded border px-1.5 py-px text-[10px] transition-colors",
    active
      ? "border-term-accent bg-term-accent/15 text-term-accent"
      : cx("border-term-border/60 hover:border-term-accent/60", dim ? "text-term-muted/70" : "text-term-muted"),
  );
}

export default function BacktestIdeas({
  universe,
  factor,
  onRun,
  running,
  runError,
}: {
  universe?: string;
  factor?: string;
  onRun: (p: BacktestProposal) => void;
  running?: boolean;
  runError?: string | null;
}) {
  const showIcons = useSettings((s) => s.showIcons);
  const [selFactors, setSelFactors] = useState<Set<string>>(new Set());
  const [selModels, setSelModels] = useState<Set<string>>(new Set());
  // Each design is an LLM call, so the query is fetch-once: it runs on first mount and ONLY refetches
  // when `applied` changes (an explicit Regenerate/Rescan). The active universe/factor are snapshotted
  // into `applied` on Regenerate — never in the live key — so completing a run doesn't auto-refetch.
  const [applied, setApplied] = useState<{
    universe?: string;
    factor?: string;
    factors: string[];
    models: string[];
    nonce: number;
  }>({ factors: [], models: [], nonce: 0 });

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ["bt-proposals", applied.universe ?? "", applied.factor ?? "", applied.factors.join(","), applied.models.join(","), applied.nonce],
    queryFn: () =>
      api.backtestProposals({ n: 8, universe: applied.universe, factor: applied.factor, factors: applied.factors, models: applied.models }),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const inv = data?.inventory;
  const proposals = data?.proposals ?? [];
  const selCount = selFactors.size + selModels.size;

  const toggle = (set: Set<string>, setSet: (s: Set<string>) => void, name: string) => {
    const next = new Set(set);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSet(next);
  };
  // Regenerate: fresh ideas for the current picks. Rescan: re-read the repo from scratch (clear the
  // focus, pick up newly built/saved factors & models). Both force a fresh backend scan (the nonce
  // makes a new query key, and the endpoint re-reads the registries on every call).
  const regenerate = () =>
    setApplied({ universe, factor, factors: [...selFactors], models: [...selModels], nonce: applied.nonce + 1 });
  const rescan = () => {
    setSelFactors(new Set());
    setSelModels(new Set());
    setApplied({ universe: undefined, factor: undefined, factors: [], models: [], nonce: applied.nonce + 1 });
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-term-panel">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-term-border px-2 py-1">
        <span className="truncate text-[11px] uppercase tracking-wider text-term-accent">Ideas</span>
        <div className="flex shrink-0 items-center gap-2">
          <button
            onClick={rescan}
            disabled={isFetching}
            title="Re-read the repository (pick up newly built/saved factors & models) and start fresh"
            className="focus-ring rounded text-[10px] uppercase tracking-wide text-term-muted hover:text-term-accent disabled:opacity-50"
          >
            {showIcons ? "⟲ " : ""}Rescan
          </button>
          <button
            onClick={regenerate}
            disabled={isFetching}
            title="Design a fresh set for the current focus"
            className="focus-ring rounded text-[10px] uppercase tracking-wide text-term-muted hover:text-term-accent disabled:opacity-50"
          >
            {isFetching ? "…" : `${showIcons ? "↻ " : ""}Regenerate`}
          </button>
        </div>
      </div>

      {/* Focus selector — pick specific factors / models to test (empty = anything) */}
      {inv && (inv.factors.length > 0 || inv.models.length > 0) && (
        <div className="no-scrollbar max-h-[88px] shrink-0 overflow-auto border-b border-term-border px-2 py-1.5">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-term-muted">
              Focus{selCount ? ` · ${selCount}` : " · all"}
            </span>
            {selCount > 0 && (
              <button
                onClick={() => {
                  setSelFactors(new Set());
                  setSelModels(new Set());
                }}
                className="focus-ring text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text"
              >
                Clear
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1">
            {inv.factors.map((f) => (
              <button key={`f-${f.name}`} onClick={() => toggle(selFactors, setSelFactors, f.name)} title={f.label} className={chipCls(selFactors.has(f.name))}>
                {f.name}
              </button>
            ))}
            {inv.models.map((m) => (
              <button key={`m-${m.name}`} onClick={() => toggle(selModels, setSelModels, m.name)} title={`${m.kind} model`} className={chipCls(selModels.has(m.name), m.kind === "trained")}>
                {m.name}
              </button>
            ))}
          </div>
          {selCount > 0 && (
            <div className="mt-1 text-[9px] text-term-muted/70">Hit Regenerate to design backtests for your picks.</div>
          )}
        </div>
      )}

      {runError && (
        <div className="shrink-0 border-b border-term-down/40 bg-term-down/10 px-2 py-1 text-[10px] text-term-down">{runError}</div>
      )}

      <div className="no-scrollbar min-h-0 flex-1 space-y-1.5 overflow-auto px-2 py-2">
        {isError ? (
          <EmptyState icon="⚠" title="Couldn't load ideas" hint={String((error as Error)?.message ?? "")} />
        ) : isFetching && proposals.length === 0 ? (
          <EmptyState icon="⚙" title="Designing backtests…" hint="Scanning factors & models." />
        ) : proposals.length === 0 ? (
          <EmptyState icon="💡" title="No ideas" hint="Clear the focus or regenerate." />
        ) : (
          proposals.map((p) => (
            <div key={p.id} className="rounded border border-term-border/70 bg-term-bg/30 p-2">
              <div className="flex items-start justify-between gap-2">
                <span className="text-[11px] font-medium text-term-text">{p.label}</span>
                <button
                  onClick={() => onRun(p)}
                  disabled={running}
                  title="Run this backtest"
                  className="focus-ring shrink-0 rounded border border-term-accent px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-term-accent transition-colors hover:bg-term-accent/15 disabled:opacity-40"
                >
                  {showIcons ? "▸ Run" : "Run"}
                </button>
              </div>
              {p.rationale && <div className="mt-0.5 text-[10px] leading-snug text-term-muted">{p.rationale}</div>}
              <div className="mt-1 flex flex-wrap items-center gap-1 text-[9px] uppercase tracking-wide text-term-muted">
                <span className={cx("rounded px-1 py-px", p.kind === "model" ? "bg-term-accent/15 text-term-accent" : "bg-term-border/40")}>{p.kind}</span>
                <span className="rounded bg-term-border/30 px-1 py-px normal-case">{p.source}</span>
                {p.generated === "template" && <span className="text-term-muted/60">template</span>}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
