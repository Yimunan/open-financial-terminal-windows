import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChartAgentFrame } from "../api/types";

// Mirror factorAgentRuns.test.ts: a controllable fake socket so the test can drive frames.
const h = vi.hoisted(() => {
  class FakeWebSocket {
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    sent: string[] = [];
    closed = false;
    send(data: string): void { this.sent.push(data); }
    close(): void { this.closed = true; this.onclose?.(); }
    open(): void { this.onopen?.(); }
    emit(frame: unknown): void { this.onmessage?.({ data: JSON.stringify(frame) }); }
  }
  const sockets: InstanceType<typeof FakeWebSocket>[] = [];
  return { sockets, make: () => { const s = new FakeWebSocket(); sockets.push(s); return s; } };
});

vi.mock("../api/client", () => ({ openChartAgentSocket: () => h.make() }));

import { useChatHistory } from "./chatHistory";
import { useChartAgent } from "./chartAgentRuns";
import { lightenJobs } from "./persistJobs";

const JOB = "panel-1:chart_studio";
const sock = () => h.sockets[h.sockets.length - 1];
const job = () => useChartAgent.getState().jobs[JOB];

const chartFrame = (id: string): ChartAgentFrame => ({
  type: "chart", id, title: "AAPL daily", engine: "price",
  action: { tool: "price_chart", args: { symbol: "AAPL" } }, open_params: null,
});

/** Run one full turn (open → chart → done) on the latest socket. */
function turn(message: string, chartId: string, reply: string): void {
  useChartAgent.getState().start(JOB, message);
  sock().open();
  sock().emit(chartFrame(chartId));
  sock().emit({ type: "done", message: reply });
}

beforeEach(() => {
  useChartAgent.setState({ jobs: {} });
  useChatHistory.setState({ sessions: [] });
  localStorage.clear();
  h.sockets.length = 0;
});

describe("chart studio history dedup", () => {
  it("a continuous chat across turns stays a single history row", () => {
    turn("AAPL candles", "c1", "here you go");
    turn("add RSI", "c2", "added RSI");
    expect(useChatHistory.getState().sessions).toHaveLength(1);
  });

  it("resuming the same chat after a reload reuses the session — still one row", async () => {
    turn("AAPL candles", "c1", "here you go");
    const sessionId = job().sessionId;

    // Simulate a page reload: snapshot the persisted storage, wipe in-memory state (which also
    // rewrites storage), restore the pre-reload snapshot, then rehydrate as a fresh load would.
    // (Before the fix, jobs were never persisted, so this minted a fresh sessionId → a 2nd row.)
    const snapshot = localStorage.getItem("oft-chart-agent");
    expect(snapshot).toContain(sessionId); // partialize persisted the session
    useChartAgent.setState({ jobs: {} });
    localStorage.setItem("oft-chart-agent", snapshot as string);
    await useChartAgent.persist.rehydrate();

    expect(job()?.sessionId).toBe(sessionId);
    expect(job()?.messages.length).toBeGreaterThan(0);
    expect(job()?.runs).toEqual([]); // heavy run payloads intentionally not persisted

    turn("add RSI", "c2", "added RSI");
    expect(useChatHistory.getState().sessions).toHaveLength(1);
  });
});

describe("lightenJobs", () => {
  it("keeps sessionId + messages, drops runs/activeRunId/running status, and omits empty jobs", () => {
    const out = lightenJobs({
      a: { status: "running", messages: [{ role: "user", text: "hi" }], runs: [1, 2], activeRunId: "r2", sessionId: "S1" },
      b: { status: "done", messages: [], runs: [9], activeRunId: "r9", sessionId: "S2" },
    });
    expect(Object.keys(out)).toEqual(["a"]); // empty job b omitted
    expect(out.a).toEqual({ status: "idle", messages: [{ role: "user", text: "hi" }], runs: [], activeRunId: null, sessionId: "S1" });
  });
});
