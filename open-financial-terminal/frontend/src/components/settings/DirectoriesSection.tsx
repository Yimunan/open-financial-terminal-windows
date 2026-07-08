import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { RegPaths } from "../../api/types";
import { cx } from "../../lib/format";
import { llmInputCls, type Msg, Status } from "./common";
import DirectoryPicker from "./DirectoryPicker";

type PathKey = keyof RegPaths;

/** Directories: the linked qhfi registry roots (Factors / Strategies / Models) — each with an in-app
 * folder picker — plus the AI-agent knowledge base (a remote crewai-service path, text only). */
export default function DirectoriesSection() {
  const qc = useQueryClient();
  const [paths, setPaths] = useState<RegPaths | null>(null);
  const [pathCounts, setPathCounts] = useState<{ factors: number; strategies: number; models: number } | null>(null);
  const [pathsBusy, setPathsBusy] = useState(false);
  const [pathsMsg, setPathsMsg] = useState<Msg>(null);
  // which registry-dir field's folder picker is open (null = none)
  const [picking, setPicking] = useState<PathKey | null>(null);

  const loadPaths = async () => {
    try {
      const [p, f, s, m] = await Promise.all([
        api.registryPaths(),
        api.registryFactors().catch(() => null),
        api.registryStrategies().catch(() => null),
        api.repoModels().catch(() => null),
      ]);
      setPaths(p);
      setPathCounts({
        factors: f?.engine?.length ?? 0,
        strategies: s?.engine?.length ?? 0,
        models: m?.models?.length ?? 0,
      });
    } catch {
      /* leave nulls — section just shows blank inputs */
    }
  };

  useEffect(() => {
    void loadPaths();
  }, []);

  const savePaths = async () => {
    if (!paths) return;
    setPathsBusy(true);
    setPathsMsg(null);
    try {
      const saved = await api.savePaths(paths);
      setPaths(saved);
      // Re-discover engine/linked content under the new dirs, then refresh the widgets.
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["reg-factors"] }),
        qc.invalidateQueries({ queryKey: ["reg-strategies"] }),
        qc.invalidateQueries({ queryKey: ["reg-models"] }),
        qc.invalidateQueries({ queryKey: ["repo-models"] }),
      ]);
      await loadPaths();
      setPathsMsg({ ok: true, detail: "Saved · widgets refreshed" });
    } catch (e) {
      setPathsMsg({ ok: false, detail: e instanceof Error ? e.message : "save failed" });
    } finally {
      setPathsBusy(false);
    }
  };

  const fields: [PathKey, string, string][] = [
    ["factors_dir", "Factors", pathCounts ? `${pathCounts.factors} in registry` : ""],
    ["strategies_dir", "Strategies", pathCounts ? `${pathCounts.strategies} in registry` : ""],
    ["models_dir", "Model repository", pathCounts ? `${pathCounts.models} model(s)` : ""],
  ];

  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold text-term-text">Linked qhfi directories</div>
      <p className="mb-2 text-[10px] leading-relaxed text-term-muted">
        Where the Factor, Strategy and Model-Repository widgets read from. Point factors/strategies
        at a folder of qhfi-style <span className="font-mono">.py</span> modules (imported so their
        <span className="font-mono"> @register</span> classes become listable + runnable); point models
        at a qhfi <span className="font-mono">ModelRepository</span> root (versioned cards). Use{" "}
        <b>Browse</b> to find a folder, or type/paste an absolute path.
      </p>

      <div className="space-y-2">
        {fields.map(([key, label, hint]) => (
          <label key={key} className="block">
            <span className="mb-0.5 flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-term-muted">{label}</span>
              {hint && <span className="text-[9px] text-term-accent">{hint}</span>}
            </span>
            <div className="flex items-center gap-1">
              <input
                value={paths?.[key] ?? ""}
                onChange={(e) => paths && setPaths({ ...paths, [key]: e.target.value })}
                placeholder={`Absolute path to ${label.toLowerCase()} directory`}
                className={cx(llmInputCls, "flex-1")}
                spellCheck={false}
              />
              <button
                onClick={() => setPicking(key)}
                disabled={!paths}
                title="Browse for a folder"
                className="focus-ring shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-40"
              >
                Browse…
              </button>
            </div>
          </label>
        ))}
      </div>

      <div className="mt-2 flex items-center gap-1.5">
        <button
          onClick={savePaths}
          disabled={pathsBusy || !paths}
          className="rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50"
        >
          Save
        </button>
        <button
          onClick={loadPaths}
          disabled={pathsBusy}
          className="rounded border border-term-border px-2.5 py-1 text-xs text-term-muted hover:text-term-text disabled:opacity-50"
        >
          ↻ Rescan
        </button>
      </div>

      <Status busy={pathsBusy} msg={pathsMsg} />

      <div className="my-3 border-t border-term-border" />
      <KnowledgeDirSetting inputCls={llmInputCls} />

      {picking && paths && (
        <DirectoryPicker
          title={`Select ${fields.find(([k]) => k === picking)?.[1] ?? "folder"} directory`}
          initialPath={paths[picking] || ""}
          onClose={() => setPicking(null)}
          onPick={(path) => {
            setPaths({ ...paths, [picking]: path });
            setPicking(null);
          }}
        />
      )}
    </div>
  );
}

