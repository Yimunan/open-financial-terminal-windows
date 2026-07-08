import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { RegModel, RepoModel } from "../api/types";
import { cx } from "../lib/format";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { EmptyState } from "../components/States";
import RegistrySummary from "../components/RegistrySummary";
import { IconButton, WidgetShell, useWidgetSymbol } from "./shell";

type Tab = "bundles" | "trained";
// Must match the backend ModelStage enum (draft|backtest|paper|production|archived) —
// promote 400s on anything else.
const STAGES = ["draft", "backtest", "paper", "production", "archived"] as const;
const STAGE_CLS: Record<string, string> = {
  production: "bg-term-up/20 text-term-up",
  paper: "bg-term-accent/20 text-term-accent",
  backtest: "bg-term-accent/10 text-term-accent",
  draft: "bg-term-border/50 text-term-muted",
  archived: "bg-term-down/15 text-term-down",
};

const inputCls =
  "focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs focus:border-term-accent";
const selCls = "focus-ring w-full rounded border border-term-border bg-term-sunken px-1 py-0.5 text-xs text-term-muted";

type Draft = {
  name: string; description: string; factor: string; strategy: string;
  universe: string; mode: string; tags: string; notes: string;
};
const EMPTY: Draft = { name: "", description: "", factor: "", strategy: "", universe: "dow30", mode: "long_short", tags: "", notes: "" };

