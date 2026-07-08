import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Algo, AlgoIn, AlgoKind, AlgoRun, Asset, PaperSimAccount, Timeframe } from "../api/types";
import { simBook, useActiveAccountId } from "../state/accounts";
import { cx, fmtCompact, fmtPrice } from "../lib/format";
import { subscribeStream, topics, equityTopics } from "../lib/wsClient";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { TextButton, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState } from "../components/States";

const POLL_MS = 5_000;

/** A fresh template algo seeded from the widget's linked symbol. */
function newTemplateAlgo(symbol: string, asset: Asset): AlgoIn {
  return {
    name: `${symbol} signal`,
    kind: "template",
    symbol,
    asset,
    timeframe: "1d",
    strategy: "sma_cross",
    params: {},
    direction: "both",
    universe: "dow30",
    factor: "momentum",
    mode: "long_short",
    top_pct: 0.2,
    size_pct: 1.0,
    cadence: { kind: "daily", seconds: 300 },
    risk: {},
    armed: false,
    book: "primary",
  };
}

/** Display name for an algo's sim book ('primary' → null, no badge). 'sim'/'sim:<id>' → account name. */
function bookAccountName(book: string | undefined, accounts: PaperSimAccount[]): string | null {
  if (!book || book === "primary") return null;
  const id = book === "sim" ? 1 : Number(book.split(":")[1]);
  return accounts.find((a) => a.id === id)?.name ?? "sim";
}

const STATUS_TONE: Record<string, string> = {
  ok: "text-term-up",
  preview: "text-term-accent",
  rejected: "text-term-accent",
  killed: "text-term-down",
  error: "text-term-down",
};

function SignalChip({ signal }: { signal: number }) {
  const label = signal > 0 ? "LONG" : signal < 0 ? "SHORT" : "FLAT";
  const tone = signal > 0 ? "text-term-up border-term-up" : signal < 0 ? "text-term-down border-term-down" : "text-term-muted border-term-border";
  return <span className={cx("rounded border px-1.5 py-px text-[9px] font-semibold tracking-wider", tone)}>{label}</span>;
}

