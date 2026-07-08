import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { LlmSettings } from "../../api/types";
import { cx } from "../../lib/format";
import { llmInputCls, type Msg, SectionHeader, Status } from "./common";

/** LLM provider (server-side): the local proxy vs a custom OpenAI-compatible API — which may be a
 * local server (Ollama, LM Studio) or a hosted provider — plus the default model.
 * Re-points the whole terminal (assistant, agents, backtest narration, news sentiment) on save. */

/** True when a base URL points at this machine (or a mDNS/LAN host) rather than a hosted API, so
 * the provider chip can say "Local API" instead of the misleading "Online API" for e.g. Ollama. */
const isLocalUrl = (u: string): boolean =>
  /^https?:\/\/(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|[^/:]+\.local)(?=[:/]|$)/i.test(u.trim());

export default function LlmSection() {
  const [llm, setLlm] = useState<LlmSettings | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [llmBusy, setLlmBusy] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [testRes, setTestRes] = useState<Msg>(null);
  const [models, setModels] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await api.llmSettings();
        if (cancelled) return;
        setLlm(s);
        setBaseUrl(s.custom ? s.base_url : "");
        setModel(s.model_pinned ? s.model : "");
        setApiKey("");
        setShowKey(false);
        setTestRes(null);
        // detect the models the active provider serves so the user can switch between them
        const r = await api.llmModels();
        if (!cancelled) setModels(r.models ?? []);
      } catch {
        /* leave defaults */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const saveLlm = async () => {
    setLlmBusy(true);
    try {
      const s = await api.saveLlmSettings({ base_url: baseUrl.trim(), api_key: apiKey, model: model.trim() });
      setLlm(s);
      setApiKey("");
      setBaseUrl(s.custom ? s.base_url : "");
      setModel(s.model_pinned ? s.model : "");
      setTestRes({
        ok: true,
        detail: s.custom ? `Saved · ${s.model}` : s.model_pinned ? `Local model · ${s.model}` : "Local proxy · auto model",
      });
      const r = await api.llmModels();
      setModels(r.models ?? []);
    } catch (e) {
      setTestRes({ ok: false, detail: e instanceof Error ? e.message : "save failed" });
    } finally {
      setLlmBusy(false);
    }
  };

  const useLocalProxy = async () => {
    setBaseUrl("");
    setModel("");
    setApiKey("");
    setLlmBusy(true);
    try {
      const s = await api.saveLlmSettings({ base_url: "" });
      setLlm(s);
      setTestRes({ ok: true, detail: "Using local proxy" });
    } finally {
      setLlmBusy(false);
    }
  };

  return (
    <div>
      <SectionHeader
        title="Model & provider"
        chip={llm?.custom ? (isLocalUrl(llm.base_url) ? "Local API" : "Online API") : "Local proxy"}
        chipActive={!!llm?.custom}
      />

      {/* ── Default model — the model used everywhere ── */}
      <label className="mb-1 block text-[11px] font-semibold text-term-text">Default model</label>
      <p className="mb-1.5 text-[10px] leading-relaxed text-term-muted">
        The model the whole terminal uses. <span className="text-term-text">Auto</span> lets the
        active provider pick.
      </p>
      <select
        value={model}
        onChange={(e) => setModel(e.target.value)}
        className={llmInputCls}
        title="Choose the default model — pick one to switch the active LLM"
      >
        <option value="">Auto (server default)</option>
        {model && !models.includes(model) && <option value={model}>{model}</option>}
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      <input
        value={model}
        onChange={(e) => setModel(e.target.value)}
        placeholder="or type a model id — e.g. gpt-4o-mini"
        className={cx(llmInputCls, "mt-1.5")}
        spellCheck={false}
      />

      {/* ── Provider (optional) — point at any OpenAI-compatible API, hosted or local ── */}
      <label className="mb-1 mt-3 block text-[11px] font-semibold text-term-text">
        Provider <span className="font-normal text-term-muted">— optional</span>
      </label>
      <p className="mb-2 text-[10px] leading-relaxed text-term-muted">
        Leave blank for the local proxy. Set a Base URL to use any OpenAI-compatible API — hosted
        (OpenAI, DeepSeek; needs an API key) or local (Ollama, LM Studio; no key).
      </p>

      <div className="space-y-1.5">
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="Base URL — https://api.openai.com/v1 or http://localhost:11434/v1"
          className={llmInputCls}
          spellCheck={false}
        />
        <div className="relative">
          <input
            type={showKey ? "text" : "password"}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={llm?.has_key ? "API key — •••• saved (blank keeps it)" : "API key"}
            className={cx(llmInputCls, "pr-12")}
            spellCheck={false}
            autoComplete="off"
          />
          <button
            type="button"
            onClick={() => setShowKey((v) => !v)}
            disabled={!apiKey}
            title={showKey ? "Mask the API key" : "Reveal the API key"}
            aria-label={showKey ? "Mask the API key" : "Reveal the API key"}
            aria-pressed={showKey}
            className="absolute inset-y-0 right-1.5 my-auto h-fit rounded px-1 text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text disabled:opacity-30"
          >
            {showKey ? "Hide" : "Show"}
          </button>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-1.5">
        <button
          onClick={saveLlm}
          disabled={llmBusy}
          className="rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50"
        >
          Save
        </button>
        <button
          onClick={useLocalProxy}
          disabled={llmBusy}
          className="ml-auto text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text disabled:opacity-50"
        >
          Use local proxy
        </button>
      </div>

      <Status busy={llmBusy} msg={testRes} />
    </div>
  );
}
