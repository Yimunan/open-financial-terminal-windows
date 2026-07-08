import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { OptionChainResponse, OptionQuote, OptionRight } from "../api/types";
import { cx, fmtCompact, fmtPct, fmtPrice, upDownClass } from "../lib/format";
import { useOptionsStatus } from "../lib/useEquityStream";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState } from "../components/States";

const SELECT_CLS = "focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-muted";
const pctIv = (v: number | null) => (v == null ? "—" : fmtPct(v * 100, false));
const num = (v: number | null, d = 3) => (v == null ? "—" : v.toFixed(d));
const RANGE_OPTS = [0, 25, 15, 8]; // ±% of spot to show; 0 = all strikes

type ColView = "price" | "greeks";
type BarMetric = "oi" | "vol";
type ColKey = "flow" | "bid" | "ask" | "iv" | "delta" | "gamma" | "theta" | "vega";
/** One leg of the trade ticket: a contract (right+strike) plus its buy/sell side. */
type Leg = { right: OptionRight; strike: number; side: "buy" | "sell" };

// Columns are ordered outer→inner (from the far edge toward the centre strike); the "signal"
// column (IV / Δ) hugs the strike. The call side renders this order left→right; the put side
// reverses it, so the layout mirrors around the strike and the header always matches the body.
const PRICE_COLS: ColKey[] = ["flow", "bid", "ask", "iv"];
const GREEK_COLS: ColKey[] = ["vega", "theta", "gamma", "delta"];
const colsFor = (v: ColView): ColKey[] => (v === "greeks" ? GREEK_COLS : PRICE_COLS);
const COL_LABEL: Record<Exclude<ColKey, "flow">, string> = {
  bid: "Bid", ask: "Ask", iv: "IV", delta: "Δ", gamma: "Γ", theta: "Θ", vega: "V",
};
const colLabel = (k: ColKey, metric: BarMetric) => (k === "flow" ? (metric === "oi" ? "OI" : "Vol") : COL_LABEL[k]);

interface Cell { text: string; cls?: string; bar?: { frac: number; intensity: number } }

/** One cell's text (+ optional sign colour, + optional OI/volume depth bar). */
function cellFor(k: ColKey, q: OptionQuote | undefined, metric: BarMetric, barMax: number, barMedian: number): Cell {
  switch (k) {
    case "flow": {
      const v = metric === "oi" ? q?.open_interest ?? null : q?.volume ?? null;
      if (v == null || v <= 0) return { text: fmtCompact(v) };
      const frac = barMax > 0 ? v / barMax : 0;
      let intensity = Math.min(0.1 + (v / (barMedian * 4)) * 0.5, 0.6);
      if (barMedian > 0 && v >= 3 * barMedian) intensity = Math.max(intensity, 0.42); // "wall" floor
      return { text: fmtCompact(v), bar: { frac, intensity } };
    }
    case "bid": return { text: fmtPrice(q?.bid ?? null) };
    case "ask": return { text: fmtPrice(q?.ask ?? null) };
    case "iv": return { text: pctIv(q?.iv ?? null) };
    case "delta": return { text: num(q?.delta ?? null, 2), cls: upDownClass(q?.delta ?? null) };
    case "gamma": return { text: num(q?.gamma ?? null, 3) };
    case "theta": return { text: num(q?.theta ?? null, 3), cls: upDownClass(q?.theta ?? null) };
    case "vega": return { text: num(q?.vega ?? null, 3) };
  }
}

/** One side of the ladder (calls or puts) for a strike. ITM shading + OI/volume depth bars are
 * side-aware: call side → up token (bar anchored to the outer/left edge), put side → down token. */
