/** "Send to…" toolbar control for cross-module hand-offs. Given a payload kind + a builder, it lists
 * every widget that declares it accepts that kind (registry-derived via sendTargets — no hardcoded
 * menus) and dispatches a `send` intent on click. The target receives the payload as panel params.
 *
 * Renders nothing if no widget accepts the kind, so a source can mount it unconditionally.
 */

import { useEffect, useRef, useState } from "react";
import { dispatchIntent, sendTargets, type SendPayload, type SendPayloadKind } from "../state/intents";
import { WIDGETS } from "../workspace/widgetRegistry";
import { cx } from "../lib/format";

export default function SendToMenu({
  kind,
  build,
  disabled,
  label = "Send to",
}: {
  kind: SendPayloadKind;
  /** Build the payload at click time (so it reflects the latest widget state). Return null to abort. */
  build: () => SendPayload | null;
  disabled?: boolean;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const targets = sendTargets(kind);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (targets.length === 0) return null;

  const choose = (target: (typeof targets)[number]) => {
    const payload = build();
    setOpen(false);
    if (payload) void dispatchIntent({ kind: "send", target, payload, open: true });
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        title={disabled ? "Run first, then send the result" : `${label}…`}
        className={cx(
          "focus-ring rounded border px-2 py-0.5 text-[10px] uppercase tracking-wide transition-colors",
          disabled
            ? "cursor-not-allowed border-term-border/50 text-term-muted/50"
            : open
              ? "border-term-accent text-term-accent"
              : "border-term-border text-term-muted hover:text-term-text",
        )}
      >
        {label} ▾
      </button>
      {open && (
        <div className="absolute right-0 top-7 z-50 min-w-[160px] rounded border border-term-border bg-term-elev py-1 shadow-elev-2">
          {targets.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => choose(t)}
              className="block w-full truncate px-3 py-1.5 text-left text-xs text-term-text hover:bg-term-border/50"
            >
              {WIDGETS[t].title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
