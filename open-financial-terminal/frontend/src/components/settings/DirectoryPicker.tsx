import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { FsList } from "../../api/types";
import { cx } from "../../lib/format";

/** Join a directory path and a child name using the OS separator, avoiding a double separator at a
 * filesystem root (e.g. "/" + "Users" → "/Users", not "//Users"). */
function joinPath(base: string, name: string, sep: string): string {
  return base.endsWith(sep) ? base + name : base + sep + name;
}

/** A short display label for a quick-root button (the last path segment, or the path itself). */
function rootLabel(p: string, sep: string): string {
  const parts = p.split(sep).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : p;
}

/**
 * In-app folder browser — a modal rendered above the Settings dialog. Lists the sub-directories of a
 * path via the backend `/fs/list` endpoint (so it works in the desktop window AND browser mode, and
 * browses the backend's own filesystem — the correct one for the registry dir fields). Navigate in
 * by clicking a folder, up via ↑, jump to Home / cwd / Data dir, or type a path. "Select this folder"
 * returns the currently-listed absolute path.
 */
export default function DirectoryPicker({
  title,
  initialPath,
  onPick,
  onClose,
}: {
  title: string;
  initialPath?: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [data, setData] = useState<FsList | null>(null);
  const [input, setInput] = useState(initialPath ?? "");
  const [busy, setBusy] = useState(false);

  const load = async (path: string) => {
    setBusy(true);
    try {
      const r = await api.fsList(path);
      setData(r);
      setInput(r.path);
    } catch (e) {
      setData((d) => (d ? { ...d, error: e instanceof Error ? e.message : "list failed" } : d));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load(initialPath ?? "");
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation(); // don't also close the parent Settings dialog
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sep = data?.sep ?? "/";

  return (
    <>
      <div className="fixed inset-0 z-[60] bg-black/60" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="fixed left-1/2 top-[14vh] z-[60] flex h-[min(480px,72vh)] w-[min(560px,92vw)] -translate-x-1/2 flex-col overflow-hidden rounded-lg border border-term-border bg-term-elev shadow-elev-3"
      >
        <div className="flex items-center justify-between border-b border-term-border px-3 py-2">
          <span className="text-xs font-semibold text-term-text">{title}</span>
          <button
            onClick={onClose}
            aria-label="Close folder picker"
            className="focus-ring rounded text-term-muted hover:text-term-text"
          >
            ×
          </button>
        </div>

        {/* path bar: editable target + up */}
        <div className="flex items-center gap-1 border-b border-term-border/60 px-3 py-2">
          <button
            onClick={() => data?.parent && load(data.parent)}
            disabled={busy || !data?.parent}
            title="Up one level"
            className="focus-ring shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-40"
          >
            ↑
          </button>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load(input)}
            placeholder="Type or paste an absolute path, then Enter"
            className="focus-ring min-w-0 flex-1 rounded border border-term-border bg-term-sunken px-2 py-1 font-mono text-[11px] text-term-text focus:border-term-accent"
            spellCheck={false}
            autoComplete="off"
          />
          <button
            onClick={() => load(input)}
            disabled={busy}
            title="Go to this path"
            className="focus-ring shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-40"
          >
            Go
          </button>
        </div>

        {/* quick roots */}
        {data && data.roots.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 border-b border-term-border/40 px-3 py-1.5">
            <span className="text-[9px] uppercase tracking-wider text-term-muted">Jump to</span>
            {data.roots.map((r) => (
              <button
                key={r}
                onClick={() => load(r)}
                title={r}
                className="rounded border border-term-border px-1.5 py-0.5 text-[10px] text-term-muted hover:border-term-accent hover:text-term-accent"
              >
                {rootLabel(r, sep)}
              </button>
            ))}
          </div>
        )}

        {/* folder list */}
        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
          {busy && !data && <div className="px-2 py-2 text-[10px] text-term-muted">Loading…</div>}
          {data && data.entries.length === 0 && !busy && (
            <div className="px-2 py-2 text-[10px] text-term-muted">No sub-folders here.</div>
          )}
          {data?.entries.map((e) => (
            <button
              key={e.name}
              onClick={() => load(joinPath(data.path, e.name, sep))}
              className="focus-ring flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs text-term-text hover:bg-term-border/40"
            >
              <span className="text-term-muted">📁</span>
              <span className="truncate">{e.name}</span>
            </button>
          ))}
        </div>

        {/* footer: current selection + actions */}
        <div className="border-t border-term-border px-3 py-2">
          {data?.error && <div className="mb-1 truncate text-[10px] text-term-down" title={data.error}>✕ {data.error}</div>}
          <div className="mb-1.5 truncate font-mono text-[10px] text-term-muted" title={data?.path}>
            {data?.path ?? "…"}
          </div>
          <div className="flex items-center justify-end gap-1.5">
            <button
              onClick={onClose}
              className="rounded border border-term-border px-2.5 py-1 text-xs text-term-muted hover:text-term-text"
            >
              Cancel
            </button>
            <button
              onClick={() => data && onPick(data.path)}
              disabled={!data}
              className={cx(
                "rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50",
              )}
            >
              Select this folder
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
