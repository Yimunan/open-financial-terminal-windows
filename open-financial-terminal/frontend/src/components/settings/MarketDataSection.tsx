import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type {
  CryptoCategory,
  EquityCategory,
  FiccCategory,
  MarketDataSettings,
  OptionsCategory,
} from "../../api/types";
import { cx } from "../../lib/format";
import { setStreamExchange } from "../../lib/wsClient";
import { useLinking } from "../../state/linking";
import { Choice, clampNum, llmInputCls, type Msg, numCls, Row, SectionHeader, Status } from "./common";

/** FICC asset classes shown in Market Data settings (yfinance bars + a selectable depth source). */
const FICC_MD: { key: "rates" | "fx" | "commodity"; label: string }[] = [
  { key: "rates", label: "Rates (futures)" },
  { key: "fx", label: "FX" },
  { key: "commodity", label: "Commodities (futures)" },
];

// Depth / options sources that require vendor credentials (entered in Settings → Data Providers).
const DEPTH_VENDORS = new Set(["databento", "dxfeed", "ibkr"]);
const OPTIONS_VENDORS = new Set(["tradier", "polygon", "ibkr"]);

/** Human label for an order-book depth-source id. */
function depthLabel(s: string): string {
  if (s === "auto") return "Auto (best available)";
  if (s === "sim") return "Simulated";
  if (s === "exchange") return "Exchange (real L2)";
  if (s === "ibkr") return "IBKR";
  if (s === "databento") return "Databento";
  if (s === "dxfeed") return "dxFeed";
  if (s === "none") return "Off";
  return s;
}

/** The instrument a depth source delivers for an asset class, when a class's sources differ:
 * "spot" | "futures" | "". Today only FX splits — Simulated/IBKR/dxFeed give spot FX, but Databento
 * serves the CME FX future. Other classes are uniform (equity/crypto = spot, rates/commodities =
 * futures), so the block header already says it and no per-option tag is needed. */
function depthInstrument(asset: string, source: string): "spot" | "futures" | "" {
  if (asset !== "fx" || source === "none" || source === "auto") return ""; // auto: unknown until resolved
  return source === "databento" ? "futures" : "spot";
}

/** Dropdown option label, tagged with spot/futures only where a class's sources differ (FX). */
function depthOptionLabel(asset: string, source: string): string {
  const inst = depthInstrument(asset, source);
  return inst ? `${depthLabel(source)} (${inst})` : depthLabel(source);
}

/** A short caveat when the selected depth vendor's book instrument differs from the block's
 * spot/futures nature — e.g. Databento has no spot FX, so it serves the CME FX future instead. */
function depthHint(asset: string, source: string): string {
  if (asset === "fx" && source === "databento") return "↳ CME FX future (6E), not spot";
  return "";
}

/** Human label for an options-chain source id. */
function optionsSourceLabel(s: string): string {
  if (s === "yfinance") return "yfinance (free, delayed)";
  if (s === "tradier") return "Tradier";
  if (s === "polygon") return "Polygon";
  if (s === "ibkr") return "IBKR";
  if (s === "none") return "Off";
  return s;
}

/** Capability note for the selected options source, e.g. "chains · IV · greeks computed locally". */
function optionsCapNote(caps?: { chains: boolean; iv: boolean; greeks: boolean; realtime: boolean }): string {
  if (!caps || !caps.chains) return "";
  const parts = ["chains"];
  if (caps.iv) parts.push("IV");
  parts.push(caps.greeks ? "greeks" : "greeks computed locally (Black-Scholes)");
  if (caps.realtime) parts.push("realtime");
  return parts.join(" · ");
}

/** A clickable "this source needs an API key" hint that jumps to Settings → Data Providers. */
function NeedsCreds({ onGoto }: { onGoto?: () => void }) {
  return (
    <button
      onClick={onGoto}
      title="This source needs vendor credentials — set them up in Data Providers"
      className="focus-ring shrink-0 rounded text-[10px] text-term-down hover:text-term-accent"
    >
      ● needs credentials → Data Providers
    </button>
  );
}

/** Market data: per-asset-class source/depth/cache/history/default-symbol config, options-chain
 * source, Alpaca credentials, and read-only status. Vendor depth/options sources that aren't
 * configured show a link to the Data Providers section. */