export default function AlgoTradingWidget(props: WidgetProps) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const qc = useQueryClient();

  const strategies = useQuery({ queryKey: ["algo-strategies"], queryFn: api.algoStrategies });
  const status = useQuery({ queryKey: ["algo-status"], queryFn: api.algoStatus, refetchInterval: POLL_MS });
  const algos = useQuery({ queryKey: ["algos"], queryFn: api.algos, refetchInterval: POLL_MS });
  const accountsQ = useQuery({ queryKey: ["paper-accounts"], queryFn: api.paperAccounts });
  const accounts = accountsQ.data?.accounts ?? [];
  const activeAccountId = useActiveAccountId();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<AlgoIn | null>(null); // editor open when non-null
  const [editingId, setEditingId] = useState<string | null>(null); // null = creating

  const isSim = (status.data?.broker ?? "sim") === "sim";
  const alpacaActive = status.data?.alpaca_active ?? false; // when true, algos can pick a book
  const paused = status.data?.paused ?? false;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["algos"] });
    qc.invalidateQueries({ queryKey: ["algo-status"] });
  };

  // ── runner controls ──
  const pause = useMutation({ mutationFn: api.algoPause, onSuccess: refresh });
  const resume = useMutation({ mutationFn: api.algoResume, onSuccess: refresh });

  // ── per-algo actions ──
  const arm = useMutation({ mutationFn: (id: string) => api.armAlgo(id), onSuccess: refresh });
  const disarm = useMutation({ mutationFn: (id: string) => api.disarmAlgo(id), onSuccess: refresh });
  const del = useMutation({
    mutationFn: (id: string) => api.deleteAlgo(id),
    onSuccess: (_d, id) => {
      if (selectedId === id) setSelectedId(null);
      refresh();
    },
  });
  const runNow = useMutation({
    mutationFn: (id: string) => api.runAlgo(id),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["algo-runs", id] });
      qc.invalidateQueries({ queryKey: ["paper-account"] });
      refresh();
    },
  });

  // ── editor save ──
  const save = useMutation({
    mutationFn: (body: AlgoIn) => (editingId ? api.updateAlgo(editingId, body) : api.createAlgo(body)),
    onSuccess: (algo: Algo) => {
      setDraft(null);
      setEditingId(null);
      setSelectedId(algo.id);
      refresh();
    },
  });

  // ── live signal preview for the open draft ──
  const preview = useMutation({ mutationFn: (body: AlgoIn) => api.previewAlgo(body) });

  const openCreate = () => {
    setEditingId(null);
    // default a new algo to the globally-active sim account's book
    setDraft({ ...newTemplateAlgo(symbol, asset), book: simBook(activeAccountId) });
    setSelectedId(null);
  };
  const openEdit = (a: Algo) => {
    setEditingId(a.id);
    setDraft({ ...a });
    setSelectedId(a.id);
  };

  const selected = algos.data?.algos.find((a) => a.id === selectedId) ?? null;

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={isSim ? "delayed" : "live"}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">Algo Trading</span>
          <span
            className={cx(
              "rounded border px-1 py-px text-[9px] font-semibold uppercase tracking-wider",
              isSim ? "border-term-muted text-term-muted" : "border-term-up text-term-up",
            )}
          >
            {isSim ? "SIM" : "ALPACA"}
          </span>
          {status.data && (
            <span className="font-mono text-[10px] text-term-muted">
              {status.data.armed_count}/{status.data.algo_count} armed
            </span>
          )}
          <span className="ml-auto flex items-center gap-1.5">
            <span
              className={cx(
                "rounded px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider",
                paused ? "bg-term-down/20 text-term-down" : "bg-term-up/15 text-term-up",
              )}
              title={paused ? "Runner paused — armed algos won't trade" : "Runner live"}
            >
              {paused ? "Paused" : "Live"}
            </span>
            <TextButton
              danger={!paused}
              active={paused}
              onClick={() => (paused ? resume.mutate() : pause.mutate())}
              title="Global kill switch for every algo"
            >
              {paused ? "Resume" : "Pause all"}
            </TextButton>
            <TextButton onClick={openCreate}>+ New</TextButton>
          </span>
        </>
      }
    >
      <div className="flex h-full min-h-0">
        {/* ── algo list ── */}
        <div className="flex w-1/2 min-w-0 flex-col border-r border-term-border">
          <div className="min-h-0 flex-1 overflow-auto">
            {algos.data && algos.data.algos.length === 0 && (
              <EmptyState title="No algos yet" hint="Click + New to schedule a strategy." />
            )}
            {algos.data?.algos.map((a) => (
              <AlgoRow
                key={a.id}
                algo={a}
                accounts={accounts}
                selected={a.id === selectedId}
                busy={arm.isPending || disarm.isPending || runNow.isPending}
                onSelect={() => setSelectedId(a.id)}
                onArm={() => (a.armed ? disarm.mutate(a.id) : arm.mutate(a.id))}
                onRun={() => runNow.mutate(a.id)}
                onEdit={() => openEdit(a)}
                onDelete={() => del.mutate(a.id)}
              />
            ))}
          </div>
        </div>

        {/* ── detail / editor ── */}
        <div className="flex w-1/2 min-w-0 flex-col">
          {draft ? (
            <AlgoEditor
              draft={draft}
              setDraft={setDraft}
              alpacaActive={alpacaActive}
              accounts={accounts}
              strategies={strategies.data}
              onSave={() => save.mutate(draft)}
              onCancel={() => {
                setDraft(null);
                setEditingId(null);
              }}
              onPreview={() => preview.mutate(draft)}
              previewResult={preview.data}
              previewError={preview.error as Error | null}
              saving={save.isPending}
              saveError={save.error as Error | null}
              editing={!!editingId}
            />
          ) : selected ? (
            <AlgoDetail algo={selected} lastRun={runNow.data} />
          ) : (
            <EmptyState title="Select an algo" hint="Or create one to see its live signal and activity." />
          )}
        </div>
      </div>
    </WidgetShell>
  );
}

