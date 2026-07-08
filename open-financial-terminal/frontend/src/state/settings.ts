import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "dark" | "light";
export type Lang =
  | "en"
  | "zh"
  | "zht"
  | "ja"
  | "ko"
  | "es"
  | "de"
  | "fr"
  | "pt"
  | "ru"
  | "it"
  | "hi"
  // European LTR
  | "nl"
  | "pl"
  | "tr"
  | "uk"
  // Asian LTR
  | "id"
  | "vi"
  | "th"
  | "ms"
  // RTL
  | "ar"
  | "fa"
  | "he"
  // Nordic
  | "sv"
  | "nb"
  | "da"
  | "fi";

export const LANGUAGES: { code: Lang; label: string }[] = [
  { code: "en", label: "English" },
  { code: "zh", label: "简体中文" },
  { code: "zht", label: "繁體中文" },
  { code: "ja", label: "日本語" },
  { code: "ko", label: "한국어" },
  { code: "es", label: "Español" },
  { code: "de", label: "Deutsch" },
  { code: "fr", label: "Français" },
  { code: "pt", label: "Português" },
  { code: "ru", label: "Русский" },
  { code: "it", label: "Italiano" },
  { code: "hi", label: "हिन्दी" },
  { code: "nl", label: "Nederlands" },
  { code: "pl", label: "Polski" },
  { code: "tr", label: "Türkçe" },
  { code: "uk", label: "Українська" },
  { code: "id", label: "Bahasa Indonesia" },
  { code: "vi", label: "Tiếng Việt" },
  { code: "th", label: "ไทย" },
  { code: "ms", label: "Bahasa Melayu" },
  { code: "ar", label: "العربية" },
  { code: "fa", label: "فارسی" },
  { code: "he", label: "עברית" },
  { code: "sv", label: "Svenska" },
  { code: "nb", label: "Norsk" },
  { code: "da", label: "Dansk" },
  { code: "fi", label: "Suomi" },
];

/** Right-to-left languages — drive the document `dir` so text, inputs and fl*box flip. */
export const RTL_LANGS = new Set<Lang>(["ar", "fa", "he"]);

export function applyDir(language: Lang): void {
  const el = document.documentElement;
  el.setAttribute("dir", RTL_LANGS.has(language) ? "rtl" : "ltr");
  el.setAttribute("lang", language);
}
/** classic = green up / red down (international); cn = red up / green down (China). */
export type CandleScheme = "classic" | "cn";

export const DEFAULT_ACCENT: Record<Theme, string> = { dark: "#e3a008", light: "#b45309" };

export const ACCENT_SWATCHES = ["#e3a008", "#4f9cf9", "#a78bfa", "#34d399", "#f472b6", "#fb923c"];

/** How many user-saved custom accent swatches to keep (newest first). */
export const SAVED_ACCENT_MAX = 4;

/** Resolved up/down candle colors per theme + scheme — mirrors the --term-up/--term-down CSS
 * tokens in index.css. Used as the color-picker default when no custom override is set. */
export const DEFAULT_CANDLE: Record<Theme, Record<CandleScheme, { up: string; down: string }>> = {
  dark: {
    classic: { up: "#26a69a", down: "#ef5350" },
    cn: { up: "#ef5350", down: "#26a69a" },
  },
  light: {
    classic: { up: "#0e8a6d", down: "#d32f2f" },
    cn: { up: "#d32f2f", down: "#0e8a6d" },
  },
};

interface SettingsState {
  theme: Theme;
  language: Lang;
  scheme: CandleScheme;
  accent: string | null; // hex override, null = theme default
  savedAccents: string[]; // user-pinned custom accent swatches (lowercase hex, newest first)
  candleUp: string | null; // hex override for up candles, null = scheme/theme default
  candleDown: string | null; // hex override for down candles, null = scheme/theme default
  showEmoji: boolean; // emoji avatars/glyphs (e.g. Committee agent avatars); default off = text initials
  showIcons: boolean; // decorative icons/emoji in agent panels (empty states, result/▣/★ glyphs); default on
  lastWorkspace: string; // bento space to reopen on reload
  tabOrder: string[]; // user-dragged order of bento-space tabs; names absent here fall back to backend (alpha) order
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  setLanguage: (l: Lang) => void;
  setScheme: (s: CandleScheme) => void;
  setAccent: (hex: string | null) => void;
  saveAccent: (hex: string) => void; // pin the given hex as a reusable swatch
  removeSavedAccent: (hex: string) => void;
  setCandleUp: (hex: string | null) => void;
  setCandleDown: (hex: string | null) => void;
  setShowEmoji: (v: boolean) => void;
  setShowIcons: (v: boolean) => void;
  setLastWorkspace: (name: string) => void;
  setTabOrder: (order: string[]) => void;
}

function hexToTriplet(hex: string): string | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
}

