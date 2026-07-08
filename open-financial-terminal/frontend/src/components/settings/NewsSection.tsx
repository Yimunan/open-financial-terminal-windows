import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { NewsFeedCandidate, NewsSource, NewsSourceSettings } from "../../api/types";
import { cx } from "../../lib/format";
import { clampNum, llmInputCls, type Msg, numCls, SectionHeader, Status, WeightSlider } from "./common";

/** News sources: which feeds the News widget pulls from, their ranking priority, custom RSS feeds,
 * feed discovery, and the composite ranking formula. */
export default function NewsSection() {
  const qc = useQueryClient();
  const [news, setNews] = useState<NewsSourceSettings | null>(null);
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [newsBusy, setNewsBusy] = useState(false);
  const [newsMsg, setNewsMsg] = useState<Msg>(null);
  const [discoverQ, setDiscoverQ] = useState("");
  const [candidates, setCandidates] = useState<NewsFeedCandidate[]>([]);

  useEffect(() => {
    api.newsSettings().then(setNews).catch(() => {});
  }, []);

  const toggleBuiltin = (key: string) =>
    setNews((n) => (n ? { ...n, builtin: { ...n.builtin, [key]: !n.builtin[key] } } : n));
  const setBuiltinWeight = (key: string, weight: number) =>
    setNews((n) => (n ? { ...n, builtin_weights: { ...n.builtin_weights, [key]: weight } } : n));
  const setRanking = (patch: Partial<NewsSourceSettings["ranking"]>) =>
    setNews((n) => (n ? { ...n, ranking: { ...n.ranking, ...patch } } : n));
  const resetRanking = () => setNews((n) => (n ? { ...n, ranking: { ...n.ranking_default } } : n));
  const patchCustom = (i: number, patch: Partial<NewsSource>) =>
    setNews((n) => (n ? { ...n, custom: n.custom.map((c, j) => (j === i ? { ...c, ...patch } : c)) } : n));
  const removeCustom = (i: number) =>
    setNews((n) => (n ? { ...n, custom: n.custom.filter((_, j) => j !== i) } : n));
  const addCustom = () => {
    if (!news || !newUrl.trim()) return;
    setNews({ ...news, custom: [...news.custom, { name: newName.trim() || "Custom feed", url: newUrl.trim(), enabled: true, weight: 50 }] });
    setNewName("");
    setNewUrl("");
  };

  const saveNews = async () => {
    if (!news) return;
    setNewsBusy(true);
    setNewsMsg(null);
    try {
      const saved = await api.saveNewsSettings({
        builtin: news.builtin,
        builtin_weights: news.builtin_weights,
        custom: news.custom,
        max_items: news.max_items,
        ranking: news.ranking,
      });
      setNews(saved);
      const active = Object.values(saved.builtin).filter(Boolean).length + saved.custom.filter((c) => c.enabled).length;
      setNewsMsg({ ok: true, detail: `Saved · ${active} source(s) active` });
      qc.invalidateQueries({ queryKey: ["news"] });
    } catch (e) {
      setNewsMsg({ ok: false, detail: e instanceof Error ? e.message : "save failed" });
    } finally {
      setNewsBusy(false);
    }
  };

  const testNewsUrl = async (url: string, name: string) => {
    if (!url.trim()) return;
    setNewsBusy(true);
    setNewsMsg(null);
    try {
      const r = await api.testNewsSource({ url: url.trim(), name: name.trim() });
      setNewsMsg({ ok: r.ok, detail: r.detail });
    } catch (e) {
      setNewsMsg({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setNewsBusy(false);
    }
  };

  const discoverFeeds = async () => {
    if (!discoverQ.trim()) return;
    setNewsBusy(true);
    setNewsMsg(null);
    setCandidates([]);
    try {
      const r = await api.discoverNewsSources(discoverQ.trim());
      setCandidates(r.candidates);
      setNewsMsg({ ok: r.ok, detail: r.detail });
    } catch (e) {
      setNewsMsg({ ok: false, detail: e instanceof Error ? e.message : "search failed" });
    } finally {
      setNewsBusy(false);
    }
  };

  const addCandidate = (c: NewsFeedCandidate) => {
    setNews((n) => (n ? { ...n, custom: [...n.custom, { name: c.title, url: c.url, enabled: true, weight: 50 }] } : n));
    setCandidates((cs) => cs.filter((x) => x.url !== c.url));
  };

  return (
    <div>
      <SectionHeader
        title="News Sources"
        chip={
          news
            ? `${Object.values(news.builtin).filter(Boolean).length + news.custom.filter((c) => c.enabled).length} active`
            : undefined
        }
      />
      <p className="mb-2 text-[10px] leading-relaxed text-term-muted">
        Choose which feeds the News widget pulls from, and set each source's ranking priority
        (the slider, 0–100 · 50 = neutral) — higher floats that source up the ranked feed. Add
        your own RSS feeds with <span className="font-mono">{"{symbol}"}</span> in the URL — e.g.{" "}
        <span className="font-mono">https://news.google.com/rss/search?q={"{symbol}"}+stock</span>.
      </p>

      {/* max headlines — the News widget feed cap (ranked, then truncated to this) */}
      {news && (
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-[10px] uppercase tracking-wider text-term-muted">Max headlines</span>
          <input
            type="number"
            min={10}
            max={100}
            step={5}
            value={news.max_items}
            onChange={(e) =>
              setNews((n) =>
                n ? { ...n, max_items: Math.max(10, Math.min(100, Number(e.target.value) || 30)) } : n,
              )
            }
            aria-label="Max headlines"
            className="focus-ring w-20 rounded border border-term-border bg-term-sunken px-2 py-1 text-xs text-term-text focus:border-term-accent"
          />
        </div>
      )}

      {/* built-in feeds */}
      <div className="space-y-1">
        {(news?.builtin_meta ?? []).map((b) => (
          <div key={b.key} className="flex items-center gap-2 text-xs text-term-text">
            <label className="flex flex-1 cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={news?.builtin[b.key] ?? false}
                onChange={() => toggleBuiltin(b.key)}
                className="accent-term-accent"
              />
              {b.label}
            </label>
            <WeightSlider value={news?.builtin_weights[b.key] ?? 50} onChange={(n) => setBuiltinWeight(b.key, n)} />
          </div>
        ))}
      </div>

      {/* custom feeds */}
      {news && news.custom.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {news.custom.map((c, i) => (
            <div key={i} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={c.enabled}
                onChange={() => patchCustom(i, { enabled: !c.enabled })}
                className="accent-term-accent"
                title="Enabled"
              />
              <input
                value={c.name}
                onChange={(e) => patchCustom(i, { name: e.target.value })}
                placeholder="Name"
                className={cx(llmInputCls, "w-24 shrink-0")}
                spellCheck={false}
              />
              <input
                value={c.url}
                onChange={(e) => patchCustom(i, { url: e.target.value })}
                placeholder="RSS URL with {symbol}"
                className={cx(llmInputCls, "flex-1")}
                spellCheck={false}
              />
              <WeightSlider value={c.weight ?? 50} onChange={(n) => patchCustom(i, { weight: n })} />
              <button
                onClick={() => testNewsUrl(c.url, c.name)}
                disabled={newsBusy}
                title="Test feed"
                className="shrink-0 rounded border border-term-border px-1.5 py-1 text-[10px] text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
              >
                Test
              </button>
              <button
                onClick={() => removeCustom(i)}
                title="Remove feed"
                className="shrink-0 px-1 text-term-muted hover:text-term-down"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {/* discover feeds by keyword / site */}
      <div className="mt-3 border-t border-term-border/40 pt-2">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-term-muted">Discover feeds</div>
        <div className="flex items-center gap-1">
          <input
            value={discoverQ}
            onChange={(e) => setDiscoverQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && discoverFeeds()}
            placeholder="Search a site or keyword, e.g. reuters or marketwatch.com"
            className={cx(llmInputCls, "flex-1")}
            spellCheck={false}
          />
          <button
            onClick={discoverFeeds}
            disabled={newsBusy || !discoverQ.trim()}
            className="shrink-0 rounded border border-term-accent px-1.5 py-1 text-[10px] text-term-accent hover:bg-term-accent/10 disabled:opacity-40"
          >
            Search
          </button>
        </div>
        {candidates.length > 0 && (
          <div className="mt-1.5 space-y-1">
            {candidates.map((c) => (
              <div key={c.url} className="flex items-center gap-1.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs text-term-text">{c.title}</div>
                  <div className="truncate text-[10px] text-term-muted" title={c.url}>
                    {c.url}
                  </div>
                </div>
                <button
                  onClick={() => addCandidate(c)}
                  className="shrink-0 rounded border border-term-accent px-1.5 py-1 text-[10px] text-term-accent hover:bg-term-accent/10"
                >
                  + Add
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* add a custom feed */}
      <div className="mt-2 flex items-center gap-1">
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="Name"
          className={cx(llmInputCls, "w-24 shrink-0")}
          spellCheck={false}
        />
        <input
          value={newUrl}
          onChange={(e) => setNewUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addCustom()}
          placeholder="RSS URL with {symbol}"
          className={cx(llmInputCls, "flex-1")}
          spellCheck={false}
        />
        <button
          onClick={() => testNewsUrl(newUrl, newName)}
          disabled={newsBusy || !newUrl.trim()}
          className="shrink-0 rounded border border-term-border px-1.5 py-1 text-[10px] text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
        >
          Test
        </button>
        <button
          onClick={addCustom}
          disabled={!newUrl.trim()}
          className="shrink-0 rounded border border-term-accent px-1.5 py-1 text-[10px] text-term-accent hover:bg-term-accent/10 disabled:opacity-40"
        >
          + Add
        </button>
      </div>

      {/* ranking formula — explanation + tunable weights */}
      {news &&
        (() => {
          const r = news.ranking;
          const wrows = [
            ["recency", "Recency"],
            ["source", "Source priority"],
            ["relevance", "Relevance (LLM)"],
            ["sentiment", "Sentiment conviction"],
            ["match", "Ticker in title"],
          ] as const;
          const sum = wrows.reduce((a, [k]) => a + (r[k] || 0), 0) || 1;
          return (
            <div className="mt-3 border-t border-term-border/40 pt-2">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-wider text-term-muted">Ranking formula</span>
                <button
                  onClick={resetRanking}
                  className="focus-ring rounded text-[10px] text-term-muted hover:text-term-accent"
                >
                  Reset to defaults
                </button>
              </div>
              <p className="mb-2 text-[10px] leading-relaxed text-term-muted">
                In <span className="text-term-accent">Ranked</span> mode every headline gets a 0–1
                score blending five signals: how <b>recent</b> it is, the <b>source priority</b>
                {" "}(the sliders above), the LLM's <b>relevance</b> to the symbol, sentiment{" "}
                <b>conviction</b> (|score|), and whether the title <b>mentions the ticker</b>.
                The weights below set each signal's share — they're normalised, so only their{" "}
                <i>proportions</i> matter (the % shown). Recency halves every{" "}
                <b>{r.halflife_h}h</b>.
              </p>
              {/* live equation — coefficients are the normalised weights */}
              <div className="mb-2 overflow-x-auto rounded border border-term-border/60 bg-term-sunken px-2 py-1.5 font-mono text-[10px] leading-relaxed">
                <div className="whitespace-nowrap">
                  <span className="text-term-accent">score</span> ={" "}
                  {wrows.map(([k], idx) => (
                    <span key={k} className="text-term-text">
                      {idx > 0 ? " + " : ""}
                      <span className="text-term-accent">{((r[k] || 0) / sum).toFixed(2)}</span>·{k}
                    </span>
                  ))}
                </div>
                <div className="whitespace-nowrap text-term-muted">
                  recency = 0.5<sup>(age_hours / {r.halflife_h})</sup> &nbsp;·&nbsp; source,
                  relevance, sentiment, match ∈ [0, 1]
                </div>
              </div>
              <div className="space-y-0.5">
                {wrows.map(([key, label]) => (
                  <div key={key} className="flex items-center gap-2 text-xs">
                    <span className="w-32 shrink-0 text-term-muted">{label}</span>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={r[key]}
                      onChange={(e) =>
                        setRanking({ [key]: Number(e.target.value) } as Partial<NewsSourceSettings["ranking"]>)
                      }
                      className="h-1 flex-1 accent-term-accent"
                      aria-label={`${label} weight`}
                    />
                    <input
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={r[key]}
                      onChange={(e) =>
                        setRanking({ [key]: clampNum(Number(e.target.value), 0, 1) } as Partial<NewsSourceSettings["ranking"]>)
                      }
                      className={numCls}
                      aria-label={`${label} weight value`}
                    />
                    <span className="w-9 text-right text-[10px] tabular-nums text-term-accent">
                      {Math.round(((r[key] || 0) / sum) * 100)}%
                    </span>
                  </div>
                ))}
                <div className="flex items-center gap-2 pt-0.5 text-xs">
                  <span className="w-32 shrink-0 text-term-muted">Recency half-life</span>
                  <input
                    type="range"
                    min={1}
                    max={72}
                    step={1}
                    value={r.halflife_h}
                    onChange={(e) => setRanking({ halflife_h: Number(e.target.value) })}
                    className="h-1 flex-1 accent-term-accent"
                    aria-label="Recency half-life (hours)"
                  />
                  <span className="flex items-center gap-0.5">
                    <input
                      type="number"
                      min={1}
                      max={168}
                      step={1}
                      value={r.halflife_h}
                      onChange={(e) => setRanking({ halflife_h: clampNum(Number(e.target.value), 1, 168) })}
                      className={numCls}
                      aria-label="Recency half-life value (hours)"
                    />
                    <span className="text-[10px] text-term-muted">h</span>
                  </span>
                </div>
              </div>
            </div>
          );
        })()}

      <div className="mt-3 flex items-center gap-1.5">
        <button
          onClick={saveNews}
          disabled={newsBusy || !news}
          className="rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50"
        >
          Save
        </button>
      </div>

      <Status busy={newsBusy} msg={newsMsg} />
    </div>
  );
}