function SideCells({ q, cols, align, itm, onSelect, selected, metric, barMax, barMedian }: {
  q?: OptionQuote; cols: ColKey[]; align: "left" | "right"; itm?: boolean;
  onSelect?: () => void; selected?: boolean; metric: BarMetric; barMax: number; barMedian: number;
}) {
  const cells = cols.map((k) => cellFor(k, q, metric, barMax, barMedian));
  const ordered = align === "right" ? [...cells].reverse() : cells;
  const itmTint = itm && (align === "left" ? "bg-term-up/10" : "bg-term-down/10");
  const barToken = align === "left" ? "--term-up" : "--term-down";
  return (
    <>
      {ordered.map((c, i) => (
        <td
          key={i}
          onClick={onSelect}
          className={cx(
            "relative px-1.5 py-0.5 tabular-nums",
            align === "right" ? "text-right" : "text-left",
            itmTint,
            selected && "bg-term-accent/25",
            onSelect && "cursor-pointer hover:bg-term-accent/15",
            c.cls,
          )}
        >
          {c.bar && c.bar.frac > 0 && (
            <div
              className="pointer-events-none absolute inset-y-0"
              style={{
                width: `${Math.min(100, c.bar.frac * 100)}%`,
                backgroundColor: `rgb(var(${barToken}) / ${c.bar.intensity})`,
                ...(align === "left" ? { left: 0 } : { right: 0 }),
              }}
            />
          )}
          <span className="relative z-10">{c.text}</span>
        </td>
      ))}
    </>
  );
}

/** One label/value cell for the header stat cluster. */
function Stat({ label, children, tone }: { label: string; children: ReactNode; tone?: string }) {
  return (
    <div className="flex items-baseline gap-1">
      <span className="text-[9px] uppercase tracking-wider text-term-muted">{label}</span>
      <span className={cx("font-mono text-[11px] tabular-nums text-term-text", tone)}>{children}</span>
    </div>
  );
}

