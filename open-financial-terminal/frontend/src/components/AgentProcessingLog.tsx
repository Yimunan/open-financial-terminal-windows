/** Read-only view of an agent run's *processing* trace — the streamed thoughts, observations, and
 * the generated-result rows (clickable to load that result). Pairs with an AgentChatPanel that
 * renders only the conversation (user/assistant), so a widget can split "how it's working it out"
 * from "what I asked / what it answered" into separate bento sub-windows. */

import { useEffect, useRef } from "react";
import { useT } from "../lib/i18n";
import { cx } from "../lib/format";
import { useSettings } from "../state/settings";
import { EmptyState } from "./States";
import type { ChatMsg } from "./AgentChatPanel";

const LOG_ROLES = new Set<ChatMsg["role"]>(["thought", "obs", "run"]);

export default function AgentProcessingLog({
  messages,
  busy,
  activeRunId,
  onSelectRun,
  emptyHint,
  title,
}: {
  messages: ChatMsg[];
  busy: boolean;
  activeRunId?: string | null;
  onSelectRun?: (runId: string) => void;
  emptyHint?: string;
  title?: string;
}) {
  const t = useT();
  const showIcons = useSettings((s) => s.showIcons);
  const scrollRef = useRef<HTMLDivElement>(null);
  const shown = messages.filter((m) => LOG_ROLES.has(m.role));

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, busy]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-term-panel">
      <div className="flex shrink-0 items-center border-b border-term-border px-2 py-1">
        <span className="truncate text-[11px] uppercase tracking-wider text-term-accent">{title ?? "Processing"}</span>
      </div>
      <div ref={scrollRef} className="no-scrollbar min-h-0 flex-1 space-y-1.5 overflow-auto px-2 py-2">
        {shown.length === 0 && !busy ? (
          <EmptyState icon="⚙" title="No activity yet" hint={emptyHint ?? "Agent steps appear here."} />
        ) : (
          shown.map((m, i) =>
            m.role === "run" ? (
              <button
                key={i}
                onClick={() => m.runId && onSelectRun?.(m.runId)}
                title={m.text}
                className={cx(
                  "flex w-full items-center gap-1.5 rounded border px-2 py-1.5 text-left font-mono text-[11px] transition-colors",
                  m.runId && activeRunId === m.runId
                    ? "border-term-accent bg-term-accent/10 text-term-text"
                    : "border-term-border/60 text-term-text hover:border-term-accent/60 hover:bg-term-border/20",
                )}
              >
                {showIcons && <span className="shrink-0 text-term-accent/80" aria-hidden>▣</span>}
                {m.best && <span className="shrink-0 text-term-up" title="best">{showIcons ? "★" : "best"}</span>}
                <span className="truncate">{m.text}</span>
              </button>
            ) : m.role === "thought" ? (
              <div key={i} className="flex gap-1.5 text-[11px] italic leading-relaxed text-term-muted">
                {showIcons && <span className="mt-[5px] h-1 w-1 shrink-0 rounded-full bg-term-border" aria-hidden />}
                <span className="min-w-0">{m.text}</span>
              </div>
            ) : (
              <div key={i} className="whitespace-pre-wrap text-[11px] leading-relaxed text-term-muted/90">{showIcons ? "↳ " : ""}{m.text}</div>
            ),
          )
        )}
        {busy && (
          <div className="flex items-center gap-1.5 px-0.5 text-[11px] text-term-accent">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-term-accent" aria-hidden />
            {t("chat.working")}
          </div>
        )}
      </div>
    </div>
  );
}
