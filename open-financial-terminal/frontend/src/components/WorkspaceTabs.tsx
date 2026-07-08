import { useEffect, useMemo, useRef, useState } from "react";
import ContextMenu, { type MenuItem } from "./ContextMenu";
import { cx } from "../lib/format";
import { useWorkspace } from "../state/workspace";
import { useSettings } from "../state/settings";
import { BUILTIN_TEMPLATES } from "../workspace/templates";

/** Top-level bento-space tabs: switch, create, rename (double-click), duplicate, delete,
 * plus a Templates menu to save the current space as a reusable template and reopen it. */
export default function WorkspaceTabs() {
  const names = useWorkspace((s) => s.names);
  const current = useWorkspace((s) => s.current);
  const templates = useWorkspace((s) => s.templates);
  const { switchTo, createSpace, renameSpace, duplicateSpace, deleteSpace, saveAsTemplate, applyTemplate, applyBuiltinTemplate, deleteTemplate } =
    useWorkspace.getState();

  const tabOrder = useSettings((s) => s.tabOrder);
  const setTabOrder = useSettings((s) => s.setTabOrder);

  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [menu, setMenu] = useState<{ x: number; y: number; name: string } | null>(null);
  const [tplOpen, setTplOpen] = useState(false);
  const [savingTpl, setSavingTpl] = useState(false);
  const [tplName, setTplName] = useState("");
  const [dragName, setDragName] = useState<string | null>(null);
  const [overName, setOverName] = useState<string | null>(null);
  const tplRef = useRef<HTMLDivElement>(null);

  // Tabs render in the user's saved drag order; any space not in that list (new, or
  // created on another device) trails behind in the backend's alphabetical order.
  const ordered = useMemo(() => {
    const known = tabOrder.filter((n) => names.includes(n));
    return [...known, ...names.filter((n) => !known.includes(n))];
  }, [names, tabOrder]);

  // Drop `dragName` into `target`'s slot and persist the resulting order.
  const reorder = (target: string) => {
    if (!dragName || dragName === target) return;
    const list = ordered.filter((n) => n !== dragName);
    const at = list.indexOf(target);
    list.splice(at < 0 ? list.length : at, 0, dragName);
    setTabOrder(list);
  };

  useEffect(() => {
    if (!tplOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (tplRef.current && !tplRef.current.contains(e.target as Node)) setTplOpen(false);
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [tplOpen]);

  const commitRename = () => {
    if (editing && editValue.trim() && editValue.trim() !== editing) {
      renameSpace(editing, editValue.trim());
    }
    setEditing(null);
  };

  const commitSaveTpl = () => {
    if (tplName.trim()) saveAsTemplate(tplName.trim());
    setSavingTpl(false);
    setTplName("");
  };

  const menuItems = (name: string): MenuItem[] => [
    { label: "Rename", onClick: () => { setEditing(name); setEditValue(name); } },
    { label: "Duplicate", onClick: () => duplicateSpace(name) },
    { label: "Save as template…", onClick: () => { setTplOpen(true); setSavingTpl(true); setTplName(name); } },
    { label: "Delete", danger: true, onClick: () => names.length > 1 && deleteSpace(name) },
  ];

  return (
    <div className="flex h-8 shrink-0 items-stretch gap-px border-b border-term-border bg-term-bg px-1">
      <div className="flex min-w-0 items-stretch gap-px overflow-x-auto">
        {ordered.map((name) => {
          const active = name === current;
          return (
            <div
              key={name}
              draggable={editing !== name}
              onClick={() => !editing && switchTo(name)}
              onDoubleClick={() => { setEditing(name); setEditValue(name); }}
              onContextMenu={(e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, name }); }}
              onDragStart={(e) => { setDragName(name); e.dataTransfer.effectAllowed = "move"; }}
              onDragOver={(e) => { if (dragName && dragName !== name) { e.preventDefault(); setOverName(name); } }}
              onDragLeave={() => setOverName((n) => (n === name ? null : n))}
              onDrop={(e) => { e.preventDefault(); reorder(name); setDragName(null); setOverName(null); }}
              onDragEnd={() => { setDragName(null); setOverName(null); }}
              className={cx(
                "group flex max-w-[200px] cursor-pointer items-center gap-1.5 self-center rounded px-3 py-1.5 text-xs transition-colors",
                active
                  ? "bg-term-elev text-term-text shadow-[inset_0_-2px_0_rgb(var(--term-accent))]"
                  : "text-term-muted hover:bg-term-panel/50 hover:text-term-text",
                dragName === name && "opacity-40",
                overName === name && "ring-1 ring-inset ring-term-accent",
              )}
              title={name}
            >
              {editing === name ? (
                <input
                  autoFocus
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename();
                    else if (e.key === "Escape") setEditing(null);
                  }}
                  onClick={(e) => e.stopPropagation()}
                  aria-label="Rename space"
                  className="focus-ring w-28 rounded border border-term-accent bg-term-sunken px-1 py-0 text-xs"
                />
              ) : (
                <span className="truncate">{name}</span>
              )}
              {names.length > 1 && editing !== name && (
                <button
                  onClick={(e) => { e.stopPropagation(); deleteSpace(name); }}
                  aria-label={`Delete space ${name}`}
                  className="focus-ring shrink-0 rounded text-term-muted opacity-40 transition-opacity hover:text-term-down group-hover:opacity-100"
                  title="Delete space"
                >
                  ×
                </button>
              )}
            </div>
          );
        })}
      </div>
      <button
        onClick={() => createSpace()}
        aria-label="New bento space"
        className="focus-ring flex items-center self-center rounded px-2 py-1 text-sm text-term-muted transition-colors hover:bg-term-panel hover:text-term-accent"
        title="New bento space"
      >
        +
      </button>

      {/* ── Templates ── */}
      <div ref={tplRef} className="relative ml-auto self-center">
        <button
          onClick={() => setTplOpen((v) => !v)}
          className={cx(
            "focus-ring flex items-center gap-1 rounded border px-2.5 py-1 text-[10px] uppercase tracking-wide transition-colors",
            tplOpen ? "border-term-accent text-term-accent" : "border-term-border text-term-muted hover:text-term-text",
          )}
          title="Workspace templates"
        >
          Templates ▾
        </button>
        {tplOpen && (
          <div className="absolute right-0 top-7 z-50 min-w-[200px] rounded border border-term-border bg-term-elev py-1 shadow-elev-2">
            {savingTpl ? (
              <div className="px-2 py-1">
                <input
                  autoFocus
                  value={tplName}
                  onChange={(e) => setTplName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitSaveTpl();
                    else if (e.key === "Escape") { setSavingTpl(false); setTplName(""); }
                  }}
                  placeholder="Template name…"
                  aria-label="Template name"
                  className="focus-ring w-full rounded border border-term-accent bg-term-sunken px-1.5 py-0.5 text-xs"
                />
                <div className="mt-1 flex justify-end gap-1">
                  <button onClick={() => { setSavingTpl(false); setTplName(""); }} className="px-1.5 py-0.5 text-[10px] text-term-muted hover:text-term-text">Cancel</button>
                  <button onClick={commitSaveTpl} className="rounded border border-term-accent px-1.5 py-0.5 text-[10px] text-term-accent hover:bg-term-accent/10">Save</button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => { setSavingTpl(true); setTplName(current); }}
                className="block w-full px-3 py-1.5 text-left text-xs text-term-accent hover:bg-term-border/50"
              >
                ＋ Save “{current}” as template
              </button>
            )}
            <div className="my-1 border-t border-term-border" />

            {/* ── built-in starter layouts ── */}
            <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-term-muted">Built-in</div>
            {BUILTIN_TEMPLATES.map((t) => (
              <button
                key={t.id}
                onClick={() => { applyBuiltinTemplate(t.id); setTplOpen(false); }}
                className="block w-full truncate px-3 py-1.5 text-left text-xs text-term-text hover:bg-term-border/50"
                title={t.description}
              >
                {t.name}
              </button>
            ))}
            <div className="my-1 border-t border-term-border" />

            <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-term-muted">My templates</div>
            {templates.length === 0 ? (
              <div className="px-3 py-1.5 text-[10px] text-term-muted">No templates yet.</div>
            ) : (
              templates.map((tpl) => (
                <div key={tpl} className="group flex items-center hover:bg-term-border/40">
                  <button
                    onClick={() => { applyTemplate(tpl); setTplOpen(false); }}
                    className="flex-1 truncate px-3 py-1.5 text-left text-xs text-term-text"
                    title={`Open a new space from “${tpl}”`}
                  >
                    {tpl}
                  </button>
                  <button
                    onClick={() => deleteTemplate(tpl)}
                    aria-label={`Delete template ${tpl}`}
                    className="focus-ring px-2 text-term-muted opacity-40 transition-opacity hover:text-term-down group-hover:opacity-100"
                    title="Delete template"
                  >
                    ×
                  </button>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {menu && <ContextMenu x={menu.x} y={menu.y} items={menuItems(menu.name)} onClose={() => setMenu(null)} />}
    </div>
  );
}
