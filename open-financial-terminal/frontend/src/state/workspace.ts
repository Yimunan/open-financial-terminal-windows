/** Workspace store: holds the Dockview API and manages multiple named "bento spaces"
 * (create / switch / rename / duplicate / delete). The autosave debounce lives here so
 * space switches can flush/cancel it cleanly. Each space is a row in the backend
 * `workspaces` table; the active one is remembered in settings so it reopens on reload.
 */

import { create } from "zustand";
import type { DockviewApi } from "dockview";
import { api as http } from "../api/client";
import { widgetTitle } from "../lib/i18n";
import { useSettings } from "./settings";
import { redockFloating, retitlePanels, seedDefaultLayout } from "../workspace/layoutUtil";
import { BUILTIN_TEMPLATES } from "../workspace/templates";
import { WIDGETS, type WidgetParams, type WidgetType } from "../workspace/widgetRegistry";

let panelSeq = 0;
let saveTimer: number | undefined;
const AUTOSAVE_MS = 1500;

interface WorkspaceState {
  api: DockviewApi | null;
  current: string;
  names: string[];
  templates: string[];
  setApi: (api: DockviewApi) => void;
  setCurrent: (name: string) => void;
  refreshList: () => Promise<void>;
  refreshTemplates: () => Promise<void>;
  scheduleSave: () => void;
  flushSave: () => Promise<void>;
  openWidget: (type: WidgetType, params?: WidgetParams) => void;
  saveCurrent: () => Promise<void>;
  load: (name: string) => Promise<boolean>;
  switchTo: (name: string) => Promise<void>;
  createSpace: (name?: string) => Promise<void>;
  renameSpace: (oldName: string, newName: string) => Promise<void>;
  duplicateSpace: (name: string) => Promise<void>;
  deleteSpace: (name: string) => Promise<void>;
  saveAsTemplate: (name: string) => Promise<void>;
  applyTemplate: (name: string) => Promise<void>;
  applyBuiltinTemplate: (id: string) => Promise<void>;
  deleteTemplate: (name: string) => Promise<void>;
}

function uniqueName(base: string, taken: string[]): string {
  let name = base;
  let i = 2;
  while (taken.includes(name)) name = `${base} ${i++}`;
  return name;
}