// ── one algo row in the list ──────────────────────────────────────────────────
function AlgoRow({
  algo, accounts, selected, busy, onSelect, onArm, onRun, onEdit, onDelete,
}: {
  algo: Algo;
  accounts: PaperSimAccount[];
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onArm: () => void;
  onRun: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const target =
    algo.kind === "template"
      ? `${algo.symbol} · ${algo.strategy}`
      : `${algo.universe} · ${algo.factor}`;
  const cadence = algo.cadence?.kind === "interval" ? `${algo.cadence.seconds}s` : "daily";
  // Sim books carry an account; show its name (e.g. "Momentum") on the row badge.
  const simBadge = bookAccountName(algo.book, accounts);
  return (
    <div
      onClick={onSelect}
      className={cx(
        "cursor-pointer border-b border-term-border/40 px-2 py-1.5 hover:bg-term-border/20",
        selected && "bg-term-border/30",
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cx("h-2 w-2 shrink-0 rounded-full", algo.armed ? "bg-term-up animate-pulse" : "bg-term-muted/40")}
          title={algo.armed ? "Armed" : "Disarmed"}
        />
        <span className="min-w-0 flex-1 truncate text-xs font-semibold">{algo.name}</span>
        <span className="rounded border border-term-border px-1 text-[8px] uppercase tracking-wider text-term-muted">
          {algo.kind === "template" ? "single" : "x-sect"}
        </span>
        {simBadge && (
          <span
            className="max-w-[6rem] truncate rounded border border-term-border px-1 text-[8px] uppercase tracking-wider text-term-muted"
            title={`Trades sim account: ${simBadge}`}
          >
            {simBadge}
          </span>
        )}
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-2 font-mono text-[10px] text-term-muted">
        <span className="truncate">{target}</span>
        <span className="shrink-0">{cadence}</span>
      </div>
      <div className="mt-1 flex items-center gap-1">
        <button
          onClick={(e) => { e.stopPropagation(); onArm(); }}
          disabled={busy}
          className={cx(
            "rounded border px-1.5 py-px text-[9px] uppercase tracking-wide disabled:opacity-40",
            algo.armed ? "border-term-down text-term-down hover:bg-term-down/10" : "border-term-up text-term-up hover:bg-term-up/10",
          )}
        >
          {algo.armed ? "Disarm" : "Arm"}
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onRun(); }}
          disabled={busy}
          className="rounded border border-term-accent px-1.5 py-px text-[9px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10 disabled:opacity-40"
          title="Run one cycle now"
        >
          Run
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onEdit(); }}
          className="rounded border border-term-border px-1.5 py-px text-[9px] uppercase tracking-wide text-term-muted hover:text-term-text"
        >
          Edit
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="ml-auto rounded border border-term-border px-1.5 py-px text-[9px] uppercase tracking-wide text-term-muted hover:text-term-down"
        >
          Del
        </button>
        {algo.last_run && (
          <span className="ml-1 shrink-0 font-mono text-[9px] text-term-muted">{algo.last_run.slice(5, 16).replace("T", " ")}</span>
        )}
      </div>
    </div>
  );
}