export default function OptionChainWidget(props: WidgetProps) {
  const { symbol, channel, setChannel } = useWidgetSymbol(props);
  const underlying = symbol.toUpperCase();
  const opt = useOptionsStatus();
  const enabled = opt.enabled;

  const [expiry, setExpiry] = useState<string>("");
  const [range, setRange] = useState<number>(0);
  const [view, setView] = useState<ColView>("price");
  const [metric, setMetric] = useState<BarMetric>("oi");
  const [legs, setLegs] = useState<Leg[]>([]); // 1 leg = single order, 2 = spread/combo
  const [qty, setQty] = useState<number>(1);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();

  const scrollRef = useRef<HTMLDivElement>(null);
  const atmRowRef = useRef<HTMLTableRowElement>(null);
  const lastScrolledKey = useRef<string>("");

  const exps = useQuery({
    queryKey: ["optExpirations", underlying, opt.source],
    queryFn: () => api.optionExpirations(underlying),
    enabled,
    staleTime: 60_000,
  });

  useEffect(() => {
    const list = exps.data?.expirations ?? [];
    if (list.length && !list.some((e) => e.date === expiry)) setExpiry(list[0].date);
  }, [exps.data, expiry]);

  const chain = useQuery<OptionChainResponse>({
    queryKey: ["optChain", underlying, expiry, opt.source],
    queryFn: () => api.optionChain(underlying, expiry),
    enabled: enabled && !!expiry,
    refetchInterval: 30_000,
  });

  // sim-book buying power for the ticket's cost check (only while a ticket is open)
  const acct = useQuery({
    queryKey: ["paper", "account", "sim"],
    queryFn: () => api.paperAccount("sim"),
    enabled: legs.length > 0,
    staleTime: 15_000,
  });

  const data = chain.data;
  const showGreeks = !!(data?.greeks_computed || opt.capabilities.greeks);
  const effView: ColView = showGreeks ? view : "price"; // greeks toggle hidden when unavailable
  const cols = colsFor(effView);

  const byStrike = useMemo(() => {
    const calls = new Map((data?.calls ?? []).map((c) => [c.strike, c]));
    const puts = new Map((data?.puts ?? []).map((p) => [p.strike, p]));
    const spot = data?.spot ?? null;
    let strikes = data?.strikes ?? [];
    if (range > 0 && spot) {
      const lo = spot * (1 - range / 100), hi = spot * (1 + range / 100);
      strikes = strikes.filter((k) => k >= lo && k <= hi);
    }
    return strikes.map((k) => ({
      strike: k,
      call: calls.get(k),
      put: puts.get(k),
      callItm: spot != null ? k < spot : calls.get(k)?.in_the_money ?? undefined,
      putItm: spot != null ? k > spot : puts.get(k)?.in_the_money ?? undefined,
    }));
  }, [data, range]);

  // per-view max/median of OI & volume (across visible strikes) to normalize the depth bars
  const barStats = useMemo(() => {
    const stat = (arr: number[]) => {
      if (!arr.length) return { max: 0, median: 1 };
      const s = [...arr].sort((a, b) => a - b);
      return { max: s[s.length - 1], median: s[Math.floor(s.length / 2)] || 1 };
    };
    const oi: number[] = [], vol: number[] = [];
    for (const r of byStrike) for (const c of [r.call, r.put]) {
      if (c?.open_interest != null) oi.push(c.open_interest);
      if (c?.volume != null) vol.push(c.volume);
    }
    return { oi: stat(oi), vol: stat(vol) };
  }, [byStrike]);
  const bar = metric === "oi" ? barStats.oi : barStats.vol;

  // header stat cluster: spot · ATM IV · DTE · put/call OI ratio · total OI
  const headerStats = useMemo(() => {
    if (!data) return null;
    let callOi = 0, putOi = 0;
    for (const c of data.calls) callOi += c.open_interest ?? 0;
    for (const p of data.puts) putOi += p.open_interest ?? 0;
    let atmIv = data.atm_strike != null ? data.calls.find((c) => c.strike === data.atm_strike)?.iv ?? null : null;
    if (atmIv == null && data.spot != null) {
      const withIv = data.calls.filter((c) => c.iv);
      if (withIv.length) atmIv = withIv.reduce((a, b) => (Math.abs(b.strike - data.spot!) < Math.abs(a.strike - data.spot!) ? b : a)).iv;
    }
    return { spot: data.spot, atmIv, dte: data.dte, pcr: callOi > 0 ? putOi / callOi : null, totalOi: callOi + putOi };
  }, [data]);

  // per-leg mark (per-share mid, else last) → net debit(+)/credit(−) of the built ticket
  const legMark = (l: Leg): number | null => {
    const r = (l.right === "call" ? data?.calls : data?.puts)?.find((x) => x.strike === l.strike);
    if (!r) return null;
    return r.bid != null && r.ask != null ? (r.bid + r.ask) / 2 : r.last;
  };
  const legMarks = legs.map(legMark);
  const priced = legs.length > 0 && legMarks.every((m) => m != null);
  const netPerSpread = legs.reduce((a, l, i) => a + (l.side === "buy" ? 1 : -1) * (legMarks[i] ?? 0) * 100, 0);
  const netTotal = netPerSpread * qty; // >0 debit (pay), <0 credit (receive)
  const buyingPower = acct.data?.cash ?? null;
  const overBP = priced && netTotal > 0 && buyingPower != null && netTotal > buyingPower;

  // click a contract → add it as a leg (buy the first, sell the second); click again → remove; cap 2
  const toggleLeg = (right: OptionRight, strike: number) => {
    setMsg(null);
    setLegs((prev) => {
      const at = prev.findIndex((l) => l.right === right && l.strike === strike);
      if (at >= 0) return prev.filter((_, i) => i !== at);
      if (prev.length >= 2) return prev;
      return [...prev, { right, strike, side: prev.length === 0 ? "buy" : "sell" }];
    });
  };
  const flipSide = (i: number) =>
    setLegs((prev) => prev.map((l, j) => (j === i ? { ...l, side: l.side === "buy" ? "sell" : "buy" } : l)));

  const submit = async () => {
    if (!legs.length || !expiry) return;
    setBusy(true);
    setMsg(null);
    try {
      if (legs.length === 1) {
        const l = legs[0];
        const r = await api.submitOptionOrder({
          underlying, expiry, strike: l.strike, right: l.right, side: l.side, quantity: qty, type: "market",
        });
        setMsg({ ok: true, text: `${l.side} ${qty} ${underlying} ${l.strike}${l.right[0].toUpperCase()} · #${r.order_id}` });
      } else {
        const r = await api.submitComboOrder({
          legs: legs.map((l) => ({ underlying, expiry, strike: l.strike, right: l.right, side: l.side })),
          quantity: qty,
        });
        const dir = r.net_debit >= 0 ? "debit" : "credit";
        setMsg({ ok: true, text: `${legs.length}-leg ${underlying} spread · net ${dir} $${Math.abs(r.net_debit).toFixed(0)}` });
      }
      qc.invalidateQueries({ queryKey: ["paper"] });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "order failed" });
    } finally {
      setBusy(false);
    }
  };

  // centre the ATM row once per underlying+expiry (not on every 30s refetch, so it won't
  // fight a user's scroll position); re-runs when they switch symbol or expiration.
  useEffect(() => {
    if (!data || byStrike.length === 0 || data.atm_strike == null) return;
    const key = `${underlying}|${expiry}`;
    if (lastScrolledKey.current === key) return;
    if (!byStrike.some((r) => r.strike === data.atm_strike)) return;
    lastScrolledKey.current = key;
    requestAnimationFrame(() => {
      const container = scrollRef.current, row = atmRowRef.current;
      if (container && row) container.scrollTop = row.offsetTop - container.clientHeight / 2 + row.clientHeight / 2;
    });
  }, [data, byStrike, underlying, expiry]);

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge={data?.source === "yfinance" ? "eod" : "live"}
      toolbar={
        <>
          <span className="font-mono text-sm font-bold">{underlying}</span>
          {enabled && (exps.data?.expirations?.length ?? 0) > 0 && (
            <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className={cx(SELECT_CLS, "ml-2")} title="Expiration">
              {exps.data!.expirations.map((e) => (
                <option key={e.date} value={e.date}>{e.date} · {e.dte}d{e.monthly ? " · M" : ""}</option>
              ))}
            </select>
          )}
          {showGreeks && (
            <div className="ml-2 flex items-center gap-px rounded border border-term-border text-[10px]" title="Price / Greeks columns">
              {(["price", "greeks"] as ColView[]).map((v) => (
                <button key={v} onClick={() => setView(v)}
                  className={cx("px-1.5 py-0.5 uppercase", view === v ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text")}>
                  {v}
                </button>
              ))}
            </div>
          )}
          {effView === "price" && (
            <div className="ml-2 flex items-center gap-px rounded border border-term-border text-[10px]" title="Depth bar metric">
              {(["oi", "vol"] as BarMetric[]).map((m) => (
                <button key={m} onClick={() => setMetric(m)}
                  className={cx("px-1.5 py-0.5 uppercase", metric === m ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text")}>
                  {m === "oi" ? "OI" : "Vol"}
                </button>
              ))}
            </div>
          )}
          {enabled && (
            <select value={range} onChange={(e) => setRange(Number(e.target.value))} className={cx(SELECT_CLS, "ml-auto")} title="Strike range around spot">
              {RANGE_OPTS.map((r) => (
                <option key={r} value={r}>{r === 0 ? "all strikes" : `±${r}%`}</option>
              ))}
            </select>
          )}
        </>
      }
    >
      <div className="flex h-full flex-col">
        {headerStats && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 border-b border-term-border/60 bg-term-sunken/30 px-2 py-1">
            <Stat label="Spot">{fmtPrice(headerStats.spot)}</Stat>
            <Stat label="ATM IV">{pctIv(headerStats.atmIv)}</Stat>
            <Stat label="DTE">{headerStats.dte}d</Stat>
            <Stat label="P/C" tone={headerStats.pcr == null ? undefined : headerStats.pcr > 1 ? "text-term-down" : "text-term-up"}>
              {headerStats.pcr == null ? "—" : headerStats.pcr.toFixed(2)}
            </Stat>
            <Stat label="ΣOI">{fmtCompact(headerStats.totalOi)}</Stat>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-hidden">
          {!enabled ? (
            <EmptyState title="Options source is off — enable it in Settings → Market Data → Options" />
          ) : exps.isLoading || (chain.isLoading && !data) ? (
            <EmptyState title={`Loading ${underlying} options…`} />
          ) : (exps.data?.expirations?.length ?? 0) === 0 ? (
            <EmptyState title={exps.data?.note || `No options chain for ${underlying}`} />
          ) : !data || byStrike.length === 0 ? (
            <EmptyState title={data?.note || `No contracts for ${underlying} ${expiry}`} />
          ) : (
            <div ref={scrollRef} className="h-full overflow-auto">
              <table className="w-full border-collapse text-[11px]">
                <thead className="sticky top-0 z-10 bg-term-panel text-[10px] uppercase tracking-wider text-term-muted">
                  <tr className="border-b border-term-border">
                    <th colSpan={cols.length} className="px-1.5 py-1 text-left text-term-up">Calls</th>
                    <th className="px-1.5 py-1 text-center">Strike</th>
                    <th colSpan={cols.length} className="px-1.5 py-1 text-right text-term-down">Puts</th>
                  </tr>
                  <tr className="border-b border-term-border text-term-muted">
                    {cols.map((k) => (
                      <th key={`c${k}`} className="px-1.5 py-0.5 text-left font-normal">{colLabel(k, metric)}</th>
                    ))}
                    <th className="px-1.5 py-0.5 text-center font-normal">K</th>
                    {[...cols].reverse().map((k) => (
                      <th key={`p${k}`} className="px-1.5 py-0.5 text-right font-normal">{colLabel(k, metric)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {byStrike.map(({ strike, call, put, callItm, putItm }) => {
                    const atm = data.atm_strike === strike;
                    return (
                      <tr
                        key={strike}
                        ref={atm ? atmRowRef : undefined}
                        className={cx("border-b border-term-border/40", atm && "border-t-2 border-t-term-accent/60 bg-term-accent/10")}
                      >
                        <SideCells q={call} cols={cols} align="left" itm={callItm} metric={metric} barMax={bar.max} barMedian={bar.median}
                          onSelect={() => toggleLeg("call", strike)}
                          selected={legs.some((l) => l.right === "call" && l.strike === strike)} />
                        <td className="px-1.5 py-0.5 text-center font-mono font-semibold tabular-nums">{strike}</td>
                        <SideCells q={put} cols={cols} align="right" itm={putItm} metric={metric} barMax={bar.max} barMedian={bar.median}
                          onSelect={() => toggleLeg("put", strike)}
                          selected={legs.some((l) => l.right === "put" && l.strike === strike)} />
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {legs.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-t border-term-border px-2 py-1.5 text-xs">
            <span className="font-mono text-[10px] text-term-muted">{underlying} · {expiry}</span>
            {legs.map((l, i) => (
              <span key={`${l.right}-${l.strike}`} className="flex items-center gap-1 rounded border border-term-border bg-term-sunken/40 px-1 py-0.5 font-mono">
                <button onClick={() => flipSide(i)} title="Toggle buy/sell"
                  className={cx("rounded px-1 text-[10px] font-bold uppercase", l.side === "buy" ? "text-term-up" : "text-term-down")}>
                  {l.side === "buy" ? "B" : "S"}
                </button>
                <span>{l.strike}{l.right === "call" ? "C" : "P"}</span>
                <button onClick={() => toggleLeg(l.right, l.strike)} className="text-term-muted hover:text-term-text" title="Remove leg">✕</button>
              </span>
            ))}
            {legs.length < 2 && <span className="text-[10px] text-term-muted">+ click a strike to add a leg</span>}
            <label className="flex items-center gap-1 text-[10px] text-term-muted">
              <input
                type="number" min={1} step={1} value={qty}
                onChange={(e) => setQty(Math.max(1, Math.floor(Number(e.target.value) || 1)))}
                className="focus-ring w-16 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs tabular-nums"
                aria-label={legs.length > 1 ? "spreads" : "contracts"}
              />
              {legs.length > 1 ? "spreads" : "contracts"}
            </label>
            {priced && (
              <span className={cx(
                "rounded border border-term-border bg-term-sunken/40 px-1.5 py-0.5 font-mono text-[10px]",
                overBP ? "text-term-down" : "text-term-muted",
              )}
                title={buyingPower != null ? `buying power $${buyingPower.toFixed(0)}` : undefined}>
                net {netTotal >= 0 ? "debit" : "credit"} ${Math.abs(netTotal).toFixed(0)}{overBP && " · > buying power"}
              </span>
            )}
            <button onClick={submit} disabled={busy || overBP || !priced}
              className="rounded border border-term-accent bg-term-accent/15 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-term-accent hover:bg-term-accent/25 disabled:opacity-40">
              {legs.length > 1 ? "Submit spread" : legs[0].side === "buy" ? "Buy" : "Sell"}
            </button>
            <button onClick={() => { setLegs([]); setMsg(null); }} className="focus-ring rounded text-term-muted hover:text-term-text" title="Clear ticket">✕</button>
            {msg && (
              <span className={cx("truncate text-[10px]", msg.ok ? "text-term-up" : "text-term-down")}>{msg.text}</span>
            )}
            <span className="ml-auto text-[9px] text-term-muted">paper · sim book</span>
          </div>
        )}
      </div>
    </WidgetShell>
  );
}
