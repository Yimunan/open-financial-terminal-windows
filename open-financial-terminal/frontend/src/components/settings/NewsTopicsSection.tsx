import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { NewsTopic } from "../../api/types";
import { cx } from "../../lib/format";
import { type Msg, Status, topicInputCls } from "./common";

/** News topics ("interest subscriptions"): comma-separated labels + a query, each openable from the
 * ⌘K launcher. Add/remove/enable persist immediately; label/keyword edits commit on "Save edits". */
export default function NewsTopicsSection() {
  const qc = useQueryClient();
  const [topics, setTopics] = useState<NewsTopic[] | null>(null);
  const [topLabel, setTopLabel] = useState("");
  const [topQuery, setTopQuery] = useState("");
  const [topBusy, setTopBusy] = useState(false);
  const [topMsg, setTopMsg] = useState<Msg>(null);

  useEffect(() => {
    api.newsTopicsConfig().then((r) => setTopics(r.topics)).catch(() => {});
  }, []);

  // Persist a topic list to the backend and reflect the server's normalized result. Shared by the
  // immediate add/remove/toggle actions and the explicit "Save edits" button.
  const persistTopics = async (list: NewsTopic[]) => {
    setTopBusy(true);
    setTopMsg(null);
    try {
      const r = await api.saveNewsTopics(list);
      setTopics(r.topics);
      setTopMsg({ ok: true, detail: `Saved · ${r.topics.length} topic(s)` });
      qc.invalidateQueries({ queryKey: ["news-topics"] }); // refresh the Cmd+K launcher
      return r.topics;
    } catch (e) {
      setTopMsg({ ok: false, detail: e instanceof Error ? e.message : "save failed" });
      return null;
    } finally {
      setTopBusy(false);
    }
  };

  // Inline label/query text edits stay local until "Save edits" (avoid a round-trip per keystroke).
  const patchTopic = (i: number, patch: Partial<NewsTopic>) =>
    setTopics((ts) => (ts ? ts.map((t, j) => (j === i ? { ...t, ...patch } : t)) : ts));
  // Add / remove / enable-toggle persist immediately so there's no separate save step for them.
  const toggleTopic = (i: number) =>
    persistTopics((topics ?? []).map((t, j) => (j === i ? { ...t, enabled: !t.enabled } : t)));
  const removeTopic = (i: number) => persistTopics((topics ?? []).filter((_, j) => j !== i));
  // Labels are entered comma-separated (one topic can carry several aliases, all on one query).
  const parseLabels = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const addTopic = async () => {
    const labels = parseLabels(topLabel);
    if (!labels.length || !topQuery.trim()) return;
    const saved = await persistTopics([...(topics ?? []), { key: "", labels, query: topQuery.trim(), enabled: true }]);
    if (saved) {
      setTopLabel("");
      setTopQuery("");
    }
  };

  const previewTopic = async (query: string) => {
    if (!query.trim()) return;
    setTopBusy(true);
    setTopMsg(null);
    try {
      const r = await api.previewNewsTopic(query.trim());
      setTopMsg({ ok: r.ok, detail: r.detail });
    } catch (e) {
      setTopMsg({ ok: false, detail: e instanceof Error ? e.message : "preview failed" });
    } finally {
      setTopBusy(false);
    }
  };

  const saveTopics = async () => {
    // Commit inline label/query edits; fold in a pending add-row so a typed topic isn't lost.
    const pendingLabels = parseLabels(topLabel);
    const pending: NewsTopic[] =
      pendingLabels.length && topQuery.trim()
        ? [{ key: "", labels: pendingLabels, query: topQuery.trim(), enabled: true }]
        : [];
    const saved = await persistTopics([...(topics ?? []), ...pending]);
    if (saved) {
      setTopLabel("");
      setTopQuery("");
    }
  };

  return (
    <div>
      <div className="mb-1 text-xs font-semibold text-term-text">Topics</div>
      <p className="mb-2 text-[10px] leading-relaxed text-term-muted">
        Subscribe to an interest (e.g. <span className="font-mono">semiconductors</span>,{" "}
        <span className="font-mono">oil prices</span>). Each becomes a topic you can open as a
        news widget from the <span className="text-term-accent">⌘K</span> launcher, alongside the
        built-in <b>Market</b> and <b>Macro</b> feeds. Give a topic <b>several labels</b>{" "}
        (comma-separated) and each appears in the launcher, all feeding the one query. Adding,
        removing and enabling apply immediately; use <b>Save edits</b> after changing labels or
        keywords.
      </p>

      {topics && topics.length > 0 && (
        <div className="space-y-1.5">
          {topics.map((tp, i) => (
            <div key={i} className="flex flex-wrap items-center gap-1">
              <input
                type="checkbox"
                checked={tp.enabled}
                onChange={() => toggleTopic(i)}
                disabled={topBusy}
                className="accent-term-accent"
                title="Enabled (applies immediately)"
              />
              <input
                value={tp.labels.join(", ")}
                onChange={(e) => patchTopic(i, { labels: parseLabels(e.target.value) })}
                placeholder="Labels (comma-separated)"
                title="One or more labels/aliases, comma-separated — all feed the same query"
                className={cx(topicInputCls, "w-40 shrink-0")}
                spellCheck={false}
              />
              <input
                value={tp.query}
                onChange={(e) => patchTopic(i, { query: e.target.value })}
                placeholder="Interest / keywords, e.g. semiconductors OR chips"
                className={cx(topicInputCls, "min-w-0 flex-1")}
                spellCheck={false}
              />
              <button
                onClick={() => previewTopic(tp.query)}
                disabled={topBusy}
                title="Preview headlines"
                className="shrink-0 rounded border border-term-border px-1.5 py-1 text-[10px] text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
              >
                Preview
              </button>
              <button
                onClick={() => removeTopic(i)}
                disabled={topBusy}
                title="Remove this topic (applies immediately)"
                className="shrink-0 rounded border border-term-down/60 px-2 py-1 text-[10px] font-semibold text-term-down hover:bg-term-down/10 disabled:opacity-50"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      {/* add a topic */}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        <input
          value={topLabel}
          onChange={(e) => setTopLabel(e.target.value)}
          placeholder="Labels, e.g. Semis, Chips"
          title="One or more labels/aliases, comma-separated — all feed the same query"
          className={cx(topicInputCls, "w-40 shrink-0")}
          spellCheck={false}
        />
        <input
          value={topQuery}
          onChange={(e) => setTopQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addTopic()}
          placeholder="Interest / keywords"
          className={cx(topicInputCls, "min-w-0 flex-1")}
          spellCheck={false}
        />
        <button
          onClick={() => previewTopic(topQuery)}
          disabled={topBusy || !topQuery.trim()}
          className="shrink-0 rounded border border-term-border px-1.5 py-1 text-[10px] text-term-muted hover:border-term-accent hover:text-term-accent disabled:opacity-50"
        >
          Preview
        </button>
        <button
          onClick={addTopic}
          disabled={!topLabel.trim() || !topQuery.trim()}
          title="Add this topic (applies immediately)"
          className="shrink-0 rounded border border-term-accent bg-term-accent/10 px-3 py-1 text-[10px] font-semibold text-term-accent hover:bg-term-accent/20 disabled:opacity-40"
        >
          Add
        </button>
      </div>

      <div className="mt-3 flex items-center gap-1.5">
        <button
          onClick={saveTopics}
          disabled={topBusy}
          title="Commit edits to existing topics' label / keywords"
          className="rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50"
        >
          Save edits
        </button>
      </div>

      <Status busy={topBusy} msg={topMsg} />
    </div>
  );
}