export default function MarketDataSection({ onGotoProviders }: { onGotoProviders?: () => void }) {
  const qc = useQueryClient();
  const [md, setMd] = useState<MarketDataSettings | null>(null);
  const [mdKey, setMdKey] = useState("");
  const [mdSecret, setMdSecret] = useState("");
  const [showMdKey, setShowMdKey] = useState(false);
  const [showMdSecret, setShowMdSecret] = useState(false);
  const [mdBusy, setMdBusy] = useState(false);
  const [mdMsg, setMdMsg] = useState<Msg>(null);

  const patchEquity = (p: Partial<EquityCategory>) =>
    setMd((m) => (m ? { ...m, categories: { ...m.categories, equity: { ...m.categories.equity, ...p } } } : m));
  const patchCrypto = (p: Partial<CryptoCategory>) =>
    setMd((m) => (m ? { ...m, categories: { ...m.categories, crypto: { ...m.categories.crypto, ...p } } } : m));
  const patchFicc = (cat: "rates" | "fx" | "commodity", p: Partial<FiccCategory>) =>
    setMd((m) => (m ? { ...m, categories: { ...m.categories, [cat]: { ...m.categories[cat], ...p } } } : m));
  const patchOptions = (p: Partial<OptionsCategory>) =>
    setMd((m) =>
      m && m.categories.options
        ? { ...m, categories: { ...m.categories, options: { ...m.categories.options, ...p } } }
        : m,
    );

  useEffect(() => {
    api
      .marketDataSettings()
      .then((s) => {
        setMd(s);
        setMdKey("");
        setMdSecret("");
      })
      .catch(() => {});
  }, []);

  const saveMarketData = async () => {
    if (!md || !md.categories) return;
    setMdBusy(true);
    setMdMsg(null);
    try {
      const saved = await api.saveMarketDataSettings({
        categories: md.categories,
        alpaca_api_key: mdKey,
        alpaca_api_secret: mdSecret,
        alpaca_paper: md.alpaca_paper,
      });
      setMd(saved);
      setMdKey("");
      setMdSecret("");
      setShowMdKey(false);
      setShowMdSecret(false);
      setStreamExchange(saved.exchange); // repoint live realtime widgets immediately
      // seed the linking channels from the saved default symbols (first run only — see App)
      useLinking.getState().seedDefaults(saved.categories.equity.default_symbol, saved.categories.crypto.default_symbol);
      qc.invalidateQueries({ queryKey: ["health"] });
      setMdMsg({ ok: true, detail: `Saved · ${saved.exchange} · broker ${saved.broker}` });
    } catch (e) {
      setMdMsg({ ok: false, detail: e instanceof Error ? e.message : "save failed" });
    } finally {
      setMdBusy(false);
    }
  };

  const testExchange = async () => {
    if (!md) return;
    setMdBusy(true);
    setMdMsg(null);
    try {
      setMdMsg(await api.testMarketDataExchange(md.categories.crypto.source));
    } catch (e) {
      setMdMsg({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setMdBusy(false);
    }
  };

  const testEquity = async () => {
    if (!md) return;
    setMdBusy(true);
    setMdMsg(null);
    try {
      // probe with the in-progress key/secret if typed (else the saved creds), on the chosen feed
      setMdMsg(await api.testMarketDataEquity({ api_key: mdKey, api_secret: mdSecret, feed: md.categories.equity.realtime_feed }));
    } catch (e) {
      setMdMsg({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setMdBusy(false);
    }
  };

  const testDepth = async (asset: string, source: string) => {
    if (!md) return;
    setMdBusy(true);
    setMdMsg(null);
    try {
      setMdMsg(await api.testMarketDataDepth({ asset, source }));
    } catch (e) {
      setMdMsg({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setMdBusy(false);
    }
  };

  const testOptions = async (source: string, underlying: string) => {
    if (!md) return;
    setMdBusy(true);
    setMdMsg(null);
    try {
      setMdMsg(await api.testMarketDataOptions({ source, underlying }));
    } catch (e) {
      setMdMsg({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setMdBusy(false);
    }
  };

  const removeAlpaca = async () => {
    if (!md || !md.has_alpaca_key) return;
    if (!window.confirm("Remove the saved Alpaca credentials? The paper broker reverts to the local simulator and equity realtime stops.")) return;
    setMdBusy(true);
    setMdMsg(null);
    try {
      const saved = await api.removeAlpacaCreds();
      setMd(saved);
      setMdKey("");
      setMdSecret("");
      setShowMdKey(false);
      setShowMdSecret(false);
      qc.invalidateQueries({ queryKey: ["health"] });
      setMdMsg({ ok: true, detail: `Alpaca credentials removed · broker ${saved.broker}` });
    } catch (e) {
      setMdMsg({ ok: false, detail: e instanceof Error ? e.message : "remove failed" });
    } finally {
      setMdBusy(false);
    }
  };

  const clearMarketCache = async () => {
    setMdBusy(true);
    setMdMsg(null);
    try {
      setMdMsg(await api.clearMarketDataCache());
      setMd(await api.marketDataSettings()); // refresh cached-symbol/realtime status
    } catch (e) {
      setMdMsg({ ok: false, detail: e instanceof Error ? e.message : "clear failed" });
    } finally {
      setMdBusy(false);
    }
  };

  return (
    <div>
      <SectionHeader title="Market Data" chip={md ? `broker · ${md.broker}` : undefined} />
      <p className="mb-3 text-[10px] leading-relaxed text-term-muted">
        Each asset class has its own <b>data source</b> (historical + realtime), cache TTL,
        history window and default symbol. Crypto's exchange drives both bars and the realtime
        ticker / order-book / time-&-sales widgets (Kraken is the safe default — binance returns
        HTTP 451 from geo-restricted IPs); equity bars come from yfinance with realtime via Alpaca.
        Vendor depth/options sources (Databento, IBKR, Polygon, Tradier, dxFeed) need credentials —
        set them up in <button onClick={onGotoProviders} className="text-term-accent hover:underline">Data Providers</button>.
      </p>

      {md && !md.categories && (
        <p className="rounded border border-term-down/50 bg-term-down/10 px-2 py-1.5 text-[10px] text-term-down">
          Backend is out of date (no per-category market-data config). Restart the API server to
          load the latest build.
        </p>
      )}

      {md && md.categories && md.category_meta && (
        <div className="space-y-3">
          {/* ── Equity category ── */}
          <div className="rounded border border-term-border/60 bg-term-sunken/30 p-2.5">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-term-accent">Equity (spot)</div>
            <div className="divide-y divide-term-border/50">
              <Row label="Bars source">
                <span className="rounded bg-term-border/40 px-1.5 py-0.5 font-mono text-[11px] text-term-text">
                  {md.categories.equity.bars_source}
                </span>
                <span className="text-[10px] text-term-muted">only provider today</span>
              </Row>
              <Row label="Realtime source">
                {md.category_meta.equity.realtime_sources.map((s) => (
                  <Choice key={s} active={md.categories.equity.realtime_source === s} onClick={() => patchEquity({ realtime_source: s })}>
                    {s === "alpaca" ? "Alpaca" : s === "auto" ? "Auto" : "Off"}
                  </Choice>
                ))}
                <button
                  onClick={testEquity}
                  disabled={mdBusy || md.categories.equity.realtime_source === "none"}
                  title="Probe Alpaca market data with the entered (or saved) API key"
                  className="shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                >
                  Test
                </button>
              </Row>
              <Row label="Realtime feed">
                {md.category_meta.equity.feeds.map((f) => (
                  <Choice
                    key={f}
                    active={md.categories.equity.realtime_feed === f}
                    disabled={md.categories.equity.realtime_source === "none"}
                    onClick={() => patchEquity({ realtime_feed: f })}
                  >
                    {f.toUpperCase()}
                  </Choice>
                ))}
              </Row>
              {md.category_meta.equity.depth_sources && (
                <Row label="Order-book depth">
                  <select
                    value={md.categories.equity.depth_source}
                    onChange={(e) => patchEquity({ depth_source: e.target.value })}
                    className={cx(llmInputCls, "min-w-[140px]")}
                    title="Order-book (L2) depth source"
                  >
                    {md.category_meta.equity.depth_sources.map((s) => (
                      <option key={s} value={s}>
                        {depthOptionLabel("equity", s)}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => testDepth("equity", md.categories.equity.depth_source)}
                    disabled={mdBusy || md.categories.equity.depth_source === "none"}
                    title="Probe the order-book depth source for the default symbol"
                    className="shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                  >
                    Test
                  </button>
                  {md.categories.equity.depth_source === "auto" && md.depth?.equity?.source && (
                    <span className="text-[10px] text-term-muted">→ {depthLabel(md.depth.equity.source)}</span>
                  )}
                  {DEPTH_VENDORS.has(md.categories.equity.depth_source) && !md.depth?.equity?.enabled && (
                    <NeedsCreds onGoto={onGotoProviders} />
                  )}
                </Row>
              )}
              <Row label="Intraday cache TTL (s)">
                <input
                  type="number"
                  min={5}
                  max={600}
                  step={5}
                  value={md.categories.equity.intraday_ttl}
                  onChange={(e) => patchEquity({ intraday_ttl: clampNum(Number(e.target.value), 5, 600) })}
                  className={cx(numCls, "w-16")}
                  aria-label="Equity intraday cache TTL seconds"
                />
              </Row>
              <Row label="Daily history (years)">
                <input
                  type="number"
                  min={1}
                  max={30}
                  step={1}
                  value={md.categories.equity.history_years}
                  onChange={(e) => patchEquity({ history_years: clampNum(Number(e.target.value), 1, 30) })}
                  className={cx(numCls, "w-16")}
                  aria-label="Equity default daily history years"
                />
              </Row>
              <Row label="Default symbol">
                <input
                  value={md.categories.equity.default_symbol}
                  onChange={(e) => patchEquity({ default_symbol: e.target.value.toUpperCase() })}
                  className={cx(llmInputCls, "max-w-[120px]")}
                  spellCheck={false}
                  autoComplete="off"
                  aria-label="Equity default symbol"
                />
              </Row>
            </div>
          </div>

          {/* ── Crypto category ── */}
          <div className="rounded border border-term-border/60 bg-term-sunken/30 p-2.5">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-term-accent">Crypto (spot)</div>
            <div className="divide-y divide-term-border/50">
              <Row label="Exchange (bars + realtime)">
                <select
                  value={md.categories.crypto.source}
                  onChange={(e) => patchCrypto({ source: e.target.value })}
                  className={cx(llmInputCls, "min-w-[140px]")}
                  title="ccxt exchange for crypto bars + realtime"
                >
                  {md.category_meta.crypto.sources.map((ex) => (
                    <option key={ex} value={ex}>
                      {ex}
                    </option>
                  ))}
                </select>
                <button
                  onClick={testExchange}
                  disabled={mdBusy}
                  title="Probe this exchange's markets"
                  className="shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                >
                  Test
                </button>
              </Row>
              <Row label="Realtime">
                <Choice active={md.categories.crypto.realtime} onClick={() => patchCrypto({ realtime: true })}>
                  On
                </Choice>
                <Choice active={!md.categories.crypto.realtime} onClick={() => patchCrypto({ realtime: false })}>
                  Off
                </Choice>
                <span className="text-[10px] text-term-muted">off keeps charts</span>
              </Row>
              {md.category_meta.crypto.depth_sources && (
                <Row label="Order-book depth">
                  <select
                    value={md.categories.crypto.depth_source}
                    onChange={(e) => patchCrypto({ depth_source: e.target.value })}
                    className={cx(llmInputCls, "min-w-[140px]")}
                    title="Order-book (L2) depth source"
                  >
                    {md.category_meta.crypto.depth_sources.map((s) => (
                      <option key={s} value={s}>
                        {depthOptionLabel("crypto", s)}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => testDepth("crypto", md.categories.crypto.depth_source)}
                    disabled={mdBusy || md.categories.crypto.depth_source === "none"}
                    title="Probe the crypto order-book depth source"
                    className="shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                  >
                    Test
                  </button>
                  {md.categories.crypto.depth_source === "auto" && md.depth?.crypto?.source && (
                    <span className="text-[10px] text-term-muted">→ {depthLabel(md.depth.crypto.source)}</span>
                  )}
                </Row>
              )}
              <Row label="Intraday cache TTL (s)">
                <input
                  type="number"
                  min={5}
                  max={600}
                  step={5}
                  value={md.categories.crypto.intraday_ttl}
                  onChange={(e) => patchCrypto({ intraday_ttl: clampNum(Number(e.target.value), 5, 600) })}
                  className={cx(numCls, "w-16")}
                  aria-label="Crypto intraday cache TTL seconds"
                />
              </Row>
              <Row label="Daily history (years)">
                <input
                  type="number"
                  min={1}
                  max={30}
                  step={1}
                  value={md.categories.crypto.history_years}
                  onChange={(e) => patchCrypto({ history_years: clampNum(Number(e.target.value), 1, 30) })}
                  className={cx(numCls, "w-16")}
                  aria-label="Crypto default daily history years"
                />
              </Row>
              <Row label="Default symbol">
                <input
                  value={md.categories.crypto.default_symbol}
                  onChange={(e) => patchCrypto({ default_symbol: e.target.value.toUpperCase() })}
                  className={cx(llmInputCls, "max-w-[120px]")}
                  spellCheck={false}
                  autoComplete="off"
                  aria-label="Crypto default symbol"
                />
              </Row>
            </div>
          </div>

          {/* ── FICC categories (rates futures / spot FX / commodity futures) ── */}
          {FICC_MD.map(({ key, label }) => {
            const cat = md.categories[key];
            if (!cat) return null;
            return (
              <div key={key} className="rounded border border-term-border/60 bg-term-sunken/30 p-2.5">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-term-accent">
                  {label}
                  <span className="ml-1.5 font-normal normal-case tracking-normal text-term-muted">· yfinance bars</span>
                </div>
                <div className="divide-y divide-term-border/50">
                  {md.category_meta[key]?.depth_sources && (
                    <Row label="Order-book depth">
                      <select
                        value={cat.depth_source}
                        onChange={(e) => patchFicc(key, { depth_source: e.target.value })}
                        className={cx(llmInputCls, "min-w-[140px]")}
                        title="Order-book (L2) depth source"
                      >
                        {md.category_meta[key].depth_sources.map((s) => (
                          <option key={s} value={s}>
                            {depthOptionLabel(key, s)}
                          </option>
                        ))}
                      </select>
                      <button
                        onClick={() => testDepth(key, cat.depth_source)}
                        disabled={mdBusy || cat.depth_source === "none"}
                        title="Probe the order-book depth source for the default symbol"
                        className="shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                      >
                        Test
                      </button>
                      {cat.depth_source === "auto" && md.depth?.[key]?.source && (
                        <span className="text-[10px] text-term-muted">→ {depthLabel(md.depth[key].source)}</span>
                      )}
                      {depthHint(key, cat.depth_source) && (
                        <span
                          className="text-[10px] text-term-muted"
                          title="This vendor's depth instrument differs from the spot/futures label above"
                        >
                          {depthHint(key, cat.depth_source)}
                        </span>
                      )}
                      {DEPTH_VENDORS.has(cat.depth_source) && !md.depth?.[key]?.enabled && (
                        <NeedsCreds onGoto={onGotoProviders} />
                      )}
                    </Row>
                  )}
                  <Row label="Intraday cache TTL (s)">
                    <input
                      type="number"
                      min={5}
                      max={600}
                      step={5}
                      value={cat.intraday_ttl}
                      onChange={(e) => patchFicc(key, { intraday_ttl: clampNum(Number(e.target.value), 5, 600) })}
                      className={cx(numCls, "w-16")}
                      aria-label={`${label} intraday cache TTL seconds`}
                    />
                  </Row>
                  <Row label="Daily history (years)">
                    <input
                      type="number"
                      min={1}
                      max={30}
                      step={1}
                      value={cat.history_years}
                      onChange={(e) => patchFicc(key, { history_years: clampNum(Number(e.target.value), 1, 30) })}
                      className={cx(numCls, "w-16")}
                      aria-label={`${label} default daily history years`}
                    />
                  </Row>
                  <Row label="Default symbol">
                    <input
                      value={cat.default_symbol}
                      onChange={(e) => patchFicc(key, { default_symbol: e.target.value.toUpperCase() })}
                      className={cx(llmInputCls, "max-w-[120px]")}
                      spellCheck={false}
                      autoComplete="off"
                      aria-label={`${label} default symbol`}
                    />
                  </Row>
                </div>
              </div>
            );
          })}

          {/* ── Options (standalone chain subsystem — a chain source + knobs) ── */}
          {md.categories.options && md.category_meta.options && (
            <div className="rounded border border-term-border/60 bg-term-sunken/30 p-2.5">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-term-accent">
                Options
                <span className="ml-1.5 font-normal normal-case tracking-normal text-term-muted">· chains</span>
              </div>
              <div className="divide-y divide-term-border/50">
                <Row label="Chain source">
                  <select
                    value={md.categories.options.source}
                    onChange={(e) => patchOptions({ source: e.target.value })}
                    className={cx(llmInputCls, "min-w-[160px]")}
                    title="Options-chain data source"
                  >
                    {md.category_meta.options.sources.map((s) => (
                      <option key={s} value={s}>
                        {optionsSourceLabel(s)}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() =>
                      testOptions(md.categories.options?.source ?? "", md.categories.options?.default_underlying ?? "")
                    }
                    disabled={mdBusy || md.categories.options.source === "none"}
                    title="Probe the options-chain source for the default underlying"
                    className="shrink-0 rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
                  >
                    Test
                  </button>
                  {OPTIONS_VENDORS.has(md.categories.options.source) && !md.options?.enabled && (
                    <NeedsCreds onGoto={onGotoProviders} />
                  )}
                </Row>
                {optionsCapNote(md.category_meta.options.capabilities[md.categories.options.source]) && (
                  <Row label="Provides">
                    <span className="text-[10px] text-term-muted">
                      {optionsCapNote(md.category_meta.options.capabilities[md.categories.options.source])}
                    </span>
                  </Row>
                )}
                <Row label="Default underlying">
                  <input
                    value={md.categories.options.default_underlying}
                    onChange={(e) => patchOptions({ default_underlying: e.target.value.toUpperCase() })}
                    className={cx(llmInputCls, "max-w-[120px]")}
                    spellCheck={false}
                    autoComplete="off"
                    aria-label="Options default underlying"
                  />
                </Row>
                <Row label="Expiries window (days)">
                  <input
                    type="number"
                    min={7}
                    max={365}
                    step={1}
                    value={md.categories.options.expiry_window}
                    onChange={(e) => patchOptions({ expiry_window: clampNum(Number(e.target.value), 7, 365) })}
                    className={cx(numCls, "w-16")}
                    aria-label="Options expiries window days"
                  />
                </Row>
                <Row label="Chain cache TTL (s)">
                  <input
                    type="number"
                    min={5}
                    max={600}
                    step={5}
                    value={md.categories.options.chain_ttl}
                    onChange={(e) => patchOptions({ chain_ttl: clampNum(Number(e.target.value), 5, 600) })}
                    className={cx(numCls, "w-16")}
                    aria-label="Options chain cache TTL seconds"
                  />
                </Row>
              </div>
            </div>
          )}

          <p className="text-[10px] leading-relaxed text-term-muted">
            Default symbols seed the channel tickers on first launch; they don't override a symbol
            you've already changed in a channel. Rates/FX/commodities use yfinance bars (no exchange
            or realtime tape) — the history window also sets how far back the auto data-refresh pulls
            their bars. Order-book depth for them comes from the selected depth source (Simulated by
            default — a modelled book around the real mid).
          </p>
        </div>
      )}

      {/* Alpaca credentials — paper broker today; reserved for an Alpaca data provider */}
      {md && (
        <div className="mt-3 border-t border-term-border pt-3">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-term-text">Alpaca credentials</span>
            <div className="flex items-center gap-1.5">
              <span
                className={cx(
                  "rounded px-1.5 py-0.5 text-[10px] font-medium",
                  md.has_alpaca_key ? "bg-term-up/15 text-term-up" : "bg-term-border/40 text-term-muted",
                )}
              >
                {md.has_alpaca_key ? "✓ key saved" : "no key saved"}
              </span>
              {md.has_alpaca_key && (
                <button
                  onClick={removeAlpaca}
                  disabled={mdBusy}
                  title="Delete the saved Alpaca credentials from the server"
                  className="focus-ring shrink-0 rounded border border-term-down/50 px-2 py-0.5 text-[10px] text-term-down hover:bg-term-down/10 disabled:opacity-50"
                >
                  Remove
                </button>
              )}
            </div>
          </div>
          <p className="mb-2 text-[10px] leading-relaxed text-term-muted">
            Used to route paper trades to Alpaca's hosted paper environment (otherwise a local
            simulator runs). Equity <b>bars stay on yfinance</b> — these keys are the hook for a
            future Alpaca market-data provider. The saved key is never shown again (only the dots);
            for security it's stored encrypted on the server.
          </p>
          <div className="space-y-1.5">
            <div className="relative">
              <input
                type={showMdKey ? "text" : "password"}
                value={mdKey}
                onChange={(e) => setMdKey(e.target.value)}
                placeholder={md.has_alpaca_key ? "API key — •••• saved (blank keeps it)" : "Alpaca API key"}
                className={cx(llmInputCls, "pr-12")}
                spellCheck={false}
                autoComplete="off"
              />
              <button
                type="button"
                onClick={() => setShowMdKey((v) => !v)}
                disabled={!mdKey}
                title={showMdKey ? "Mask the API key" : "Reveal the API key"}
                aria-label={showMdKey ? "Mask the API key" : "Reveal the API key"}
                aria-pressed={showMdKey}
                className="absolute inset-y-0 right-1.5 my-auto h-fit rounded px-1 text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text disabled:opacity-30"
              >
                {showMdKey ? "Hide" : "Show"}
              </button>
            </div>
            <div className="relative">
              <input
                type={showMdSecret ? "text" : "password"}
                value={mdSecret}
                onChange={(e) => setMdSecret(e.target.value)}
                placeholder={md.has_alpaca_key ? "API secret — •••• saved (blank keeps it)" : "Alpaca API secret"}
                className={cx(llmInputCls, "pr-12")}
                spellCheck={false}
                autoComplete="off"
              />
              <button
                type="button"
                onClick={() => setShowMdSecret((v) => !v)}
                disabled={!mdSecret}
                title={showMdSecret ? "Mask the API secret" : "Reveal the API secret"}
                aria-label={showMdSecret ? "Mask the API secret" : "Reveal the API secret"}
                aria-pressed={showMdSecret}
                className="absolute inset-y-0 right-1.5 my-auto h-fit rounded px-1 text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text disabled:opacity-30"
              >
                {showMdSecret ? "Hide" : "Show"}
              </button>
            </div>
          </div>
          {(mdKey || mdSecret) && (
            <p className="mt-1 text-[10px] font-medium text-term-accent">
              ● Unsaved — click <b>Save</b> below to store these. <b>Test</b> only probes; it doesn't save.
            </p>
          )}
          <Row label="Environment">
            <Choice active={md.alpaca_paper} onClick={() => setMd({ ...md, alpaca_paper: true })}>
              Paper
            </Choice>
            <Choice active={!md.alpaca_paper} onClick={() => setMd({ ...md, alpaca_paper: false })}>
              Live
            </Choice>
          </Row>
          <p className="mt-1 text-[10px] leading-relaxed text-term-muted">
            Real-time equity ticker + time-&-sales (Market Board, Watchlist, Time&nbsp;&amp;&nbsp;Sales)
            stream from Alpaca when the <b>Equity → Realtime source</b> is Alpaca and keys are set.
            The <b>IEX</b>/<b>SIP</b> feed is chosen in the Equity category above. Alpaca has no equity
            L2, so order-book depth for equities/rates/FX/commodities comes from the per-class
            <b>Order-book depth</b> source (Simulated by default); crypto uses its real exchange L2.
          </p>
        </div>
      )}

      {/* read-only status */}
      {md && (
        <div className="mt-3 border-t border-term-border pt-3">
          <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-term-muted">Status</div>
          <dl className="space-y-1 text-[10px]">
            <div className="flex items-start justify-between gap-3">
              <dt className="text-term-muted">Cache dir</dt>
              <dd className="truncate text-right font-mono text-term-text" title={md.data_dir}>{md.data_dir}</dd>
            </div>
            <div className="flex items-start justify-between gap-3">
              <dt className="text-term-muted">qhfi lake</dt>
              <dd className="truncate text-right font-mono text-term-text" title={md.lake_dir}>{md.lake_dir}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-term-muted">Cached symbols</dt>
              <dd className="tabular-nums text-term-text">{md.cached_symbols}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-term-muted">Realtime</dt>
              <dd className="text-term-text">
                {md.realtime.topics.length} topic(s)
                {md.realtime.exchanges.length ? ` · ${md.realtime.exchanges.join(", ")}` : ""}
              </dd>
            </div>
          </dl>
        </div>
      )}

      <div className="mt-3 flex items-center gap-1.5">
        <button
          onClick={saveMarketData}
          disabled={mdBusy || !md}
          className="rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50"
        >
          Save
        </button>
        <button
          onClick={clearMarketCache}
          disabled={mdBusy}
          title="Drop the intraday cache + rebuild the data manager"
          className="rounded border border-term-border px-2.5 py-1 text-xs text-term-muted hover:text-term-text disabled:opacity-50"
        >
          Clear cache
        </button>
      </div>

      <Status busy={mdBusy} msg={mdMsg} />
    </div>
  );
}
