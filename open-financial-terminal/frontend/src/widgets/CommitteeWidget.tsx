import { useEffect, useRef, useState } from "react";
import { api, openCommitteeSocket } from "../api/client";
import { cx } from "../lib/format";
import type {
  CommitteeMember,
  CommitteePreset,
  CommitteeTemplate,
  CommitteeVerdict,
  KnowledgeFile,
} from "../api/types";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { WidgetShell, useWidgetSymbol, TextButton, IconButton } from "./shell";
import { EmptyState } from "../components/States";
import { useSettings } from "../state/settings";

type AgentStatus = "idle" | "active" | "done";

interface Pt {
  x: number;
  y: number;
}
/** A directed relationship: the `from` agent's assessment feeds into `to`. */
interface Edge {
  from: string;
  to: string;
}

/** One saved committee = a sub-widget inside the Committees module: its own name, mandate, and
 * roster (each member carrying its own per-agent knowledge base). The relationship canvas stores
 * node positions (`layout`, by role) and connections (`edges`). All persisted in panel params. */
interface Committee {
  id: string;
  name: string;
  prompt: string;
  members: CommitteeMember[];
  layout?: Record<string, Pt>;
  edges?: Edge[];
}

/** Canvas viewBox + node radius — shared by the editable relationship graph. */
const NR = 20; // node radius in px
// Node positions are stored as FRACTIONS of the canvas (0..1) so they stay correct as the canvas
// resizes and can be placed anywhere in the whole area. (Legacy layouts used 320x250 px coords.)
const LEGACY_VW = 320;
const LEGACY_VH = 250;

/** Folder-name slug — must match the crewai service's `_slug` so the canvas can tell which agents
 * have knowledge files on disk (under <dir>/<committee>/<agent>/). */
