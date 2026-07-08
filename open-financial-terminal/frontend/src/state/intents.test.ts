import { beforeEach, describe, expect, it, vi } from "vitest";
import { dispatchIntent, sendTargets } from "./intents";
import { useLinking } from "./linking";
import { useWorkspace } from "./workspace";

/** Inject a fake Dockview api + spies into the stores. Panels may carry an `api` with the methods
 * the `send`-to-existing path uses (updateParameters/setActive). */
function setup(panels: { id: string; api?: Record<string, unknown> }[] = []) {
  const openWidget = vi.fn();
  const setContext = vi.fn();
  useWorkspace.setState({
    openWidget,
    api: { panels, getPanel: (id: string) => panels.find((p) => p.id === id) } as never,
    current: "default",
    names: ["default"],
    templates: [],
  });
  useLinking.setState({ setContext } as never);
  return { openWidget, setContext };
}

beforeEach(() => setup());

describe("dispatchIntent", () => {
  it("open: opens a widget and strips hallucinated params", async () => {
    const { openWidget } = setup([{ id: "chart-1" }]);
    const res = await dispatchIntent({ kind: "open", widget: "chart", params: { symbol: "NVDA", bogus: 1 } as never });
    expect(res.ok).toBe(true);
    const [type, params] = openWidget.mock.calls[0];
    expect(type).toBe("chart");
    expect(params).toMatchObject({ symbol: "NVDA", asset: "equity" });
    expect(params).not.toHaveProperty("bogus");
  });

  it("set_context: merges context onto the channel and ensures a chart", async () => {
    const { openWidget, setContext } = setup([]); // no chart open
    const res = await dispatchIntent({
      kind: "set_context",
      channel: "red",
      context: { symbol: "TSLA", asset: "equity", timeframe: "1h" },
    });
    expect(res.ok).toBe(true);
    expect(setContext).toHaveBeenCalledWith("red", { symbol: "TSLA", asset: "equity", timeframe: "1h" });
    expect(openWidget).toHaveBeenCalledWith("chart", { channel: "red" });
  });

  it("send screen_result → watchlist opens it with the basket params", async () => {
    const { openWidget } = setup([]); // no watchlist open
    const res = await dispatchIntent({
      kind: "send",
      target: "watchlist",
      payload: { kind: "screen_result", universe: "dow30", factor: "momentum", symbols: ["AAPL", "MSFT"], asset: "equity" },
    });
    expect(res.ok).toBe(true);
    expect(openWidget).toHaveBeenCalledWith("watchlist", { symbols: ["AAPL", "MSFT"], asset: "equity" });
  });

  it("send screen_result → backtest maps onto the incoming* params", async () => {
    const { openWidget } = setup([]);
    await dispatchIntent({
      kind: "send",
      target: "backtest",
      payload: { kind: "screen_result", universe: "sp500", factor: "value", symbols: ["XOM"], asset: "equity" },
    });
    const [type, params] = openWidget.mock.calls[0];
    expect(type).toBe("backtest");
    expect(params).toMatchObject({ btMode: "factor", incomingUniverse: "sp500", incomingFactor: "value", incomingSymbols: ["XOM"] });
  });

  it("send updates an already-open target panel instead of opening a new one", async () => {
    const updateParameters = vi.fn();
    const setActive = vi.fn();
    const { openWidget } = setup([{ id: "watchlist-1-1", api: { updateParameters, setActive } }]);
    const res = await dispatchIntent({
      kind: "send",
      target: "watchlist",
      payload: { kind: "symbols", symbols: ["BTC/USDT"], asset: "crypto" },
    });
    expect(res.ok).toBe(true);
    expect(updateParameters).toHaveBeenCalledWith({ symbols: ["BTC/USDT"], asset: "crypto" });
    expect(openWidget).not.toHaveBeenCalled();
  });

  it("send to a widget that does not accept the kind fails", async () => {
    const res = await dispatchIntent({
      kind: "send",
      target: "chart",
      payload: { kind: "symbols", symbols: ["AAPL"], asset: "equity" },
    });
    expect(res.ok).toBe(false);
  });

  it("notify returns ok with the message", async () => {
    const res = await dispatchIntent({ kind: "notify", level: "info", message: "hi" });
    expect(res).toEqual({ ok: true, result: "hi" });
  });
});

describe("sendTargets", () => {
  it("derives targets from the registry accepts map", () => {
    expect(sendTargets("screen_result").sort()).toEqual(["backtest", "watchlist"]);
    expect(sendTargets("backtest_result").sort()).toEqual(["paper", "strategies"]);
    expect(sendTargets("symbols").sort()).toEqual(["options_chain", "options_surface", "watchlist"]);
  });
});