// ── selected-algo detail: live signal + activity feed ──────────────────────────
function AlgoDetail({ algo, lastRun }: { algo: Algo; lastRun?: AlgoRun }) {
  const runs = useQuery({
    queryKey: ["algo-runs", algo.id],
    queryFn: () => api.algoRuns(algo.id),
    refetchInterval: POLL_MS,
  });

  // best-effort live last price for a single-symbol template
  const [live, setLive] = useState<number | null>(null);
  useEffect(() => {
    setLive(null);
    if (algo.kind !== "template" || !algo.symbol) return;
    const topic = algo.asset === "crypto" ? topics.ticker(algo.symbol) : equityTopics.ticker(algo.symbol);
    return subscribeStream(topic, (frame) => {
      if (frame.type === "ticker" && frame.data) {
        const px = (frame.data as { last?: number; price?: number }).last ?? (frame.data as { price?: number }).price;
        if (typeof px === "number") setLive(px);
      }
    });
  }, [algo.id, algo.kind, algo.symbol, algo.asset]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-term-border px-2 py-1.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold">{algo.name}</span>
          {live != null && (
            <span className="ml-auto font-mono text-[10px] text-term-muted">
              last <span className="text-term-text">{fmtPrice(live)}</span>
            </span>
          )}
        </div>
        <div className="mt-0.5 font-mono text-[10px] text-term-muted">
          {algo.kind === "template"
            ? `${algo.symbol} · ${algo.strategy} · ${algo.direction} · size ${algo.size_pct}`
            : `${algo.universe} · ${algo.factor} · ${algo.mode} · top ${(algo.top_pct ?? 0) * 100}% · gross ${algo.size_pct}`}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        <div className="mb-1 text-[9px] uppercase tracking-wider text-term-muted">Activity</div>
        {runs.data && runs.data.runs.length === 0 && (
          <div className="text-[11px] text-term-muted">No cycles yet. Arm it or click Run.</div>
        )}
        <div className="space-y-1.5">
          {runs.data?.runs.map((r) => (
            <RunCard key={r.id ?? r.ts} run={r} />
          ))}
        </div>
        {lastRun?.status === "error" && lastRun.error && (
          <div className="mt-2 text-[10px] text-term-down">last run error: {lastRun.error}</div>
        )}
      </div>
    </div>
  );
}

function RunCard({ run }: { run: AlgoRun }) {
  const submitted = run.submitted ?? [];
  const orders = run.orders ?? [];
  return (
    <div className="rounded border border-term-border/60 bg-term-sunken/30 px-2 py-1.5">
      <div className="flex items-center gap-2">
        <span className={cx("text-[10px] font-semibold uppercase tracking-wider", STATUS_TONE[run.status] ?? "text-term-muted")}>
          {run.status}
        </span>
        {run.signal?.kind === "template" && typeof run.signal.signal === "number" && (
          <SignalChip signal={run.signal.signal} />
        )}
        {run.signal?.kind === "xsection" && (
          <span className="text-[9px] text-term-muted">{run.signal.n_names} names</span>
        )}
        {run.ts && <span className="ml-auto font-mono text-[9px] text-term-muted">{run.ts.slice(5, 16).replace("T", " ")}</span>}
      </div>
      {run.reason && <div className="mt-0.5 text-[10px] text-term-accent">⚠ {run.reason}</div>}
      {run.error && <div className="mt-0.5 text-[10px] text-term-down">{run.error}</div>}
      {(submitted.length > 0 ? submitted : orders).map((o, i) => (
        <div key={i} className="mt-0.5 flex items-center gap-1 font-mono text-[10px]">
          <span className={o.side === "buy" ? "text-term-up" : "text-term-down"}>{o.side}</span>
          <span>{o.quantity != null ? Number(o.quantity).toFixed(2) : ""}</span>
          <span className="text-term-text">{o.symbol}</span>
          {"error" in o && o.error && <span className="text-term-down">— {o.error}</span>}
        </div>
      ))}
      {run.equity != null && (
        <div className="mt-0.5 text-[9px] text-term-muted">equity ${fmtCompact(run.equity)}</div>
      )}
    </div>
  );
}

// ── create / edit form ──────────────────────────────────────────────────────
function field<K extends keyof AlgoIn>(d: AlgoIn, k: K, v: AlgoIn[K]): AlgoIn {
  return { ...d, [k]: v };
}