function slug(s: string): string {
  return (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "untitled";
}

/** Default fan-in layout as canvas fractions: analysts along the top, Chair centered near bottom. */
function defaultLayout(members: CommitteeMember[]): Record<string, Pt> {
  const out: Record<string, Pt> = {};
  const analysts = members.slice(0, -1);
  const chair = members[members.length - 1];
  const A = Math.max(analysts.length, 1);
  analysts.forEach((m, i) => (out[m.role] = { x: (i + 0.5) / A, y: 0.16 }));
  if (chair) out[chair.role] = { x: 0.5, y: 0.78 };
  return out;
}
/** Read a stored position as a fraction, migrating legacy 320x250 px coords. */
function asFraction(p: Pt): Pt {
  return p.x > 1 || p.y > 1 ? { x: p.x / LEGACY_VW, y: p.y / LEGACY_VH } : p;
}
/** Default relationships: every analyst feeds the Chair. */
function defaultEdges(members: CommitteeMember[]): Edge[] {
  const chair = members[members.length - 1];
  if (!chair) return [];
  return members.slice(0, -1).map((a) => ({ from: a.role, to: chair.role }));
}

interface Runtime {
  outputs: Record<string, string>;
  verdict: CommitteeVerdict | null;
  chairNote: string;
  running: boolean;
  error: string;
}
const EMPTY_RUNTIME: Runtime = { outputs: {}, verdict: null, chairNote: "", running: false, error: "" };

/** Strip the fenced ```json verdict block from the Chair's output and parse it. */
function parseVerdict(text: string): { markdown: string; verdict: CommitteeVerdict | null } {
  const m = text.match(/```json\s*(\{[\s\S]*?\})\s*```/);
  if (!m) return { markdown: text.trim(), verdict: null };
  let verdict: CommitteeVerdict | null = null;
  try {
    verdict = JSON.parse(m[1]) as CommitteeVerdict;
  } catch {
    verdict = null;
  }
  return { markdown: text.replace(m[0], "").trim(), verdict };
}

function recTone(rec: string): string {
  const r = (rec || "").toLowerCase();
  if (r.includes("buy") || r.includes("accumulate")) return "text-term-up border-term-up";
  if (r.includes("sell") || r.includes("reduce")) return "text-term-down border-term-down";
  return "text-term-accent border-term-accent";
}

const AVATARS: [RegExp, string][] = [
  [/bull|growth/i, "🐂"],
  [/bear|short/i, "🐻"],
  [/risk/i, "🛡️"],
  [/macro|strateg/i, "🌐"],
  [/chair|cio|chief/i, "⚖️"],
  [/quant|data/i, "🔢"],
  [/value/i, "🧮"],
  [/esg|sustain/i, "🌱"],
  [/tech|momentum/i, "📈"],
  [/credit|bond|fixed/i, "🏦"],
];
function avatarFor(role: string): string {
  for (const [re, e] of AVATARS) if (re.test(role)) return e;
  return "🧠";
}
/** Text fallback when emoji avatars are disabled: up to two leading word-initials. */
function initialsFor(role: string): string {
  const w = (role || "").replace(/[^a-zA-Z ]/g, " ").split(/\s+/).filter(Boolean);
  return ((w[0]?.[0] ?? "A") + (w[1]?.[0] ?? "")).toUpperCase();
}

/** Committees — a CrewAI module that holds multiple committees as sub-widgets. Each committee is a
 * crew where every agent is visualized as a card, has its own editable prompt, and its OWN knowledge
 * base (RAG, separate from the other agents). The user can create or remove committees; deliberation
 * streams live into each agent's card and the Chair returns a structured verdict. */
export default function CommitteeWidget(props: WidgetProps) {
  const { symbol, channel, setChannel } = useWidgetSymbol(props);
  const patch = (p: Record<string, unknown>) => props.api.updateParameters(p);

  const [committees, setCommitteesState] = useState<Committee[]>(
    (props.params.committees as Committee[]) ?? [],
  );
  const [activeId, setActiveIdState] = useState<string>(
    (props.params.activeCommitteeId as string) ?? "",
  );
  // Agent-folder slugs (for the active committee) that currently hold knowledge files, so the
  // canvas can flag which agents have RAG. Refreshed from the Directory/Committee/Agent tree.
  const [knowSlugs, setKnowSlugs] = useState<Set<string>>(new Set());
  const [supportedExts, setSupportedExts] = useState<string[]>([]);
  const [editMode, setEditMode] = useState(false);
  const [runtime, setRuntime] = useState<Record<string, Runtime>>({});
  const [presets, setPresets] = useState<CommitteePreset[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [committeeSearch, setCommitteeSearch] = useState("");
  const [view, setView] = useState<"cards" | "flow">("flow");
  const [zoomIdx, setZoomIdx] = useState<number | null>(null);

  const rosterRef = useRef<CommitteeMember[] | null>(null);
  const runRef = useRef<string>("");
  const wsRef = useRef<WebSocket | null>(null);

  const setCommittees = (cs: Committee[]) => {
    setCommitteesState(cs);
    patch({ committees: cs });
  };
  const setActiveId = (id: string) => {
    setActiveIdState(id);
    patch({ activeCommitteeId: id });
  };

  const active = committees.find((c) => c.id === activeId) ?? committees[0] ?? null;
  const rt = (active && runtime[active.id]) || EMPTY_RUNTIME;

  // First run / migration: seed one sub-widget per built-in preset (Investment Committee +
  // Risk Committee), carrying over any legacy single-committee params into the first; or repair a
  // dangling active id.
  useEffect(() => {
    let cancelled = false;
    if (!committees.length) {
      const legacy = props.params.members as CommitteeMember[] | undefined;
      const seed = (list: CommitteePreset[]) => {
        if (cancelled) return;
        const sources: CommitteePreset[] = list.length
          ? list
          : [{ name: "Investment Committee", members: legacy ?? [] }];
        rosterRef.current = rosterRef.current ?? sources[0]?.members ?? null;
        const seeded: Committee[] = sources.map((p, i) => ({
          id: crypto.randomUUID(),
          name: p.name,
          prompt: i === 0 ? ((props.params.prompt as string) ?? "") : "",
          members: (i === 0 && legacy?.length ? legacy : p.members).map((m) => ({ ...m })),
        }));
        setCommittees(seeded);
        setActiveId(seeded[0].id);
      };
      api.committeePresets().then((r) => seed(r.presets)).catch(() => seed([]));
    } else if (!committees.some((c) => c.id === activeId)) {
      setActiveId(committees[0].id);
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [committees.length]);

  const activeName = active?.name ?? "";
  const refreshKnow = () =>
    api.committeeKnowledgeTree().then((t) => {
      setSupportedExts(t.supported);
      const node = t.committees.find((c) => c.name === slug(activeName));
      setKnowSlugs(new Set((node?.agents ?? []).filter((a) => a.files.length).map((a) => a.name)));
    }).catch(() => undefined);
  useEffect(() => {
    api.committeeRoster().then((r) => (rosterRef.current = r.members)).catch(() => undefined);
    api.committeePresets().then((r) => setPresets(r.presets)).catch(() => undefined);
  }, []);

  // Refresh the per-agent knowledge flags on demand only — NO background polling. Triggers: the
  // active committee changes, the window regains focus, and right before a convene (see convene()).
  // This detects external directory edits without continuous compute.
  useEffect(() => {
    refreshKnow();
    const onFocus = () => {
      if (!document.hidden) refreshKnow();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeName]);

  useEffect(() => setZoomIdx(null), [activeId]);

  // Let the mouse wheel scroll the committee tab strip horizontally (non-passive so we can
  // translate vertical wheel deltas into horizontal scroll and not scroll the body).
  const tabsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = tabsRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth) return; // nothing to scroll
      const delta = Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
      if (!delta) return;
      e.preventDefault();
      el.scrollLeft += delta;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Convene WebSocket: frames are routed to the committee that started the run (runRef).
  useEffect(() => {
    const ws = openCommitteeSocket();
    wsRef.current = ws;
    const setRt = (fn: (r: Runtime) => Runtime) => {
      const id = runRef.current;
      if (!id) return;
      setRuntime((cur) => ({ ...cur, [id]: fn(cur[id] ?? EMPTY_RUNTIME) }));
    };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data) as { type: string; payload?: unknown; detail?: string };
      if (m.type === "task") {
        const p = m.payload as { agent?: string; raw?: string } | null;
        if (!p?.agent) return;
        const { markdown } = parseVerdict(p.raw ?? "");
        setRt((r) => ({ ...r, outputs: { ...r.outputs, [p.agent as string]: markdown } }));
      } else if (m.type === "result") {
        const { verdict: v, markdown } = parseVerdict(String(m.payload ?? ""));
        setRt((r) => ({ ...r, verdict: v, chairNote: markdown }));
      } else if (m.type === "error") {
        setRt((r) => ({ ...r, error: m.detail ?? String(m.payload ?? "error"), running: false }));
      } else if (m.type === "done") {
        setRt((r) => ({ ...r, running: false }));
      }
    };
    ws.onclose = () => setRt((r) => ({ ...r, running: false }));
    return () => ws.close();
  }, []);

  // ── committee (sub-widget) management ──────────────────────────────────────
  const addCommittee = (name: string, roster: CommitteeMember[]) => {
    const dupes = committees.filter((c) => c.name === name || c.name.startsWith(`${name} `)).length;
    const c: Committee = {
      id: crypto.randomUUID(),
      name: dupes ? `${name} ${dupes + 1}` : name,
      prompt: "",
      members: roster.map((m) => ({ ...m })),
    };
    setCommittees([...committees, c]);
    setActiveId(c.id);
    setEditMode(true);
    setMenuOpen(false);
  };
  const createBlank = () => addCommittee("Committee", []); // truly blank — no agents
  const removeCommittee = (id: string) => {
    const next = committees.filter((c) => c.id !== id);
    setCommittees(next);
    if (activeId === id) setActiveId(next[0]?.id ?? "");
  };

  // ── active-committee mutators ──────────────────────────────────────────────
  const updateActive = (p: Partial<Committee>) =>
    active && setCommittees(committees.map((c) => (c.id === active.id ? { ...c, ...p } : c)));
  const setMembers = (members: CommitteeMember[]) => updateActive({ members });
  // editMember is intentionally omitted — per-agent editing happens via the zoom (editMemberKeyed).
  const dragIndexRef = useRef<number | null>(null);
  // Drag-reorder cards. The LAST member is the Chair, so dragging to the end makes that agent Chair.
  const reorderMember = (from: number | null, to: number) => {
    if (from == null || from === to || !active) return;
    const arr = [...active.members];
    const [m] = arr.splice(from, 1);
    arr.splice(to, 0, m);
    setMembers(arr);
    if (zoomIdx === from) setZoomIdx(to);
  };
  // Per-agent editing happens via the zoom; a role rename also migrates the canvas layout/edges.
  const editMemberKeyed = (i: number, p: Partial<CommitteeMember>) => {
    if (!active) return;
    const oldRole = active.members[i]?.role;
    const up: Partial<Committee> = {
      members: active.members.map((m, idx) => (idx === i ? { ...m, ...p } : m)),
    };
    if (p.role && oldRole && p.role !== oldRole) {
      if (active.layout?.[oldRole]) {
        const l = { ...active.layout };
        l[p.role] = l[oldRole];
        delete l[oldRole];
        up.layout = l;
      }
      if (active.edges) {
        up.edges = active.edges.map((e) => ({
          from: e.from === oldRole ? p.role! : e.from,
          to: e.to === oldRole ? p.role! : e.to,
        }));
      }
    }
    updateActive(up);
  };
  const removeMember = (i: number) => {
    if (!active) return; // a committee may be emptied entirely (truly blank)
    setMembers(active.members.filter((_, idx) => idx !== i));
  };
  const addAnalyst = () => {
    if (!active) return;
    // First agent in a blank committee = the Chair (synthesizer); later agents are analysts
    // inserted before the Chair (which stays last).
    if (active.members.length === 0) {
      setMembers([
        {
          role: "Chair",
          goal: "Weigh the discussion and issue the committee's decision",
          backstory: "The committee chair, accountable for the final call and synthesis.",
        },
      ]);
      return;
    }
    const blank: CommitteeMember = { role: "New Analyst", goal: "", backstory: "" };
    setMembers([...active.members.slice(0, -1), blank, active.members[active.members.length - 1]]);
  };

  const convene = () => {
    if (!active || rt.running || wsRef.current?.readyState !== WebSocket.OPEN) return;
    const err =
      !active.members.length
        ? "Add at least one agent (Edit agents) to convene."
        : !active.prompt.trim()
          ? "Write a mandate to convene."
          : "";
    if (err) {
      setRuntime((cur) => ({
        ...cur,
        [active.id]: { ...(cur[active.id] ?? EMPTY_RUNTIME), error: err },
      }));
      return;
    }
    runRef.current = active.id;
    setRuntime((cur) => ({ ...cur, [active.id]: { ...EMPTY_RUNTIME, running: true } }));
    setEditMode(false);
    refreshKnow(); // sync the knowledge indicators with the directory at run time
    wsRef.current.send(
      JSON.stringify({
        prompt: active.prompt,
        symbol,
        committee: active.name,
        members: active.members,
        edges: active.edges ?? defaultEdges(active.members),
      }),
    );
  };

  const members = active?.members ?? [];
  const firstPending = members.findIndex((m) => !rt.outputs[m.role]);
  const statusOf = (i: number, role: string): AgentStatus => {
    if (rt.outputs[role]) return "done";
    if (rt.running && i === firstPending) return "active";
    return "idle";
  };

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <>
          <span className="text-[10px] uppercase tracking-wider text-term-muted">
            Committees · <span className="font-mono text-term-text">{symbol}</span>
          </span>
          <div className="ml-auto flex items-center gap-2">
            {active && (
              <TextButton active={editMode} onClick={() => setEditMode((v) => !v)}>
                {editMode ? "Done editing" : "Edit agents"}
              </TextButton>
            )}
            {active && (
              <TextButton active onClick={convene}>
                {rt.running ? "Convening…" : "Convene"}
              </TextButton>
            )}
          </div>
        </>
      }
    >
      <div className="relative flex h-full flex-col">
        {/* Committee tabs + searchable dropdown + create menu */}
        <div className="flex items-center gap-1 border-b border-term-border bg-term-elev px-1.5 py-1">
          {/* scrollable tabs (mouse wheel scrolls horizontally) */}
          <div
            ref={tabsRef}
            className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:thin]"
          >
            {committees.map((c) => (
              <span
                key={c.id}
                className={cx(
                  "group flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs",
                  c.id === active?.id
                    ? "bg-term-accent/15 text-term-accent"
                    : "text-term-muted hover:text-term-text",
                )}
              >
                <button
                  type="button"
                  onClick={() => setActiveId(c.id)}
                  className="focus-ring max-w-[140px] truncate"
                  title={c.name}
                >
                  {c.name}
                </button>
                <IconButton
                  label={`remove ${c.name}`}
                  danger
                  className="opacity-40 group-hover:opacity-100"
                  onClick={() => removeCommittee(c.id)}
                >
                  ✕
                </IconButton>
              </span>
            ))}
          </div>

          {/* searchable dropdown of all committees (jump to any, even when tabs overflow) */}
          <div className="relative shrink-0">
            <button
              type="button"
              onClick={() => {
                setSelectorOpen((v) => !v);
                setCommitteeSearch("");
                setMenuOpen(false);
              }}
              aria-haspopup="listbox"
              aria-expanded={selectorOpen}
              title="Search committees"
              className="focus-ring rounded border border-term-border px-1.5 py-1 text-xs text-term-muted hover:text-term-text"
            >
              Search ▾
            </button>
            {selectorOpen && (
              <div
                role="listbox"
                className="absolute right-0 z-20 mt-1 w-60 rounded border border-term-border bg-term-elev p-1 shadow-elev-2"
              >
                <input
                  autoFocus
                  value={committeeSearch}
                  onChange={(e) => setCommitteeSearch(e.target.value)}
                  placeholder="Search committees…"
                  className="focus-ring mb-1 w-full rounded border border-term-border bg-term-sunken px-2 py-1 text-xs"
                />
                <div className="max-h-56 overflow-auto">
                  {committees
                    .filter((c) => c.name.toLowerCase().includes(committeeSearch.trim().toLowerCase()))
                    .map((c) => (
                      <div
                        key={c.id}
                        className={cx(
                          "group flex items-center gap-1 rounded px-1",
                          c.id === active?.id ? "bg-term-accent/15" : "hover:bg-term-border/50",
                        )}
                      >
                        <button
                          type="button"
                          role="option"
                          aria-selected={c.id === active?.id}
                          onClick={() => {
                            setActiveId(c.id);
                            setSelectorOpen(false);
                          }}
                          className={cx(
                            "focus-ring min-w-0 flex-1 truncate px-1 py-1 text-left text-xs",
                            c.id === active?.id ? "text-term-accent" : "text-term-text",
                          )}
                          title={c.name}
                        >
                          {c.name}
                          <span className="ml-1.5 text-[10px] text-term-muted">{c.members.length}</span>
                        </button>
                        <IconButton
                          label={`remove ${c.name}`}
                          danger
                          className="opacity-40 group-hover:opacity-100"
                          onClick={() => removeCommittee(c.id)}
                        >
                          ✕
                        </IconButton>
                      </div>
                    ))}
                  {committees.filter((c) =>
                    c.name.toLowerCase().includes(committeeSearch.trim().toLowerCase()),
                  ).length === 0 && (
                    <div className="px-2 py-2 text-[11px] text-term-muted">No committees match.</div>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="relative shrink-0">
            <button
              type="button"
              onClick={() => {
                setMenuOpen((v) => !v);
                setSelectorOpen(false);
              }}
              title="New committee"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className="focus-ring rounded border border-dashed border-term-border px-2 py-1 text-xs text-term-muted hover:border-term-accent hover:text-term-accent"
            >
              ＋ Committee ▾
            </button>
            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 z-20 mt-1 min-w-[180px] rounded border border-term-border bg-term-elev p-1 shadow-elev-2"
                onMouseLeave={() => setMenuOpen(false)}
              >
                <div className="px-2 py-1 text-[9px] uppercase tracking-wider text-term-muted">
                  New from preset
                </div>
                {presets.map((p) => (
                  <button
                    key={p.name}
                    type="button"
                    role="menuitem"
                    onClick={() => addCommittee(p.name, p.members)}
                    className="focus-ring block w-full rounded px-2 py-1 text-left text-xs hover:bg-term-accent/15 hover:text-term-accent"
                  >
                    {p.name}
                    <span className="ml-1 text-term-muted">{p.members.length}</span>
                  </button>
                ))}
                <div className="my-1 border-t border-term-border" />
                <button
                  type="button"
                  role="menuitem"
                  onClick={createBlank}
                  className="focus-ring block w-full rounded px-2 py-1 text-left text-xs text-term-muted hover:bg-term-border/60 hover:text-term-text"
                >
                  Blank committee
                </button>
              </div>
            )}
          </div>
        </div>

        {!active ? (
          <div className="flex flex-1 items-center justify-center p-4">
            <EmptyState
              title="No committees yet."
              hint="Create one to assemble a crew of AI analysts."
            />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* Name (editable in edit mode) + mandate */}
            <div className="border-b border-term-border p-2">
              {editMode && (
                <input
                  value={active.name}
                  onChange={(e) => updateActive({ name: e.target.value })}
                  className="focus-ring mb-1.5 w-full rounded border border-term-border bg-term-sunken px-2 py-1 text-xs font-semibold"
                  placeholder="committee name"
                />
              )}
              <label className="mb-1 block text-[10px] uppercase tracking-wider text-term-muted">
                Mandate / context for {symbol}
              </label>
              <textarea
                value={active.prompt}
                onChange={(e) => updateActive({ prompt: e.target.value })}
                rows={2}
                placeholder={`e.g. Should we initiate a position in ${symbol} for a long-only growth book? Weigh valuation vs. the AI capex cycle.`}
                className="focus-ring w-full resize-none rounded border border-term-border bg-term-sunken px-2 py-1.5 text-xs placeholder:text-term-muted focus:border-term-accent"
              />
            </div>

            {editMode && (
              <EditToolbar members={members} setMembers={setMembers} addAnalyst={addAnalyst} />
            )}

            <div className="flex min-h-0 flex-1 flex-col overflow-auto p-2">
              {rt.error && (
                <div className="mb-2 rounded border border-term-down/50 bg-term-down/10 px-2 py-1.5 text-xs text-term-down">
                  ⚠️ {rt.error}
                </div>
              )}

              {/* View toggle: Relationship (diagram) vs Cards (detail). Editing forces cards. */}
              {!editMode && (
                <div className="mb-2 flex items-center gap-1">
                  {([
                    ["flow", "Relationship"],
                    ["cards", "Cards"],
                  ] as const).map(([v, label]) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => setView(v)}
                      className={cx(
                        "focus-ring rounded border px-2 py-0.5 text-[10px] uppercase tracking-wide",
                        view === v
                          ? "border-term-accent bg-term-accent/15 text-term-accent"
                          : "border-term-border text-term-muted hover:text-term-text",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                  <span className="ml-1 text-[10px] text-term-muted">
                    {view === "flow" ? "analysts → Chair synthesis" : "click a card to edit · drag to reorder"}
                  </span>
                </div>
              )}

              {members.length === 0 ? (
                <EmptyState
                  title="This committee has no agents yet."
                  hint={editMode ? "Click ＋ Agent above to add one." : "Click 'Edit agents' to add agents."}
                />
              ) : !editMode && view === "flow" ? (
                <CommitteeFlow
                  key={active.id}
                  members={members}
                  layout={active.layout}
                  edges={active.edges}
                  onLayout={(layout) => updateActive({ layout })}
                  onEdges={(edges) => updateActive({ edges })}
                  statusOf={statusOf}
                  selectedRole={zoomIdx != null ? members[zoomIdx]?.role ?? null : null}
                  onOpenAgent={(role) => setZoomIdx(members.findIndex((m) => m.role === role))}
                  verdict={rt.verdict}
                  hasKnowledge={(role) => knowSlugs.has(slug(role))}
                />
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  {members.map((m, i) => {
                    const isChair = i === members.length - 1;
                    return (
                      <AgentCard
                        key={i}
                        member={m}
                        index={i}
                        isChair={isChair}
                        status={statusOf(i, m.role)}
                        output={rt.outputs[m.role] ?? (isChair ? rt.chairNote : "")}
                        editMode={editMode}
                        onOpen={() => setZoomIdx(i)}
                        onRemove={() => removeMember(i)}
                        onDragStart={() => (dragIndexRef.current = i)}
                        onDropOn={() => reorderMember(dragIndexRef.current, i)}
                      />
                    );
                  })}
                </div>
              )}

              {rt.verdict && (
                <div className="mt-2 rounded border border-term-accent/60 bg-term-panel p-2.5">
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-term-muted">
                    Committee Verdict
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={cx(
                        "rounded border px-2 py-0.5 text-xs font-semibold uppercase",
                        recTone(rt.verdict.recommendation),
                      )}
                    >
                      {rt.verdict.recommendation}
                    </span>
                    <span className="text-[11px] text-term-muted">
                      Conviction: <span className="text-term-text">{rt.verdict.conviction}</span>
                    </span>
                    <span className="text-[11px] text-term-muted">
                      Sizing: <span className="text-term-text">{rt.verdict.sizing}</span>
                    </span>
                  </div>
                  {rt.verdict.key_risks?.length > 0 && (
                    <div className="mt-1 text-[11px]">
                      <span className="text-term-muted">Key risks:</span>
                      <ul className="ml-4 list-disc text-xs">
                        {rt.verdict.key_risks.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {rt.verdict.dissent && (
                    <div className="mt-1 text-[11px] text-term-muted">
                      Dissent: <span className="text-term-text">{rt.verdict.dissent}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Zoomed agent editor — opens when an agent is clicked in the Relationship canvas OR a card. */}
        {active &&
          zoomIdx != null &&
          active.members[zoomIdx] && (
            <AgentZoom
              member={active.members[zoomIdx]}
              index={zoomIdx}
              isChair={zoomIdx === active.members.length - 1}
              status={statusOf(zoomIdx, active.members[zoomIdx].role)}
              output={
                rt.outputs[active.members[zoomIdx].role] ??
                (zoomIdx === active.members.length - 1 ? rt.chairNote : "")
              }
              committeeName={active.name}
              supported={supportedExts}
              onKnowledgeChanged={refreshKnow}
              onChange={(p) => editMemberKeyed(zoomIdx, p)}
              onClose={() => setZoomIdx(null)}
            />
          )}
      </div>
    </WidgetShell>
  );
}

/** Add-agent + committee-template (reusable roster) row, visible in edit mode. */
function EditToolbar({
  members,
  setMembers,
  addAnalyst,
}: {
  members: CommitteeMember[];
  setMembers: (m: CommitteeMember[]) => void;
  addAnalyst: () => void;
}) {
  const [templates, setTemplates] = useState<CommitteeTemplate[]>([]);
  const [tplName, setTplName] = useState("");
  const [busy, setBusy] = useState(false);
  const reload = () =>
    api.committeeTemplates().then((r) => setTemplates(r.templates)).catch(() => undefined);
  useEffect(() => {
    reload();
  }, []);

  const save = async () => {
    if (!tplName.trim()) return;
    setBusy(true);
    try {
      await api.saveCommitteeTemplate(tplName.trim(), members);
      setTplName("");
      reload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-term-border bg-term-sunken/40 p-2 text-xs">
      <TextButton onClick={addAnalyst}>＋ Agent</TextButton>
      <span className="mx-1 text-term-muted">·</span>
      <input
        value={tplName}
        onChange={(e) => setTplName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && save()}
        placeholder="save roster as template…"
        className="focus-ring w-40 rounded border border-term-border bg-term-sunken px-1.5 py-0.5 text-xs"
      />
      <TextButton onClick={save}>{busy ? "…" : "Save"}</TextButton>
      {templates.map((t) => (
        <span
          key={t.name}
          className="group flex items-center gap-1 rounded border border-term-border bg-term-bg px-1.5 py-0.5"
        >
          <button
            type="button"
            onClick={() => setMembers(t.members)}
            title={`Load "${t.name}" (${t.members.length} members)`}
            className="focus-ring hover:text-term-accent"
          >
            {t.name}
            <span className="ml-1 text-term-muted">{t.members.length}</span>
          </button>
          <IconButton
            label={`delete template ${t.name}`}
            danger
            className="opacity-40 group-hover:opacity-100"
            onClick={() => api.deleteCommitteeTemplate(t.name).then(reload)}
          >
            ✕
          </IconButton>
        </span>
      ))}
    </div>
  );
}

const STATUS_DOT: Record<AgentStatus, string> = {
  idle: "bg-term-muted/50",
  active: "bg-term-accent animate-pulse",
  done: "bg-term-up",
};

/** One committee member as a uniform, draggable card. Click to open the zoom editor; drag to
 * reorder (the last position is the Chair). Same size for every agent. */
function AgentCard({
  member,
  index,
  isChair,
  status,
  output,
  editMode,
  onOpen,
  onRemove,
  onDragStart,
  onDropOn,
}: {
  member: CommitteeMember;
  index: number;
  isChair: boolean;
  status: AgentStatus;
  output: string;
  editMode: boolean;
  onOpen: () => void;
  onRemove: () => void;
  onDragStart: () => void;
  onDropOn: () => void;
}) {
  const showEmoji = useSettings((s) => s.showEmoji);
  const [dragOver, setDragOver] = useState(false);
  return (
    <div
      draggable
      onDragStart={(e) => {
        onDragStart();
        e.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        onDropOn();
      }}
      onClick={onOpen}
      title="Click to edit · drag to reorder"
      className={cx(
        "flex h-36 cursor-pointer flex-col rounded border bg-term-bg p-2 transition-colors hover:border-term-accent",
        isChair ? "border-term-accent/60" : "border-term-border",
        status === "active" && "ring-1 ring-term-accent/60",
        dragOver && "border-term-accent ring-1 ring-term-accent",
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cx(
            "grid h-7 w-7 shrink-0 place-items-center rounded-full bg-term-sunken",
            showEmoji ? "text-base" : "text-[10px] font-semibold uppercase text-term-muted",
          )}
        >
          {showEmoji ? avatarFor(member.role) : initialsFor(member.role)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-semibold">{member.role}</div>
          <div className="text-[9px] uppercase tracking-wider text-term-muted">
            {isChair ? "Chair" : `Analyst #${index + 1}`}
          </div>
        </div>
        <span className={cx("h-2 w-2 shrink-0 rounded-full", STATUS_DOT[status])} title={status} />
        {editMode && (
          <IconButton
            label="remove agent"
            danger
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
          >
            ✕
          </IconButton>
        )}
      </div>
      <div className="mt-1.5 min-h-0 flex-1 overflow-hidden text-xs leading-relaxed text-term-text">
        {output ? (
          <p className="line-clamp-5 whitespace-pre-wrap">{output}</p>
        ) : (
          <span className="text-term-muted">
            {status === "active" ? "deliberating…" : member.goal || "Click to edit this agent."}
          </span>
        )}
      </div>
    </div>
  );
}

const STROKE: Record<AgentStatus, string> = {
  idle: "rgb(var(--term-muted))",
  active: "rgb(var(--term-accent))",
  done: "rgb(var(--term-up))",
};

/** Editable relationship CANVAS: drag agents to reposition them, drag the ◇ handle from one agent
 * onto another to connect them (the source's take then feeds the target), and click a link to
 * remove it. Connections drive how the crew deliberates. Nodes/edges light up live during a convene;
 * click an agent (without dragging) to read its take below. */
function CommitteeFlow({
  members,
  layout,
  edges: edgesProp,
  onLayout,
  onEdges,
  statusOf,
  selectedRole,
  onOpenAgent,
  verdict,
  hasKnowledge,
}: {
  members: CommitteeMember[];
  layout?: Record<string, Pt>;
  edges?: Edge[];
  onLayout: (l: Record<string, Pt>) => void;
  onEdges: (e: Edge[]) => void;
  statusOf: (i: number, role: string) => AgentStatus;
  selectedRole: string | null;
  onOpenAgent: (role: string) => void;
  verdict: CommitteeVerdict | null;
  hasKnowledge: (role: string) => boolean;
}) {
  const showEmoji = useSettings((s) => s.showEmoji);
  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ mode: "move" | "link"; role: string; ox: number; oy: number; moved: boolean } | null>(null);

  const roles = members.map((m) => m.role);
  const initPos = () => {
    const d = defaultLayout(members); // fractions
    for (const r of roles) if (layout?.[r]) d[r] = asFraction(layout[r]);
    return d;
  };
  const [pos, setPos] = useState<Record<string, Pt>>(initPos); // fractions (0..1)
  const [edges, setEdges] = useState<Edge[]>(
    () => (edgesProp ?? defaultEdges(members)).filter((e) => roles.includes(e.from) && roles.includes(e.to)),
  );
  const [linkTo, setLinkTo] = useState<Pt | null>(null);
  const [dims, setDims] = useState({ w: 320, h: 250 });

  // The canvas fills its container; measure it so nodes use the WHOLE area (no fixed boundary).
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setDims({ w: el.clientWidth || 320, h: el.clientHeight || 250 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (members.length < 1) return null;
  const vw = Math.max(dims.w, 1);
  const vh = Math.max(dims.h, 1);
  const chair = members[members.length - 1];
  const statusByRole: Record<string, AgentStatus> = {};
  members.forEach((m, i) => (statusByRole[m.role] = statusOf(i, m.role)));

  const recColor = (rec: string) => {
    const r = (rec || "").toLowerCase();
    if (r.includes("buy") || r.includes("accumulate")) return "rgb(var(--term-up))";
    if (r.includes("sell") || r.includes("reduce")) return "rgb(var(--term-down))";
    return "rgb(var(--term-accent))";
  };
  const trunc = (s: string) => (s.length > 16 ? s.slice(0, 15) + "…" : s);
  // Stored fractions → pixel coords for the current canvas size.
  const at = (role: string): Pt => {
    const f = pos[role] ?? { x: 0.5, y: 0.5 };
    return { x: f.x * vw, y: f.y * vh };
  };

  const toSvg = (e: { clientX: number; clientY: number }): Pt => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const m = svg.getScreenCTM();
    if (!m) return { x: 0, y: 0 };
    const p = svg.createSVGPoint();
    p.x = e.clientX;
    p.y = e.clientY;
    const r = p.matrixTransform(m.inverse());
    return { x: r.x, y: r.y };
  };
  // Clamp a PIXEL position so the node (incl. its label) stays fully on the canvas.
  const clampPx = (p: Pt): Pt => ({
    x: Math.max(NR, Math.min(vw - NR, p.x)),
    y: Math.max(NR + 4, Math.min(vh - NR - 16, p.y)),
  });
  const nodeAt = (p: Pt): string | null => {
    for (const r of roles) {
      const n = at(r);
      if ((n.x - p.x) ** 2 + (n.y - p.y) ** 2 <= (NR + 4) ** 2) return r;
    }
    return null;
  };

  const startMove = (role: string, e: React.PointerEvent) => {
    e.stopPropagation();
    const p = toSvg(e);
    const n = at(role);
    drag.current = { mode: "move", role, ox: p.x - n.x, oy: p.y - n.y, moved: false };
    try {
      svgRef.current?.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic / non-primary pointers can't be captured — dragging still works over the svg */
    }
  };
  const startLink = (role: string, e: React.PointerEvent) => {
    e.stopPropagation();
    drag.current = { mode: "link", role, ox: 0, oy: 0, moved: true };
    setLinkTo(at(role));
    try {
      svgRef.current?.setPointerCapture(e.pointerId);
    } catch {
      /* see startMove */
    }
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const p = toSvg(e);
    if (d.mode === "move") {
      d.moved = d.moved || Math.abs(p.x - (at(d.role).x + d.ox)) > 3 || Math.abs(p.y - (at(d.role).y + d.oy)) > 3;
      const px = clampPx({ x: p.x - d.ox, y: p.y - d.oy });
      setPos((cur) => ({ ...cur, [d.role]: { x: px.x / vw, y: px.y / vh } })); // store as fraction
    } else {
      setLinkTo(p);
    }
  };
  const endDrag = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.mode === "move") {
      if (!d.moved) onOpenAgent(d.role);
      else setPos((cur) => (onLayout(cur), cur));
    } else {
      const target = nodeAt(toSvg(e));
      setLinkTo(null);
      if (target && target !== d.role && !edges.some((x) => x.from === d.role && x.to === target)) {
        const next = [...edges, { from: d.role, to: target }];
        setEdges(next);
        onEdges(next);
      }
    }
  };
  const removeEdge = (i: number) => {
    const next = edges.filter((_, idx) => idx !== i);
    setEdges(next);
    onEdges(next);
  };
  const reset = () => {
    const l = defaultLayout(members);
    const ed = defaultEdges(members);
    setPos(l);
    setEdges(ed);
    onLayout(l);
    onEdges(ed);
  };

  return (
    <div className="flex h-full min-h-[280px] flex-1 flex-col">
      <div className="mb-1 flex items-center gap-2 text-[10px] text-term-muted">
        <span>Click an agent to edit · drag to move · drag ◇ to connect · click a link to remove</span>
        <button
          type="button"
          onClick={reset}
          className="focus-ring ml-auto rounded border border-term-border px-1.5 py-0.5 uppercase tracking-wide hover:text-term-text"
        >
          Reset
        </button>
      </div>
      <div
        ref={wrapRef}
        className="relative min-h-0 flex-1 rounded border border-term-border bg-term-sunken/30"
      >
      <svg
        ref={svgRef}
        viewBox={`0 0 ${vw} ${vh}`}
        className="absolute inset-0 h-full w-full touch-none select-none"
        role="img"
        aria-label="committee relationship canvas"
        onPointerMove={onMove}
        onPointerUp={endDrag}
      >
        <defs>
          <marker id="cmte-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" style={{ fill: "rgb(var(--term-muted))" }} />
          </marker>
        </defs>

        {/* edges */}
        {edges.map((ed, i) => {
          const a = at(ed.from);
          const b = at(ed.to);
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const len = Math.hypot(dx, dy) || 1;
          const ux = dx / len;
          const uy = dy / len;
          const x1 = a.x + ux * NR;
          const y1 = a.y + uy * NR;
          const x2 = b.x - ux * (NR + 4);
          const y2 = b.y - uy * (NR + 4);
          const st = statusByRole[ed.from];
          return (
            <g key={`${ed.from}->${ed.to}`} className="cursor-pointer" onClick={() => removeEdge(i)}>
              <line x1={x1} y1={y1} x2={x2} y2={y2} style={{ stroke: "transparent", strokeWidth: 10 }}>
                <title>{`${ed.from} → ${ed.to} (click to remove)`}</title>
              </line>
              <line
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                markerEnd="url(#cmte-arrow)"
                className={st === "active" ? "animate-pulse" : ""}
                style={{ stroke: st === "done" ? "rgb(var(--term-accent))" : "rgb(var(--term-muted))", strokeWidth: 1.5, opacity: st === "idle" ? 0.45 : 0.95 }}
              />
            </g>
          );
        })}

        {/* link-in-progress line */}
        {drag.current?.mode === "link" && linkTo && (
          <line
            x1={at(drag.current.role).x}
            y1={at(drag.current.role).y}
            x2={linkTo.x}
            y2={linkTo.y}
            style={{ stroke: "rgb(var(--term-accent))", strokeWidth: 1.5, strokeDasharray: "3 3" }}
          />
        )}

        {/* nodes */}
        {members.map((m) => {
          const { x, y } = at(m.role);
          const st = statusByRole[m.role];
          const knows = hasKnowledge(m.role);
          const isChair = m.role === chair.role;
          return (
            <g key={m.role}>
              {selectedRole === m.role && (
                <circle cx={x} cy={y} r={NR + 4} fill="none" style={{ stroke: "rgb(var(--term-accent))", strokeWidth: 1 }} />
              )}
              <circle
                cx={x}
                cy={y}
                r={NR}
                className={cx("cursor-grab", st === "active" && "animate-pulse")}
                onPointerDown={(e) => startMove(m.role, e)}
                style={{ fill: "rgb(var(--term-sunken))", stroke: STROKE[st], strokeWidth: isChair ? 2.5 : 2 }}
              />
              <text
                x={x}
                y={y}
                textAnchor="middle"
                dominantBaseline="central"
                pointerEvents="none"
                style={{ fill: "rgb(var(--term-text))" }}
                fontSize={showEmoji ? 17 : 11}
                fontWeight={showEmoji ? 400 : 700}
              >
                {showEmoji ? avatarFor(m.role) : initialsFor(m.role)}
              </text>
              {knows && (
                <circle cx={x + NR * 0.78} cy={y - NR * 0.78} r={4.5} pointerEvents="none" style={{ fill: "rgb(var(--term-accent))" }}>
                  <title>has knowledge files</title>
                </circle>
              )}
              {/* connect handle */}
              <rect
                x={x - 4}
                y={y + NR - 4}
                width={8}
                height={8}
                rx={1.5}
                transform={`rotate(45 ${x} ${y + NR})`}
                className="cursor-crosshair"
                onPointerDown={(e) => startLink(m.role, e)}
                style={{ fill: "rgb(var(--term-bg))", stroke: "rgb(var(--term-accent))", strokeWidth: 1.5 }}
              >
                <title>drag to connect this agent to another</title>
              </rect>
              <text x={x} y={y + NR + 15} textAnchor="middle" pointerEvents="none" style={{ fill: "rgb(var(--term-muted))" }} fontSize={9}>
                {trunc(m.role)}
              </text>
              {isChair && verdict && (
                <text x={x} y={y + NR + 27} textAnchor="middle" pointerEvents="none" fontSize={11} fontWeight={700} style={{ fill: recColor(verdict.recommendation) }}>
                  {verdict.recommendation.toUpperCase()}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      </div>
    </div>
  );
}

/** Zoomed agent editor — a focused overlay (opened by clicking an agent in the canvas) to edit that
 * one agent's prompt (role/goal/backstory/focus) and its own knowledge base, and read its take. */
function AgentZoom({
  member,
  index,
  isChair,
  status,
  output,
  committeeName,
  supported,
  onKnowledgeChanged,
  onChange,
  onClose,
}: {
  member: CommitteeMember;
  index: number;
  isChair: boolean;
  status: AgentStatus;
  output: string;
  committeeName: string;
  supported: string[];
  onKnowledgeChanged: () => void;
  onChange: (p: Partial<CommitteeMember>) => void;
  onClose: () => void;
}) {
  const showEmoji = useSettings((s) => s.showEmoji);
  return (
    <div
      className="absolute inset-0 z-30 flex items-center justify-center bg-term-bg/70 p-3 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="max-h-full w-full max-w-md overflow-auto rounded-lg border border-term-accent/60 bg-term-elev p-3 shadow-elev-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-2 flex items-center gap-2">
          <span
            className={cx(
              "grid h-10 w-10 shrink-0 place-items-center rounded-full bg-term-sunken",
              showEmoji ? "text-2xl" : "text-sm font-semibold uppercase text-term-muted",
            )}
          >
            {showEmoji ? avatarFor(member.role) : initialsFor(member.role)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-wider text-term-muted">
              {isChair ? "Chair · synthesizer" : `Analyst #${index + 1}`}
            </div>
            <input
              value={member.role}
              onChange={(e) => onChange({ role: e.target.value })}
              className="focus-ring w-full rounded border border-term-border bg-term-sunken px-2 py-1 text-sm font-semibold"
              placeholder="role"
            />
          </div>
          <span className={cx("h-2.5 w-2.5 shrink-0 rounded-full", STATUS_DOT[status])} title={status} />
          <IconButton label="close" onClick={onClose}>
            ✕
          </IconButton>
        </div>

        <div className="space-y-2">
          <label className="block">
            <span className="text-[10px] uppercase tracking-wider text-term-muted">Goal</span>
            <input
              value={member.goal}
              onChange={(e) => onChange({ goal: e.target.value })}
              placeholder="what this agent argues for"
              className="focus-ring mt-0.5 w-full rounded border border-term-border bg-term-sunken px-2 py-1 text-xs"
            />
          </label>
          <label className="block">
            <span className="text-[10px] uppercase tracking-wider text-term-muted">Backstory / persona</span>
            <textarea
              value={member.backstory}
              onChange={(e) => onChange({ backstory: e.target.value })}
              rows={3}
              className="focus-ring mt-0.5 w-full resize-none rounded border border-term-border bg-term-sunken px-2 py-1 text-xs"
            />
          </label>
          <label className="block">
            <span className="text-[10px] uppercase tracking-wider text-term-muted">Focus (optional)</span>
            <textarea
              value={member.instructions ?? ""}
              onChange={(e) => onChange({ instructions: e.target.value })}
              rows={2}
              placeholder="specific focus for this deliberation"
              className="focus-ring mt-0.5 w-full resize-none rounded border border-term-border bg-term-sunken px-2 py-1 text-xs"
            />
          </label>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-term-muted">
              Knowledge base · {slug(committeeName)}/{slug(member.role)}/
            </span>
            <div className="mt-0.5">
              <AgentFiles
                committee={committeeName}
                role={member.role}
                supported={supported}
                onChanged={onKnowledgeChanged}
              />
            </div>
          </div>
          {output && (
            <div>
              <span className="text-[10px] uppercase tracking-wider text-term-muted">Latest take</span>
              <div className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-term-border bg-term-bg p-2 text-xs leading-relaxed">
                {output}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** An agent's own knowledge files, stored on disk at <dir>/<committee>/<agent>/. Upload, list, and
 * delete files scoped to this (committee, agent) — no shared pool, no naming. */
function AgentFiles({
  committee,
  role,
  supported,
  onChanged,
}: {
  committee: string;
  role: string;
  supported: string[];
  onChanged?: () => void;
}) {
  const [files, setFiles] = useState<KnowledgeFile[]>([]);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const accept = (supported.length ? supported : [".txt", ".md", ".markdown", ".pdf", ".csv"]).join(",");

  const load = () =>
    api.agentFiles(committee, role).then((r) => setFiles(r.files)).catch(() => setFiles([]));
  useEffect(() => {
    load();
    const onFocus = () => load(); // pick up files added/removed directly in the directory
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [committee, role]);

  const upload = async (fl: FileList | null) => {
    if (!fl) return;
    setBusy(true);
    try {
      for (const f of Array.from(fl)) await api.uploadAgentFile(committee, role, f);
      await load();
      onChanged?.();
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };
  const del = async (name: string) => {
    await api.deleteAgentFile(committee, role, name);
    await load();
    onChanged?.();
  };

  return (
    <div className="rounded border border-term-border bg-term-sunken/50 p-1.5">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[11px] text-term-muted">
          {files.length ? `${files.length} file(s)` : "no files yet (.txt/.md/.pdf/.csv)"}
        </span>
        <label className="cursor-pointer text-[11px] text-term-accent hover:underline">
          {busy ? "…" : "＋ files"}
          <input
            ref={fileRef}
            type="file"
            multiple
            accept={accept}
            className="hidden"
            onChange={(e) => upload(e.target.files)}
          />
        </label>
      </div>
      {files.length > 0 && (
        <ul className="space-y-0.5">
          {files.map((f) => (
            <li key={f.name} className="flex items-center justify-between text-[11px]">
              <span className="truncate font-mono">{f.name}</span>
              <IconButton label={`remove ${f.name}`} danger onClick={() => del(f.name)}>
                ✕
              </IconButton>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
