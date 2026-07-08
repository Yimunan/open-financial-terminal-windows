import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { ProviderIn, ProviderStatus } from "../../api/types";
import { cx } from "../../lib/format";
import { Choice, llmInputCls, type Msg, Status } from "./common";

/** Static descriptor for one vendor card: what it powers, its secret field, and how to Test it. */
type ProviderDef = {
  id: string;
  name: string;
  powers: string; // "order-book depth" | "options chains" | both
  blurb: string;
  secretField?: "api_key" | "token" | "address"; // the encrypted field (absent for IBKR)
  secretLabel?: string;
  hasEnv?: boolean; // tradier: live/sandbox
  ibkr?: boolean; // host/port instead of a secret
  test: "depth" | "options";
};

const PROVIDERS: ProviderDef[] = [
  {
    id: "databento",
    name: "Databento",
    powers: "Order-book depth (equities, futures)",
    blurb: "Live MBP-10 10-level depth for equities and CME futures.",
    secretField: "api_key",
    secretLabel: "API key",
    test: "depth",
  },
  {
    id: "dxfeed",
    name: "dxFeed",
    powers: "Order-book depth (equities, futures, FX)",
    blurb: "Aggregated depth from a dxFeed endpoint (a token address, or demo.dxfeed.com:7300).",
    secretField: "address",
    secretLabel: "Endpoint address",
    test: "depth",
  },
  {
    id: "polygon",
    name: "Polygon.io",
    powers: "Options chains",
    blurb: "Options chains with greeks + IV via the snapshot API.",
    secretField: "api_key",
    secretLabel: "API key",
    test: "options",
  },
  {
    id: "tradier",
    name: "Tradier",
    powers: "Options chains",
    blurb: "Options chains with greeks + IV. The free sandbox works with delayed data.",
    secretField: "token",
    secretLabel: "API token",
    hasEnv: true,
    test: "options",
  },
  {
    id: "ibkr",
    name: "Interactive Brokers",
    powers: "Order-book depth + options",
    blurb: "Depth + option chains via a running IB Gateway / TWS. Set the API host + port.",
    ibkr: true,
    test: "depth",
  },
];

/** Per-provider editable form state (secrets stay blank = keep saved). */
type Draft = { secret: string; show: boolean; env: string; host: string; port: string };
const emptyDraft = (): Draft => ({ secret: "", show: false, env: "live", host: "", port: "" });

/** Settings → Data Providers: enter the market-data vendor credentials that were previously env-var
 * only (Databento / Polygon / Tradier / dxFeed / IBKR). Stored encrypted server-side; a saved secret
 * is never shown again. Test reuses the depth/options probe against the saved credentials. */
