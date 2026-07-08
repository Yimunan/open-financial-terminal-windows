/** Adapter from the Assistant control loop's legacy action frames onto the typed `dispatchIntent`
 * vocabulary (`state/intents.ts`). The agent emits `action` frames (open_widget, set_symbol,
 * configure_widget, switch_workspace, apply_template, read_workspace); this maps each onto an Intent
 * and returns the same `{ok, result}` observation the widget sends back over the socket.
 *
 * ALLOWLISTED to navigate/configure only — no order placement, no deletes, no model/watchlist
 * writes (mirrors backend assistant_agent.CLIENT_ACTIONS). The verbs here are exactly the subset of
 * the intent vocabulary the LLM may drive; widget-to-widget `send`/`notify` intents are not exposed
 * to the agent.
 */

import { dispatchIntent, inferAsset, type Intent } from "./intents";
import { type Channel } from "./linking";
import { WIDGETS, type WidgetType } from "../workspace/widgetRegistry";

export interface ActionResult {
  ok: boolean;
  result: string | Record<string, unknown>;
}

const LINK_CHANNELS = ["red", "blue", "green"] as const;

// Re-export so existing importers (and the param-hygiene contract) keep one source of truth.
export { cleanParams } from "./intents";

/** Map one legacy Assistant action onto an Intent. Returns null for an unknown/disallowed verb. */
function toIntent(name: string, args: Record<string, unknown>): Intent | null {
  switch (name) {
    case "open_widget":
      return { kind: "open", widget: args.type as WidgetType, params: (args.params ?? args) as never };
    case "set_symbol": {
      const symbol = String(args.symbol ?? "").trim();
      if (!symbol) return null;
      const channel = (LINK_CHANNELS.includes(args.channel as never) ? args.channel : "red") as Exclude<
        Channel,
        "none"
      >;
      const asset = inferAsset(symbol, args.asset) ?? "equity";
      return { kind: "set_context", channel, context: { symbol, asset } };
    }
    case "configure_widget":
      return { kind: "configure", panelId: String(args.id ?? ""), params: (args.params ?? {}) as never };
    case "switch_workspace":
      return { kind: "switch_workspace", name: String(args.name ?? "") };
    case "apply_template":
      return { kind: "apply_template", name: String(args.name ?? "") };
    case "read_workspace":
      return { kind: "read_workspace" };
    default:
      return null;
  }
}

/** Execute one Assistant action. Never throws — failures come back as {ok:false, result}. */
export async function executeAction(
  name: string,
  args: Record<string, unknown> = {},
): Promise<ActionResult> {
  // Preserve the legacy guard: set_symbol with no symbol reports the same message as before.
  if (name === "set_symbol" && !String(args.symbol ?? "").trim()) {
    return { ok: false, result: "no symbol given" };
  }
  // The Assistant may only open widgets that opt in (declare an `assistant` field). Widget-initiated
  // opens (via dispatchIntent directly) are unrestricted; this gate is specific to the agent path.
  if (name === "open_widget") {
    const type = args.type as WidgetType;
    if (!(type in WIDGETS) || !WIDGETS[type].assistant) {
      return { ok: false, result: `cannot open '${String(args.type)}'` };
    }
  }
  const intent = toIntent(name, args);
  if (!intent) return { ok: false, result: `unknown action '${name}'` };
  return dispatchIntent(intent);
}
