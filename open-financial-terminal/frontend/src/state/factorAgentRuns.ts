/** Background run store for the chat-driven factor-performance agent.
 *
 * Mirrors `agentRuns.ts` (the backtest agent store): the WebSocket + streamed frames + accumulated
 * messages/results live here, not in the widget, so a run keeps going across unmount / tab switches.
 * Each job is keyed by the Dockview panel id. Results come in three kinds — leaderboard (board),
 * single-factor drill-down (detail), and monitor snapshot history — and the widget renders the
 * currently-selected one.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { openFactorMonitorAgentSocket } from "../api/client";
import type {
  FactorCorrelationMatrix,
  FactorDetail,
  FactorMonitorAgentFrame,
  FactorMonitorResultKind,
  FactorScorecard,
  MonitorHistory,
} from "../api/types";
import type { ChatMsg } from "../components/AgentChatPanel";
import { useChatHistory, type ChatSession } from "./chatHistory";
import { lightenJobs } from "./persistJobs";

export type FMResult = FactorScorecard | FactorDetail | MonitorHistory | FactorCorrelationMatrix;

export interface FMRunRec {
  id: string;
  kind: FactorMonitorResultKind;
  label: string;
  data: FMResult;
  params: Record<string, string | number>;
}

export interface FMJob {
  status: "idle" | "running" | "done" | "error";
  messages: ChatMsg[];
  runs: FMRunRec[];
  activeRunId: string | null;
  sessionId: string; // groups a conversation for chat history
}

interface FactorAgentState {
  jobs: Record<string, FMJob>;
  start: (jobId: string, goal: string, context: Record<string, unknown>) => void;
  stop: (jobId: string) => void;
  selectRun: (jobId: string, runId: string) => void;
  newChat: (jobId: string) => void;
  loadSession: (jobId: string, session: ChatSession) => void;
}

const sockets = new Map<string, WebSocket>();

function newId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `s${Date.now()}-${Math.round(performance.now())}`;
}

function titleOf(messages: ChatMsg[]): string {
  return (messages.find((m) => m.role === "user")?.text ?? "Conversation").slice(0, 80);
}

/** Save a job's conversation to history if it has any messages. */
function archive(job: FMJob | undefined): void {
  if (job && job.messages.length > 0) {
    useChatHistory.getState().save({
      id: job.sessionId,
      kind: "factor_monitor",
      title: titleOf(job.messages),
      at: Date.now(),
      messages: job.messages,
    });
  }
}

export const useFactorAgent = create<FactorAgentState>()(
  persist(
    (set, get) => {
  const patch = (jobId: string, fn: (j: FMJob) => FMJob) =>
    set((s) => {
      const j = s.jobs[jobId];
      return j ? { jobs: { ...s.jobs, [jobId]: fn(j) } } : s;
    });

  return {
    jobs: {},

    start: (jobId, goal, context) => {
      sockets.get(jobId)?.close();
      const prev = get().jobs[jobId];
      set((s) => ({
        jobs: {
          ...s.jobs,
          [jobId]: {
            status: "running",
            messages: [...(prev?.messages ?? []), { role: "user", text: goal }],
            runs: prev?.runs ?? [],
            activeRunId: prev?.activeRunId ?? null,
            sessionId: prev?.sessionId ?? newId(),
          },
        },
      }));

      const ws = openFactorMonitorAgentSocket();
      sockets.set(jobId, ws);
      ws.onopen = () => ws.send(JSON.stringify({ op: "run", goal, context }));
      ws.onmessage = (e) => {
        const f = JSON.parse(e.data) as FactorMonitorAgentFrame;
        if (f.type === "thought") {
          patch(jobId, (j) => ({ ...j, messages: [...j.messages, { role: "thought", text: f.text }] }));
        } else if (f.type === "result") {
          const rec: FMRunRec = { id: f.id, kind: f.kind, label: f.label, data: f.data, params: f.params };
          patch(jobId, (j) => ({
            ...j,
            runs: [...j.runs, rec],
            activeRunId: f.id,
            messages: [...j.messages, { role: "run", runId: f.id, text: f.label }],
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
          archive(get().jobs[jobId]);
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

    stop: (jobId) => {
      sockets.get(jobId)?.close();
      sockets.delete(jobId);
      patch(jobId, (j) => ({ ...j, status: j.status === "running" ? "idle" : j.status }));
    },

    selectRun: (jobId, runId) => patch(jobId, (j) => ({ ...j, activeRunId: runId })),

    // archive the current conversation, then reset to a fresh one
    newChat: (jobId) => {
      const job = get().jobs[jobId];
      archive(job);
      sockets.get(jobId)?.close();
      sockets.delete(jobId);
      if (!job) return;
      set((s) => ({
        jobs: { ...s.jobs, [jobId]: { status: "idle", messages: [], runs: [], activeRunId: null, sessionId: newId() } },
      }));
    },

    // archive current, then load a past conversation for review (results aren't persisted, so the
    // dashboard repopulates only on the next run)
    loadSession: (jobId, session) => {
      const job = get().jobs[jobId];
      if (job && job.sessionId !== session.id) archive(job);
      sockets.get(jobId)?.close();
      sockets.delete(jobId);
      set((s) => ({
        jobs: {
          ...s.jobs,
          [jobId]: { status: "idle", messages: session.messages, runs: [], activeRunId: null, sessionId: session.id },
        },
      }));
    },
  };
    },
    {
      name: "oft-factor-agent",
      partialize: (s) => ({ jobs: lightenJobs(s.jobs) }),
    },
  ),
);