export default function ModelRepository(props: WidgetProps) {
  const { channel, setChannel } = useWidgetSymbol(props);
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("bundles");
  const { data } = useQuery({ queryKey: ["reg-models"], queryFn: () => api.registryModels("") });
  const { data: factors } = useQuery({ queryKey: ["reg-factors"], queryFn: api.registryFactors });
  const { data: strategies } = useQuery({ queryKey: ["reg-strategies"], queryFn: api.registryStrategies });
  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.universes });

  const [q, setQ] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);

  const models = useMemo(() => {
    const all = data?.models ?? [];
    if (!q) return all;
    const s = q.toLowerCase();
    return all.filter((m) =>
      [m.name, m.description, m.factor, m.strategy, m.universe, m.notes, (m.tags ?? []).join(" ")]
        .some((x) => (x ?? "").toLowerCase().includes(s)),
    );
  }, [data, q]);

  const save = useMutation({
    mutationFn: (d: Draft) =>
      api.saveModel(d.name.trim(), {
        description: d.description, factor: d.factor, strategy: d.strategy, universe: d.universe,
        mode: d.mode, notes: d.notes, tags: d.tags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reg-models"] }),
  });
  const del = useMutation({
    mutationFn: (name: string) => api.deleteModel(name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reg-models"] }); setDraft(null); },
  });

  const open = (m: RegModel) =>
    setDraft({
      name: m.name, description: m.description ?? "", factor: m.factor ?? "", strategy: m.strategy ?? "",
      universe: m.universe ?? "dow30", mode: m.mode ?? "long_short", tags: (m.tags ?? []).join(", "), notes: m.notes ?? "",
    });

  const factorOpts = [...(factors?.builtin ?? []), ...(factors?.custom ?? [])].map((f) => f.name);
  const stratOpts = [...(strategies?.builtin ?? []), ...(strategies?.custom ?? [])].map((s) => s.name);

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <div className="flex items-center gap-0.5 rounded border border-term-border p-0.5">
            {(["bundles", "trained"] as Tab[]).map((tb) => (
              <button
                key={tb}
                onClick={() => setTab(tb)}
                className={cx(
                  "rounded px-2 py-0.5 text-[10px] uppercase tracking-wide",
                  tab === tb ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:text-term-text",
                )}
              >
                {tb === "bundles" ? "Bundles" : "Trained models"}
              </button>
            ))}
          </div>
          {tab === "bundles" && (
            <button
              onClick={() => setDraft({ ...EMPTY })}
              className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10"
            >
              ＋ New model
            </button>
          )}
        </>
      }
    >
      {tab === "trained" ? (
        <TrainedModels />
      ) : (
      <div className="flex h-full flex-col">
        <RegistrySummary kind="models" />
        {/* prominent search box */}
        <div className="shrink-0 border-b border-term-border p-2">
          <div className="flex items-center gap-2 rounded border border-term-border bg-term-sunken px-2 focus-within:border-term-accent">
            <span className="text-term-muted">🔍</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search models — name, factor, strategy, tags…"
              aria-label="Search models"
              className="flex-1 bg-transparent py-1.5 font-mono text-xs outline-none"
            />
            {q && (
              <IconButton label="Clear search" onClick={() => setQ("")}>
                ×
              </IconButton>
            )}
          </div>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* list */}
          <div className="min-h-0 w-1/2 overflow-auto border-r border-term-border p-1.5">
            {models.length === 0 ? (
              <EmptyState
                title={q ? "No models match." : "No models yet"}
                hint={q ? undefined : "Register one with ＋ New model. A model bundles a factor + strategy + universe + params for reuse."}
              />
            ) : (
              models.map((m) => (
                <button
                  key={m.name}
                  onClick={() => open(m)}
                  className={cx(
                    "mb-1 block w-full rounded border border-term-border/60 px-2 py-1.5 text-left hover:border-term-accent",
                    draft?.name === m.name && "border-term-accent bg-term-accent/10",
                  )}
                >
                  <div className="font-mono text-xs font-semibold">{m.name}</div>
                  {m.description && <div className="truncate text-[10px] text-term-muted">{m.description}</div>}
                  <div className="mt-0.5 flex flex-wrap gap-1 text-[9px] text-term-muted">
                    {m.factor && <span className="rounded bg-term-border/50 px-1">factor: {m.factor}</span>}
                    {m.strategy && <span className="rounded bg-term-border/50 px-1">strat: {m.strategy}</span>}
                    {m.universe && <span className="rounded bg-term-border/50 px-1">{m.universe}</span>}
                    {(m.tags ?? []).map((tg) => <span key={tg} className="rounded bg-term-accent/15 px-1 text-term-accent">#{tg}</span>)}
                  </div>
                </button>
              ))
            )}
          </div>

          {/* editor */}
          <div className="min-h-0 w-1/2 overflow-auto p-3">
            {draft ? (
              <div className="space-y-2">
                <Fld label="Name"><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={inputCls} placeholder="Dow Momentum v2" aria-label="Name" /></Fld>
                <Fld label="Description"><input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className={inputCls} aria-label="Description" /></Fld>
                <div className="grid grid-cols-2 gap-2">
                  <Fld label="Factor">
                    <select value={draft.factor} onChange={(e) => setDraft({ ...draft, factor: e.target.value })} className={selCls} aria-label="Factor">
                      <option value="">—</option>
                      {factorOpts.map((f) => <option key={f}>{f}</option>)}
                    </select>
                  </Fld>
                  <Fld label="Strategy">
                    <select value={draft.strategy} onChange={(e) => setDraft({ ...draft, strategy: e.target.value })} className={selCls} aria-label="Strategy">
                      <option value="">—</option>
                      {stratOpts.map((s) => <option key={s}>{s}</option>)}
                    </select>
                  </Fld>
                  <Fld label="Universe">
                    <select value={draft.universe} onChange={(e) => setDraft({ ...draft, universe: e.target.value })} className={selCls} aria-label="Universe">
                      {(universes?.universes ?? [draft.universe]).map((u) => <option key={u}>{u}</option>)}
                    </select>
                  </Fld>
                  <Fld label="Mode">
                    <select value={draft.mode} onChange={(e) => setDraft({ ...draft, mode: e.target.value })} className={selCls} aria-label="Mode">
                      {["long_only", "long_short"].map((m) => <option key={m}>{m}</option>)}
                    </select>
                  </Fld>
                </div>
                <Fld label="Tags (comma-separated)"><input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} className={inputCls} placeholder="momentum, dow, v2" aria-label="Tags (comma-separated)" /></Fld>
                <Fld label="Notes"><textarea value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} rows={4} className={cx(inputCls, "resize-none leading-snug")} aria-label="Notes" /></Fld>
                <div className="flex items-center gap-2 pt-1">
                  <button
                    onClick={() => draft.name.trim() && save.mutate(draft)}
                    disabled={save.isPending || !draft.name.trim()}
                    className="rounded border border-term-accent px-3 py-1 text-xs uppercase text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
                  >
                    {save.isPending ? "Saving…" : "Save"}
                  </button>
                  <button onClick={() => del.mutate(draft.name.trim())} className="rounded border border-term-down px-3 py-1 text-xs uppercase text-term-down hover:bg-term-down/10">Delete</button>
                </div>
              </div>
            ) : (
              <EmptyState title="Select a model to view/edit, or ＋ New model." hint="Search filters by name, factor, strategy, universe, tags, and notes." />
            )}
          </div>
        </div>
      </div>
      )}
    </WidgetShell>
  );
}

