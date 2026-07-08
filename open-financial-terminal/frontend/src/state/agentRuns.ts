/** Background agent-run store for the backtest agents.
 *
 * The run lifecycle (WebSocket + streamed frames + accumulated messages/runs) lives here, not in
 * the widget component, so a run keeps going when the widget unmounts — switching workspace tabs,
 * closing/reopening the panel. Each job is keyed by the Dockview panel id (stable across layout
 * save/restore). The WebSocket is held in a module Map (not in React state); its callbacks patch
 * the store regardless of whether any component is mounted, so progress streams in the background.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { openBacktestAgentSocket } from "../api/client";
import type { BacktestAgentFrame, BacktestResponse, LabResult } from "../api/types";
import type { ChatMsg } from "../components/AgentChatPanel";
import { useChatHistory, type ChatSession } from "./chatHistory";
import { lightenJobs } from "./persistJobs";

export type RunKind = "factor" | "lab" | "factor_monitor" | "chart_studio";
export type RunResult = BacktestResponse | LabResult;

export interface RunRec {
  id: string;
  label: string;
  result: RunResult;
  params: Record<string, string | number>;
}

export interface AgentJob {
  kind: RunKind;
  status: "idle" | "running" | "done" | "error";
  messages: ChatMsg[];
  runs: RunRec[];
  activeRunId: string | null;
  sessionId: string; // groups a conversation for chat history
}

interface AgentRunsState {
  jobs: Record<string, AgentJob>;
  start: (jobId: string, kind: RunKind, goal: string, context: Record<string, unknown>) => void;
  /** Inject a result produced outside the agent (e.g. a saved-model backtest) as a run, so it shows
   *  in the Dashboard / Processing / History exactly like an agent run. */
  injectRun: (
    jobId: string,
    run: { intent: string; label: string; result: RunResult; params?: Record<string, string | number> },
  ) => void;
  stop: (jobId: string) => void;
  selectRun: (jobId: string, runId: string) => void;
  clear: (jobId: string) => void;
  newChat: (jobId: string) => void;
  loadSession: (jobId: string, session: ChatSession) => void;
}

const sockets = new Map<string, WebSocket>();

function newId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `s${Date.now()}-${Math.round(performance.now())}`;
}

function titleOf(messages: ChatMsg[]): string {
  return (messages.find((m) => m.role === "user")?.text ?? "Conversation").slice(0, 80);
}

function fmtMetrics(kind: RunKind, m: Record<string, number | null>): string {
  if (kind === "factor")
    return `Sharpe ${m.sharpe ?? "—"} · CAGR ${m.cagr ?? "—"}% · MaxDD ${m.max_drawdown ?? "—"}%`;
  return `net ${m.net_pnl_pct ?? "—"}% · Sharpe ${m.sharpe ?? "—"} · ${m.trades ?? 0} trades`;
}

/** Save a job's conversation AND its results to history if it has any messages. */
function archive(job: AgentJob | undefined): void {
  if (job && job.messages.length > 0) {
    useChatHistory.getState().save({
      id: job.sessionId,
      kind: job.kind,
      title: titleOf(job.messages),
      at: Date.now(),
      messages: job.messages,
      runs: job.runs,
      activeRunId: job.activeRunId,
    });
  }
}

