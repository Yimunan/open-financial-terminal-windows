import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { WatchItem } from "../api/types";
import FlashCell from "../components/FlashCell";
import Sparkline from "../components/Sparkline";
import ContextMenu, { type MenuItem } from "../components/ContextMenu";
import QuoteStrip from "../components/QuoteStrip";
import { cx, fmtPct, fmtPrice, upDownClass } from "../lib/format";
import { useT } from "../lib/i18n";
import { equityTopics, subscribeStream, topics } from "../lib/wsClient";
import { useCryptoStreamEnabled, useEquityStreamEnabled } from "../lib/useEquityStream";
import { useWorkspace } from "../state/workspace";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { IconButton, SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState } from "../components/States";

/** One row: equities poll the quote (EOD); crypto rows ride the live ticker stream.
 * `showBidAsk` controls only the live NBBO bid/ask sub-line under the price — the price/change/
 * sparkline columns always show. */
function Row({
  item,
  active,
  showBidAsk,
  dragging,
  over,
  onPick,
  onMenu,
  onRemove,
  onDragStart,
  onDragEnter,
  onDrop,
  onDragEnd,
}: {
  item: WatchItem;
  active: boolean;
  showBidAsk: boolean;
  dragging: boolean;
  over: boolean;
  onPick: () => void;
  onMenu: (e: React.MouseEvent) => void;
  onRemove: () => void;
  onDragStart: () => void;
  onDragEnter: () => void;
  onDrop: () => void;
  onDragEnd: () => void;
}) {
  const equityStream = useEquityStreamEnabled();
  const cryptoStream = useCryptoStreamEnabled();
  const streamed = (item.asset === "crypto" && cryptoStream) || (item.asset === "equity" && equityStream);
  const { data: q } = useQuery({
    queryKey: ["quote", item.symbol, item.asset],
    queryFn: () => api.quote(item.symbol, item.asset, 30),
    refetchInterval: 60_000,
    retry: 1,
  });

  const [live, setLive] = useState<{
    last: number | null; change_pct: number | null;
    bid: number | null; ask: number | null; bid_size: number | null; ask_size: number | null;
  } | null>(null);
  useEffect(() => {
    if (!streamed) return;
    const topic = item.asset === "crypto" ? topics.ticker(item.symbol) : equityTopics.ticker(item.symbol);
    return subscribeStream(topic, (frame) => {
      if (frame.type === "ticker") {
        const d = frame.data;
        setLive({ last: d.last, change_pct: d.change_pct, bid: d.bid, ask: d.ask, bid_size: d.bid_size, ask_size: d.ask_size });
      }
    });
  }, [item.symbol, item.asset, streamed]);

  const price = live?.last ?? q?.price ?? null;
  const pct = live?.change_pct ?? q?.change_pct ?? null;

  return (
    <tr
      draggable
      onClick={onPick}
      onContextMenu={onMenu}
      onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; onDragStart(); }}
      onDragEnter={onDragEnter}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => { e.preventDefault(); onDrop(); }}
      onDragEnd={onDragEnd}
      className={cx(
        "group cursor-pointer border-b border-term-border/40 hover:bg-term-border/30",
        active && "bg-term-border/40",
        dragging && "opacity-40",
        over && "border-t-2 border-t-term-accent",
      )}
    >
      <td className="px-2 py-1">
        <div className="flex items-center gap-1.5">
          <span
            className="cursor-grab select-none text-term-muted opacity-0 transition-opacity group-hover:opacity-60"
            title="Drag to reorder"
            aria-hidden
          >
            ⠿
          </span>
          {streamed && (
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-term-up" title="live" />
          )}
          <span className="font-mono text-xs font-semibold">{item.symbol}</span>
        </div>
      </td>
      <td className="px-1 py-1">
        <Sparkline points={q?.spark} />
      </td>
      <td className="px-2 py-1 text-right font-mono text-xs">
        <FlashCell value={price}>{fmtPrice(price)}</FlashCell>
        {showBidAsk && live && live.bid != null && live.ask != null && (
          <div className="flex justify-end">
            <QuoteStrip bid={live.bid} ask={live.ask} bidSize={live.bid_size} askSize={live.ask_size} />
          </div>
        )}
      </td>
      <td className={cx("px-2 py-1 text-right font-mono text-xs", upDownClass(pct))}>
        <FlashCell value={pct}>{fmtPct(pct)}</FlashCell>
      </td>
      <td className="w-5 px-1 py-1 text-right">
        <IconButton
          label={`Remove ${item.symbol} from watchlist`}
          title="Remove from watchlist"
          danger
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="opacity-40 transition-opacity group-hover:opacity-100"
        >
          ×
        </IconButton>
      </td>
    </tr>
  );
}

