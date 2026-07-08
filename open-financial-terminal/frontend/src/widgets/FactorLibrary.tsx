import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EngineFactor, RegFactor } from "../api/types";
import { cx } from "../lib/format";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { EmptyState } from "../components/States";
import RegistrySummary from "../components/RegistrySummary";
import { useWorkspace } from "../state/workspace";
import { WidgetShell, useWidgetSymbol } from "./shell";

const inputCls =
  "focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs focus:border-term-accent";

export default function FactorLibrary(props: WidgetProps) {
  const { channel, setChannel } = useWidgetSymbol(props);
  const qc = useQueryClient();
  const openWidget = useWorkspace((s) => s.openWidget);
  const { data } = useQuery({ queryKey: ["reg-factors"], queryFn: api.registryFactors });
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<RegFactor | null>(null); // selected built-in (read-only)
  const [engSel, setEngSel] = useState<EngineFactor | null>(null); // selected qhfi-engine factor
  const [customSel, setCustomSel] = useState<RegFactor | null>(null); // selected custom (read-only; authored in Sandbox)

  const builtin = (data?.builtin ?? []).filter((f) => match(f, q));
  const custom = (data?.custom ?? []).filter((f) => match(f, q));
  const engine = (data?.engine ?? []).filter((f) => matchEngine(f, q));
  const clear = () => { setSel(null); setEngSel(null); setCustomSel(null); };

  const del = useMutation({
    mutationFn: (name: string) => api.deleteFactor(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reg-factors"] });
      setCustomSel(null);
    },
  });

  const openInSandbox = (f: RegFactor) =>
    openWidget("sandbox", {
      initialMode: "factor", initialTrust: "sandboxed",
      initialName: f.name, initialCode: f.code ?? "",
      initialMeta: { kind: f.kind, direction: f.direction, description: f.description ?? "" },
    });

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">Factor library</span>
          <button
            onClick={() => openWidget("sandbox", { initialMode: "factor", initialTrust: "sandboxed" })}
            className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10"
          >
            ＋ New
          </button>
        </>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        <RegistrySummary kind="factors" />
        <div className="flex min-h-0 flex-1">
        {/* list */}
        <div className="flex w-60 shrink-0 flex-col border-r border-term-border">
          <div className="p-1.5">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search factors…" aria-label="Search factors" className={inputCls} />
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-2">
            <Section label="Built-in">
              {builtin.map((f) => (
                <Row key={f.name} active={sel?.name === f.name} onClick={() => { clear(); setSel(f); }}>
                  <span>{f.name}</span>
                  <span className="text-[9px] text-term-muted">{f.kind}</span>
                </Row>
              ))}
            </Section>
            <Section label={`qhfi engine (${engine.length})`}>
              {engine.length === 0 && <EmptyState title="Registry empty." />}
              {engine.map((f) => (
                <Row key={f.name} active={engSel?.name === f.name} onClick={() => { clear(); setEngSel(f); }}>
                  <span>{f.name}</span>
                  <span className={cx("text-[9px]", f.source === "linked" ? "text-term-accent" : "text-term-muted")}>{f.source}</span>
                </Row>
              ))}
            </Section>
            <Section label={`Custom (${custom.length})`}>
              {custom.length === 0 && <EmptyState title="None yet." />}
              {custom.map((f) => (
                <Row key={f.name} active={customSel?.name === f.name} onClick={() => { clear(); setCustomSel(f); }}>
                  <span>{f.name}</span>
                  <span className="text-[9px] text-term-accent">view</span>
                </Row>
              ))}
            </Section>
          </div>
        </div>

        {/* detail / editor */}
        <div className="min-w-0 flex-1 overflow-auto p-3">
          {customSel ? (
            <div className="space-y-2 font-mono text-xs">
              <div className="text-sm font-semibold">{customSel.name}</div>
              <Stat k="Kind" v={customSel.kind} />
              <Stat k="Direction" v={customSel.direction} />
              {customSel.description && <div className="pt-1 leading-snug text-term-muted">{customSel.description}</div>}
              <pre className="mt-1 overflow-auto rounded border border-term-border bg-term-bg/40 p-2 text-[11px] leading-snug">{customSel.code ?? ""}</pre>
              <div className="flex items-center gap-2 pt-1">
                <button
                  onClick={() => openInSandbox(customSel)}
                  className="rounded border border-term-accent px-3 py-1 text-xs uppercase text-term-accent hover:bg-term-accent/10"
                >
                  Open in Sandbox
                </button>
                <button onClick={() => del.mutate(customSel.name)} disabled={del.isPending} className="rounded border border-term-down px-3 py-1 text-xs uppercase text-term-down hover:bg-term-down/10 disabled:opacity-50">
                  Delete
                </button>
                {del.error && <span className="text-[10px] text-term-down">{(del.error as Error).message}</span>}
              </div>
            </div>
          ) : engSel ? (
            <div className="space-y-2 font-mono text-xs">
              <div className="text-sm font-semibold">{engSel.name}</div>
              <Stat k="Label" v={engSel.label} />
              <Stat k="Direction" v={engSel.direction} />
              <Stat k="Source" v={engSel.source === "linked" ? "linked directory" : "qhfi built-in"} />
              {engSel.doc && <div className="pt-1 leading-snug text-term-muted">{engSel.doc}</div>}
              <div className="mt-2 rounded border border-term-border bg-term-bg/40 p-2 text-[11px] text-term-muted">
                Live from the qhfi factor registry (linked factors dir). Use it in the Backtest widget /
                agent (e.g. “{engSel.name} long-short on the dow”). Drop a new
                <span className="font-mono"> @register</span>ed factor into the linked dir and Rescan in Settings.
              </div>
            </div>
          ) : sel ? (
            <div className="space-y-2 font-mono text-xs">
              <div className="text-sm font-semibold">{sel.name}</div>
              <Stat k="Label" v={sel.label ?? sel.name} />
              <Stat k="Kind" v={sel.kind} />
              <Stat k="Direction" v={sel.direction} />
              <div className="mt-2 rounded border border-term-border bg-term-bg/40 p-2 text-[11px] text-term-muted">
                Built-in factor — read-only. Use it in the Backtest agent (e.g. “{sel.name} long-short on the dow”).
              </div>
            </div>
          ) : (
            <div className="text-xs text-term-muted">
              Browse built-in factors or author your own (＋ New opens the Sandbox). Click a custom factor
              to view it, then “Open in Sandbox” to run or edit. Authoring lives in the Sandbox.
            </div>
          )}
        </div>
        </div>
      </div>
    </WidgetShell>
  );
}

function match(f: RegFactor, q: string): boolean {
  if (!q) return true;
  const s = q.toLowerCase();
  return [f.name, f.label, f.kind, f.direction, f.description].some((x) => (x ?? "").toLowerCase().includes(s));
}

function matchEngine(f: EngineFactor, q: string): boolean {
  if (!q) return true;
  const s = q.toLowerCase();
  return [f.name, f.label, f.direction, f.doc].some((x) => (x ?? "").toLowerCase().includes(s));
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="px-1 py-1 text-[9px] uppercase tracking-wider text-term-muted">{label}</div>
      {children}
    </div>
  );
}

function Row({ active, onClick, children }: { active?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cx(
        "flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-[11px] hover:bg-term-border/40",
        active && "bg-term-border/50",
      )}
    >
      {children}
    </button>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between border-b border-term-border/30 py-0.5">
      <span className="text-term-muted">{k}</span>
      <span className="text-term-text">{v}</span>
    </div>
  );
}
