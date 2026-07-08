/** Standalone chat-history list — past agent sessions with "＋ New" and per-row delete.
 * Used as its own bento sub-window next to the chat (and by the dropdown inside AgentChatPanel). */

import { fmtAgo } from "../lib/format";
import { useT } from "../lib/i18n";
import type { ChatSession } from "../state/chatHistory";
import { useSettings } from "../state/settings";
import { EmptyState } from "./States";

export default function ChatHistoryList({
  history,
  onLoadSession,
  onNewChat,
  onDeleteSession,
  title,
}: {
  history: ChatSession[];
  onLoadSession: (session: ChatSession) => void;
  onNewChat?: () => void;
  onDeleteSession?: (id: string) => void;
  title?: string;
}) {
  const t = useT();
  const showIcons = useSettings((s) => s.showIcons);
  return (
    <div className="flex h-full min-h-0 flex-col bg-term-panel">
      <div className="flex shrink-0 items-center justify-between gap-1 border-b border-term-border px-2 py-1">
        <span className="truncate text-[11px] uppercase tracking-wider text-term-accent">
          {title ?? t("chat.history")}
        </span>
        {onNewChat && (
          <button
            onClick={onNewChat}
            className="focus-ring shrink-0 rounded text-[11px] text-term-muted hover:text-term-text"
          >
            ＋ {t("chat.new")}
          </button>
        )}
      </div>
      <div className="no-scrollbar min-h-0 flex-1 overflow-auto p-1">
        {history.length === 0 ? (
          <EmptyState icon="≡" title={t("chat.noHistory")} hint="Saved runs appear here." />
        ) : (
          history.map((sess) => {
            const hasResults = Array.isArray(sess.runs) && sess.runs.length > 0;
            return (
              <div key={sess.id} className="group flex items-stretch rounded hover:bg-term-border/40">
                <button
                  onClick={() => onLoadSession(sess)}
                  className="min-w-0 flex-1 px-2 py-1 text-left"
                  title={sess.title}
                >
                  <div className="truncate text-[11px] text-term-text">{sess.title}</div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-term-muted">
                    <span>{fmtAgo(Math.floor(sess.at / 1000))} ago</span>
                    {hasResults && (
                      <span className="text-term-accent/80" title="results saved">{showIcons ? "▣ " : ""}results</span>
                    )}
                  </div>
                </button>
                {onDeleteSession && (
                  <button
                    onClick={() => onDeleteSession(sess.id)}
                    aria-label="Delete chat"
                    className="focus-ring flex items-center rounded px-1.5 text-term-muted opacity-0 transition-opacity hover:text-term-down group-hover:opacity-100"
                  >
                    ×
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