export const useAgentRuns = create<AgentRunsState>()(
  persist(
    (set, get) => {
  const patch = (jobId: string, fn: (j: AgentJob) => AgentJob) =>
    set((s) => {
      const j = s.jobs[jobId];
      return j ? { jobs: { ...s.jobs, [jobId]: fn(j) } } : s;
    });

  return {
    jobs: {},

    start: (jobId, kind, goal, context) => {
      sockets.get(jobId)?.close();
      const prev = get().jobs[jobId];
      set((s) => ({
        jobs: {
          ...s.jobs,
          [jobId]: {
            kind,
            status: "running",
            messages: [...(prev?.messages ?? []), { role: "user", text: goal }],
            runs: prev?.runs ?? [],
            activeRunId: prev?.activeRunId ?? null,
            sessionId: prev?.sessionId ?? newId(),
          },
        },
      }));

      const ws = openBacktestAgentSocket();
      sockets.set(jobId, ws);
      ws.onopen = () => ws.send(JSON.stringify({ op: "run", engine: kind, goal, context }));
      ws.onmessage = (e) => {
        const f = JSON.parse(e.data) as BacktestAgentFrame;
        if (f.type === "thought") {
          patch(jobId, (j) => ({ ...j, messages: [...j.messages, { role: "thought", text: f.text }] }));
        } else if (f.type === "run") {
          const rec: RunRec = { id: f.id, label: f.label, result: f.result, params: f.params };
          patch(jobId, (j) => ({
            ...j,
            runs: [...j.runs, rec],
            activeRunId: f.id,
            // Backtest (factor) shows run rows in its Processing sub-window — keep them clean like
            // Chart Studio (label only; full metrics live in the dashboard). Strategy Lab (lab) shows
            // run rows inline in its chat, so it keeps the at-a-glance metrics suffix.
            messages: [
              ...j.messages,
              { role: "run", runId: f.id, text: kind === "factor" ? f.label : `${f.label}  —  ${fmtMetrics(kind, f.metrics)}` },
            ],
          }));
        } else if (f.type === "obs") {
          patch(jobId, (j) => ({ ...j, messages: [...j.messages, { role: "obs", text: f.text }] }));
        } else if (f.type === "done") {
          patch(jobId, (j) => ({
            ...j,
            status: "done",
            activeRunId: f.best_id && j.runs.some((r) => r.id === f.best_id) ? f.best_id : j.activeRunId,
            messages: [
              ...j.messages.map((x) =>
                x.role === "run" && f.best_id && x.runId === f.best_id ? { ...x, best: true } : x,
              ),
              { role: "assistant", text: f.message },
            ],
          }));
          archive(get().jobs[jobId]); // snapshot the finished conversation to history
          ws.close();
          sockets.delete(jobId);
        } else if (f.type === "error") {
          patch(jobId, (j) => ({ ...j, status: "error", messages: [...j.messages, { role: "error", text: f.detail }] }));
          ws.close();
          sockets.delete(jobId);
        }
      };
      ws.onerror = () => patch(jobId, (j) => (j.status === "running" ? { ...j, status: "error" } : j));
      ws.onclose = () => {
        sockets.delete(jobId);
        patch(jobId, (j) => (j.status === "running" ? { ...j, status: "done" } : j));
      };
    },

    injectRun: (jobId, { intent, label, result, params }) => {
      const id = newId();
      set((s) => {
        const prev = s.jobs[jobId];
        const base: AgentJob = prev ?? {
          kind: "factor",
          status: "idle",
          messages: [],
          runs: [],
          activeRunId: null,
          sessionId: newId(),
        };
        const rec: RunRec = { id, label, result, params: params ?? {} };
        return {
          jobs: {
            ...s.jobs,
            [jobId]: {
              ...base,
              status: "done",
              runs: [...base.runs, rec],
              activeRunId: id,
              messages: [
                ...base.messages,
                { role: "user", text: intent },
                { role: "run", runId: id, text: label },
                { role: "assistant", text: `Backtested ${label}.` },
              ],
            },
          },
        };
      });
      archive(get().jobs[jobId]);
    },

    stop: (jobId) => {
      sockets.get(jobId)?.close();
      sockets.delete(jobId);
      patch(jobId, (j) => ({ ...j, status: j.status === "running" ? "idle" : j.status }));
    },

    selectRun: (jobId, runId) => patch(jobId, (j) => ({ ...j, activeRunId: runId })),

    clear: (jobId) => patch(jobId, (j) => ({ ...j, messages: [] })),

    // archive the current conversation, then reset to a fresh one
    newChat: (jobId) => {
      const job = get().jobs[jobId];
      archive(job);
      sockets.get(jobId)?.close();
      sockets.delete(jobId);
      if (!job) return;
      set((s) => ({
        jobs: {
          ...s.jobs,
          [jobId]: { kind: job.kind, status: "idle", messages: [], runs: [], activeRunId: null, sessionId: newId() },
        },
      }));
    },

    // archive current, then load a past conversation — restoring its results so the dashboard
    // repopulates immediately (no re-run needed)
    loadSession: (jobId, session) => {
      const job = get().jobs[jobId];
      if (job && job.sessionId !== session.id) archive(job);
      sockets.get(jobId)?.close();
      sockets.delete(jobId);
      set((s) => ({
        jobs: {
          ...s.jobs,
          [jobId]: {
            kind: session.kind,
            status: "idle",
            messages: session.messages,
            runs: (session.runs as RunRec[] | undefined) ?? [],
            activeRunId: session.activeRunId ?? null,
            sessionId: session.id,
          },
        },
      }));
    },
  };
    },
    {
      name: "oft-backtest-agent",
      partialize: (s) => ({ jobs: lightenJobs(s.jobs) }),
    },
  ),
);

/** Count of jobs currently running — for the top-bar indicator. */
export function useRunningCount(): number {
  return useAgentRuns((s) => Object.values(s.jobs).filter((j) => j.status === "running").length);
}
