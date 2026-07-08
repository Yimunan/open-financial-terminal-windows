import { describe, expect, it } from "vitest";
import { depthBookTopic } from "./wsClient";

/** depthBookTopic builds a book topic from a backend-supplied token, or null when depth is off.
 * The token encodes the source+asset (e.g. "sim.equity") or a ccxt exchange id (crypto real L2);
 * the frontend never constructs it — this just joins it into the hub's kind:exchange:symbol scheme. */
describe("depthBookTopic", () => {
  it("builds book:<token>:<symbol> for a simulated per-class token", () => {
    expect(depthBookTopic("AAPL", "sim.equity")).toBe("book:sim.equity:AAPL");
    expect(depthBookTopic("ZN", "sim.rates")).toBe("book:sim.rates:ZN");
  });

  it("preserves slash symbols (FX / crypto pairs)", () => {
    expect(depthBookTopic("EUR/USD", "sim.fx")).toBe("book:sim.fx:EUR/USD");
    expect(depthBookTopic("BTC/USDT", "kraken")).toBe("book:kraken:BTC/USDT");
  });

  it("returns null when the token is empty (depth off) so the widget doesn't subscribe", () => {
    expect(depthBookTopic("AAPL", "")).toBeNull();
  });
});