export default function WatchlistWidget(props: WidgetProps) {
  const { symbol: activeSymbol, channel, setChannel, setSymbol } = useWidgetSymbol(props);
  const t = useT();
  const openWidget = useWorkspace((s) => s.openWidget);
  const qc = useQueryClient();

  // Per-widget: show the live NBBO bid/ask sub-line under each price (default on). Persisted in
  // the panel's params. The price/change/sparkline columns are always shown.
  const showBidAsk = (props.params.showBidAsk as boolean | undefined) ?? true;
  const toggleBidAsk = () => props.api.updateParameters({ showBidAsk: !showBidAsk });

  const { data, isLoading } = useQuery({ queryKey: ["watchlist"], queryFn: api.watchlist });
  const add = useMutation({
    mutationFn: ({ symbol, asset }: WatchItem) => api.addWatch(symbol, asset),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  const remove = useMutation({
    mutationFn: (symbol: string) => api.removeWatch(symbol),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });

  // Drag-to-reorder: optimistically reorder the cached list, then persist the new order.
  const reorder = useMutation({
    mutationFn: (order: string[]) => api.reorderWatch(order),
    onMutate: async (order) => {
      await qc.cancelQueries({ queryKey: ["watchlist"] });
      const prev = qc.getQueryData<{ items: WatchItem[] }>(["watchlist"]);
      if (prev) {
        const bySym = new Map(prev.items.map((i) => [i.symbol, i]));
        const items = order.map((s) => bySym.get(s)).filter((x): x is WatchItem => !!x);
        qc.setQueryData(["watchlist"], { ...prev, items });
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(["watchlist"], ctx.prev); },
    onSettled: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  const [dragSym, setDragSym] = useState<string | null>(null);
  const [overSym, setOverSym] = useState<string | null>(null);
  const dropOnto = (targetSym: string) => {
    const src = dragSym;
    setDragSym(null);
    setOverSym(null);
    if (!src || src === targetSym || !data) return;
    const order = data.items.map((i) => i.symbol);
    const from = order.indexOf(src);
    const to = order.indexOf(targetSym);
    if (from < 0 || to < 0) return;
    order.splice(from, 1);
    order.splice(to, 0, src);
    reorder.mutate(order);
  };

  // Consume a basket handed off from another module (Screener "Add to Watchlist", or any `symbols`
  // payload): add each symbol once on mount, then clear the param so re-renders don't re-add.
  const consumedIncoming = useRef(false);
  useEffect(() => {
    const incoming = props.params.symbols;
    if (incoming?.length && !consumedIncoming.current) {
      consumedIncoming.current = true;
      const asset = props.params.asset ?? "equity";
      for (const s of incoming) add.mutate({ symbol: s, asset });
      props.api.updateParameters({ symbols: undefined });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [adding, setAdding] = useState("");
  const { data: hits } = useQuery({
    queryKey: ["search", adding],
    queryFn: () => api.search(adding),
    enabled: adding.trim().length >= 1,
    staleTime: 60_000,
  });

  const [menu, setMenu] = useState<{ x: number; y: number; item: WatchItem } | null>(null);

  const menuItems = (item: WatchItem): MenuItem[] => {
    const pick = () => setSymbol({ symbol: item.symbol, asset: item.asset });
    const items: MenuItem[] = [
      { label: t("watch.openChart"), onClick: () => { pick(); openWidget("chart", { channel }); } },
      { label: t("watch.openProfile"), onClick: () => { pick(); openWidget("metrics", { channel }); } },
      { label: t("watch.openNews"), onClick: () => { pick(); openWidget("news", { channel }); } },
    ];
    if (item.asset === "crypto") {
      items.push(
        { label: t("watch.openBook"), onClick: () => { pick(); openWidget("orderbook", { channel }); } },
        { label: t("watch.openTape"), onClick: () => { pick(); openWidget("timesales", { channel }); } },
      );
    }
    items.push({ label: t("common.remove"), danger: true, onClick: () => remove.mutate(item.symbol) });
    return items;
  };

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <div className="relative flex flex-1 items-center gap-1.5">
          <input
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            placeholder={t("watch.add")}
            aria-label={t("watch.add")}
            className="focus-ring w-full max-w-[180px] rounded border border-term-border bg-term-sunken px-2 py-1 text-xs placeholder:text-term-muted focus:border-term-accent"
          />
          <button
            onClick={toggleBidAsk}
            title={showBidAsk ? "Hide bid/ask under price" : "Show bid/ask under price"}
            aria-label={showBidAsk ? "Hide bid/ask under price" : "Show bid/ask under price"}
            aria-pressed={showBidAsk}
            className={cx(
              "ml-auto shrink-0 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide transition-colors",
              showBidAsk
                ? "border-term-accent text-term-accent"
                : "border-term-border text-term-muted hover:text-term-text",
            )}
          >
            Bid/Ask
          </button>
          {adding && (hits?.results.length ?? 0) > 0 && (
            <div className="absolute left-0 top-6 z-40 max-h-56 min-w-[220px] overflow-auto rounded border border-term-border bg-term-elev shadow-elev-2">
              {hits!.results.map((h) => (
                <button
                  key={`${h.symbol}-${h.universe}`}
                  onClick={() => {
                    add.mutate({ symbol: h.symbol, asset: h.asset });
                    setAdding("");
                  }}
                  className="focus-ring flex w-full items-center justify-between px-2 py-1.5 text-xs hover:bg-term-border/50"
                >
                  <span className="font-mono font-semibold">{h.symbol}</span>
                  <span className="truncate pl-2 text-term-muted">{h.asset} · {h.sector ?? h.name ?? h.universe}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      }
    >
      {isLoading && <SkeletonRows />}
      {data && data.items.length === 0 && <EmptyState title={t("watch.empty")} />}
      {data && data.items.length > 0 && (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
              <th className="px-2 py-1 text-left font-medium">{t("common.symbol")}</th>
              <th className="px-1 py-1 text-left font-medium">30d</th>
              <th className="px-2 py-1 text-right font-medium">{t("common.last")}</th>
              <th className="px-2 py-1 text-right font-medium">{t("common.chg")}</th>
              <th className="w-5" />
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <Row
                key={item.symbol}
                item={item}
                active={item.symbol === activeSymbol}
                showBidAsk={showBidAsk}
                dragging={dragSym === item.symbol}
                over={overSym === item.symbol && dragSym !== item.symbol}
                onPick={() => setSymbol({ symbol: item.symbol, asset: item.asset })}
                onMenu={(e) => {
                  e.preventDefault();
                  setMenu({ x: e.clientX, y: e.clientY, item });
                }}
                onRemove={() => remove.mutate(item.symbol)}
                onDragStart={() => setDragSym(item.symbol)}
                onDragEnter={() => setOverSym(item.symbol)}
                onDrop={() => dropOnto(item.symbol)}
                onDragEnd={() => { setDragSym(null); setOverSym(null); }}
              />
            ))}
          </tbody>
        </table>
      )}
      {menu && <ContextMenu x={menu.x} y={menu.y} items={menuItems(menu.item)} onClose={() => setMenu(null)} />}
    </WidgetShell>
  );
}
