/** Persisted chat history for the backtest agents.
 *
 * Each completed (or archived) conversation is saved here — the messages AND the results (runs +
 * activeRunId) — persisted to localStorage so it survives reloads. The History sub-window lists
 * them; reopening one restores both the conversation and the dashboard/chart it produced. Deleting
 * a session removes its results too, since they're stored on the session (no orphans).
 *
 * Note: results carry heavy arrays (equity curves, candles). localStorage is ~5MB/origin, so a
 * very large or numerous set of sessions can exceed quota — a failed write keeps the prior value.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ChatMsg } from "../components/AgentChatPanel";
import type { RunKind } from "./agentRuns";

export interface ChatSession {
  id: string;
  kind: RunKind;
  title: string;
  at: number; // epoch ms
  messages: ChatMsg[];
  // Persisted results so reopening a session restores the dashboard/chart, not just the chat.
  // Typed loosely because each agent kind has its own run-record shape; the owning store casts.
  // Deleting the session drops these too (results are owned by the session, no orphans).
  runs?: unknown[];
  activeRunId?: string | null;
}

interface ChatHistoryState {
  sessions: ChatSession[];
  save: (session: ChatSession) => void;
  remove: (id: string) => void;
}

const MAX_SESSIONS = 40;

export const useChatHistory = create<ChatHistoryState>()(
  persist(
    (set) => ({
      sessions: [],
      save: (session) =>
        set((s) => ({
          sessions: [session, ...s.sessions.filter((x) => x.id !== session.id)].slice(0, MAX_SESSIONS),
        })),
      remove: (id) => set((s) => ({ sessions: s.sessions.filter((x) => x.id !== id) })),
    }),
    { name: "oft-chat-history" },
  ),
);
