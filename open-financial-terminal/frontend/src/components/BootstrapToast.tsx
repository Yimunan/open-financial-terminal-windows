import { useEffect, useRef, useState } from "react";

import type { Health } from "../api/types";
import { cx } from "../lib/format";

type Boot = NonNullable<Health["bootstrap"]>;

/**
 * First-run data bootstrap toast.
 *
 * On a fresh install the backend pulls a baseline lake in the background (see
 * app/services/bootstrap.py); GET /api/health reports it under `bootstrap`. While it runs we show
 * an unobtrusive progress card bottom-right, then flash a brief "ready" on completion. Renders
 * nothing on an already-populated lake (state "skipped"/"idle") — we only react once we've actually
 * seen it "running", so opening the app after setup never pops a stray toast.
 */
export default function BootstrapToast({ bootstrap }: { bootstrap?: Boot }) {
  const state = bootstrap?.state;
  const sawRunning = useRef(false);
  const [phase, setPhase] = useState<"hidden" | "running" | "done" | "error">("hidden");

  useEffect(() => {
    if (state === "running") {
      sawRunning.current = true;
      setPhase("running");
      return;
    }
    if (state === "done" && sawRunning.current) {
      setPhase("done");
      const id = window.setTimeout(() => setPhase("hidden"), 5000);
      return () => window.clearTimeout(id);
    }
    if (state === "error" && sawRunning.current) {
      setPhase("error");
      const id = window.setTimeout(() => setPhase("hidden"), 9000);
      return () => window.clearTimeout(id);
    }
  }, [state]);

  if (phase === "hidden") return null;

  const total = bootstrap?.total ?? 0;
  const done = bootstrap?.done ?? 0;
  const failed = bootstrap?.failed ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  const dot =
    phase === "running" ? "bg-term-accent animate-pulse" : phase === "done" ? "bg-term-up" : "bg-term-down";
  const title =
    phase === "running" ? "Loading market data" : phase === "done" ? "Market data ready" : "Market data incomplete";

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-4 right-4 z-40 w-72 rounded-md border border-term-border bg-term-elev px-3 py-2.5 text-term-text shadow-elev-2"
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className={cx("inline-block h-1.5 w-1.5 shrink-0 rounded-full", dot)} />
        <span className="text-xs font-semibold">{title}</span>
        <div className="ml-auto flex items-center gap-2">
          {phase === "running" && total > 0 && (
            <span className="text-[10px] tabular-nums text-term-muted">
              {done}/{total}
            </span>
          )}
          <button
            onClick={() => setPhase("hidden")}
            aria-label="Dismiss"
            className="focus-ring grid h-4 w-4 place-items-center rounded text-term-muted transition-colors hover:text-term-text"
          >
            <span aria-hidden className="text-sm leading-none">
              &times;
            </span>
          </button>
        </div>
      </div>

      {phase === "running" && (
        <>
          <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-term-border">
            <div
              className="h-full rounded-full bg-term-accent transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="mt-1 text-[10px] text-term-muted">
            First-run setup{bootstrap?.universe ? ` · ${bootstrap.universe}` : ""} — charts fill in as data lands.
          </p>
        </>
      )}
      {phase === "done" && (
        <p className="mt-1 text-[10px] text-term-muted">
          {done - failed}/{total} symbols cached.{failed > 0 ? ` ${failed} skipped.` : ""}
        </p>
      )}
      {phase === "error" && (
        <p className="mt-1 text-[10px] text-term-muted">
          {bootstrap?.detail || "Pull data from Settings → Data Refresh."}
        </p>
      )}
    </div>
  );
}
