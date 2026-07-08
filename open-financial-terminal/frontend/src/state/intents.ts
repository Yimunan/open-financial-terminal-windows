/** The terminal's single typed interaction vocabulary.
 *
 * `dispatchIntent` is a typed DISPATCHER over the Zustand stores — deliberately NOT a pub/sub event
 * bus (the terminal stays store-driven, no subscriber registry). Both widgets and the Assistant
 * speak the same `Intent` union, so "what can drive the terminal" has one source of truth. The old
 * Assistant verb set (`terminalActions.executeAction`) is now a thin adapter onto this.
 *
 * Cross-module hand-offs (`send`) land by writing typed params into the target panel's Dockview
 * params — the target reacts through its normal `props.params` + a mount effect (the same pattern
 * ScreenerWidget already uses for `initialQuery`). Because params live in the layout, hand-offs
 * survive reloads. Nothing here subscribes.
 */

import { useLinking, type Channel, type ChannelContext } from "./linking";
import { useWorkspace } from "./workspace";
import { snapshotWorkspace } from "../lib/terminalState";
import { WIDGETS, type WidgetParams, type WidgetType } from "../workspace/widgetRegistry";
import type { Asset } from "../api/types";

const LINK_CHANNELS = ["red", "blue", "green"] as const;
type LinkChannel = (typeof LINK_CHANNELS)[number];

/** Typed payloads one module can hand to another. Discriminated on `kind`; a target widget opts in
 * to a kind via its registry `accepts` map. */
export type SendPayload =
  | {
      kind: "screen_result";
      universe: string;
      factor: string;
      symbols: string[];
      weights?: Record<string, number>;
      asset: Asset;
    }
  | {
      kind: "backtest_result";
      strategyKey?: string;
      params: Record<string, unknown>;
      universe: string;
      metrics?: Record<string, number>;
    }
  | { kind: "symbols"; symbols: string[]; asset: Asset };

export type SendPayloadKind = SendPayload["kind"];

/** A widget declares which payload kinds it accepts and how each maps to its panel params. Each
 * builder is typed to its specific payload kind (no in-builder narrowing needed). Lives in the
 * registry as `WidgetMeta.accepts`. */
export type AcceptMap = {
  [K in SendPayloadKind]?: (p: Extract<SendPayload, { kind: K }>) => WidgetParams;
};

/** The full interaction vocabulary. Keep `kind` literals in sync with `app/schemas/intents.py`
 * (the backend parity test enforces this). */
export type Intent =
  | { kind: "open"; widget: WidgetType; params?: WidgetParams }
  | { kind: "set_context"; channel: LinkChannel; context: ChannelContext }
  | { kind: "configure"; panelId: string; params: WidgetParams }
  | { kind: "send"; target: WidgetType; payload: SendPayload; open?: boolean }
  | { kind: "switch_workspace"; name: string }
  | { kind: "apply_template"; name: string }
  | { kind: "read_workspace" }
  | { kind: "notify"; level: "info" | "warn" | "error"; message: string };

export type IntentKind = Intent["kind"];

/** The kind literals, exported so the capability catalog / parity test have one source of truth. */
export const INTENT_KINDS: IntentKind[] = [
  "open",
  "set_context",
  "configure",
  "send",
  "switch_workspace",
  "apply_template",
  "read_workspace",
  "notify",
];

export interface IntentResult {
  ok: boolean;
  result: string | Record<string, unknown>;
}

// ── param hygiene (canonical home; terminalActions re-exports for back-compat) ────────────────────

export function inferAsset(symbol?: string, asset?: unknown): Asset | undefined {
  if (asset === "equity" || asset === "crypto") return asset;
  if (typeof symbol === "string" && symbol.includes("/")) return "crypto";
  return symbol ? "equity" : undefined;
}

/** Keep only params this widget type declares (+ the universal symbol/asset/channel/label), and
 * coerce indicators to an array — so a hallucinated key or a stringified list can't corrupt a
 * panel. Used for every externally-sourced params object (Assistant actions, `send` hand-offs). */
export function cleanParams(type: WidgetType, raw: unknown): WidgetParams {
  const out: WidgetParams = {};
  if (!raw || typeof raw !== "object") return out;
  const allowed = new Set([
    "symbol",
    "asset",
    "channel",
    "label",
    ...Object.keys(WIDGETS[type].assistant?.params ?? {}),
    ...(WIDGETS[type].accepts ? ACCEPT_PARAM_KEYS : []),
  ]);
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!allowed.has(k) || v == null) continue;
    if (k === "indicators") {
      out.indicators = Array.isArray(v) ? v.map(String) : String(v).split(/[,\s]+/).filter(Boolean);
    } else {
      (out as Record<string, unknown>)[k] = v;
    }
  }
  if (out.symbol && !out.asset) out.asset = inferAsset(out.symbol as string);
  return out;
}

/** Params a `send` hand-off may inject (declared by registry `accepts` builders). Allow-listed in
 * `cleanParams` for any widget that accepts hand-offs so the target receives them intact. */
