import { useRef } from "react";

/**
 * Dependency-free Python code editor: monospace textarea with a synced line-number
 * gutter and tab-to-indent. Themed via CSS variables; horizontal scroll (no wrap).
 */
export default function CodeEditor({
  value,
  onChange,
  rows = 16,
}: {
  value: string;
  onChange: (v: string) => void;
  rows?: number;
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const gutRef = useRef<HTMLDivElement>(null);
  const lineCount = Math.max(1, value.split("\n").length);

  const syncScroll = () => {
    if (gutRef.current && taRef.current) {
      gutRef.current.scrollTop = taRef.current.scrollTop;
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const ta = e.currentTarget;
      const s = ta.selectionStart;
      const en = ta.selectionEnd;
      const next = value.slice(0, s) + "    " + value.slice(en);
      onChange(next);
      requestAnimationFrame(() => {
        ta.selectionStart = ta.selectionEnd = s + 4;
      });
    }
  };

  return (
    <div className="flex overflow-hidden rounded border border-term-border bg-term-bg font-mono text-[11px] leading-[1.5]">
      <div
        ref={gutRef}
        className="select-none overflow-hidden whitespace-pre py-1 pl-1.5 pr-1 text-right text-term-muted/50"
        style={{ minWidth: 30 }}
      >
        {Array.from({ length: lineCount }, (_, i) => `${i + 1}`).join("\n")}
      </div>
      <textarea
        ref={taRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onScroll={syncScroll}
        spellCheck={false}
        wrap="off"
        rows={rows}
        className="min-h-0 flex-1 resize-y bg-transparent py-1 pr-1.5 leading-[1.5] text-term-text outline-none"
        style={{ whiteSpace: "pre", overflowWrap: "normal", tabSize: 4 }}
      />
    </div>
  );
}
