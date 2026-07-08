import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, openAgentSocket, openAgentCoderSocket } from "../api/client";
import type { AgentCoderFrame, AgentFrame, AgentGraphSpec, AgentNode, AgentNodeType, ResearchFrame } from "../api/types";
import CodeEditor from "../components/CodeEditor";
import BentoSubGrid from "../components/BentoSubGrid";
import AgentProcessingLog from "../components/AgentProcessingLog";
import type { ChatMsg as ProcMsg } from "../components/AgentChatPanel";
import { GraphCanvas, type GraphData, type NodeStatus } from "../lib/graphCanvas";
import { graphColors } from "../lib/chartTheme";
import { cx } from "../lib/format";
import { usePalette } from "../state/settings";
import { useWorkspace } from "../state/workspace";
import { panelTypeOf } from "../state/intents";
import type { WidgetParams, WidgetProps, WidgetType } from "../workspace/widgetRegistry";
import { IconButton, WidgetShell, useWidgetSymbol } from "./shell";

const inputCls =
  "focus-ring w-full rounded border border-term-border bg-term-sunken px-1.5 py-0.5 font-mono text-xs focus:border-term-accent";

/** Node types that correspond to a terminal module. When such a node runs with Reveal ON, the
 * workflow opens/focuses that module and streams the node's output into it (via drivePayload):
 * backtest/screen render the full result; quote/fundamentals/news/committee retarget the module's
 * symbol; execution/research/portfolio show a read-only result banner. Node types without a module
 * counterpart (input/output/data/strategy/scenario/llm) are omitted — their results stay in the
 * Result footer and node inspector. */
const NODE_TO_WIDGET: Partial<Record<string, WidgetType>> = {
  backtest: "backtest",
  quote: "quote",
  fundamentals: "metrics",
  news: "news",
  committee: "committee",
  screen: "screener",
  portfolio: "portfolios",
  execution: "paper",
  research: "research_loop",
  code: "sandbox",
};

/** Translate a finished node's output into params that make its module mirror the result:
 *  • backtest → the full result blob, rendered with no re-run (`incomingResult`)
 *  • symbol-driven modules (quote/metrics/news/committee) → the node's symbol, which they fetch
 *  • screen → an NL query the Screener re-runs
 * Returns null when there's nothing to drive (reveal/focus only). */
function drivePayload(nodeType: string, value: unknown): WidgetParams | null {
  const v = (value ?? {}) as Record<string, unknown>;
  if (nodeType === "backtest" && value && typeof value === "object") {
    return { incomingResult: value as WidgetParams["incomingResult"] };
  }
  if (nodeType === "screen" && value && typeof value === "object") {
    // Render the node's own screen result (no /api/ask re-run), so Stop/Pause leaves nothing running.
    return { incomingScreen: value as WidgetParams["incomingScreen"] };
  }
  if (nodeType === "execution" && value && typeof value === "object") {
    // The dry-run / armed order plan → a read-only banner in Paper Trading.
    return { incomingOrders: { orders: Array.isArray(v.orders) ? (v.orders as string[]) : [], armed: Boolean(v.armed) } };
  }
  if (nodeType === "research" && value && typeof value === "object") {
    // The best strategy + scorecard/target outcome → a summary banner in the Research Loop module.
    // Pass ONLY the small summary fields (not the full `history`, which bloats the panel params).
    const slim = {
      best: v.best,
      n_iterations: v.n_iterations,
      target_sharpe: v.target_sharpe,
      target_met: v.target_met,
      best_sharpe: v.best_sharpe,
    };
    return { incomingResearch: slim as unknown as WidgetParams["incomingResearch"] };
  }
  if (nodeType === "portfolio" && value && typeof value === "object") {
    // The accumulated pipeline spec → a config banner in Portfolio Builder (non-destructive).
    return { incomingSpec: value as WidgetParams["incomingSpec"] };
  }
  if (nodeType === "quote" || nodeType === "fundamentals" || nodeType === "news" || nodeType === "committee") {
    const symbol = v.symbol ? String(v.symbol) : "";
    if (symbol) return { symbol, asset: (v.asset as WidgetParams["asset"]) ?? undefined };
  }
  return null;
}

/** The node whose result represents the workflow's output, for the Result footer: an explicit
 * `output` node if present, else the sole terminal (no outgoing edge). null when ambiguous
 * (multiple terminals and no `output` node — add an Output node to disambiguate). */
function resultNodeId(spec: AgentGraphSpec): string | null {
  const out = spec.nodes.find((n) => n.type === "output");
  if (out) return out.id;
  const sources = new Set(spec.edges.map((e) => e.source));
  const terminals = spec.nodes.filter((n) => !sources.has(n.id));
  return terminals.length === 1 ? terminals[0].id : null;
}

/** A short human label for a live research-loop progress frame, for the Processing run-log. */
function progressLabel(p: ResearchFrame): string {
  switch (p.type) {
    case "started": return "research loop started";
    case "phase": return `· iter ${p.iteration + 1} · ${p.phase}…`;
    case "design": return `· iter ${p.iteration + 1} · designed`;
    case "iteration": return `· iter ${p.iteration + 1} · ${p.record?.n_checks_passed ?? "?"}/5 checks`;
    case "reflect": return `· iter ${p.iteration + 1} · reflect → ${p.next_change}`;
    case "done": return "research loop done";
    default: return p.type;
  }
}

/** One saved workflow = a sub-widget inside the Agent Workflow module: its own name + graph spec.
 * Multiple live side-by-side as tabs, all persisted in panel params (like the Committees module). */
interface Workflow {
  id: string;
  name: string;
  spec: AgentGraphSpec;
}

/** Default quant pipeline: data → strategy → portfolio → backtest results + paper execution. */
function seedGraph(_symbol: string): AgentGraphSpec {
  return {
    nodes: [
      { id: "n1", type: "data", x: 30, y: 150, config: { universe: "dow30" } },
      { id: "n2", type: "strategy", x: 210, y: 150, config: { factor: "momentum", mode: "long_only", top_pct: 0.2, years: 3 } },
      { id: "n3", type: "portfolio", x: 400, y: 150, config: { initial: 100000 } },
      { id: "n4", type: "backtest", x: 600, y: 70, config: {} },
      { id: "n5", type: "execution", x: 600, y: 240, config: { top_n: 5, arm: "off" } },
    ],
    edges: [
      { source: "n1", target: "n2" },
      { source: "n2", target: "n3" },
      { source: "n3", target: "n4" },
      { source: "n3", target: "n5" },
    ],
  };
}

