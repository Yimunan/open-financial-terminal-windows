/** Channel-group linking: the classic terminal pattern where widgets on the same color
 * channel share an active symbol. Clicking AAPL in a channel-red watchlist retargets
 * every channel-red chart/news/book widget. 'none' widgets keep a local symbol in
 * their Dockview panel params instead.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Asset, Timeframe } from "../api/types";

export type Channel = "none" | "red" | "blue" | "green";
export const LINK_CHANNELS: Exclude<Channel, "none">[] = ["red", "blue", "green"];

export const CHANNEL_DOT: Record<Exclude<Channel, "none">, string> = {
  red: "#ef5350",
  blue: "#4f9cf9",
  green: "#34d399",
};

/** The minimal payload every linked widget needs. Kept as the historical shape so `useWidgetSymbol`
 * and the persisted store stay byte-compatible. */
export interface SymbolRef {
  symbol: string;
  asset: Asset;
}

/** The full context that travels with a channel. A strict SUPERSET of `SymbolRef`, so every
 * previously-persisted `{symbol, asset}` is already a valid ChannelContext — the persist migration
 * is just a version stamp. Widgets opt in to the richer fields via `useChannelContext`. */
export interface ChannelContext extends SymbolRef {
  timeframe?: Timeframe;
  range?: { start: string; end: string }; // ISO dates
  universe?: string;
  factor?: string;
  extra?: Record<string, unknown>;
}

interface LinkingState {
  symbols: Record<Exclude<Channel, "none">, ChannelContext>;
  /** Set only the symbol/asset on a channel, preserving any richer context already there. */
  setSymbol: (channel: Exclude<Channel, "none">, ref: SymbolRef) => void;
  /** Merge a (partial) context onto a channel — set just the fields you provide, keep the rest. So
   * retargeting the symbol preserves an existing timeframe/range. Used by `set_context` intents. */
  setContext: (channel: Exclude<Channel, "none">, ctx: Partial<ChannelContext>) => void;
  /** Seed the channel tickers from the per-asset-class default symbols (Settings → Market Data).
   * Used to initialise channels on first launch; see App's first-run guard. */
  seedDefaults: (equity: string, crypto: string) => void;
}

export const useLinking = create<LinkingState>()(
  persist(
    (set) => ({
      symbols: {
        red: { symbol: "AAPL", asset: "equity" },
        blue: { symbol: "BTC/USDT", asset: "crypto" },
        green: { symbol: "NVDA", asset: "equity" },
      },
      // Merge so a symbol pick keeps the channel's timeframe/range/etc.
      setSymbol: (channel, ref) =>
        set((s) => ({ symbols: { ...s.symbols, [channel]: { ...s.symbols[channel], ...ref } } })),
      setContext: (channel, ctx) =>
        set((s) => ({ symbols: { ...s.symbols, [channel]: { ...s.symbols[channel], ...ctx } } })),
      seedDefaults: (equity, crypto) =>
        set({
          symbols: {
            red: { symbol: equity, asset: "equity" },
            blue: { symbol: crypto, asset: "crypto" },
            green: { symbol: equity, asset: "equity" },
          },
        }),
    }),
    {
      name: "oft-linking",
      // v0 persisted `{symbols: Record<channel, SymbolRef>}`. SymbolRef ⊂ ChannelContext, so old
      // blobs are already valid — the migration is a version stamp (no field renames).
      version: 1,
      migrate: (persisted) => persisted as LinkingState,
    },
  ),
);