function AlgoEditor({
  draft, setDraft, alpacaActive, accounts, strategies, onSave, onCancel, onPreview, previewResult, previewError, saving, saveError, editing,
}: {
  draft: AlgoIn;
  setDraft: (d: AlgoIn) => void;
  alpacaActive: boolean;
  accounts: PaperSimAccount[];
  strategies: import("../api/types").AlgoStrategies | undefined;
  onSave: () => void;
  onCancel: () => void;
  onPreview: () => void;
  previewResult?: AlgoRun;
  previewError: Error | null;
  saving: boolean;
  saveError: Error | null;
  editing: boolean;
}) {
  const tmpl = useMemo(
    () => strategies?.templates.find((t) => t.key === draft.strategy),
    [strategies, draft.strategy],
  );

  const inputCls = "focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs focus:border-term-accent";
  const labelCls = "flex flex-col gap-0.5 text-[9px] uppercase tracking-wider text-term-muted";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-term-border px-2 py-1.5">
        <span className="text-xs font-semibold">{editing ? "Edit algo" : "New algo"}</span>
        <div className="ml-auto flex gap-1">
          <TextButton onClick={onPreview} title="Compute the signal + orders without trading">Preview</TextButton>
          <TextButton onClick={onCancel}>Cancel</TextButton>
          <TextButton active onClick={onSave}>{saving ? "Saving…" : "Save"}</TextButton>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-auto p-2">
        <label className={labelCls}>
          Name
          <input className={inputCls} value={draft.name} onChange={(e) => setDraft(field(draft, "name", e.target.value))} />
        </label>

        {/* kind */}
        <div className="flex items-center gap-px rounded border border-term-border">
          {(["template", "xsection"] as AlgoKind[]).map((k) => (
            <button
              key={k}
              onClick={() => setDraft(field(draft, "kind", k))}
              className={cx(
                "flex-1 px-2 py-0.5 text-[10px] font-semibold uppercase",
                draft.kind === k ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:text-term-text",
              )}
            >
              {k === "template" ? "Single-symbol" : "Cross-sectional"}
            </button>
          ))}
        </div>

        {draft.kind === "template" ? (
          <>
            <div className="grid grid-cols-2 gap-2">
              <label className={labelCls}>
                Symbol
                <input className={cx(inputCls, "uppercase")} value={draft.symbol ?? ""} onChange={(e) => setDraft(field(draft, "symbol", e.target.value.toUpperCase()))} />
              </label>
              <label className={labelCls}>
                Asset
                <select className={inputCls} value={draft.asset} onChange={(e) => setDraft(field(draft, "asset", e.target.value as Asset))}>
                  <option value="equity">equity</option>
                  <option value="crypto">crypto</option>
                </select>
              </label>
              <label className={labelCls}>
                Strategy
                <select className={inputCls} value={draft.strategy} onChange={(e) => setDraft(field(draft, "strategy", e.target.value))}>
                  {strategies?.templates.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                </select>
              </label>
              <label className={labelCls}>
                Timeframe
                <select className={inputCls} value={draft.timeframe} onChange={(e) => setDraft(field(draft, "timeframe", e.target.value as Timeframe))}>
                  {(["1d", "1h", "15m", "5m", "1m"] as Timeframe[]).map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
            </div>
            {/* dynamic template params */}
            {tmpl && tmpl.params.length > 0 && (
              <div className="grid grid-cols-3 gap-2">
                {tmpl.params.map((p) => (
                  <label key={p.key} className={labelCls}>
                    {p.label}
                    <input
                      type="number"
                      step="any"
                      className={inputCls}
                      value={draft.params?.[p.key] ?? p.default}
                      onChange={(e) => setDraft(field(draft, "params", { ...draft.params, [p.key]: Number(e.target.value) }))}
                    />
                  </label>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <label className={labelCls}>
                Direction
                <select className={inputCls} value={draft.direction} onChange={(e) => setDraft(field(draft, "direction", e.target.value as AlgoIn["direction"]))}>
                  <option value="both">both</option>
                  <option value="long_only">long only</option>
                  <option value="short_only">short only</option>
                </select>
              </label>
              <label className={labelCls}>
                Position size (× equity)
                <input type="number" step="any" min={0} className={inputCls} value={draft.size_pct} onChange={(e) => setDraft(field(draft, "size_pct", Number(e.target.value)))} />
              </label>
            </div>
          </>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <label className={labelCls}>
              Universe
              <select className={inputCls} value={draft.universe} onChange={(e) => setDraft(field(draft, "universe", e.target.value))}>
                {strategies?.universes.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </label>
            <label className={labelCls}>
              Factor
              <select className={inputCls} value={draft.factor} onChange={(e) => setDraft(field(draft, "factor", e.target.value))}>
                {strategies?.factors.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
              </select>
            </label>
            <label className={labelCls}>
              Mode
              <select className={inputCls} value={draft.mode} onChange={(e) => setDraft(field(draft, "mode", e.target.value as AlgoIn["mode"]))}>
                <option value="long_short">long / short</option>
                <option value="long_only">long only</option>
              </select>
            </label>
            <label className={labelCls}>
              Top fraction
              <input type="number" step="any" min={0.05} max={0.5} className={inputCls} value={draft.top_pct} onChange={(e) => setDraft(field(draft, "top_pct", Number(e.target.value)))} />
            </label>
            <label className={labelCls}>
              Gross (× equity)
              <input type="number" step="any" min={0} className={inputCls} value={draft.size_pct} onChange={(e) => setDraft(field(draft, "size_pct", Number(e.target.value)))} />
            </label>
          </div>
        )}

        {/* cadence */}
        <div className="grid grid-cols-2 gap-2">
          <label className={labelCls}>
            Cadence
            <select
              className={inputCls}
              value={draft.cadence?.kind ?? "daily"}
              onChange={(e) => setDraft(field(draft, "cadence", { ...draft.cadence, kind: e.target.value as "daily" | "interval" }))}
            >
              <option value="daily">daily (after close)</option>
              <option value="interval">interval</option>
            </select>
          </label>
          {draft.cadence?.kind === "interval" ? (
            <label className={labelCls}>
              Every (seconds)
              <input
                type="number"
                min={10}
                className={inputCls}
                value={draft.cadence?.seconds ?? 300}
                onChange={(e) => setDraft(field(draft, "cadence", { ...draft.cadence, kind: "interval", seconds: Number(e.target.value) }))}
              />
            </label>
          ) : (
            <label className={labelCls}>
              After (local time)
              <input
                type="time"
                className={inputCls}
                value={draft.cadence?.at ?? "16:10"}
                onChange={(e) => setDraft(field(draft, "cadence", { ...draft.cadence, kind: "daily", at: e.target.value }))}
                title="Fire only after this time (default 16:10 US market close, so the daily bar is final)"
              />
            </label>
          )}
        </div>

        {/* risk overrides */}
        <div className="grid grid-cols-2 gap-2">
          <label className={labelCls}>
            Max position (× equity)
            <input
              type="number"
              step="any"
              className={inputCls}
              placeholder="default"
              value={draft.risk?.max_position ?? ""}
              onChange={(e) => setDraft(field(draft, "risk", { ...draft.risk, max_position: e.target.value === "" ? null : Number(e.target.value) }))}
            />
          </label>
          <label className={labelCls}>
            Drawdown kill
            <input
              type="number"
              step="any"
              className={inputCls}
              placeholder="0.20"
              value={draft.risk?.max_drawdown_kill ?? ""}
              onChange={(e) => setDraft(field(draft, "risk", { ...draft.risk, max_drawdown_kill: e.target.value === "" ? null : Number(e.target.value) }))}
            />
          </label>
        </div>

        {/* book — which paper account (or the Alpaca primary) this algo trades */}
        <label className={labelCls}>
          Book
          <select
            className={inputCls}
            value={draft.book ?? "primary"}
            onChange={(e) => setDraft(field(draft, "book", e.target.value as AlgoIn["book"]))}
          >
            {alpacaActive && <option value="primary">Alpaca paper (primary)</option>}
            {accounts.map((a) => (
              <option key={a.id} value={simBook(a.id)}>
                {a.name} (sim)
              </option>
            ))}
          </select>
          <span className="mt-0.5 normal-case text-term-muted">
            Sim-book algos use the local kill-switch; Alpaca-book P&L lives on its dashboard.
          </span>
        </label>

        {/* preview result */}
        {(previewResult || previewError) && (
          <div className="rounded border border-term-border bg-term-sunken/40 p-2">
            <div className="mb-1 text-[9px] uppercase tracking-wider text-term-muted">Signal preview</div>
            {previewError ? (
              <div className="text-[10px] text-term-down">{previewError.message}</div>
            ) : previewResult ? (
              <RunCard run={previewResult} />
            ) : null}
          </div>
        )}
        {saveError && <div className="text-[10px] text-term-down">{saveError.message}</div>}
      </div>
    </div>
  );
}
