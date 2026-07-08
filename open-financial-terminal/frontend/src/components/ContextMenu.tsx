import { useEffect, useRef } from "react";

export interface MenuItem {
  label: string;
  onClick: () => void;
  danger?: boolean;
}

/** Minimal right-click menu rendered at a fixed point; closes on any click/escape. */
export default function ContextMenu({
  x,
  y,
  items,
  onClose,
}: {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = () => onClose();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("click", close);
    window.addEventListener("contextmenu", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("contextmenu", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  // keep on-screen
  const style: React.CSSProperties = {
    left: Math.min(x, window.innerWidth - 180),
    top: Math.min(y, window.innerHeight - items.length * 30 - 10),
  };

  return (
    <div
      ref={ref}
      style={style}
      className="fixed z-50 min-w-[160px] rounded border border-term-border bg-term-elev py-1 shadow-elev-2"
    >
      {items.map((item) => (
        <button
          key={item.label}
          onClick={(e) => {
            e.stopPropagation();
            onClose();
            item.onClick();
          }}
          className={`focus-ring block w-full px-3 py-1.5 text-left text-xs hover:bg-term-border/50 ${
            item.danger ? "text-term-down" : "text-term-text"
          }`}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
