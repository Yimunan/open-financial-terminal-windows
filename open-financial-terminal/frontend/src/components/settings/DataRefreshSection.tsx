import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { DataRefreshConfigIn, DataRefreshStatus } from "../../api/types";
import { cx } from "../../lib/format";
import { Choice, clampNum, type Msg, numCls, relTime, Status } from "./common";

/** Automatic background data refresh: master toggle, market-hours gate, per-job enable/interval,
 * live status (polled every 5s while open) and manual "Run now" triggers. */
export default function DataRefreshSection() {
  const [dr, setDr] = useState<DataRefreshStatus | null>(null);
  const [drBusy, setDrBusy] = useState(false);
  const [drMsg, setDrMsg] = useState<Msg>(null);

  const loadDr = async () => {
    try {
      setDr(await api.dataRefreshStatus());
    } catch {
      /* leave — section shows a loading hint */
    }
  };

  // Poll live status every 5s while this section is mounted (last-run / running state).
  useEffect(() => {
    void loadDr();
    const id = setInterval(() => void loadDr(), 5000);
    return () => clearInterval(id);
  }, []);

  const applyDr = async (body: DataRefreshConfigIn) => {
    setDrBusy(true);
    setDrMsg(null);
    try {
      setDr(await api.saveDataRefreshConfig(body));
      setDrMsg({ ok: true, detail: "Saved" });
    } catch (e) {
      setDrMsg({ ok: false, detail: e instanceof Error ? e.message : "save failed" });
    } finally {
      setDrBusy(false);
    }
  };

  const runDr = async (job: string) => {
    setDrBusy(true);
    setDrMsg(null);
    try {
      const res = await api.runDataRefreshJob(job);
      setDrMsg({ ok: res.status !== "error", detail: `${job} → ${res.status}` });
      await loadDr();
    } catch (e) {
      setDrMsg({ ok: false, detail: e instanceof Error ? e.message : "run failed" });
    } finally {
      setDrBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs font-semibold text-term-text">Automatic data refresh</span>
        {dr && (
          <span className="flex items-center gap-1.5">
            <span className="text-[10px] text-term-muted">All auto-refresh</span>
            <Choice active={dr.master_enabled} onClick={() => applyDr({ master_enabled: true })}>On</Choice>
            <Choice active={!dr.master_enabled} onClick={() => applyDr({ master_enabled: false })}>Off</Choice>
          </span>
        )}
      </div>
      <p className="mb-3 text-[10px] leading-relaxed text-term-muted">
        Keeps the data lake current in the background for your active symbols (watchlist +
        holdings + algo universes). Each category runs on its own schedule — market data and
        news refresh frequently, the slower domains less often. Fundamentals are fetched live
        on demand (no schedule).
      </p>

      {!dr && <div className="text-[10px] text-term-muted">Loading…</div>}

      {dr && (
        <>
          <div className="mb-3 flex items-center justify-between rounded border border-term-border/60 bg-term-sunken/30 px-2.5 py-2">
            <span className="text-[11px] text-term-muted">
              Active set:{" "}
              {Object.entries(dr.active_by_asset ?? {}).filter(([, n]) => n > 0).length === 0 ? (
                <span className="text-term-text">empty</span>
              ) : (
                Object.entries(dr.active_by_asset)
                  .filter(([, n]) => n > 0)
                  .map(([a, n], i) => (
                    <span key={a}>
                      {i > 0 ? " · " : ""}
                      <span className="text-term-text">{n}</span> {a}
                    </span>
                  ))
              )}
            </span>
            <span className="flex items-center gap-1.5">
              <span
                className="text-[10px] text-term-muted"
                title="Skip equity refresh outside US market hours (crypto always runs)"
              >
                Market hours only
              </span>
              <Choice active={dr.market_hours_only} onClick={() => applyDr({ market_hours_only: true })}>On</Choice>
              <Choice active={!dr.market_hours_only} onClick={() => applyDr({ market_hours_only: false })}>Off</Choice>
            </span>
          </div>

          <div className="space-y-2">
            {Object.entries(dr.jobs).map(([name, job]) => (
              <div key={name} className="rounded border border-term-border/60 bg-term-sunken/30 p-2.5">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-[11px] font-semibold text-term-text">
                    {job.label}
                    {job.running && <span className="ml-1.5 text-[9px] text-term-accent">● running</span>}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <Choice active={job.enabled} onClick={() => applyDr({ jobs: { [name]: { enabled: true } } })}>On</Choice>
                    <Choice active={!job.enabled} onClick={() => applyDr({ jobs: { [name]: { enabled: false } } })}>Off</Choice>
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-1.5 text-[10px] text-term-muted">
                    Every
                    <input
                      type="number"
                      min={2}
                      step={1}
                      value={Math.round(job.interval_minutes)}
                      onChange={(e) =>
                        setDr((d) =>
                          d
                            ? {
                                ...d,
                                jobs: {
                                  ...d.jobs,
                                  [name]: { ...d.jobs[name], interval_minutes: clampNum(Number(e.target.value), 2, 10080) },
                                },
                              }
                            : d,
                        )
                      }
                      onBlur={(e) => applyDr({ jobs: { [name]: { interval_minutes: clampNum(Number(e.target.value), 2, 10080) } } })}
                      className={cx(numCls, "w-16")}
                      aria-label={`${job.label} interval minutes`}
                    />
                    min
                  </span>
                  <span className="flex items-center gap-2 text-[10px] text-term-muted">
                    <span title={job.last_run ?? "never"}>last {relTime(job.last_run)}</span>
                    {job.enabled && job.last_run && <span title={job.next_run ?? ""}>· next {relTime(job.next_run)}</span>}
                    <button
                      onClick={() => runDr(name)}
                      disabled={drBusy || job.running}
                      className="rounded border border-term-accent/70 px-2 py-0.5 text-[10px] text-term-accent hover:bg-term-accent/15 disabled:opacity-40"
                    >
                      Run now
                    </button>
                  </span>
                </div>
                {job.last_result?.status === "error" && (
                  <div className="mt-1 truncate text-[9px] text-term-down" title={String(job.last_result.error ?? "")}>
                    ✕ {String(job.last_result.error ?? "error")}
                  </div>
                )}
                {job.last_result?.status === "ok" && (
                  <div className="mt-1 truncate text-[9px] text-term-muted">
                    {Object.entries(job.last_result)
                      .filter(([k]) => !["ts", "status", "duration_s"].includes(k))
                      .map(([k, v]) => `${k}=${String(v)}`)
                      .join(" · ")}
                  </div>
                )}
              </div>
            ))}
          </div>

          <p className="mt-3 text-[9px] leading-relaxed text-term-muted">
            News is fetched live and cached on demand — this keeps the cache warm. SEC filings
            need a configured SEC_USER_AGENT and are off by default. Rates/macro pull from FRED
            (with fallbacks) and may be unavailable on restricted networks.
          </p>
        </>
      )}

      <Status busy={drBusy} msg={drMsg} />
    </div>
  );
}