function applyAll(s: Pick<SettingsState, "theme" | "scheme" | "accent" | "candleUp" | "candleDown">): void {
  const el = document.documentElement;
  el.setAttribute("data-theme", s.theme);
  el.setAttribute("data-scheme", s.scheme);
  // Each override sets the token as an inline style so it wins over the theme/scheme CSS rules
  // (and thus flows everywhere the token is read: candles, flash cells, order book, P&L).
  applyToken(el, "--term-accent", s.accent);
  applyToken(el, "--term-up", s.candleUp);
  applyToken(el, "--term-down", s.candleDown);
}

function applyToken(el: HTMLElement, token: string, hex: string | null): void {
  const triplet = hex && hexToTriplet(hex);
  if (triplet) el.style.setProperty(token, triplet);
  else el.style.removeProperty(token);
}

export const useSettings = create<SettingsState>()(
  persist(
    (set, get) => ({
      theme: "dark",
      language: "en",
      scheme: "classic",
      accent: null,
      savedAccents: [],
      candleUp: null,
      candleDown: null,
      showEmoji: false,
      showIcons: true,
      lastWorkspace: "default",
      tabOrder: [],
      setLastWorkspace: (lastWorkspace) => set({ lastWorkspace }),
      setTabOrder: (tabOrder) => set({ tabOrder }),
      setShowEmoji: (showEmoji) => set({ showEmoji }),
      setShowIcons: (showIcons) => set({ showIcons }),
      setTheme: (theme) => {
        set({ theme });
        applyAll(get());
      },
      toggleTheme: () => get().setTheme(get().theme === "dark" ? "light" : "dark"),
      setLanguage: (language) => {
        set({ language });
        applyDir(language);
      },
      setScheme: (scheme) => {
        set({ scheme });
        applyAll(get());
      },
      setAccent: (accent) => {
        set({ accent });
        applyAll(get());
      },
      // Pin a hex as a reusable swatch: skip values already offered as presets, dedupe, keep
      // newest first, and cap the list so it never overruns the row.
      saveAccent: (hex) => {
        const norm = hex.trim().toLowerCase();
        if (!hexToTriplet(norm) || ACCENT_SWATCHES.some((h) => h.toLowerCase() === norm)) return;
        set((s) => ({
          savedAccents: [norm, ...s.savedAccents.filter((h) => h !== norm)].slice(0, SAVED_ACCENT_MAX),
        }));
      },
      removeSavedAccent: (hex) => {
        const norm = hex.trim().toLowerCase();
        set((s) => ({ savedAccents: s.savedAccents.filter((h) => h !== norm) }));
      },
      setCandleUp: (candleUp) => {
        set({ candleUp });
        applyAll(get());
      },
      setCandleDown: (candleDown) => {
        set({ candleDown });
        applyAll(get());
      },
    }),
    {
      name: "oft-settings",
      version: 1,
      // v1: emoji avatars now default OFF. Reset the flag once for state persisted under the old
      // (true) default so the new default takes effect; future explicit toggles still persist.
      migrate: (persisted, version) => {
        const s = (persisted ?? {}) as Partial<SettingsState>;
        if (version < 1) s.showEmoji = false;
        return s as SettingsState;
      },
      onRehydrateStorage: () => (state) => {
        if (state) {
          applyAll(state);
          applyDir(state.language);
        }
      },
    },
  ),
);

/** Webfonts load async; a canvas drawn before they're ready bakes in the fallback font.
 * This (non-persisted) flag flips once `document.fonts.ready` resolves so charts redraw
 * with the real Inter/JetBrains Mono. Call `markFontsReady()` from the app entry. */
const useFontsReady = create<{ ready: boolean; mark: () => void }>((set) => ({
  ready: false,
  mark: () => set({ ready: true }),
}));

export function markFontsReady(): void {
  useFontsReady.getState().mark();
}

/** Changes whenever any chart-relevant token changes — use as an effect dep so canvas
 * charts (which can't read CSS variables live) are rebuilt with fresh colors. Also flips
 * when webfonts finish loading so chart text re-renders in the real font, not the fallback.
 */
export function usePalette(): string {
  const tokens = useSettings(
    (s) => `${s.theme}|${s.scheme}|${s.accent ?? "default"}|${s.candleUp ?? "u"}|${s.candleDown ?? "d"}`,
  );
  const fontsReady = useFontsReady((s) => s.ready);
  return `${tokens}|${fontsReady ? "f1" : "f0"}`;
}

/** Read a theme token as a CSS color string (charts can't use CSS variables).
 * Emits legacy comma syntax — lightweight-charts' parser rejects the modern
 * space-separated `rgb(r g b)` form.
 */
export function themeColor(token: string, alpha = 1): string {
  const parts = getComputedStyle(document.documentElement)
    .getPropertyValue(token)
    .trim()
    .split(/\s+/)
    .join(", ");
  return alpha === 1 ? `rgb(${parts})` : `rgba(${parts}, ${alpha})`;
}