export default function DataProvidersSection() {
  const [providers, setProviders] = useState<Record<string, ProviderStatus>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState<string>(""); // provider id currently acting, "" = idle
  const [msg, setMsg] = useState<Msg>(null);

  const applyStatus = (p: Record<string, ProviderStatus>) => {
    setProviders(p);
    // seed non-secret drafts (env / host / port) from the saved status; keep secrets blank
    setDrafts((prev) => {
      const next = { ...prev };
      for (const def of PROVIDERS) {
        const st = p[def.id] ?? {};
        next[def.id] = {
          ...(prev[def.id] ?? emptyDraft()),
          env: (st.env as string) || prev[def.id]?.env || "live",
          host: st.host ?? prev[def.id]?.host ?? "",
          port: st.port ?? prev[def.id]?.port ?? "",
        };
      }
      return next;
    });
  };

  useEffect(() => {
    api.providerSettings().then((r) => applyStatus(r.providers)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const patch = (id: string, p: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [id]: { ...(d[id] ?? emptyDraft()), ...p } }));

  const save = async (def: ProviderDef) => {
    const d = drafts[def.id] ?? emptyDraft();
    const body: ProviderIn = { name: def.id };
    if (def.secretField) body[def.secretField] = d.secret; // blank keeps the saved secret
    if (def.hasEnv) body.env = d.env;
    if (def.ibkr) {
      body.host = d.host;
      body.port = d.port;
    }
    setBusy(def.id);
    setMsg(null);
    try {
      const r = await api.saveProvider(body);
      applyStatus(r.providers);
      patch(def.id, { secret: "", show: false });
      setMsg({ ok: true, detail: `${def.name} saved` });
    } catch (e) {
      setMsg({ ok: false, detail: e instanceof Error ? e.message : "save failed" });
    } finally {
      setBusy("");
    }
  };

  const remove = async (def: ProviderDef) => {
    if (!window.confirm(`Remove the saved ${def.name} credentials?`)) return;
    setBusy(def.id);
    setMsg(null);
    try {
      const r = await api.removeProvider(def.id);
      applyStatus(r.providers);
      patch(def.id, emptyDraft());
      setMsg({ ok: true, detail: `${def.name} credentials removed` });
    } catch (e) {
      setMsg({ ok: false, detail: e instanceof Error ? e.message : "remove failed" });
    } finally {
      setBusy("");
    }
  };

  const test = async (def: ProviderDef) => {
    setBusy(def.id);
    setMsg(null);
    try {
      const r =
        def.test === "options"
          ? await api.testMarketDataOptions({ source: def.id })
          : await api.testMarketDataDepth({ asset: "equity", source: def.id });
      setMsg(r);
    } catch (e) {
      setMsg({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setBusy("");
    }
  };

  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold text-term-text">Data Providers</div>
      <p className="mb-3 text-[10px] leading-relaxed text-term-muted">
        Credentials for the optional market-data vendors selected in <b>Market Data</b> (order-book
        depth + options chains). Keys are stored <b>encrypted</b> on the server — a saved secret is
        never shown again (leave a field blank to keep it). These also fall back to their historical
        environment variables. <b>Test</b> probes the vendor with the <i>saved</i> credentials, so
        save first.
      </p>

      <div className="space-y-3">
        {PROVIDERS.map((def) => {
          const st = providers[def.id] ?? {};
          const d = drafts[def.id] ?? emptyDraft();
          const configured = def.ibkr ? !!st.configured : !!st.has_key;
          const chip = configured
            ? { text: "✓ configured", cls: "bg-term-up/15 text-term-up" }
            : st.from_env
              ? { text: "via env", cls: "bg-term-accent/15 text-term-accent" }
              : { text: "● not set", cls: "bg-term-border/40 text-term-muted" };
          const isBusy = busy === def.id;
          return (
            <div key={def.id} className="rounded border border-term-border/60 bg-term-sunken/30 p-2.5">
              <div className="mb-1 flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="text-[11px] font-semibold text-term-text">{def.name}</span>
                  <span className="ml-1.5 text-[10px] text-term-muted">· {def.powers}</span>
                </div>
                <span className={cx("shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium", chip.cls)}>{chip.text}</span>
              </div>
              <p className="mb-2 text-[10px] leading-relaxed text-term-muted">{def.blurb}</p>

              {/* credential fields */}
              {def.secretField && (
                <div className="relative">
                  <input
                    type={d.show ? "text" : "password"}
                    value={d.secret}
                    onChange={(e) => patch(def.id, { secret: e.target.value })}
                    placeholder={st.has_key ? `${def.secretLabel} — •••• saved (blank keeps it)` : def.secretLabel}
                    className={cx(llmInputCls, "pr-12")}
                    spellCheck={false}
                    autoComplete="off"
                    aria-label={`${def.name} ${def.secretLabel}`}
                  />
                  <button
                    type="button"
                    onClick={() => patch(def.id, { show: !d.show })}
                    disabled={!d.secret}
                    title={d.show ? "Mask" : "Reveal"}
                    aria-pressed={d.show}
                    className="absolute inset-y-0 right-1.5 my-auto h-fit rounded px-1 text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text disabled:opacity-30"
                  >
                    {d.show ? "Hide" : "Show"}
                  </button>
                </div>
              )}

              {def.hasEnv && (
                <div className="mt-1.5 flex items-center gap-1.5">
                  <span className="text-[10px] text-term-muted">Environment</span>
                  <Choice active={d.env !== "sandbox"} onClick={() => patch(def.id, { env: "live" })}>
                    Live
                  </Choice>
                  <Choice active={d.env === "sandbox"} onClick={() => patch(def.id, { env: "sandbox" })}>
                    Sandbox
                  </Choice>
                </div>
              )}

              {def.ibkr && (
                <div className="flex items-center gap-1.5">
                  <input
                    value={d.host}
                    onChange={(e) => patch(def.id, { host: e.target.value })}
                    placeholder="Host — 127.0.0.1"
                    className={cx(llmInputCls, "flex-1")}
                    spellCheck={false}
                    autoComplete="off"
                    aria-label="IBKR host"
                  />
                  <input
                    value={d.port}
                    onChange={(e) => patch(def.id, { port: e.target.value.replace(/[^0-9]/g, "") })}
                    placeholder="Port — 4002"
                    className={cx(llmInputCls, "w-24 shrink-0")}
                    spellCheck={false}
                    autoComplete="off"
                    inputMode="numeric"
                    aria-label="IBKR port"
                  />
                </div>
              )}

              <div className="mt-2 flex items-center gap-1.5">
                <button
                  onClick={() => save(def)}
                  disabled={isBusy}
                  className="rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50"
                >
                  Save
                </button>
                <button
                  onClick={() => test(def)}
                  disabled={isBusy}
                  title="Probe the vendor with the saved credentials"
                  className="rounded border border-term-border px-2.5 py-1 text-xs text-term-muted hover:text-term-text disabled:opacity-50"
                >
                  Test
                </button>
                {configured && (
                  <button
                    onClick={() => remove(def)}
                    disabled={isBusy}
                    title="Delete the saved credentials from the server"
                    className="ml-auto rounded border border-term-down/50 px-2.5 py-1 text-xs text-term-down hover:bg-term-down/10 disabled:opacity-50"
                  >
                    Remove
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <Status busy={!!busy} msg={msg} />
    </div>
  );
}
