import { beforeEach, describe, expect, it, vi } from "vitest";
import { executeAction } from "./terminalActions";
import { useLinking } from "./linking";
import { useWorkspace } from "./workspace";
import { snapshotWorkspace } from "../lib/terminalState";
import { buildAssistantCapabilities } from "../workspace/widgetRegistry";

/** Inject a fake Dockview api + spies into the stores (no real panels in a unit test). */
function setup(panels: { id: string; params?: Record<string, unknown> }[] = []) {
  const openWidget = vi.fn();
  const setSymbol = vi.fn();
  const setContext = vi.fn();
  useWorkspace.setState({ openWidget, api: { panels } as never });
  useLinking.setState({ setSymbol, setContext } as never);
  return { openWidget, setSymbol, setContext };
}

beforeEach(() => {
  useWorkspace.setState({ current: "default", names: ["default"], templates: [] });
});

describe("executeAction", () => {
  it("open_widget opens a known assistant widget and filters unknown params", async () => {
    const { openWidget } = setup([{ id: "chart-1" }]);
    const res = await executeAction("open_widget", { type: "chart", params: { symbol: "NVDA", bogus: 1 } });
    expect(res.ok).toBe(true);
    expect(openWidget).toHaveBeenCalledTimes(1);
    const [type, params] = openWidget.mock.calls[0];
    expect(type).toBe("chart");
    expect(params).toMatchObject({ symbol: "NVDA", asset: "equity" }); // asset inferred
    expect(params).not.toHaveProperty("bogus"); // hallucinated key dropped
  });

  it("open_widget coerces an indicators string into an array", async () => {
    const { openWidget } = setup([{ id: "chart-1" }]);
    await executeAction("open_widget", { type: "chart", params: { indicators: "sma:50, rsi:14" } });
    expect(openWidget.mock.calls[0][1].indicators).toEqual(["sma:50", "rsi:14"]);
  });

  it("open_widget refuses a type without an assistant entry", async () => {
    const { openWidget } = setup();
    const res = await executeAction("open_widget", { type: "market_board" });
    expect(res.ok).toBe(false);
    expect(openWidget).not.toHaveBeenCalled();
  });

  it("set_symbol retargets the channel (crypto inferred) and ensures a chart exists", async () => {
    const { openWidget, setContext } = setup([]); // no chart open
    const res = await executeAction("set_symbol", { channel: "blue", symbol: "BTC/USDT" });
    expect(res.ok).toBe(true);
    // set_symbol is unified onto a set_context intent → setContext (merge), preserving any
    // existing timeframe/range on the channel.
    expect(setContext).toHaveBeenCalledWith("blue", { symbol: "BTC/USDT", asset: "crypto" });
    expect(openWidget).toHaveBeenCalledWith("chart", { channel: "blue" });
  });

  it("set_symbol does not add a chart when one is already open", async () => {
    const { openWidget } = setup([{ id: "chart-9" }]);
    await executeAction("set_symbol", { symbol: "TSLA" }); // channel defaults to red
    expect(openWidget).not.toHaveBeenCalled();
  });

  it("switch_workspace rejects an unknown name", async () => {
    setup();
    const res = await executeAction("switch_workspace", { name: "ghost" });
    expect(res.ok).toBe(false);
  });

  it("read_workspace returns a workspace snapshot", async () => {
    setup([{ id: "chart-1", params: { symbol: "AAPL" } }]);
    const res = await executeAction("read_workspace", {});
    expect(res.ok).toBe(true);
    expect((res.result as { workspace: unknown }).workspace).toBeTruthy();
  });

  it("rejects an action outside the allowlist", async () => {
    const res = await executeAction("submit_order", { symbol: "AAPL", qty: 100 });
    expect(res.ok).toBe(false);
  });
});

describe("snapshotWorkspace", () => {
  it("derives panel type from the id and keeps only whitelisted params", () => {
    useWorkspace.setState({
      api: { panels: [{ id: "chart-123-1", params: { symbol: "AAPL", timeframe: "1d", junk: 9 } }] } as never,
      current: "default", names: ["default"], templates: [],
    });
    useLinking.setState({
      symbols: {
        red: { symbol: "AAPL", asset: "equity" },
        blue: { symbol: "BTC/USDT", asset: "crypto" },
        green: { symbol: "NVDA", asset: "equity" },
      },
    } as never);
    const snap = snapshotWorkspace();
    expect(snap.panels[0].type).toBe("chart");
    expect(snap.panels[0].params).toEqual({ symbol: "AAPL", timeframe: "1d" });
    expect(snap.channels.red.symbol).toBe("AAPL");
  });
});

describe("buildAssistantCapabilities", () => {
  it("exposes the action verbs and a registry-derived widget catalog", () => {
    const caps = buildAssistantCapabilities();
    expect(caps.actions).toContain("open_widget");
    expect(caps.actions).toContain("read_workspace");
    const chart = caps.widgets.find((w) => w.type === "chart");
    expect(chart?.params).toHaveProperty("timeframe");
    // a widget without an assistant entry is not offered
    expect(caps.widgets.some((w) => w.type === "market_board")).toBe(false);
  });
});
