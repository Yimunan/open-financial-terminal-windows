import { useEffect, useRef, useState } from "react";
import { api, openChatSocket } from "../api/client";
import { useT } from "../lib/i18n";
import { buildAssistantCapabilities, type WidgetProps } from "../workspace/widgetRegistry";
import { snapshotWorkspace } from "../lib/terminalState";
import { executeAction } from "../state/terminalActions";
import { WidgetShell, useWidgetSymbol } from "./shell";
import AgentChatPanel, { type ChatMsg, type Suggestion } from "../components/AgentChatPanel";
import AgentProcessingLog from "../components/AgentProcessingLog";
import BentoSubGrid from "../components/BentoSubGrid";

/** Starter prompts — each maps to one of the grounded read-only tools (quote / fundamentals /
 * news / compare / screen / performance). Clicking fills the input so the user can edit + send. */
const SUGGESTIONS: Suggestion[] = [
  { label: "open chart", prompt: "Open a chart for NVDA on the daily with a 50-day SMA and RSI" },
  { label: "on screen", prompt: "What widgets do I have open right now?" },
  { label: "retarget", prompt: "Switch the linked symbol to TSLA" },
  { label: "quote", prompt: "What's the current price and 52-week range for AAPL?" },
  { label: "news", prompt: "Open the news for NVDA and summarize the headlines" },
  { label: "screen", prompt: "Screen the Dow 30 for the highest-momentum stocks" },
];

/** Render an agent terminal action as a one-line Processing entry, e.g. "▣ open_widget · type=chart". */
function actionLine(name: string, args: Record<string, unknown>): string {
  const a = Object.entries(args)
    .map(([k, v]) => `${k}=${v && typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" ");
  return `▣ ${name}${a ? ` · ${a}` : ""}`;
}

/** Format a grounding tool fetch as a one-line processing-log entry, e.g.
 * "⚡ get_quote · symbol=AAPL". The structured `summary` is left out to keep the trace scannable. */
function toolLine(name: string, args: Record<string, unknown>): string {
  const argStr = Object.entries(args)
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`)
    .join(" ");
  return `⚡ ${name}${argStr ? ` · ${argStr}` : ""}`;
}

/** Grounded streaming chat over /api/ws/chat, laid out as a bento grid (like Chart Studio and the
 * Backtest module): the conversation lives in a Chat sub-window while the agent's grounding steps
 * (the read-only tool fetches it ran before answering) stream into a separate Processing sub-window.
 * Symbol-aware: the linked symbol is sent so the backend grounds the answer in real terminal data.
 */
export default function AssistantWidget(props: WidgetProps) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const t = useT();
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [busy, setBusy] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const msgsRef = useRef<ChatMsg[]>([]);
  msgsRef.current = msgs;

  useEffect(() => {
    const ws = openChatSocket();
    wsRef.current = ws;
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "tool") {
        // The grounded agent fetched real terminal data before answering — surface it in the
        // Processing window for transparency (role "obs" so the chat pane filters it out).
        setMsgs((cur) => [...cur, { role: "obs", text: toolLine(m.name, m.args ?? {}) }]);
      } else if (m.type === "thought") {
        setMsgs((cur) => [...cur, { role: "thought", text: m.text }]);
      } else if (m.type === "action") {
        // Control loop: execute the UI action in the browser, then feed the observation back so
        // the agent can continue (the mid-stream round-trip the backend awaits via gen.asend).
        setMsgs((cur) => [...cur, { role: "obs", text: actionLine(m.name, m.args ?? {}) }]);
        executeAction(m.name, m.args ?? {}).then((res) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ op: "observation", id: m.id, ok: res.ok, result: res.result }));
          }
        });
      } else if (m.type === "token") {
        setMsgs((cur) => {
          const last = cur[cur.length - 1];
          if (last?.role === "assistant") {
            return [...cur.slice(0, -1), { ...last, text: last.text + m.text }];
          }
          return [...cur, { role: "assistant", text: m.text }];
        });
      } else if (m.type === "done") {
        setBusy(false);
      } else if (m.type === "error") {
        setMsgs((cur) => [...cur, { role: "error", text: `⚠️ ${m.detail}` }]);
        setBusy(false);
      }
    };
    return () => ws.close();
  }, []);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy || wsRef.current?.readyState !== WebSocket.OPEN) return;
    const next: ChatMsg[] = [...msgsRef.current, { role: "user", text: trimmed }];
    setMsgs(next);
    setBusy(true);
    // The socket expects {role,content} conversation turns — only the user/assistant ones.
    const convo = next
      .filter((x) => x.role === "user" || x.role === "assistant")
      .map((x) => ({ role: x.role, content: x.text }));
    // Sending `capabilities` + `workspace` puts the backend into the ReAct CONTROL loop, so the
    // assistant can both answer AND drive the terminal's modules.
    wsRef.current.send(JSON.stringify({
      messages: convo,
      symbol,
      workspace: snapshotWorkspace(),
      capabilities: buildAssistantCapabilities(),
    }));
  };

  const summarize = async () => {
    if (busy) return;
    setMsgs((c) => [...c, { role: "user", text: `Summarize ${symbol}` }]);
    setBusy(true);
    try {
      const r = await api.summarize(symbol, asset);
      setMsgs((c) => [...c, { role: "assistant", text: r.summary }]);
    } catch (e) {
      setMsgs((c) => [...c, { role: "error", text: `⚠️ ${(e as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">
            {t("widget.assistant")} · <span className="font-mono text-term-text">{symbol}</span>
          </span>
          <button
            onClick={summarize}
            disabled={busy}
            className="ml-auto rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10 disabled:opacity-50"
          >
            {t("ai.summarize")}
          </button>
        </>
      }
    >
      <BentoSubGrid
        storageKey={`${props.api.id}:v1`}
        seed={(api) => {
          // Chat fills the main area; Processing docks as a narrow right column (the agent's
          // grounding fetches). Drag to rearrange / tab them together — layout persists per panel.
          api.addPanel({ id: "chat", component: "chat", title: "Chat" });
          const processing = api.addPanel({
            id: "processing",
            component: "processing",
            title: "Processing",
            position: { referencePanel: "chat", direction: "right" },
          });
          processing.api.setSize({ width: 240 });
        }}
        panels={[
          {
            id: "chat",
            title: "Chat",
            content: (
              <AgentChatPanel
                title={t("widget.assistant")}
                messages={msgs}
                busy={busy}
                onSend={send}
                roles={["user", "assistant", "error"]}
                placeholder={t("ai.placeholder")}
                emptyHint={t("ai.empty", { x: symbol })}
                suggestions={SUGGESTIONS}
              />
            ),
          },
          {
            id: "processing",
            title: "Processing",
            content: (
              <AgentProcessingLog
                messages={msgs}
                busy={busy}
                emptyHint={t("ai.fetched")}
              />
            ),
          },
        ]}
      />
    </WidgetShell>
  );
}
