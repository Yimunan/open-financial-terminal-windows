import { useEffect, useMemo, useRef, useState } from "react";
import { cx } from "../lib/format";
import { useT } from "../lib/i18n";
import type { ChatSession } from "../state/chatHistory";
import { useSettings } from "../state/settings";

export type ChatRole = "user" | "thought" | "run" | "assistant" | "error" | "obs";

export interface ChatMsg {
  role: ChatRole;
  text: string;
  runId?: string; // for role "run" — click to load that run into the dashboard
  best?: boolean; // badge the winning run
}

/** A recommended prompt: `label` is shown on the chip, `prompt` is what gets sent to the agent. */
export interface Suggestion {
  label: string;
  prompt: string;
}

/**
 * Reusable chat panel for the backtest agents (factor + lab). Renders a streamed message log
 * — user prompts, agent thoughts, clickable run rows, observations, the final summary — plus an
 * input. Run rows call `onSelectRun(runId)` so clicking one loads that backtest into the dashboard.
 *
 * `historyPane` switches the past-sessions list from a header dropdown to a persistent LEFT
 * sidebar (so the chat + recommendations + input sit in the right sub-window). Use it when the
 * panel is wide (e.g. a full-width bottom dock); keep it off for narrow side-column panels.
 */
export default function AgentChatPanel({
  title,
  messages,
  busy,
  onSend,
  onStop,
  onSelectRun,
  roles,
  activeRunId,
  placeholder,
  emptyHint,
  suggestions,
  history,
  onLoadSession,
  onNewChat,
  onDeleteSession,
  historyPane,
}: {
  title: string;
  messages: ChatMsg[];
  busy: boolean;
  onSend: (text: string) => void;
  onStop?: () => void;
  onSelectRun?: (runId: string) => void;
  /** Restrict which message roles render here (default: all). Lets a split layout route processing
   *  logs and the conversation into separate sub-windows. */
  roles?: ChatRole[];
  activeRunId?: string | null;
  placeholder?: string;
  emptyHint?: string;
  suggestions?: Suggestion[];
  history?: ChatSession[];
  onLoadSession?: (session: ChatSession) => void;
  onNewChat?: () => void;
  onDeleteSession?: (id: string) => void;
  historyPane?: boolean;
}) {
  const t = useT();
  const showIcons = useSettings((s) => s.showIcons);
  const [input, setInput] = useState("");
  const [histOpen, setHistOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const hasHistory = !!(history && onLoadSession);
  const sidebar = !!(historyPane && hasHistory);

  // Show a few suggestions stacked above the box; "Reshuffle" draws a fresh random
  // subset/order from the full pool. Re-randomizes automatically when the pool changes.
  const [sugSeed, setSugSeed] = useState(0);
  const sugKey = (suggestions ?? []).map((s) => s.label).join("|");
  const shownSug = useMemo(() => {
    const arr = [...(suggestions ?? [])];
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr.slice(0, 3);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sugKey, sugSeed]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, busy]);

  // Auto-grow the input up to a few lines, then scroll within.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [input]);

  const submit = () => {
    const v = input.trim();
    if (!v || busy) return;
    onSend(v);
    setInput("");
  };

  // When `roles` is set, only those message roles render here (the rest go to a sibling panel).
  const shown = roles ? messages.filter((m) => roles.includes(m.role)) : messages;

  const sessionRow = (sess: ChatSession, onPick: () => void) => (
    <div key={sess.id} className="group flex items-center rounded hover:bg-term-border/40">
      <button
        onClick={onPick}
        className="min-w-0 flex-1 truncate px-2 py-1 text-left text-[11px]"
        title={sess.title}
      >
        {sess.title}
      </button>
      {onDeleteSession && (
        <button
          onClick={() => onDeleteSession(sess.id)}
          aria-label="Delete chat"
          className="focus-ring rounded px-1.5 text-term-muted opacity-0 transition-opacity hover:text-term-down group-hover:opacity-100"
        >
          ×
        </button>
      )}
    </div>
  );

  return (
    <div className={cx("flex h-full min-h-0", sidebar ? "flex-row" : "flex-col")}>
      {/* ── left sub-window: chat history (sidebar mode) ── */}
      {sidebar && (
        <aside className="flex w-44 shrink-0 flex-col border-r border-term-border bg-term-bg/30">
          <div className="flex shrink-0 items-center justify-between gap-1 border-b border-term-border px-2 py-1">
            <span className="truncate text-[11px] uppercase tracking-wider text-term-accent">
              {t("chat.history")}
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
          <div className="min-h-0 flex-1 overflow-auto p-1">
            {history!.length === 0 ? (
              <div className="px-2 py-2 text-[11px] text-term-muted">{t("chat.noHistory")}</div>
            ) : (
              history!.map((sess) => sessionRow(sess, () => onLoadSession!(sess)))
            )}
          </div>
        </aside>
      )}

      {/* ── right sub-window: conversation + recommendations + chat box ── */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center justify-between gap-1 border-b border-term-border px-2 py-1">
          <span className="truncate text-[11px] uppercase tracking-wider text-term-accent">{title}</span>
          {/* dropdown history only when not using the sidebar */}
          {!sidebar && (
            <div className="flex shrink-0 items-center gap-2">
              {onNewChat && (
                <button onClick={onNewChat} className="text-[11px] text-term-muted hover:text-term-text">
                  ＋ {t("chat.new")}
                </button>
              )}
              {hasHistory && (
                <div className="relative">
                  <button
                    onClick={() => setHistOpen((v) => !v)}
                    className="text-[11px] text-term-muted hover:text-term-text"
                  >
                    {t("chat.history")} ▾
                  </button>
                  {histOpen && (
                    <div className="absolute right-0 top-5 z-50 max-h-64 w-60 overflow-auto rounded border border-term-border bg-term-panel p-1 shadow-xl">
                      {history!.length === 0 ? (
                        <div className="px-2 py-1 text-[11px] text-term-muted">{t("chat.noHistory")}</div>
                      ) : (
                        history!.map((sess) =>
                          sessionRow(sess, () => {
                            onLoadSession!(sess);
                            setHistOpen(false);
                          }),
                        )
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        <div ref={scrollRef} className="no-scrollbar min-h-0 flex-1 space-y-1 overflow-auto px-2 py-1.5">
          {shown.length === 0 ? (
            <div className="text-[11px] leading-relaxed text-term-muted">{emptyHint}</div>
          ) : (
            shown.map((m, i) =>
              m.role === "run" ? (
                <button
                  key={i}
                  onClick={() => m.runId && onSelectRun?.(m.runId)}
                  className={cx(
                    "block w-full rounded border px-2 py-1 text-left font-mono text-[11px]",
                    m.runId && activeRunId === m.runId
                      ? "border-term-accent bg-term-accent/10 text-term-text"
                      : "border-term-border/60 text-term-text hover:border-term-accent/60",
                  )}
                >
                  {m.best && <span className="mr-1 text-term-up">★ best</span>}
                  {m.text}
                </button>
              ) : m.role === "thought" ? (
                <div key={i} className="pl-1 text-[11px] italic text-term-muted">{m.text}</div>
              ) : m.role === "obs" ? (
                <div key={i} className="whitespace-pre-wrap pl-2 text-[11px] text-term-muted">↳ {m.text}</div>
              ) : (
                <div
                  key={i}
                  className={cx(
                    "whitespace-pre-wrap rounded px-2 py-1 text-[11px] leading-snug",
                    m.role === "user"
                      ? "bg-term-accent/10 text-term-text"
                      : m.role === "error"
                        ? "bg-term-down/10 text-term-down"
                        : "bg-term-border/30 text-term-text",
                  )}
                >
                  {m.text}
                </div>
              ),
            )
          )}
          {busy && (
            <div className="flex items-center gap-1.5 px-1 text-[11px] text-term-accent">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-term-accent" aria-hidden />
              {t("chat.working")}
            </div>
          )}
        </div>

        {!busy && shownSug.length > 0 && (
          <div className="shrink-0 border-t border-term-border px-1.5 pt-1.5">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-term-muted">{t("chat.suggestions")}</span>
              {(suggestions?.length ?? 0) > 1 && (
                <button
                  onClick={() => setSugSeed((s) => s + 1)}
                  title={t("chat.reshuffle")}
                  aria-label={t("chat.reshuffle")}
                  className="focus-ring flex items-center gap-1 rounded text-[10px] uppercase tracking-wide text-term-muted transition-colors hover:text-term-accent"
                >
                  <span aria-hidden>↻</span> {t("chat.reshuffle")}
                </button>
              )}
            </div>
            <div className="flex flex-col gap-1">
              {shownSug.map((s, i) => (
                <button
                  key={s.label + i}
                  onClick={() => {
                    setInput(s.prompt);
                    inputRef.current?.focus();
                  }}
                  title={s.prompt}
                  className="focus-ring flex w-full items-start gap-1.5 rounded border border-term-border px-2 py-1 text-left text-[11px] leading-snug text-term-muted transition-colors hover:border-term-accent hover:text-term-accent"
                >
                  {showIcons && <span className="mt-px shrink-0 text-term-accent/70" aria-hidden>›</span>}
                  <span className="line-clamp-2">{s.prompt}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex shrink-0 items-end gap-1.5 border-t border-term-border p-1.5">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            disabled={busy}
            placeholder={placeholder}
            rows={1}
            className="no-scrollbar focus-ring max-h-[120px] min-h-[28px] flex-1 resize-none rounded border border-term-border bg-term-sunken px-2 py-1.5 font-mono text-xs leading-snug text-term-text placeholder:text-term-muted focus:border-term-accent"
          />
          {/* Text "Send"/"Stop" button — matches the Agent Workflow widget's chat button so all
              four agent chats (Assistant, Backtest, Strategy Lab, Factor Performance) look alike. */}
          {busy && onStop ? (
            <button
              onClick={onStop}
              aria-label={t("chat.stop")}
              title={t("chat.stop")}
              className="focus-ring shrink-0 rounded border border-term-down px-2 py-1.5 text-[10px] uppercase tracking-wide text-term-down transition-colors hover:bg-term-down/15"
            >
              {t("chat.stop")}
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={busy || !input.trim()}
              aria-label={t("chat.send")}
              title={t("chat.send")}
              className="focus-ring shrink-0 rounded border border-term-accent px-2 py-1.5 text-[10px] uppercase tracking-wide text-term-accent transition-colors hover:bg-term-accent/15 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("chat.send")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
