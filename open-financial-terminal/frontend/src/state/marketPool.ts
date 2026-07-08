/** Cross-asset correlation pools: user-defined sets of instruments (across asset classes) fed to a
 * correlation heatmap. Persisted to localStorage so a curated pool survives reloads.
 *
 * Two independent pools share one implementation: the Market Board's macro basket (yfinance tickers
 * routed as equity) and the FICC board's native-asset basket (rates/fx/commodity by clean id).
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { BoardItem } from "../api/types";

const same = (a: string, b: string) => a.toUpperCase() === b.toUpperCase();

export interface PoolState {
  pool: BoardItem[];
  add: (item: BoardItem) => void;
  remove: (symbol: string) => void;
  reset: () => void;
}

function makePoolStore(name: string, defaultPool: BoardItem[]) {
  return create<PoolState>()(
    persist(
      (set) => ({
        pool: defaultPool,
        add: (item) =>
          set((s) =>
            s.pool.some((p) => same(p.symbol, item.symbol)) ? s : { pool: [...s.pool, item] },
          ),
        remove: (symbol) => set((s) => ({ pool: s.pool.filter((p) => !same(p.symbol, symbol)) })),
        reset: () => set({ pool: defaultPool }),
      }),
      { name },
    ),
  );
}

/** Market Board pool — classic macro cross-asset basket (yfinance tickers fetched as equity). */
const MARKET_DEFAULT: BoardItem[] = [
  { symbol: "^GSPC", name: "S&P 500", asset: "equity" },
  { symbol: "^IXIC", name: "Nasdaq", asset: "equity" },
  { symbol: "GC=F", name: "Gold", asset: "equity" },
  { symbol: "CL=F", name: "WTI Crude", asset: "equity" },
  { symbol: "TLT", name: "20+Y Treasury", asset: "equity" },
  { symbol: "DX-Y.NYB", name: "US Dollar", asset: "equity" },
  { symbol: "BTC/USD", name: "Bitcoin", asset: "crypto" },
];

/** FICC board pool — native-asset cross-asset basket (rates / FX / commodity by clean id). */
const FICC_DEFAULT: BoardItem[] = [
  { symbol: "ZN", name: "10Y T-Note", asset: "rates" },
  { symbol: "ZB", name: "30Y T-Bond", asset: "rates" },
  { symbol: "EUR/USD", name: "Euro", asset: "fx" },
  { symbol: "USD/JPY", name: "Japanese Yen", asset: "fx" },
  { symbol: "GC", name: "Gold", asset: "commodity" },
  { symbol: "CL", name: "WTI Crude", asset: "commodity" },
  { symbol: "HG", name: "Copper", asset: "commodity" },
];

export const useMarketPool = makePoolStore("oft-market-pool", MARKET_DEFAULT);
export const useFiccPool = makePoolStore("oft-ficc-pool", FICC_DEFAULT);
