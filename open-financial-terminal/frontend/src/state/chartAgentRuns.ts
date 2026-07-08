/** Background run store for the Chart Studio agent.
 *
 * Mirrors `factorAgentRuns.ts`: the WebSocket + streamed frames + accumulated messages/charts live
 * here (not in the widget) so a run survives unmount / tab switches. Each job is keyed by the
 * Dockview panel id. Each `chart` frame becomes a ChartRunRec the widget renders; the chat is
 * archived to `chatHistory` on done, and the last chart's action is fed back for refinement.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { openChartAgentSocket } from "../api/client";
import type {
  ChartAction,
  ChartAgentFrame,
  ChartEngine,
  ChartHeatmapPayload,
  ChartPricePayload,
  ChartSeriesPayload,
} from "../api/types";
import type { ChatMsg } from "../components/AgentChatPanel";
import { useChatHistory, type ChatSession } from "./chatHistory";
import { lightenJobs } from "./persistJobs";

export interface ChartRunRec {
  id: string;
  title: string;
  engine: ChartEngine;
  price?: ChartPricePayload;
  series?: ChartSeriesPayload;
  heatmap?: ChartHeatmapPayload;
  action: ChartAction;
  openParams?: Record<string, unknown> | null;
}

export interface ChartJob {
  status: "idle" | "running" | "done" | "error";
  messages: ChatMsg[];
  runs: ChartRunRec[];
  activeRunId: string | null;
  sessionId: string;
}

interface ChartAgentState {
  jobs: Record<string, ChartJob>;
  start: (jobId: string, message: string) => void;
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

function archive(job: ChartJob | undefined): void {
  if (job && job.messages.length > 0) {
    useChatHistory.getState().save({
      id: job.sessionId,
      kind: "chart_studio",
      title: titleOf(job.messages),
      at: Date.now(),
      messages: job.messages,
      runs: job.runs,
      activeRunId: job.activeRunId,
    });
  }
}

export const useChartAgent = create<ChartAgentState>()(
  persist(
    (set, get) => {
  const patch = (jobId: string, fn: (j: ChartJob) => ChartJob) =>
    set((s) => {
      const j = s.jobs[jobId];
      return j ? { jobs: { ...s.jobs, [jobId]: fn(j) } } : s;
    });

  return {
    jobs: {},

    start: (jobId, message) => {
      sockets.get(jobId)?.close();
      const prev = get().jobs[jobId];
      // Feed the last chart's action back so the agent can refine ("add RSI").
      const lastAction = prev?.runs.length ? prev.runs[prev.runs.length - 1].action : undefined;
      set((s) => ({
        jobs: {
          ...s.jobs,
          [jobId]: {
            status: "running",
            messages: [...(prev?.messages ?? []), { role: "user", text: message }],
            runs: prev?.runs ?? [],
            activeRunId: prev?.activeRunId ?? null,
            sessionId: prev?.sessionId ?? newId(),
          },
        },
      }));

      const ws = openChartAgentSocket();
      sockets.set(jobId, ws);
      ws.onopen = () =>
        ws.send(JSON.stringify({ op: "run", message, context: lastAction ? { action: lastAction } : {} }));
      ws.onmessage = (e) => {
        const f = JSON.parse(e.data) as ChartAgentFrame;
        if (f.type === "thought") {
          patch(jobId, (j) => ({ ...j, messages: [...j.messages, { role: "thought", text: f.text }] }));
        } else if (f.type === "chart") {
          const rec: ChartRunRec = {
            id: f.id,
            title: f.title,
            engine: f.engine,
            price: f.price,
            series: f.series,
            heatmap: f.heatmap,
            action: f.action,
            openParams: f.open_params ?? null,
          };
          patch(jobId, (j) => ({
            ...j,
            runs: [...j.runs, rec],
            activeRunId: f.id,
            messages: [...j.messages, { role: "run", runId: f.id, text: f.title }],
          }));
        } else if (f.type === "obs") {
          patch(jobId, (j) => ({ ...j, messages: [...j.messages, { role: "obs", text: f.text }] }));
        } else if (f.type === "done") {
          patch(jobId, (j) => ({
            ...j,
            status: "done",
            messages: [...j.messages, { role: "assistant", text: f.message }],
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

    loadSession: (jobId, session) => {
      const job = get().jobs[jobId];
      if (job && job.sessionId !== session.id) archive(job);
      sockets.get(jobId)?.close();
      sockets.delete(jobId);
      set((s) => ({
        jobs: {
          ...s.jobs,
          [jobId]: {
            status: "idle",
            messages: session.messages,
            runs: (session.runs as ChartRunRec[] | undefined) ?? [],
            activeRunId: session.activeRunId ?? null,
            sessionId: session.id,
          },
        },
      }));
    },
  };
    },
    {
      name: "oft-chart-agent",
      partialize: (s) => ({ jobs: lightenJobs(s.jobs) }),
    },
  ),
);