/** AI agent knowledge base directory — where the Committees module saves agents' RAG files, laid
 * out as Directory → Committee → Agent. Lives in the CrewAI service's filesystem (a WSL path), so
 * the local folder picker can't target it — this stays a text field. */
function KnowledgeDirSetting({ inputCls }: { inputCls: string }) {
  const [dir, setDir] = useState("");
  const [def, setDef] = useState("");
  const [supported, setSupported] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  const load = () =>
    api
      .committeeKnowledgeDir()
      .then((r) => {
        setDir(r.dir);
        setDef(r.default);
        setSupported(r.supported);
      })
      .catch((e) => setMsg({ ok: false, detail: e?.message ?? "crewai-service unreachable" }));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async (value: string) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.setCommitteeKnowledgeDir(value);
      setDir(r.dir);
      setMsg({ ok: true, detail: `Saved · ${r.dir}` });
    } catch (e) {
      setMsg({ ok: false, detail: (e as Error)?.message ?? "failed" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold text-term-text">AI agent knowledge base</div>
      <p className="mb-2 text-[10px] leading-relaxed text-term-muted">
        Where the Committees module saves each agent's RAG files, laid out as{" "}
        <span className="font-mono">Directory → Committee → Agent</span>. This folder is on the{" "}
        <b>AI service's</b> filesystem (a remote/WSL path, e.g.{" "}
        <span className="font-mono">/root/crewai/knowledge</span> or{" "}
        <span className="font-mono">/mnt/c/Users/you/kb</span>) — not this machine, so type it directly.
        Supported files:{" "}
        <span className="font-mono">{(supported.length ? supported : [".txt", ".md", ".pdf", ".csv"]).join(" ")}</span>.
      </p>
      <label className="block">
        <span className="text-[10px] uppercase tracking-wider text-term-muted">Directory</span>
        <input
          value={dir}
          onChange={(e) => setDir(e.target.value)}
          placeholder={def || "/root/crewai/knowledge"}
          className={inputCls}
          spellCheck={false}
        />
      </label>
      <div className="mt-2 flex items-center gap-1.5">
        <button
          onClick={() => save(dir)}
          disabled={busy}
          className="rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50"
        >
          Save
        </button>
        <button
          onClick={() => save("")}
          disabled={busy}
          className="rounded border border-term-border px-2.5 py-1 text-xs text-term-muted hover:text-term-text disabled:opacity-50"
          title={def}
        >
          Use default
        </button>
      </div>
      <Status busy={busy} msg={msg} />
    </div>
  );
}