function TrainedModels() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["repo-models"], queryFn: api.repoModels });
  const [selName, setSelName] = useState<string | null>(null);
  const promote = useMutation({
    mutationFn: ({ name, version, stage }: { name: string; version: number; stage: string }) =>
      api.promoteRepoModel(name, { version, stage }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["repo-models"] }),
  });

  const models = data?.models ?? [];
  const sel: RepoModel | undefined = models.find((m) => m.name === selName) ?? models[0];

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 truncate border-b border-term-border p-2 text-[10px] text-term-muted" title={data?.root}>
        {isLoading ? "Loading…" : <>Repository: <span className="font-mono">{data?.root}</span>{data && !data.exists && " · (empty / not found — set in Settings)"}</>}
      </div>
      <div className="flex min-h-0 flex-1">
        {/* names */}
        <div className="min-h-0 w-1/3 overflow-auto border-r border-term-border p-1.5">
          {models.length === 0 ? (
            <EmptyState
              title="No trained models"
              hint="Point the models directory at a qhfi ModelRepository root in Settings → Linked qhfi directories, or train one via qhfi."
            />
          ) : (
            models.map((m) => (
              <button
                key={m.name}
                onClick={() => setSelName(m.name)}
                className={cx(
                  "mb-1 block w-full rounded border border-term-border/60 px-2 py-1.5 text-left hover:border-term-accent",
                  sel?.name === m.name && "border-term-accent bg-term-accent/10",
                )}
              >
                <div className="font-mono text-xs font-semibold">{m.name}</div>
                <div className="mt-0.5 flex items-center gap-1 text-[9px] text-term-muted">
                  <span>{m.versions.length} version(s)</span>
                  {m.production_version != null && (
                    <span className={cx("rounded px-1", STAGE_CLS.production)}>prod v{m.production_version}</span>
                  )}
                </div>
              </button>
            ))
          )}
        </div>

        {/* version history */}
        <div className="min-h-0 flex-1 overflow-auto p-2">
          {sel ? (
            <div className="space-y-2">
              <div className="font-mono text-sm font-semibold">{sel.name}</div>
              {sel.versions.map((v) => (
                <div key={v.version} className="rounded border border-term-border/60 p-2 font-mono text-[11px]">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-semibold">v{v.version}</span>
                    <span className={cx("rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wide", STAGE_CLS[v.stage] ?? STAGE_CLS.dev)}>
                      {v.stage}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1 text-[9px] text-term-muted">
                    <span className="rounded bg-term-border/50 px-1">{v.framework}</span>
                    {v.domain && <span className="rounded bg-term-border/50 px-1">{v.domain}</span>}
                    {v.asset_class && <span className="rounded bg-term-border/50 px-1">{v.asset_class}</span>}
                    {v.created_at && <span className="rounded bg-term-border/50 px-1">{v.created_at.slice(0, 10)}</span>}
                  </div>
                  {Object.keys(v.metrics).length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-2 text-[10px]">
                      {Object.entries(v.metrics).map(([k, val]) => (
                        <span key={k} className="text-term-muted">{k}=<span className="text-term-text">{typeof val === "number" ? val.toFixed(4) : String(val)}</span></span>
                      ))}
                    </div>
                  )}
                  <div className="mt-1.5 flex items-center gap-1">
                    <span className="text-[9px] uppercase tracking-wider text-term-muted">Promote</span>
                    {STAGES.filter((st) => st !== v.stage).map((st) => (
                      <button
                        key={st}
                        onClick={() => promote.mutate({ name: sel.name, version: v.version, stage: st })}
                        disabled={promote.isPending}
                        className="rounded border border-term-border px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                      >
                        {st}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {promote.error && <div className="text-[10px] text-term-down">{(promote.error as Error).message}</div>}
            </div>
          ) : (
            <EmptyState title="Select a model to see its version history and promote stages." />
          )}
        </div>
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
