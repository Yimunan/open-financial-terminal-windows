import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FactorMonitorAgentFrame } from "../api/types";

// The store opens its WebSocket via `openFactorMonitorAgentSocket` from ../api/client. We mock that
// single export with a controllable fake socket so the test can drive frames. Everything the mock
// references lives inside vi.hoisted, since vi.mock factories run before the module body executes.
const h = vi.hoisted(() => {
  class FakeWebSocket {
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    sent: string[] = [];
    closed = false;

    send(data: string): void {
      this.sent.push(data);
    }
    close(): void {
      this.closed = true;
      this.onclose?.();
    }
    // test helpers
    open(): void {
      this.onopen?.();
    }
    emit(frame: unknown): void {
      this.onmessage?.({ data: JSON.stringify(frame) });
    }
  }
  const sockets: InstanceType<typeof FakeWebSocket>[] = [];
  return {
    sockets,
    make: () => {
      const s = new FakeWebSocket();
      sockets.push(s);
      return s;
    },
  };
});

vi.mock("../api/client", () => ({
  openFactorMonitorAgentSocket: () => h.make(),
}));

import { useChatHistory } from "./chatHistory";
import { useFactorAgent } from "./factorAgentRuns";

const JOB = "panel-1:factor_monitor";

const boardFrame = (id = "r1"): FactorMonitorAgentFrame => ({
  type: "result",
  id,
  kind: "board",
  label: `Leaderboard · dow30 · h5 · q5`,
  params: { universe: "dow30", horizon: 5, q: 5 },
  data: { universe: "dow30", horizon: 5, q: 5, n_instruments: 30, window_start: "a", window_end: "b", rows: [], errors: [] } as never,
});

/** Latest fake socket the store opened. */
const sock = () => h.sockets[h.sockets.length - 1];
const job = () => useFactorAgent.getState().jobs[JOB];

beforeEach(() => {
  useFactorAgent.setState({ jobs: {} });
  useChatHistory.setState({ sessions: [] });
  localStorage.clear();
  h.sockets.length = 0;
});

describe("factorAgentRuns store", () => {
  it("start: running status, user message, and op:run sent on open", () => {
    useFactorAgent.getState().start(JOB, "rank the dow", { universe: "dow30" });
    expect(job().status).toBe("running");
    expect(job().messages).toEqual([{ role: "user", text: "rank the dow" }]);

    sock().open();
    expect(JSON.parse(sock().sent[0])).toEqual({ op: "run", goal: "rank the dow", context: { universe: "dow30" } });
  });

  it("result frame appends a run record, sets activeRunId, and pushes a run message", () => {
    useFactorAgent.getState().start(JOB, "rank", {});
    sock().emit(boardFrame("r1"));

    const j = job();
    expect(j.runs).toHaveLength(1);
    expect(j.runs[0]).toMatchObject({ id: "r1", kind: "board", label: "Leaderboard · dow30 · h5 · q5" });
    expect(j.activeRunId).toBe("r1");
    expect(j.messages.at(-1)).toEqual({ role: "run", runId: "r1", text: "Leaderboard · dow30 · h5 · q5" });
  });

  it("thought and obs frames append the matching message roles", () => {
    useFactorAgent.getState().start(JOB, "go", {});
    sock().emit({ type: "thought", text: "thinking" });
    sock().emit({ type: "obs", text: "saved monitor 'X'", ok: true });

    const roles = job().messages.map((m) => m.role);
    expect(roles).toEqual(["user", "thought", "obs"]);
    expect(job().messages[1].text).toBe("thinking");
    expect(job().messages[2].text).toBe("saved monitor 'X'");
  });

  it("done frame: marks best run, appends assistant summary, archives to history, closes socket", () => {
    useFactorAgent.getState().start(JOB, "rank then drill", {});
    const s = sock();
    s.emit(boardFrame("r1"));
    s.emit({ type: "done", message: "top is momentum", best_id: "r1" });

    const j = job();
    expect(j.status).toBe("done");
    expect(j.activeRunId).toBe("r1");
    const runMsg = j.messages.find((m) => m.role === "run");
    expect(runMsg?.best).toBe(true);
    expect(j.messages.at(-1)).toEqual({ role: "assistant", text: "top is momentum" });
    expect(s.closed).toBe(true);

    const sessions = useChatHistory.getState().sessions;
    expect(sessions).toHaveLength(1);
    expect(sessions[0].kind).toBe("factor_monitor");
    expect(sessions[0].title).toBe("rank then drill");
    expect(sessions[0].messages.some((m) => m.role === "assistant")).toBe(true);
  });

  it("error frame: error status, error message, socket closed", () => {
    useFactorAgent.getState().start(JOB, "rank", {});
    const s = sock();
    s.emit({ type: "error", detail: "LLM error: TimeoutError" });

    expect(job().status).toBe("error");
    expect(job().messages.at(-1)).toEqual({ role: "error", text: "LLM error: TimeoutError" });
    expect(s.closed).toBe(true);
  });

  it("selectRun switches the active result", () => {
    useFactorAgent.getState().start(JOB, "rank", {});
    sock().emit(boardFrame("r1"));
    sock().emit(boardFrame("r2"));
    expect(job().activeRunId).toBe("r2");

    useFactorAgent.getState().selectRun(JOB, "r1");
    expect(job().activeRunId).toBe("r1");
  });

  it("newChat archives the current conversation and resets to a fresh session", () => {
    useFactorAgent.getState().start(JOB, "first question", {});
    sock().emit(boardFrame("r1"));
    const oldSession = job().sessionId;

    useFactorAgent.getState().newChat(JOB);

    const j = job();
    expect(j.messages).toEqual([]);
    expect(j.runs).toEqual([]);
    expect(j.activeRunId).toBeNull();
    expect(j.status).toBe("idle");
    expect(j.sessionId).not.toBe(oldSession);
    // the prior conversation was archived
    expect(useChatHistory.getState().sessions.some((x) => x.title === "first question")).toBe(true);
  });

  it("loadSession restores a past conversation's messages", () => {
    const session = {
      id: "sess-1",
      kind: "factor_monitor" as const,
      title: "old chat",
      at: 1,
      messages: [{ role: "user" as const, text: "old chat" }, { role: "assistant" as const, text: "answer" }],
    };
    useChatHistory.setState({ sessions: [session] });
    useFactorAgent.getState().start(JOB, "current", {});

    useFactorAgent.getState().loadSession(JOB, session);

    const j = job();
    expect(j.sessionId).toBe("sess-1");
    expect(j.status).toBe("idle");
    expect(j.runs).toEqual([]);
    expect(j.messages).toEqual(session.messages);
  });
});
