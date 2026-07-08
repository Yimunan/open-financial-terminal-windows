import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";

export type RegistryKind = "factors" | "strategies" | "models";

/** A compact, collapsible "✦ Summarize" panel for the registry modules. Asks the local LLM for a
 * natural-language overview of the current registry contents and renders it inline. Read-only —
 * nothing is persisted. Render it at the top of a module's content column. */
export default function RegistrySummary({ kind }: { kind: RegistryKind }) {
  const m = useMutation({ mutationFn: () => api.summarizeRegistry(kind) });
  const shown = m.isPending || m.isError || m.data != null;

  return (
    <div className="shrink-0 border-b border-term-border">
      <div className="flex items-center gap-2 px-2 py-1">
        <button
          onClick={() => m.mutate()}
          disabled={m.isPending}
          title={`AI overview of your ${kind} registry`}
          className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
        >
          {m.isPending ? "Summarizing…" : "✦ Summarize"}
        </button>
        <span className="text-[10px] text-term-muted">AI overview of your {kind}</span>
        {shown && !m.isPending && (
          <button
            onClick={() => m.reset()}
            className="ml-auto text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text"
          >
            hide
          </button>
        )}
      </div>
      {shown && (
        <div className="px-3 pb-2">
          {m.isPending && <div className="text-[11px] text-term-muted">Generating summary…</div>}
          {m.isError && <div className="text-[11px] text-term-down">{(m.error as Error).message}</div>}
          {m.data && (
            <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-term-text/90">{m.data.summary}</p>
          )}
        </div>
      )}
    </div>
  );
}
