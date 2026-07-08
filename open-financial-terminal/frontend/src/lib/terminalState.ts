/** Perception for the Assistant control loop: a compact, serializable snapshot of what's on
 * screen — open panels (id · type · key params), the linked channel symbols, and the available
 * workspaces/templates. Sent with each control request so the agent knows the current state and
 * can target a specific panel by id. */

import { useLinking } from "../state/linking";
import { useWorkspace } from "../state/workspace";
import { WIDGETS } from "../workspace/widgetRegistry";

/** Params worth showing the agent — enough to know a panel's state without dumping everything. */
const PARAM_KEYS = [
  "symbol", "asset", "timeframe", "indicators", "chartType", "channel", "category",
  "btMode", "initialQuery",
  // cross-module hand-off params (transient — present only between a `send` and the target's
  // mount-consume), so the agent can see a pending hand-off in the snapshot.
  "symbols", "incomingUniverse", "incomingSymbols", "incomingFactor", "incomingStrategy",
] as const;

/** openWidget ids are `${type}-${ts}-${seq}`; seeded/bento layouts may use the bare type. */
function panelType(id: string): string {
  const stripped = id.replace(/-\d+-\d+$/, "");
  if (stripped in WIDGETS) return stripped;
  if (id in WIDGETS) return id;
  return id.split("-")[0];
}

export interface PanelSnapshot {
  id: string;
  type: string;
  params: Record<string, unknown>;
}

export interface WorkspaceSnapshot {
  panels: PanelSnapshot[];
  channels: Record<string, { symbol: string; asset: string }>;
  current: string;
  names: string[];
  templates: string[];
}

export function snapshotWorkspace(): WorkspaceSnapshot {
  const ws = useWorkspace.getState();
  const link = useLinking.getState();
  const panels: PanelSnapshot[] = (ws.api?.panels ?? []).map((p) => {
    const raw = (p.params ?? {}) as Record<string, unknown>;
    const params: Record<string, unknown> = {};
    for (const k of PARAM_KEYS) {
      const v = raw[k];
      if (v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0)) {
        params[k] = v;
      }
    }
    return { id: p.id, type: panelType(p.id), params };
  });
  return {
    panels,
    channels: link.symbols as unknown as Record<string, { symbol: string; asset: string }>,
    current: ws.current,
    names: ws.names,
    templates: ws.templates,
  };
}