const ACCEPT_PARAM_KEYS = [
  "symbols",
  "incomingUniverse",
  "incomingSymbols",
  "incomingFactor",
  "incomingWeights",
  "incomingStrategy",
  "incomingParams",
] as const;

// ── helpers ───────────────────────────────────────────────────────────────────────────────────

/** openWidget ids are `${type}-${ts}-${seq}`; seeded/bento layouts may use the bare type. */
export function panelTypeOf(id: string): string {
  const stripped = id.replace(/-\d+-\d+$/, "");
  if (stripped in WIDGETS) return stripped;
  if (id in WIDGETS) return id;
  return id.split("-")[0];
}

function ensureChart(channel: Channel) {
  const ws = useWorkspace.getState();
  const hasChart = ws.api?.panels.some((p) => p.id.startsWith("chart"));
  if (!hasChart) ws.openWidget("chart", { channel });
}

// ── the dispatcher ──────────────────────────────────────────────────────────────────────────────

/** Execute one intent against the stores. Never throws — failures come back as {ok:false}. */
export async function dispatchIntent(intent: Intent): Promise<IntentResult> {
  try {
    switch (intent.kind) {
      case "open": {
        const type = intent.widget;
        if (!(type in WIDGETS)) return { ok: false, result: `cannot open '${String(type)}'` };
        const params = cleanParams(type, intent.params ?? {});
        useWorkspace.getState().openWidget(type, params);
        return {
          ok: true,
          result: `opened ${WIDGETS[type].title}${params.symbol ? ` for ${params.symbol}` : ""}`,
        };
      }

      case "set_context": {
        const channel = (LINK_CHANNELS.includes(intent.channel) ? intent.channel : "red") as LinkChannel;
        const symbol = String(intent.context?.symbol ?? "").trim();
        if (!symbol) return { ok: false, result: "no symbol given" };
        const asset = inferAsset(symbol, intent.context.asset) ?? "equity";
        useLinking.getState().setContext(channel, { ...intent.context, symbol, asset });
        ensureChart(channel);
        return { ok: true, result: `${channel} channel set to ${symbol} (${asset})` };
      }

      case "configure": {
        const panel = useWorkspace.getState().api?.getPanel(intent.panelId);
        if (!panel) return { ok: false, result: `no open panel with id '${intent.panelId}'` };
        const type = panelTypeOf(intent.panelId);
        const patch = cleanParams((type in WIDGETS ? type : "chart") as WidgetType, intent.params ?? {});
        panel.api.updateParameters(patch);
        return { ok: true, result: `configured ${intent.panelId}: ${JSON.stringify(patch)}` };
      }

      case "send": {
        const { target, payload } = intent;
        const builder = WIDGETS[target]?.accepts?.[payload.kind] as
          | ((p: SendPayload) => WidgetParams)
          | undefined;
        if (!builder) {
          return { ok: false, result: `${target} does not accept ${payload.kind}` };
        }
        const params = cleanParams(target, builder(payload));
        const ws = useWorkspace.getState();
        const existing = intent.open
          ? undefined
          : ws.api?.panels.find((p) => panelTypeOf(p.id) === target);
        if (existing) {
          existing.api.updateParameters(params);
          existing.api.setActive();
          return { ok: true, result: `sent ${payload.kind} to open ${WIDGETS[target].title}` };
        }
        ws.openWidget(target, params);
        return { ok: true, result: `sent ${payload.kind} to a new ${WIDGETS[target].title}` };
      }

      case "switch_workspace": {
        if (!useWorkspace.getState().names.includes(intent.name)) {
          return { ok: false, result: `no workspace named '${intent.name}'` };
        }
        await useWorkspace.getState().switchTo(intent.name);
        return { ok: true, result: `switched to workspace ${intent.name}` };
      }

      case "apply_template": {
        if (!useWorkspace.getState().templates.includes(intent.name)) {
          return { ok: false, result: `no template named '${intent.name}'` };
        }
        await useWorkspace.getState().applyTemplate(intent.name);
        return { ok: true, result: `applied template ${intent.name}` };
      }

      case "read_workspace":
        return { ok: true, result: { workspace: snapshotWorkspace() } };

      case "notify": {
        const line = `[${intent.level}] ${intent.message}`;
        if (intent.level === "error") console.error(line);
        else if (intent.level === "warn") console.warn(line);
        else console.info(line);
        return { ok: true, result: intent.message };
      }

      default: {
        const _exhaustive: never = intent;
        return { ok: false, result: `unknown intent ${JSON.stringify(_exhaustive)}` };
      }
    }
  } catch (e) {
    return { ok: false, result: `error: ${(e as Error).message}` };
  }
}

/** Target widget types that accept a given `send` payload kind — drives the "Send to…" menu.
 * Pure registry derivation; adding an `accepts` entry extends the menu automatically. */
export function sendTargets(kind: SendPayloadKind): WidgetType[] {
  return (Object.keys(WIDGETS) as WidgetType[]).filter((t) => WIDGETS[t].accepts?.[kind]);
}
