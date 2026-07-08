import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset, RebalancePlan, ScreenResponse } from "../api/types";
import { cx, fmtCompact, fmtPct, fmtPrice, upDownClass } from "../lib/format";
import { useT } from "../lib/i18n";
import { useLinking } from "../state/linking";
import { simBook, useActiveAccountId } from "../state/accounts";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState, ErrorState } from "../components/States";
import SendToMenu from "../components/SendToMenu";
import type { SendPayload } from "../state/intents";

export default function ScreenerWidget(props: WidgetProps) {
  const { channel, setChannel, setSymbol } = useWidgetSymbol(props);
  const t = useT();
  const setRed = useLinking((s) => s.setSymbol);

  const [universe, setUniverse] = useState("dow30");
  const [factor, setFactor] = useState("momentum");
  const [ask, setAsk] = useState("");
  const [result, setResult] = useState<ScreenResponse | null>(null);

  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.universes });
  const { data: factors } = useQuery({ queryKey: ["factors"], queryFn: api.screenFactors });

  const screen = useMutation({
    mutationFn: () => api.screen(universe, factor, 25),
    onSuccess: setResult,
  });
  const askLlm = useMutation({
    mutationFn: (q: string) => api.ask(q),
    onSuccess: (r) => {
      setResult(r);
      setUniverse(r.universe);
      setFactor(r.factor);
    },
  });

  // Cmd+K NL fallback opens the widget with an initialQuery — run it once on mount.
  const ranInitial = useRef(false);
  useEffect(() => {
    const q = props.params.initialQuery;
    if (q && !ranInitial.current) {
      ranInitial.current = true;
      setAsk(q);
      askLlm.mutate(q);
      props.api.updateParameters({ initialQuery: undefined });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Render a precomputed screen streamed in by the Agent Workflow's `screen` node: set the result
  // directly — NO /api/ask, no re-run — then clear the param. Because nothing runs here, a stopped or
  // paused workflow leaves nothing churning in the Screener. Watches the param (the panel may already
  // be mounted when the node finishes) and de-dupes by object identity.
  const injectedScreenRef = useRef<unknown>(null);
  useEffect(() => {
    const s = props.params.incomingScreen as ScreenResponse | undefined;
    if (s && s !== injectedScreenRef.current) {
      injectedScreenRef.current = s;
      setResult(s);
      if (s.universe) setUniverse(s.universe);
      if (s.factor) setFactor(s.factor);
      props.api.updateParameters({ incomingScreen: undefined });
    }
  }, [props.params.incomingScreen]);

  const busy = screen.isPending || askLlm.isPending;
  const err = (screen.error ?? askLlm.error) as Error | null;

  // ── deploy top-N as an equal-weight paper book ──
  const [topN, setTopN] = useState(10);
  const [gross, setGross] = useState(1.0);
  const [deployBook, setDeployBook] = useState<"primary" | "sim">("primary");
  const [plan, setPlan] = useState<RebalancePlan | null>(null);
  const { data: paperCfg } = useQuery({ queryKey: ["paper-config"], queryFn: () => api.paperConfig() });

  const buildWeights = () => {
    const rows = (result?.results ?? []).slice(0, topN);
    const w = 1 / Math.max(rows.length, 1);
    return Object.fromEntries(rows.map((r) => [r.symbol, w]));
  };
  const deployAsset = result?.results[0]?.asset ?? "equity";
  // Deploy to the Alpaca book only when explicitly chosen; otherwise the globally-active sim account.
  const activeAccountId = useActiveAccountId();
  const apiBook =
    paperCfg?.alpaca_active && deployBook === "primary" ? "primary" : simBook(activeAccountId);

  const previewDeploy = useMutation({
    mutationFn: () =>
      api.rebalancePaper({ weights: buildWeights(), asset: deployAsset, gross, execute: false }, apiBook),
    onSuccess: setPlan,
  });
  const executeDeploy = useMutation({
    mutationFn: () =>
      api.rebalancePaper({ weights: buildWeights(), asset: deployAsset, gross, execute: true }, apiBook),
    onSuccess: () => setPlan(null),
  });

  const pick = (symbol: string, asset: Asset) => {
    // Screeners are usually unlinked; route picks to the red channel so charts follow.
    if (channel === "none") setRed("red", { symbol, asset });
    else setSymbol({ symbol, asset });
  };

  // Hand the current screen result to another module (Backtest, Watchlist) as a typed payload.
  const buildScreenPayload = (): SendPayload | null => {
    const rows = result?.results ?? [];
    if (rows.length === 0) return null;
    return {
      kind: "screen_result",
      universe: result!.universe,
      factor: result!.factor,
      symbols: rows.map((r) => r.symbol),
      asset: rows[0]?.asset ?? "equity",
    };
  };

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      sendMenu={<SendToMenu kind="screen_result" build={buildScreenPayload} disabled={!result?.results?.length} />}
      toolbar={
        <form
          className="flex flex-1 items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (ask.trim()) askLlm.mutate(ask.trim());
          }}
        >
          <input
            value={ask}
            onChange={(e) => setAsk(e.target.value)}
            placeholder={t("screen.placeholder")}
            aria-label={t("screen.placeholder")}
            className="focus-ring min-w-0 flex-1 rounded border border-term-border bg-term-sunken px-2 py-0.5 text-xs placeholder:text-term-muted focus:border-term-accent"
          />
          <select
            value={universe}
            onChange={(e) => setUniverse(e.target.value)}
            aria-label="Universe"
            className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-muted"
          >
            {(universes?.universes ?? [universe]).map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
          <select
            value={factor}
            onChange={(e) => setFactor(e.target.value)}
            aria-label="Factor"
            className="focus-ring max-w-[120px] rounded border border-term-border bg-term-sunken px-1 py-0.5 text-[10px] text-term-muted"
          >
            {(factors?.factors ?? []).map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
            {!factors && <option value={factor}>{factor}</option>}
          </select>
          <button
            type="button"
            onClick={() => screen.mutate()}
            disabled={busy}
            className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
          >
            {t("common.run")}
          </button>
        </form>
      }
    >
      {busy && <SkeletonRows rows={8} />}
      {err && <ErrorState message={err.message} />}
      {!busy && result && (
        <>
          {result.rationale && (
            <div className="border-b border-term-border bg-term-bg/60 px-3 py-2 text-xs text-term-muted">
              <span className="text-term-accent">LLM:</span> {result.rationale}
              <span className="ml-2 text-[10px]">
                ({result.factor} · {result.universe} · {t("screen.coverage")} {result.coverage})
              </span>
            </div>
          )}
          {/* deploy the ranked basket into the paper account */}
          <div className="flex flex-wrap items-center gap-2 border-b border-term-border bg-term-sunken/30 px-3 py-1.5 text-[10px] text-term-muted">
            <span className="uppercase tracking-wider">Deploy to paper</span>
            <label className="flex items-center gap-1">
              top
              <input
                type="number"
                min={1}
                max={result.results.length}
                value={topN}
                onChange={(e) => setTopN(Math.max(1, Number(e.target.value)))}
                aria-label="Top N"
                className="focus-ring w-14 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
              />
            </label>
            <label className="flex items-center gap-1">
              gross
              <input
                type="number"
                min={0}
                step="0.1"
                value={gross}
                onChange={(e) => setGross(Number(e.target.value))}
                aria-label="Gross exposure"
                className="focus-ring w-14 rounded border border-term-border bg-term-sunken px-1 py-0.5 font-mono text-xs"
              />
            </label>
            {paperCfg?.alpaca_active && (
              <label className="flex items-center gap-1">
                book
                <select
                  value={deployBook}
                  onChange={(e) => setDeployBook(e.target.value as "primary" | "sim")}
                  aria-label="Paper book"
                  className="focus-ring rounded border border-term-border bg-term-sunken px-1 py-0.5 text-xs"
                >
                  <option value="primary">Alpaca</option>
                  <option value="sim">Sim</option>
                </select>
              </label>
            )}
            <button
              type="button"
              onClick={() => previewDeploy.mutate()}
              disabled={previewDeploy.isPending}
              className="rounded border border-term-accent px-2 py-0.5 uppercase tracking-wide text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
            >
              {previewDeploy.isPending ? "…" : "Preview rebalance"}
            </button>
            {previewDeploy.error && <span className="text-term-down">{(previewDeploy.error as Error).message}</span>}
          </div>
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
                <th className="px-2 py-1 text-left font-medium">#</th>
                <th className="px-2 py-1 text-left font-medium">{t("common.symbol")}</th>
                <th className="px-2 py-1 text-left font-medium">{t("common.sector")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("screen.score")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("screen.ret20")}</th>
                <th className="px-2 py-1 text-right font-medium">{t("common.price")}</th>
              </tr>
            </thead>
            <tbody>
              {result.results.map((r, i) => (
                <tr
                  key={r.symbol}
                  onClick={() => pick(r.symbol, r.asset)}
                  className="cursor-pointer border-b border-term-border/30 hover:bg-term-border/30"
                >
                  <td className="px-2 py-1 text-term-muted">{i + 1}</td>
                  <td className="px-2 py-1 font-mono font-semibold">{r.symbol}</td>
                  <td className="px-2 py-1 text-term-muted">{r.sector ?? "—"}</td>
                  <td className="px-2 py-1 text-right font-mono">{r.score.toFixed(3)}</td>
                  <td className={cx("px-2 py-1 text-right font-mono", upDownClass(r.ret_20d))}>
                    {fmtPct(r.ret_20d)}
                  </td>
                  <td className="px-2 py-1 text-right font-mono">{fmtPrice(r.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {!busy && !result && !err && <EmptyState title={t("screen.empty")} />}

      {/* ── rebalance preview (confirm-before-send) ── */}
      {plan && (
        <>
          <div className="fixed inset-0 z-50 bg-black/50" onClick={() => setPlan(null)} />
          <div className="fixed left-1/2 top-[18vh] z-50 flex max-h-[64vh] w-[min(460px,92vw)] -translate-x-1/2 flex-col rounded-lg border border-term-border bg-term-elev p-4 shadow-elev-3">
            <div className="mb-1 text-sm font-semibold">Rebalance to paper</div>
            <div className="mb-2 text-[11px] text-term-muted">
              Equity ${fmtCompact(plan.equity)} · {plan.orders.length} order(s) · sells fill before buys.
              {plan.skipped.length > 0 && <span className="text-term-down"> Skipped (no price): {plan.skipped.join(", ")}.</span>}
            </div>
            {!plan.gate.approved && (
              <div className="mb-2 rounded border border-term-accent/40 bg-term-accent/10 px-2 py-1 text-[11px] text-term-accent">
                ⚠ Risk gate: {plan.gate.reason} (advisory — you can still deploy)
              </div>
            )}
            <div className="min-h-0 flex-1 overflow-auto rounded border border-term-border">
              <table className="w-full border-collapse text-[11px]">
                <thead className="sticky top-0 bg-term-panel">
                  <tr className="border-b border-term-border text-[9px] uppercase tracking-wider text-term-muted">
                    <th className="px-2 py-1 text-left">Order</th>
                    <th className="px-2 py-1 text-right">Notional</th>
                    <th className="px-2 py-1 text-right">→ Wt</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {plan.orders.map((o) => (
                    <tr key={o.symbol} className="border-b border-term-border/30">
                      <td className="px-2 py-0.5">
                        <span className={o.side === "buy" ? "text-term-up" : "text-term-down"}>{o.side}</span>{" "}
                        {o.quantity} {o.symbol}
                      </td>
                      <td className="px-2 py-0.5 text-right text-term-muted">${fmtCompact(Math.abs(o.notional))}</td>
                      <td className="px-2 py-0.5 text-right">{fmtPct(o.target_weight * 100)}</td>
                    </tr>
                  ))}
                  {plan.orders.length === 0 && (
                    <tr>
                      <td colSpan={3} className="px-2 py-3 text-center text-term-muted">Already at target — no orders.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button onClick={() => setPlan(null)} className="rounded border border-term-border px-3 py-1 text-xs text-term-muted hover:text-term-text">
                Cancel
              </button>
              <button
                onClick={() => executeDeploy.mutate()}
                disabled={executeDeploy.isPending || plan.orders.length === 0}
                className="rounded border border-term-up px-3 py-1 text-xs font-semibold uppercase text-term-up hover:bg-term-up/10 disabled:opacity-50"
              >
                {executeDeploy.isPending ? "Deploying…" : "Deploy"}
              </button>
            </div>
            {executeDeploy.error && <div className="mt-1 text-[11px] text-term-down">{(executeDeploy.error as Error).message}</div>}
          </div>
        </>
      )}
    </WidgetShell>
  );
}