export const useWorkspace = create<WorkspaceState>()((set, get) => ({
  api: null,
  current: "default",
  names: [],
  templates: [],

  setApi: (api) => set({ api }),
  setCurrent: (name) => set({ current: name }),

  refreshList: async () => {
    try {
      const { workspaces } = await http.workspaces();
      set({ names: workspaces.map((w) => w.name) });
    } catch {
      /* offline — keep current list */
    }
  },

  refreshTemplates: async () => {
    try {
      const { templates } = await http.templates();
      set({ templates: templates.map((t) => t.name) });
    } catch {
      /* offline — keep current list */
    }
  },

  scheduleSave: () => {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      const { api, current } = get();
      if (api) http.saveWorkspace(current, api.toJSON()).catch(() => {});
    }, AUTOSAVE_MS);
  },

  flushSave: async () => {
    window.clearTimeout(saveTimer);
    const { api, current } = get();
    if (api) await http.saveWorkspace(current, api.toJSON()).catch(() => {});
  },

  openWidget: (type, params = {}) => {
    const { api } = get();
    if (!api) return;
    panelSeq += 1;
    const active = api.activePanel;
    api.addPanel({
      id: `${type}-${Date.now()}-${panelSeq}`,
      component: type,
      // topic widgets carry a per-topic label; everything else uses the registry title
      title: (params.label as string) ?? widgetTitle(type, useSettings.getState().language),
      params: { channel: WIDGETS[type].defaultChannel, ...params },
      ...(active ? { position: { referencePanel: active.id, direction: "right" } } : {}),
    });
  },

  saveCurrent: async () => {
    const { api, current } = get();
    if (!api) return;
    await http.saveWorkspace(current, api.toJSON());
  },

  load: async (name) => {
    const { api } = get();
    if (!api) return false;
    try {
      const ws = await http.loadWorkspace(name);
      api.fromJSON(ws.layout as never);
      redockFloating(api);
      retitlePanels(api, useSettings.getState().language);
      set({ current: name });
      useSettings.getState().setLastWorkspace(name);
      return true;
    } catch {
      return false;
    }
  },

  switchTo: async (name) => {
    const { api, current } = get();
    if (!api || name === current) return;
    await get().flushSave(); // persist the space we're leaving under its own name
    await get().load(name);
  },

  createSpace: async (name) => {
    const { api, names } = get();
    if (!api) return;
    await get().flushSave();
    const n = uniqueName(name?.trim() || "Workspace", names);
    api.clear();
    seedDefaultLayout(api);
    set({ current: n });
    useSettings.getState().setLastWorkspace(n);
    await http.saveWorkspace(n, api.toJSON());
    await get().refreshList();
  },

  renameSpace: async (oldName, newName) => {
    const { api, current, names } = get();
    const n = uniqueName(newName.trim() || oldName, names.filter((x) => x !== oldName));
    if (n === oldName) return;
    let layout: object;
    if (oldName === current && api) {
      await get().flushSave();
      layout = api.toJSON();
    } else {
      layout = (await http.loadWorkspace(oldName)).layout;
    }
    await http.saveWorkspace(n, layout);
    await http.deleteWorkspace(oldName);
    if (current === oldName) {
      set({ current: n });
      useSettings.getState().setLastWorkspace(n);
    }
    await get().refreshList();
  },

  duplicateSpace: async (name) => {
    const { api, current, names } = get();
    let layout: object;
    if (name === current && api) {
      await get().flushSave();
      layout = api.toJSON();
    } else {
      layout = (await http.loadWorkspace(name)).layout;
    }
    const n = uniqueName(`${name} copy`, names);
    await http.saveWorkspace(n, layout);
    await get().refreshList();
    await get().switchTo(n);
  },

  deleteSpace: async (name) => {
    const { current, names } = get();
    if (names.length <= 1) return; // always keep at least one bento space
    const wasCurrent = current === name;
    await http.deleteWorkspace(name);
    await get().refreshList();
    if (wasCurrent) {
      const next = get().names[0] ?? "default";
      await get().load(next);
    }
  },

  // ── templates: reusable layout snapshots, independent of live spaces ─────────
  saveAsTemplate: async (name) => {
    const { api } = get();
    if (!api || !name.trim()) return;
    await http.saveTemplate(name.trim(), api.toJSON());
    await get().refreshTemplates();
  },

  applyTemplate: async (name) => {
    const { api, names } = get();
    if (!api) return;
    const tpl = await http.loadTemplate(name);
    await get().flushSave(); // persist the space we're leaving
    const n = uniqueName(name, names); // new live space instantiated from the template
    api.fromJSON(tpl.layout as never);
    redockFloating(api);
    retitlePanels(api, useSettings.getState().language);
    set({ current: n });
    useSettings.getState().setLastWorkspace(n);
    await http.saveWorkspace(n, api.toJSON());
    await get().refreshList();
  },

  // built-in templates: code-defined starter layouts (see workspace/templates.ts). Mirrors
  // createSpace — clear the grid, run the builder, then persist+switch to a new live space.
  applyBuiltinTemplate: async (id) => {
    const { api, names } = get();
    if (!api) return;
    const tpl = BUILTIN_TEMPLATES.find((t) => t.id === id);
    if (!tpl) return;
    await get().flushSave(); // persist the space we're leaving
    const n = uniqueName(tpl.name, names);
    api.clear();
    tpl.build(api);
    redockFloating(api);
    set({ current: n });
    useSettings.getState().setLastWorkspace(n);
    await http.saveWorkspace(n, api.toJSON());
    await get().refreshList();
  },

  deleteTemplate: async (name) => {
    await http.deleteTemplate(name);
    await get().refreshTemplates();
  },
}));
