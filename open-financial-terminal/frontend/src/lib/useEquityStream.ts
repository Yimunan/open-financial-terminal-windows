import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset, DepthStatus, OptionsStatus, TradesStatus } from "../api/types";

/** Whether equity real-time streaming is available (backend has Alpaca credentials).
 *
 * Reads the shared `["health"]` query (already polled by App), so widgets reactively switch between
 * the Alpaca live stream and interval polling when credentials are added/removed. When false, equity
 * widgets keep their existing polled/EOD behaviour.
 */
export function useEquityStreamEnabled(): boolean {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 30_000 });
  return !!data?.equity_stream?.enabled;
}

/** Whether crypto real-time streaming is on (Settings → Market Data → Crypto → Realtime). When off,
 * crypto widgets fall back to polling — bars/charts keep working. Reads the shared health query. */
export function useCryptoStreamEnabled(): boolean {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 30_000 });
  return data?.crypto_stream?.enabled ?? true; // default on until health loads
}

/** The order-book depth status for an asset class (Settings → Market Data → Depth source). Returns
 * the hub topic token to subscribe on and whether depth is available now. Reads the shared health
 * query, so the OrderBook widget reactively switches live/empty when the source is changed. */
export function useDepthStatus(asset: Asset): DepthStatus {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 30_000 });
  return data?.depth?.[asset] ?? { source: "none", token: "", enabled: false };
}

/** The time-&-sales (tape) status for an asset class. Returns the hub topic token to subscribe on
 * and whether a tape is available now — crypto/equity real feeds, or the simulated FICC tape. Reads
 * the shared health query, so the Time & Sales widget reactively switches live/empty per class when
 * the crypto toggle or Alpaca creds change. Mirrors `useDepthStatus`. */
export function useTradesStatus(asset: Asset): TradesStatus {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 30_000 });
  return data?.trades?.[asset] ?? { source: "none", token: "", enabled: false };
}

/** Options-chain source status (Settings → Market Data → Options). Drives the Options Chain widget's
 * live/unavailable state, the seed underlying, and whether source-native greeks are expected. Reads
 * the shared health query. */
export function useOptionsStatus(): OptionsStatus {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 30_000 });
  return (
    data?.options ?? {
      source: "none",
      enabled: false,
      capabilities: { chains: false, iv: false, greeks: false, realtime: false },
      default_underlying: "AAPL",
      expiry_window: 60,
    }
  );
}
