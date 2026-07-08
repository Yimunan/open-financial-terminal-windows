import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { McpServer, McpTestResult } from "../../api/types";
import { cx } from "../../lib/format";
import { Choice, llmInputCls, type Msg, Status, topicInputCls } from "./common";

/** External MCP servers whose tools the grounded assistant can call mid-answer. */
export default function McpSection() {
  const [mcp, setMcp] = useState<McpServer[] | null>(null);
  const [mcpBusy, setMcpBusy] = useState(false);
  const [mcpMsg, setMcpMsg] = useState<Msg>(null);

  useEffect(() => {
    api.mcpSettings().then((r) => setMcp(r.servers)).catch(() => {});
  }, []);

  const patchServer = (i: number, patch: Partial<McpServer>) =>
    setMcp((s) => (s ? s.map((srv, j) => (j === i ? { ...srv, ...patch } : srv)) : s));
  const removeServer = (i: number) => setMcp((s) => (s ? s.filter((_, j) => j !== i) : s));
  const addServer = () =>
    setMcp((s) => [
      ...(s ?? []),
      { name: "", transport: "stdio", command: "", args: [], env: {}, url: "", headers: {}, enabled: true },
    ]);

  const saveMcp = async () => {
    if (!mcp) return;
    setMcpBusy(true);
    setMcpMsg(null);
    try {
      const saved = await api.saveMcpSettings({ servers: mcp });
      setMcp(saved.servers);
      const active = saved.servers.filter((s) => s.enabled).length;
      setMcpMsg({ ok: true, detail: `Saved · ${active} server(s) enabled` });
    } catch (e) {
      setMcpMsg({ ok: false, detail: e instanceof Error ? e.message : "save failed" });
    } finally {
      setMcpBusy(false);
    }
  };

  const testServer = async (srv: McpServer) => {
    setMcpBusy(true);
    setMcpMsg(null);
    try {
      const r: McpTestResult = await api.testMcpServer(srv);
      const names = r.tools.map((tl) => tl.name).slice(0, 6).join(", ");
      setMcpMsg({
        ok: r.ok,
        detail: r.ok ? `${srv.name || "server"}: ${r.detail}${names ? ` — ${names}` : ""}` : r.detail,
      });
    } catch (e) {
      setMcpMsg({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setMcpBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold text-term-text">External MCP servers</div>
      <p className="mb-2 text-[10px] leading-relaxed text-term-muted">
        Register Model Context Protocol servers and the grounded assistant can call their tools
        mid-answer (namespaced <span className="font-mono">mcp:server:tool</span>). A down or
        misconfigured server is simply skipped. <span className="font-mono">stdio</span> spawns a
        local command; <span className="font-mono">http</span> connects to a streamable-HTTP URL.
      </p>

      {mcp && mcp.length > 0 && (
        <div className="space-y-2">
          {mcp.map((srv, i) => (
            <div key={i} className="rounded border border-term-border bg-term-sunken/40 p-2">
              <div className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={srv.enabled}
                  onChange={() => patchServer(i, { enabled: !srv.enabled })}
                  className="accent-term-accent"
                  title="Enabled"
                />
                <input
                  value={srv.name}
                  onChange={(e) => patchServer(i, { name: e.target.value })}
                  placeholder="name"
                  className={cx(topicInputCls, "flex-1")}
                  spellCheck={false}
                />
                <Choice active={srv.transport === "stdio"} onClick={() => patchServer(i, { transport: "stdio" })}>
                  stdio
                </Choice>
                <Choice active={srv.transport === "http"} onClick={() => patchServer(i, { transport: "http" })}>
                  http
                </Choice>
                <button
                  onClick={() => testServer(srv)}
                  disabled={mcpBusy}
                  className="rounded border border-term-border px-2 py-1 text-xs text-term-muted hover:text-term-text disabled:opacity-50"
                >
                  Test
                </button>
                <button
                  onClick={() => removeServer(i)}
                  className="shrink-0 px-1 text-term-muted hover:text-term-down"
                  title="Remove"
                >
                  ×
                </button>
              </div>
              {srv.transport === "stdio" ? (
                <div className="mt-1.5 flex items-center gap-1.5">
                  <input
                    value={srv.command ?? ""}
                    onChange={(e) => patchServer(i, { command: e.target.value })}
                    placeholder="command (e.g. npx, python)"
                    className={cx(topicInputCls, "flex-1")}
                    spellCheck={false}
                  />
                  <input
                    value={(srv.args ?? []).join(" ")}
                    onChange={(e) => patchServer(i, { args: e.target.value.split(/\s+/).filter(Boolean) })}
                    placeholder="args (space-separated)"
                    className={cx(topicInputCls, "flex-1")}
                    spellCheck={false}
                  />
                </div>
              ) : (
                <div className="mt-1.5">
                  <input
                    value={srv.url ?? ""}
                    onChange={(e) => patchServer(i, { url: e.target.value })}
                    placeholder="https://host/mcp"
                    className={llmInputCls}
                    spellCheck={false}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="mt-2 flex items-center gap-1.5">
        <button
          onClick={addServer}
          className="rounded border border-term-border px-2.5 py-1 text-xs text-term-muted hover:text-term-text"
        >
          + Add server
        </button>
        <button
          onClick={saveMcp}
          disabled={mcpBusy || !mcp}
          className="rounded border border-term-accent bg-term-accent/10 px-2.5 py-1 text-xs text-term-accent hover:bg-term-accent/20 disabled:opacity-50"
        >
          Save
        </button>
      </div>

      <Status busy={mcpBusy} msg={mcpMsg} />
    </div>
  );
}