export default function AgentGraphWidget(props: WidgetProps) {
  const { symbol, channel, setChannel } = useWidgetSymbol(props);
  const palette = usePalette();

  const { data: nodeTypesResp } = useQuery({ queryKey: ["agent-node-types"], queryFn: api.agentNodeTypes });
  const nodeTypes = nodeTypesResp?.node_types ?? [];
  const typeByKey = useMemo(() => {
    const m = new Map<string, AgentNodeType>();
    nodeTypes.forEach((t) => m.set(t.key, t));
    return m;
  }, [nodeTypes]);

  // Multiple workflows (each its own graph) live as tabs in panel params so they persist with the
  // layout — same model as the Committees module.
  const [workflows, setWorkflowsState] = useState<Workflow[]>(
    (props.params.workflows as Workflow[] | undefined) ?? [],
  );
  const [activeId, setActiveIdState] = useState<string>(
    (props.params.activeWorkflowId as string | undefined) ?? "",
  );
  const setWorkflows = (ws: Workflow[]) => {
    setWorkflowsState(ws);
    props.api.updateParameters({ workflows: ws });
  };
  const setActiveId = (id: string) => {
    setActiveIdState(id);
    props.api.updateParameters({ activeWorkflowId: id });
  };

  const active = workflows.find((w) => w.id === activeId) ?? workflows[0] ?? null;
  // Refs so the canvas callbacks (wired once) always mutate the CURRENT active workflow.
  const activeRef = useRef(active);
  activeRef.current = active;
  const workflowsListRef = useRef(workflows);
  workflowsListRef.current = workflows;

  // The active workflow's graph spec; writing it patches just that workflow in the array.
  const spec = active?.spec ?? seedGraph(symbol);
  const setSpec = (s: AgentGraphSpec) => {
    const a = activeRef.current;
    if (!a) return;
    setWorkflows(workflowsListRef.current.map((w) => (w.id === a.id ? { ...w, spec: s } : w)));
  };

  // ── workflow tab UI state ──
  const [menuOpen, setMenuOpen] = useState(false);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [workflowSearch, setWorkflowSearch] = useState("");
  const tabsRef = useRef<HTMLDivElement>(null);

  const [selected, setSelected] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, NodeStatus>>({});
  const [outputs, setOutputs] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  // Node-execution trace for the Processing sub-window (a line per node start/finish + live research
  // progress). Mirrors the Backtest module's Processing pane (AgentProcessingLog). Cleared each run.
  const [runLog, setRunLog] = useState<ProcMsg[]>([]);
  const logLine = (text: string) => setRunLog((l) => [...l, { role: "obs", text }]);
  // When on, running a node wired to a module opens/focuses that module and streams the node's
  // output into it. Each module is revealed once per run (tracked here).
  const [revealModules, setRevealModules] = useState(true);
  const revealedRef = useRef<Set<string>>(new Set());
  // Paused = the user hit Pause; the backend holds the graph at the next node boundary. Tracked
  // locally (we know we sent {op:"pause"}); no backend frame needed.
  const [paused, setPaused] = useState(false);
  const seqRef = useRef(spec.nodes.length);

  // ── AI assistant / agent ──
  type ChatMsg = { role: "user" | "assistant" | "error" | "step" | "obs" | "thought"; text: string };
  const [aiMode, setAiMode] = useState<"assistant" | "agent">("assistant");
  const [aiInput, setAiInput] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const coderWsRef = useRef<WebSocket | null>(null);

  const fmtArgs = (tool?: string, args?: Record<string, unknown>): string => {
    if (!args) return "";
    if (tool === "connect" || tool === "disconnect") return `${args.source}→${args.target}`;
    if (tool === "add_node") return String(args.type ?? "");
    if (tool === "edit_config") return `${args.id}.${args.key}`;
    if (tool === "delete_node") return String(args.id ?? "");
    return "";
  };

  const bumpSeq = (s: AgentGraphSpec) => {
    const maxId = s.nodes.reduce((mx, n) => {
      const mt = /^n(\d+)$/.exec(n.id);
      return mt ? Math.max(mx, Number(mt[1])) : mx;
    }, 0);
    seqRef.current = Math.max(maxId, s.nodes.length);
  };

  const runCoder = (goal: string) => {
    setMessages((m) => [...m, { role: "user", text: goal }]);
    setAiInput("");
    setAiBusy(true);
    setStatus({});
    const ws = openAgentCoderSocket();
    coderWsRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ op: "code", spec: specRef.current, goal }));
    ws.onmessage = (e) => {
      const f = JSON.parse(e.data) as AgentCoderFrame;
      if (f.type === "step") {
        if (f.thought) setMessages((m) => [...m, { role: "thought", text: f.thought! }]);
        setMessages((m) => [...m, { role: "step", text: `▸ ${f.tool ?? "?"} ${fmtArgs(f.tool, f.args)}`.trim() }]);
      } else if (f.type === "obs") {
        setMessages((m) => [...m, { role: "obs", text: f.text }]);
      } else if (f.type === "spec") {
        setSpec(f.spec);
      } else if (f.type === "node") {
        setStatus((cur) => ({ ...cur, [f.id]: f.status }));
      } else if (f.type === "done") {
        setMessages((m) => [...m, { role: "assistant", text: f.message }]);
        if (f.spec) {
          setSpec(f.spec);
          bumpSeq(f.spec);
        }
        setAiBusy(false);
        ws.close();
      } else if (f.type === "error") {
        setMessages((m) => [...m, { role: "error", text: f.detail }]);
        setAiBusy(false);
        ws.close();
      }
    };
    ws.onerror = () => setAiBusy(false);
    ws.onclose = () => setAiBusy(false);
  };

  const aiSubmit = () => {
    const msg = aiInput.trim();
    if (!msg || aiBusy) return;
    if (aiMode === "agent") runCoder(msg);
    else sendAi();
  };

  const sendAi = async () => {
    const msg = aiInput.trim();
    if (!msg || aiBusy) return;
    setMessages((m) => [...m, { role: "user", text: msg }]);
    setAiInput("");
    setAiBusy(true);
    try {
      const r = await api.assistAgent(spec, msg);
      setMessages((m) => [...m, { role: r.ok ? "assistant" : "error", text: r.message }]);
      if (r.ok) {
        setSpec(r.spec);
        setSelected(null);
        setStatus({});
        setOutputs({});
        const maxId = r.spec.nodes.reduce((mx, n) => {
          const mt = /^n(\d+)$/.exec(n.id);
          return mt ? Math.max(mx, Number(mt[1])) : mx;
        }, 0);
        seqRef.current = Math.max(maxId, r.spec.nodes.length);
      }
    } catch (e) {
      setMessages((m) => [...m, { role: "error", text: e instanceof Error ? e.message : "request failed" }]);
    } finally {
      setAiBusy(false);
    }
  };

  // ── canvas ──
  const chartRef = useRef<GraphCanvas | null>(null);
  const specRef = useRef(spec);
  specRef.current = spec;

  // First run / migration: seed one workflow, carrying over any legacy single-graph param; or
  // repair a dangling active id.
  useEffect(() => {
    if (!workflows.length) {
      const legacy = props.params.agentSpec as AgentGraphSpec | undefined;
      const seeded: Workflow[] = [
        { id: crypto.randomUUID(), name: "Workflow 1", spec: legacy ?? seedGraph(symbol) },
      ];
      setWorkflows(seeded);
      setActiveId(seeded[0].id);
    } else if (!workflows.some((w) => w.id === activeId)) {
      setActiveId(workflows[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflows.length]);

  // Switching tabs resets the transient run/selection/chat state and re-seeds the node-id counter
  // for the newly active graph (its spec already persists in params).
  useEffect(() => {
    wsRef.current?.close();
    coderWsRef.current?.close();
    setSelected(null);
    setStatus({});
    setOutputs({});
    setRunning(false);
    setMessages([]);
    bumpSeq(specRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  // Mouse wheel scrolls the workflow tab strip horizontally (non-passive).
  useEffect(() => {
    const el = tabsRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth) return;
      const delta = Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
      if (!delta) return;
      e.preventDefault();
      el.scrollLeft += delta;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Canvas lifecycle as a CALLBACK REF (not a mount effect): the host div lives inside a BentoSubGrid
  // panel rendered through a dockview portal, so it mounts AFTER this component — a mount effect would
  // see a null host and never build the canvas. A callback ref fires exactly when the div mounts (and
  // unmounts), and re-fires when `palette` changes so the canvas is rebuilt with the new theme.
  const setCanvasHost = useCallback((node: HTMLDivElement | null) => {
    if (!node) {
      chartRef.current?.destroy();
      chartRef.current = null;
      return;
    }
    chartRef.current = new GraphCanvas(node, graphColors(), {
      onMove: (id, x, y) => {
        const s = specRef.current;
        setSpec({ ...s, nodes: s.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)) });
      },
      onSelect: (id) => setSelected(id),
      onConnect: (source, target) => {
        const s = specRef.current;
        if (s.edges.some((e) => e.source === source && e.target === target)) return;
        setSpec({ ...s, edges: [...s.edges, { source, target }] });
      },
      onDeleteEdge: (source, target) => {
        const s = specRef.current;
        setSpec({ ...s, edges: s.edges.filter((e) => !(e.source === source && e.target === target)) });
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [palette]);

  // push graph data to the canvas whenever spec / selection / status changes
  useEffect(() => {
    const data: GraphData = {
      nodes: spec.nodes.map((n) => ({
        id: n.id,
        type: n.type,
        label: typeByKey.get(n.type)?.label ?? n.type,
        x: n.x,
        y: n.y,
        inputs: typeByKey.get(n.type)?.inputs ?? 1,
        status: status[n.id] ?? "idle",
      })),
      edges: spec.edges,
      selected,
    };
    chartRef.current?.setData(data);
  }, [spec, selected, status, typeByKey]);

  const addNode = (t: AgentNodeType) => {
    seqRef.current += 1;
    const id = `n${seqRef.current}`;
    const config: Record<string, string | number> = {};
    t.params.forEach((p) => (config[p.key] = p.default));
    const node: AgentNode = { id, type: t.key, x: 60 + Math.random() * 80, y: 60 + Math.random() * 200, config };
    setSpec({ ...spec, nodes: [...spec.nodes, node] });
    setSelected(id);
  };

  const deleteNode = (id: string) => {
    setSpec({
      nodes: spec.nodes.filter((n) => n.id !== id),
      edges: spec.edges.filter((e) => e.source !== id && e.target !== id),
    });
    setSelected(null);
  };

  const updateConfig = (id: string, key: string, value: string | number) => {
    setSpec({
      ...spec,
      nodes: spec.nodes.map((n) => (n.id === id ? { ...n, config: { ...n.config, [key]: value } } : n)),
    });
  };

  // ── run ──
  const wsRef = useRef<WebSocket | null>(null);
  // Live research-loop frames accumulated for the current run, forwarded (append-only) into the
  // Research Loop module so it mirrors the agent's research node iteration-by-iteration.
  const researchFramesRef = useRef<ResearchFrame[]>([]);
  // Open the module a node maps to, or focus it if a panel of that type is already open, optionally
  // pushing params into it. Mirrors the focus-or-open dedup pattern in state/intents.ts (send case).
  const revealTo = (widget: WidgetType, params?: WidgetParams) => {
    const ws = useWorkspace.getState();
    const existing = ws.api?.panels.find((p) => panelTypeOf(p.id) === widget);
    if (existing) {
      if (params) existing.api.updateParameters(params);
      existing.api.setActive();
    } else {
      ws.openWidget(widget, { channel, ...params });
    }
  };
  const run = () => {
    if (running) return;
    setStatus({});
    setOutputs({});
    setRunning(true);
    setPaused(false);
    revealedRef.current = new Set();
    researchFramesRef.current = [];
    setRunLog([]);
    const ws = openAgentSocket();
    wsRef.current = ws;
    ws.onopen = () =>
      ws.send(JSON.stringify({ op: "run", spec, seed: { symbol }, scenario: activeScenario || undefined }));
    ws.onmessage = (e) => {
      const f = JSON.parse(e.data) as AgentFrame;
      if (f.type === "node") {
        setStatus((cur) => ({ ...cur, [f.id]: f.status }));
        const nodeLabel = typeByKey.get(specRef.current.nodes.find((n) => n.id === f.id)?.type ?? "")?.label ?? f.id;
        if (f.status === "running" && !f.progress) logLine(`▸ ${nodeLabel}`);
        else if (f.status === "done") logLine(`✔ ${nodeLabel}${f.summary ? ` — ${f.summary.split("\n")[0]}` : ""}`);
        else if (f.status === "error") logLine(`✘ ${nodeLabel} — ${f.summary ?? ""}`);
        const widget = revealModules
          ? NODE_TO_WIDGET[specRef.current.nodes.find((n) => n.id === f.id)?.type ?? ""]
          : undefined;
        if (widget && f.status === "running" && !revealedRef.current.has(widget)) {
          revealedRef.current.add(widget); // reveal the module once, as the node starts
          revealTo(widget);
        }
        // Live research progress → mirror it into the (already-revealed) Research Loop module. Just
        // patch its params (no setActive) so the agent's loop streams in without stealing focus.
        if (f.status === "running" && f.progress && widget === "research_loop") {
          researchFramesRef.current = [...researchFramesRef.current, f.progress];
          const panel = useWorkspace.getState().api?.panels.find((p) => panelTypeOf(p.id) === "research_loop");
          panel?.api.updateParameters({ incomingResearchFrames: researchFramesRef.current });
          logLine(`  ${progressLabel(f.progress)}`);
        }
        if (f.status === "done" || f.status === "error") {
          setOutputs((cur) => ({ ...cur, [f.id]: f.summary ?? "" }));
        }
        if (widget && f.status === "done") {
          // stream the node's output into its module (renders the result there)
          const ntype = specRef.current.nodes.find((n) => n.id === f.id)?.type ?? "";
          const payload = drivePayload(ntype, f.value);
          if (payload) revealTo(widget, payload);
        }
      } else if (f.type === "done") {
        logLine("✓ workflow complete");
        setRunning(false);
        ws.close();
      } else if (f.type === "error") {
        logLine(`✕ ${f.detail}`);
        setRunning(false);
        setOutputs((cur) => ({ ...cur, _error: f.detail }));
        ws.close();
      }
    };
    ws.onerror = () => setRunning(false);
    ws.onclose = () => setRunning(false);
  };
  // Stop: ask the backend to cancel the graph ({op:"stop"}), then tear down locally so state resets
  // even if the server is slow. Partial node statuses/outputs are left visible on purpose.
  const stop = () => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ op: "stop" }));
    ws?.close();
    setRunning(false);
    setPaused(false);
  };
  // Pause/Resume: the backend holds the graph at the next node boundary (it can't suspend mid-node),
  // so the current step finishes first. Honest label is set on the button title.
  const pause = () => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ op: "pause" })); setPaused(true); }
  };
  const resume = () => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ op: "resume" })); setPaused(false); }
  };
  useEffect(() => () => { wsRef.current?.close(); coderWsRef.current?.close(); }, []);

  // ── save / load ──
  const { data: savedResp, refetch: refetchSaved } = useQuery({ queryKey: ["agent-graphs"], queryFn: api.agentGraphs });
  const saved = savedResp?.graphs ?? [];
  const [libOpen, setLibOpen] = useState(false);
  const [saveName, setSaveName] = useState("");

  // ── scenarios (named variable presets + market shocks) ──
  const { data: scenResp, refetch: refetchScen } = useQuery({ queryKey: ["agent-scenarios"], queryFn: api.agentScenarios });
  const scenarios = scenResp?.scenarios ?? [];
  const [activeScenario, setActiveScenario] = useState<string>("");
  const [scenOpen, setScenOpen] = useState(false);
  const [scenForm, setScenForm] = useState({ name: "", universe: "dow30", factor: "momentum", mode: "long_only", years: 3, equity_pct: 0, crypto_pct: 0, vol_mult: 1 });

  const saveScenario = async () => {
    if (!scenForm.name.trim()) return;
    await api.saveScenario(scenForm.name.trim(), {
      variables: { universe: scenForm.universe, factor: scenForm.factor, mode: scenForm.mode, years: scenForm.years },
      shocks: { equity_pct: scenForm.equity_pct, crypto_pct: scenForm.crypto_pct, vol_mult: scenForm.vol_mult },
    });
    setScenForm({ ...scenForm, name: "" });
    refetchScen();
  };

  const doSave = async () => {
    if (!saveName.trim()) return;
    await api.saveAgentGraph(saveName.trim(), spec);
    setSaveName("");
    setLibOpen(false);
    refetchSaved();
  };
  const doLoad = async (name: string) => {
    const g = await api.loadAgentGraph(name);
    setSpec(g.spec);
    setSelected(null);
    setStatus({});
    setOutputs({});
    setLibOpen(false);
    bumpSeq(g.spec); // robust to id gaps (avoids cross-load id reuse re-attaching stale outputs)
  };

  // ── workflow (sub-widget) management ──
  const addWorkflow = (name: string, graph: AgentGraphSpec) => {
    const dupes = workflows.filter((w) => w.name === name || w.name.startsWith(`${name} `)).length;
    const w: Workflow = {
      id: crypto.randomUUID(),
      name: dupes ? `${name} ${dupes + 1}` : name,
      spec: graph,
    };
    setWorkflows([...workflows, w]);
    setActiveId(w.id);
    setMenuOpen(false);
  };
  const createBlank = () => addWorkflow("Workflow", { nodes: [], edges: [] });
  const createPipeline = () => addWorkflow("Pipeline", seedGraph(symbol));
  const createFromSaved = async (name: string) => {
    try {
      const g = await api.loadAgentGraph(name);
      addWorkflow(name, g.spec);
    } catch {
      /* ignore load failures */
    }
  };
  const removeWorkflow = (id: string) => {
    const next = workflows.filter((w) => w.id !== id);
    setWorkflows(next);
    if (activeId === id) setActiveId(next[0]?.id ?? "");
  };

  const selNode = spec.nodes.find((n) => n.id === selected);
  const selType = selNode ? typeByKey.get(selNode.type) : undefined;
  // The always-visible footer shows the workflow's result(s): a designated Output node / sole
  // terminal when there is one, else EVERY finished terminal — so a multi-terminal graph (the
  // default pipeline's backtest + execution) shows all of its results, not just whichever finished
  // last. Only terminals that have produced output are listed; cleared on the next run.
  const terminalIds = useMemo(() => {
    const sources = new Set(spec.edges.map((e) => e.source));
    return new Set(spec.nodes.filter((n) => !sources.has(n.id)).map((n) => n.id));
  }, [spec]);
  const footerIds = useMemo(() => {
    const designated = resultNodeId(spec);
    if (designated) return designated in outputs ? [designated] : [];
    return spec.nodes.filter((n) => terminalIds.has(n.id) && n.id in outputs).map((n) => n.id);
  }, [spec, outputs, terminalIds]);
  const upstream = selNode ? spec.edges.filter((e) => e.target === selNode.id).map((e) => e.source) : [];

  // Processing sub-window feed: the workflow run-log + the AI builder's own steps (thought/obs), so
  // the pane shows both "the agent's steps" and the run rows (mirrors the Backtest Processing pane).
  const processingMessages: ProcMsg[] = [
    ...messages
      .filter((m) => m.role === "thought" || m.role === "obs" || m.role === "step")
      .map((m) => ({ role: m.role === "step" ? ("obs" as const) : m.role, text: m.text })),
    ...runLog,
  ];

  // ── bento sub-window contents (the widget keeps all state; BentoSubGrid owns only the layout) ──
  const graphContent = (
    <div className="flex h-full min-h-0">
      {/* palette */}
      <div className="w-28 shrink-0 overflow-auto border-r border-term-border p-1.5">
        <div className="mb-1 text-[9px] uppercase tracking-wider text-term-muted">Add node</div>
        {nodeTypes.map((t) => (
          <button
            key={t.key}
            onClick={() => addNode(t)}
            className={cx(
              "mb-1 block w-full truncate rounded border px-1.5 py-1 text-left text-[11px]",
              t.category === "llm"
                ? "border-term-accent/40 text-term-accent"
                : t.category === "io"
                  ? "border-term-border text-term-muted"
                  : "border-term-border text-term-text hover:border-term-accent",
            )}
            title={t.key}
          >
            {t.label}
          </button>
        ))}
      </div>
      {/* canvas + result footer */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div ref={setCanvasHost} className="min-h-[120px] flex-1" />
        {footerIds.length > 0 && (
          <div className="max-h-48 shrink-0 overflow-y-auto border-t border-term-border bg-term-panel/40">
            {footerIds.map((tid) => {
              const node = spec.nodes.find((n) => n.id === tid);
              return (
                <div key={tid} className="border-b border-term-border last:border-b-0">
                  <div className="flex items-center gap-2 px-2 pt-1">
                    <span className="shrink-0 text-[9px] font-semibold uppercase tracking-wider text-term-accent">
                      {footerIds.length > 1 ? "Results" : "Result"}
                    </span>
                    {node && (
                      <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-term-muted">
                        {(typeByKey.get(node.type)?.label ?? node.type) + " · " + tid}
                      </span>
                    )}
                    <button
                      onClick={() => setSelected(tid)}
                      className="ml-auto shrink-0 text-[10px] text-term-muted hover:text-term-text"
                      title="Select the result node in the inspector"
                    >
                      inspect ↗
                    </button>
                  </div>
                  <pre className="max-h-32 overflow-auto whitespace-pre-wrap px-2 py-1.5 text-[11px] leading-snug text-term-text">
                    {outputs[tid] || "(no output)"}
                  </pre>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );

  const inspectorContent = (
    <div className="h-full overflow-auto p-2">
      {selNode && selType ? (
        <>
          <div className="mb-2 flex items-center justify-between">
            <span className="font-mono text-xs font-semibold">{selType.label}</span>
            <button onClick={() => deleteNode(selNode.id)} className="text-[10px] text-term-muted hover:text-term-down">
              Delete
            </button>
          </div>
          {selType.params.map((p) => (
            <label key={p.key} className="mb-1.5 block">
              <span className="mb-0.5 block text-[9px] uppercase tracking-wider text-term-muted">{p.label}</span>
              {p.key === "universe" ? (
                <UniversePicker
                  value={String(selNode.config[p.key] ?? "")}
                  onChange={(v) => updateConfig(selNode.id, p.key, v)}
                />
              ) : p.type === "code" ? (
                <CodeEditor
                  value={String(selNode.config[p.key] ?? "")}
                  onChange={(v) => updateConfig(selNode.id, p.key, v)}
                />
              ) : p.type === "textarea" ? (
                <textarea
                  rows={5}
                  value={String(selNode.config[p.key] ?? "")}
                  onChange={(e) => updateConfig(selNode.id, p.key, e.target.value)}
                  className={cx(inputCls, "resize-none leading-snug")}
                />
              ) : p.type === "select" ? (
                <select
                  value={String(selNode.config[p.key] ?? p.default)}
                  onChange={(e) => updateConfig(selNode.id, p.key, e.target.value)}
                  aria-label={p.label}
                  className="focus-ring w-full rounded border border-term-border bg-term-sunken px-1 py-0.5 text-xs text-term-muted"
                >
                  {(p.options ?? []).map((o) => <option key={o}>{o}</option>)}
                </select>
              ) : (
                <input
                  type={p.type === "number" ? "number" : "text"}
                  value={String(selNode.config[p.key] ?? "")}
                  onChange={(e) =>
                    updateConfig(selNode.id, p.key, p.type === "number" ? Number(e.target.value) : e.target.value)
                  }
                  className={inputCls}
                />
              )}
            </label>
          ))}
          {selType.category === "llm" && upstream.length > 0 && (
            <div className="mt-1 text-[9px] text-term-muted">
              Reference inputs in the prompt: {upstream.map((u) => `{${u}}`).join(" ")} or {"{input}"}
            </div>
          )}
          {outputs[selNode.id] && (
            <div className="mt-2">
              <div className="mb-0.5 text-[9px] uppercase tracking-wider text-term-muted">Output</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-term-border bg-term-bg/40 p-1.5 text-[10px] text-term-text">
                {outputs[selNode.id]}
              </pre>
            </div>
          )}
        </>
      ) : (
        <div className="text-[10px] text-term-muted">
          Click a node to edit its inputs / prompt. Drag from an output port (right) to an input
          port (left) to connect. Click an edge to remove it.
          {outputs._error && <div className="mt-2 text-term-down">{outputs._error}</div>}
        </div>
      )}
    </div>
  );

  const processingContent = (
    <AgentProcessingLog
      messages={processingMessages}
      busy={running || aiBusy}
      title="Processing"
      emptyHint="Run a workflow — node execution streams here."
    />
  );

  const assistantContent = (
    <div className="flex h-full min-h-0 flex-col bg-term-panel/40">
      <div className="flex items-center justify-between border-b border-term-border px-2 py-1">
        <div className="flex overflow-hidden rounded border border-term-border">
          {(["assistant", "agent"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setAiMode(mode)}
              disabled={aiBusy}
              className={cx(
                "px-2 py-0.5 text-[10px] uppercase tracking-wide disabled:opacity-50",
                aiMode === mode ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:text-term-text",
              )}
            >
              {mode === "assistant" ? "Assistant" : "Agent ⟳"}
            </button>
          ))}
        </div>
        {messages.length > 0 && (
          <button onClick={() => setMessages([])} className="text-[10px] text-term-muted hover:text-term-text">
            Clear
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-auto px-2 py-1.5">
        {messages.length === 0 ? (
          <div className="text-[11px] leading-relaxed text-term-muted">
            {aiMode === "assistant"
              ? "Assistant: I rewrite the whole graph in one shot. e.g. “screen the S&P 500 for value, backtest 5 years, output the metrics”."
              : "Agent: I build it step by step — add nodes, wire them, RUN the workflow, read the errors, and fix them until it runs clean. e.g. “build a momentum screen + 3-year backtest and make it run”."}
          </div>
        ) : (
          messages.map((m, i) =>
            m.role === "step" ? (
              <div key={i} className="font-mono text-[11px] text-term-text">{m.text}</div>
            ) : m.role === "thought" ? (
              <div key={i} className="pl-2 text-[11px] italic text-term-muted">{m.text}</div>
            ) : m.role === "obs" ? (
              <div key={i} className="whitespace-pre-wrap pl-3 text-[11px] text-term-muted">↳ {m.text}</div>
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
        {aiBusy && (
          <div className="px-2 text-[11px] text-term-muted">{aiMode === "agent" ? "Working…" : "Thinking…"}</div>
        )}
      </div>
      <div className="flex gap-1 border-t border-term-border p-1.5">
        <input
          value={aiInput}
          onChange={(e) => setAiInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && aiSubmit()}
          disabled={aiBusy}
          placeholder={
            aiMode === "agent"
              ? "Tell the agent what workflow to build and run…"
              : "Ask the assistant to build or change the workflow…"
          }
          aria-label={
            aiMode === "agent"
              ? "Tell the agent what workflow to build and run…"
              : "Ask the assistant to build or change the workflow…"
          }
          className={inputCls}
        />
        <button
          onClick={aiSubmit}
          disabled={aiBusy || !aiInput.trim()}
          className="focus-ring rounded border border-term-accent px-2 text-[10px] uppercase tracking-wide text-term-accent transition-colors hover:bg-term-accent/15 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Send
        </button>
      </div>
    </div>
  );

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">Agent · {symbol}</span>
          {running ? (
            <>
              <button
                onClick={paused ? resume : pause}
                title={paused ? "Resume the run" : "Pause after the current step finishes (the graph holds at the next node)"}
                aria-pressed={paused}
                className={cx(
                  "rounded border px-2 py-0.5 text-[10px] uppercase tracking-wide",
                  paused
                    ? "border-term-accent bg-term-accent/10 text-term-accent"
                    : "border-term-border text-term-muted hover:text-term-text",
                )}
              >
                {paused ? "Resume" : "Pause"}
              </button>
              <button
                onClick={stop}
                title="Stop the run (cancels the graph)"
                className="rounded border border-term-down px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-down hover:bg-term-down/15"
              >
                Stop
              </button>
            </>
          ) : (
            <button
              onClick={run}
              className="rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-accent hover:bg-term-accent/10"
            >
              Run
            </button>
          )}
          <button
            onClick={() => setRevealModules((v) => !v)}
            title="When running, open the module each node maps to and stream its output there"
            aria-pressed={revealModules}
            className={cx(
              "rounded border px-2 py-0.5 text-[10px] uppercase tracking-wide",
              revealModules
                ? "border-term-accent bg-term-accent/10 text-term-accent"
                : "border-term-border text-term-muted hover:text-term-text",
            )}
          >
            Reveal ▸
          </button>
          <div className="relative">
            <button
              onClick={() => setLibOpen((v) => !v)}
              className="rounded border border-term-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text"
            >
              Workflows ▾
            </button>
            {libOpen && (
              <div className="absolute left-0 top-7 z-50 min-w-[200px] rounded border border-term-border bg-term-elev p-1.5 shadow-elev-2">
                <div className="mb-1 flex gap-1">
                  <input
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && doSave()}
                    placeholder="Save as…"
                    aria-label="Save as…"
                    className={inputCls}
                  />
                  <button onClick={doSave} className="rounded border border-term-accent px-2 text-[10px] text-term-accent">
                    Save
                  </button>
                </div>
                <div className="my-1 border-t border-term-border" />
                {saved.length === 0 ? (
                  <div className="px-2 py-1 text-[10px] text-term-muted">No saved workflows.</div>
                ) : (
                  saved.map((g) => (
                    <div key={g.name} className="group flex items-center hover:bg-term-border/40">
                      <button onClick={() => doLoad(g.name)} className="flex-1 truncate px-2 py-1 text-left text-xs">
                        {g.name}
                      </button>
                      <IconButton
                        label={`Delete workflow ${g.name}`}
                        onClick={async () => { await api.deleteAgentGraph(g.name); refetchSaved(); }}
                        danger
                        className="px-2 opacity-40 group-hover:opacity-100"
                      >
                        ×
                      </IconButton>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
          <div className="relative">
            <button
              onClick={() => setScenOpen((v) => !v)}
              className={cx(
                "rounded border px-2 py-0.5 text-[10px] uppercase tracking-wide",
                activeScenario ? "border-term-accent bg-term-accent/10 text-term-accent" : "border-term-border text-term-muted hover:text-term-text",
              )}
              title={activeScenario ? `Active scenario: ${activeScenario}` : "Run under a saved scenario"}
            >
              {activeScenario ? `◆ ${activeScenario}` : "Scenarios ▾"}
            </button>
            {scenOpen && (
              <div className="absolute left-0 top-7 z-50 min-w-[260px] rounded border border-term-border bg-term-elev p-1.5 shadow-elev-2">
                <button
                  onClick={() => setActiveScenario("")}
                  className={cx("mb-1 block w-full rounded px-2 py-1 text-left text-[11px]", !activeScenario ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:bg-term-border/40")}
                >
                  ✕ None (use node configs)
                </button>
                {scenarios.map((s) => (
                  <div key={s.name} className="group flex items-center hover:bg-term-border/40">
                    <button
                      onClick={() => { setActiveScenario(s.name); setScenOpen(false); }}
                      className={cx("flex-1 truncate px-2 py-1 text-left text-[11px]", activeScenario === s.name && "text-term-accent")}
                      title={s.description}
                    >
                      {activeScenario === s.name ? "◆ " : ""}{s.name}
                    </button>
                    <IconButton label={`Delete scenario ${s.name}`} onClick={async () => { await api.deleteScenario(s.name); if (activeScenario === s.name) setActiveScenario(""); refetchScen(); }} danger className="px-2 opacity-40 group-hover:opacity-100">×</IconButton>
                  </div>
                ))}
                <div className="my-1 border-t border-term-border" />
                <div className="text-[9px] uppercase tracking-wider text-term-muted">New scenario</div>
                <input value={scenForm.name} onChange={(e) => setScenForm({ ...scenForm, name: e.target.value })} placeholder="name" aria-label="name" className={cx(inputCls, "my-1")} />
                <div className="grid grid-cols-2 gap-1">
                  <input value={scenForm.universe} onChange={(e) => setScenForm({ ...scenForm, universe: e.target.value })} placeholder="universe" aria-label="universe" className={inputCls} />
                  <input value={scenForm.factor} onChange={(e) => setScenForm({ ...scenForm, factor: e.target.value })} placeholder="factor" aria-label="factor" className={inputCls} />
                  <select value={scenForm.mode} onChange={(e) => setScenForm({ ...scenForm, mode: e.target.value })} aria-label="mode" className={inputCls}>
                    {["long_only", "long_short"].map((m) => <option key={m}>{m}</option>)}
                  </select>
                  <label className="flex items-center gap-1 text-[10px] text-term-muted">yrs<input type="number" value={scenForm.years} onChange={(e) => setScenForm({ ...scenForm, years: +e.target.value || 3 })} className={inputCls} /></label>
                  <label className="flex items-center gap-1 text-[10px] text-term-muted">eq%<input type="number" value={scenForm.equity_pct} onChange={(e) => setScenForm({ ...scenForm, equity_pct: +e.target.value || 0 })} className={inputCls} /></label>
                  <label className="flex items-center gap-1 text-[10px] text-term-muted">vol×<input type="number" value={scenForm.vol_mult} onChange={(e) => setScenForm({ ...scenForm, vol_mult: +e.target.value || 1 })} className={inputCls} /></label>
                </div>
                <button onClick={saveScenario} className="mt-1 w-full rounded border border-term-accent px-2 py-0.5 text-[10px] uppercase text-term-accent hover:bg-term-accent/10">Save scenario</button>
              </div>
            )}
          </div>
        </>
      }
    >
      <div className="flex h-full flex-col">
        {/* Workflow tabs + searchable dropdown + create menu (mirrors the Committees module) */}
        <div className="flex items-center gap-1 border-b border-term-border bg-term-elev px-1.5 py-1">
          <div
            ref={tabsRef}
            className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:thin]"
          >
            {workflows.map((w) => (
              <span
                key={w.id}
                className={cx(
                  "group flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs",
                  w.id === active?.id
                    ? "bg-term-accent/15 text-term-accent"
                    : "text-term-muted hover:text-term-text",
                )}
              >
                <button
                  type="button"
                  onClick={() => setActiveId(w.id)}
                  className="focus-ring max-w-[140px] truncate"
                  title={w.name}
                >
                  {w.name}
                </button>
                <IconButton
                  label={`remove ${w.name}`}
                  danger
                  className="opacity-40 group-hover:opacity-100"
                  onClick={() => removeWorkflow(w.id)}
                >
                  ✕
                </IconButton>
              </span>
            ))}
          </div>

          {/* searchable dropdown of all workflows (jump to any, even when tabs overflow) */}
          <div className="relative shrink-0">
            <button
              type="button"
              onClick={() => {
                setSelectorOpen((v) => !v);
                setWorkflowSearch("");
                setMenuOpen(false);
              }}
              aria-haspopup="listbox"
              aria-expanded={selectorOpen}
              title="Search workflows"
              className="focus-ring rounded border border-term-border px-1.5 py-1 text-xs text-term-muted hover:text-term-text"
            >
              Search ▾
            </button>
            {selectorOpen && (
              <div
                role="listbox"
                className="absolute right-0 z-30 mt-1 w-60 rounded border border-term-border bg-term-elev p-1 shadow-elev-2"
              >
                <input
                  autoFocus
                  value={workflowSearch}
                  onChange={(e) => setWorkflowSearch(e.target.value)}
                  placeholder="Search workflows…"
                  className="focus-ring mb-1 w-full rounded border border-term-border bg-term-sunken px-2 py-1 text-xs"
                />
                <div className="max-h-56 overflow-auto">
                  {workflows
                    .filter((w) => w.name.toLowerCase().includes(workflowSearch.trim().toLowerCase()))
                    .map((w) => (
                      <div
                        key={w.id}
                        className={cx(
                          "group flex items-center gap-1 rounded px-1",
                          w.id === active?.id ? "bg-term-accent/15" : "hover:bg-term-border/50",
                        )}
                      >
                        <button
                          type="button"
                          role="option"
                          aria-selected={w.id === active?.id}
                          onClick={() => {
                            setActiveId(w.id);
                            setSelectorOpen(false);
                          }}
                          className={cx(
                            "focus-ring min-w-0 flex-1 truncate px-1 py-1 text-left text-xs",
                            w.id === active?.id ? "text-term-accent" : "text-term-text",
                          )}
                          title={w.name}
                        >
                          {w.name}
                          <span className="ml-1.5 text-[10px] text-term-muted">{w.spec.nodes.length}</span>
                        </button>
                        <IconButton
                          label={`remove ${w.name}`}
                          danger
                          className="opacity-40 group-hover:opacity-100"
                          onClick={() => removeWorkflow(w.id)}
                        >
                          ✕
                        </IconButton>
                      </div>
                    ))}
                  {workflows.filter((w) =>
                    w.name.toLowerCase().includes(workflowSearch.trim().toLowerCase()),
                  ).length === 0 && (
                    <div className="px-2 py-2 text-[11px] text-term-muted">No workflows match.</div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* create menu: blank, default pipeline, or a copy of a saved workflow */}
          <div className="relative shrink-0">
            <button
              type="button"
              onClick={() => {
                setMenuOpen((v) => !v);
                setSelectorOpen(false);
              }}
              title="New workflow"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className="focus-ring rounded border border-dashed border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent"
            >
              ＋ Workflow ▾
            </button>
            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 z-30 mt-1 min-w-[200px] rounded border border-term-border bg-term-elev p-1 shadow-elev-2"
                onMouseLeave={() => setMenuOpen(false)}
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={createPipeline}
                  className="focus-ring block w-full rounded px-2 py-1 text-left text-xs hover:bg-term-accent/15 hover:text-term-accent"
                >
                  Default pipeline
                  <span className="ml-1 text-term-muted">data→…→backtest</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={createBlank}
                  className="focus-ring block w-full rounded px-2 py-1 text-left text-xs text-term-muted hover:bg-term-border/60 hover:text-term-text"
                >
                  Blank workflow
                </button>
                {saved.length > 0 && (
                  <>
                    <div className="my-1 border-t border-term-border" />
                    <div className="px-2 py-1 text-[9px] uppercase tracking-wider text-term-muted">
                      New from saved
                    </div>
                    {saved.map((g) => (
                      <button
                        key={g.name}
                        type="button"
                        role="menuitem"
                        onClick={() => createFromSaved(g.name)}
                        className="focus-ring block w-full truncate rounded px-2 py-1 text-left text-xs hover:bg-term-accent/15 hover:text-term-accent"
                        title={g.name}
                      >
                        {g.name}
                      </button>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1">
          <BentoSubGrid
            storageKey={`agentwf:${props.api.id}`}
            seed={(api) => {
              // Default bento: Graph fills the top, Inspector docks right, Processing + Assistant
              // form a bottom row. The user can drag/resize/close — the layout persists per panel.
              api.addPanel({ id: "graph", component: "graph", title: "Graph" });
              const insp = api.addPanel({ id: "inspector", component: "inspector", title: "Inspector", position: { referencePanel: "graph", direction: "right" } });
              const proc = api.addPanel({ id: "processing", component: "processing", title: "Processing", position: { referencePanel: "graph", direction: "below" } });
              api.addPanel({ id: "assistant", component: "assistant", title: "Assistant", position: { referencePanel: "processing", direction: "right" } });
              insp.api.setSize({ width: 240 });
              proc.api.setSize({ height: 200 });
            }}
            panels={[
              { id: "graph", title: "Graph", content: graphContent },
              { id: "inspector", title: "Inspector", content: inspectorContent },
              { id: "processing", title: "Processing", content: processingContent },
              { id: "assistant", title: "Assistant", content: assistantContent },
            ]}
          />
        </div>
      </div>
    </WidgetShell>
  );
}

/** Universe (data category) picker for a node's `universe` param: keeps the free-text input but adds
 * a "Scan ▾" dropdown that scans the lake live (GET /api/universes) and lists the available data
 * categories to pick from. Opening the dropdown triggers the scan; the list is filterable. */
function UniversePicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const { data, isFetching } = useQuery({
    queryKey: ["universes"],
    queryFn: api.universes,
    enabled: open, // scan on demand, when the dropdown is opened
    staleTime: 60_000,
  });
  const universes = data?.universes ?? [];
  const shown = q.trim()
    ? universes.filter((u) => u.toLowerCase().includes(q.trim().toLowerCase()))
    : universes;

  return (
    <div className="relative">
      <div className="flex gap-1">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="dow30"
          aria-label="Universe"
          className={inputCls}
        />
        <button
          type="button"
          onClick={() => { setOpen((v) => !v); setQ(""); }}
          aria-haspopup="listbox"
          aria-expanded={open}
          title="Scan available data categories from the lake"
          className="focus-ring shrink-0 rounded border border-term-border px-1.5 text-[10px] uppercase tracking-wide text-term-muted hover:border-term-accent hover:text-term-accent"
        >
          Scan ▾
        </button>
      </div>
      {open && (
        <div
          role="listbox"
          className="absolute right-0 z-30 mt-1 w-full min-w-[180px] rounded border border-term-border bg-term-elev p-1 shadow-elev-2"
        >
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter categories…"
            aria-label="Filter data categories"
            className="focus-ring mb-1 w-full rounded border border-term-border bg-term-sunken px-2 py-1 text-[11px]"
          />
          <div className="max-h-44 overflow-auto">
            {isFetching && <div className="px-2 py-1 text-[11px] text-term-muted">Scanning…</div>}
            {!isFetching && shown.length === 0 && (
              <div className="px-2 py-1 text-[11px] text-term-muted">No data categories.</div>
            )}
            {shown.map((u) => (
              <button
                key={u}
                type="button"
                role="option"
                aria-selected={u === value}
                onClick={() => { onChange(u); setOpen(false); setQ(""); }}
                className={cx(
                  "focus-ring block w-full truncate rounded px-2 py-1 text-left font-mono text-[11px]",
                  u === value ? "bg-term-accent/15 text-term-accent" : "text-term-text hover:bg-term-border/50",
                )}
                title={u}
              >
                {u}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
