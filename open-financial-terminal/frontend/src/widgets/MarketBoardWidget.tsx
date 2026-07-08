import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BoardItem } from "../api/types";
import FlashCell from "../components/FlashCell";
import Sparkline from "../components/Sparkline";
import { cx, fmtPct, fmtPrice, upDownClass } from "../lib/format";
import { useT } from "../lib/i18n";
import { equityTopics, subscribeStream, topics } from "../lib/wsClient";
import { useCryptoStreamEnabled, useEquityStreamEnabled } from "../lib/useEquityStream";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState, ErrorState } from "../components/States";
import CorrelationPanel from "./CorrelationPanel";

/** Sentinel tab key for the cross-asset correlation view (not an asset-class section). */
const CORR = "__corr__";

/** One board row: crypto (and, with Alpaca creds, equity) rows ride the live ticker; otherwise the
 * quote is polled (delayed). Polling stays as a fallback for the price/sparkline either way. */
function Row({ item, active, onPick }: { item: BoardItem; active: boolean; onPick: () => void }) {
  const equityStream = useEquityStreamEnabled();
  const cryptoStream = useCryptoStreamEnabled();
  const streamed = (item.asset === "crypto" && cryptoStream) || (item.asset === "equity" && equityStream);
  const { data: q } = useQuery({
    queryKey: ["quote", item.symbol, item.asset],
    queryFn: () => api.quote(item.symbol, item.asset, 30),
    refetchInterval: 60_000,
    retry: 1,
  });

  const [live, setLive] = useState<{ last: number | null; change_pct: number | null } | null>(null);
  useEffect(() => {
    if (!streamed) return;
    const topic = item.asset === "crypto" ? topics.ticker(item.symbol) : equityTopics.ticker(item.symbol);
    return subscribeStream(topic, (frame) => {
      if (frame.type === "ticker") setLive({ last: frame.data.last, change_pct: frame.data.change_pct });
    });
  }, [item.symbol, item.asset, streamed]);

  const price = live?.last ?? q?.price ?? null;
  const pct = live?.change_pct ?? q?.change_pct ?? null;

  return (
    <tr
      onClick={onPick}
      className={cx(
        "group cursor-pointer border-b border-term-border/40 hover:bg-term-border/30",
        active && "bg-term-border/40",
      )}
    >
      <td className="px-2 py-1">
        <div className="flex items-center gap-1.5">
          {streamed && (
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-term-up" title="live" />
          )}
          <span className="font-mono text-xs font-semibold">{item.symbol}</span>
          <span className="truncate text-[10px] text-term-muted">{item.name}</span>
        </div>
      </td>
      <td className="px-1 py-1">
        <Sparkline points={q?.spark} />
      </td>
      <td className="px-2 py-1 text-right font-mono text-xs">
        <FlashCell value={price}>{fmtPrice(price)}</FlashCell>
      </td>
      <td className={cx("px-2 py-1 text-right font-mono text-xs", upDownClass(pct))}>
        <FlashCell value={pct}>{fmtPct(pct)}</FlashCell>
      </td>
    </tr>
  );
}

/** Multi-asset market overview: one tile, a tab per asset class (indices · commodities · bonds ·
 * FX · crypto). yfinance tabs poll EOD/delayed quotes; the crypto tab streams live from the hub.
 * Clicking a row pushes its symbol to the widget's link channel, retargeting linked Chart/News. */
export default function MarketBoardWidget(props: WidgetProps) {
  const { symbol: activeSymbol, channel, setChannel, setSymbol } = useWidgetSymbol(props);
  const t = useT();
  const equityStream = useEquityStreamEnabled();
  const cryptoStream = useCryptoStreamEnabled();
  const { data, isLoading, error } = useQuery({
    queryKey: ["board"],
    queryFn: api.board,
    staleTime: Infinity,
  });

  const sections = data?.sections ?? [];
  const [tabKey, setTabKey] = useState<string | null>(null);
  const isCorr = tabKey === CORR;
  const active = isCorr ? undefined : sections.find((s) => s.key === tabKey) ?? sections[0];
  // "live" when the active tab streams: crypto always; equity-class tabs when Alpaca creds are set.
  const activeLive = !isCorr && ((cryptoStream && active?.key === "crypto") || (equityStream && active?.items[0]?.asset === "equity"));

  const tabClass = (on: boolean) =>
    cx(
      "rounded px-2 py-0.5 text-[10px] uppercase tracking-wide",
      on ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
    );

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={activeLive ? "live" : "delayed"}
      toolbar={
        <div className="flex flex-wrap items-center gap-1">
          {sections.map((s) => (
            <button key={s.key} onClick={() => setTabKey(s.key)} className={tabClass(!isCorr && active?.key === s.key)}>
              {s.label}
            </button>
          ))}
          {sections.length > 0 && (
            <button onClick={() => setTabKey(CORR)} className={tabClass(isCorr)}>
              {t("board.correlation")}
            </button>
          )}
        </div>
      }
    >
      {isLoading && <SkeletonRows />}
      {error && <ErrorState message={(error as Error).message} />}
      {isCorr && <CorrelationPanel />}
      {!isCorr && active && active.items.length === 0 && <EmptyState title={t("board.empty")} />}
      {!isCorr && active && active.items.length > 0 && (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
              <th className="px-2 py-1 text-left font-medium">{t("common.symbol")}</th>
              <th className="px-1 py-1 text-left font-medium">30d</th>
              <th className="px-2 py-1 text-right font-medium">{t("common.last")}</th>
              <th className="px-2 py-1 text-right font-medium">{t("common.chg")}</th>
            </tr>
          </thead>
          <tbody>
            {active.items.map((item) => (
              <Row
                key={item.symbol}
                item={item}
                active={item.symbol === activeSymbol}
                onPick={() => setSymbol({ symbol: item.symbol, asset: item.asset })}
              />
            ))}
          </tbody>
        </table>
      )}
    </WidgetShell>
  );
}
