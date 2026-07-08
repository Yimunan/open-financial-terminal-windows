import { cx } from "../../lib/format";
import { useT, type Lang } from "../../lib/i18n";
import { retitlePanels } from "../../workspace/layoutUtil";
import {
  ACCENT_SWATCHES,
  DEFAULT_ACCENT,
  DEFAULT_CANDLE,
  LANGUAGES,
  SAVED_ACCENT_MAX,
  useSettings,
  type CandleScheme,
} from "../../state/settings";
import { useWorkspace } from "../../state/workspace";
import { Choice, Row } from "./common";

/** Appearance: theme / language / candle scheme + custom colors / accent / emoji / icons.
 * The only fully client-side section — everything here drives the persisted Zustand settings store. */
export default function AppearanceSection() {
  const t = useT();
  const {
    theme,
    language,
    scheme,
    accent,
    savedAccents,
    candleUp,
    candleDown,
    showEmoji,
    showIcons,
    setTheme,
    setLanguage,
    setScheme,
    setAccent,
    saveAccent,
    removeSavedAccent,
    setCandleUp,
    setCandleDown,
    setShowEmoji,
    setShowIcons,
  } = useSettings();

  // Dockview tab titles are serialized strings, not reactive — retitle live panels whenever the
  // language flips (shared with the load-time retitle that propagates module renames).
  const retitle = (lang: Lang) => {
    const dv = useWorkspace.getState().api;
    if (dv) retitlePanels(dv, lang);
  };

  // Recover the theme's base bull/bear colors from the live tokens by undoing the active scheme's
  // swap, so each candle-scheme swatch previews its own convention.
  const schemeGreen = scheme === "cn" ? "rgb(var(--term-down))" : "rgb(var(--term-up))";
  const schemeRed = scheme === "cn" ? "rgb(var(--term-up))" : "rgb(var(--term-down))";

  return (
    <div className="divide-y divide-term-border/50">
      <Row label={t("settings.theme")}>
        <Choice active={theme === "dark"} onClick={() => setTheme("dark")}>
          {t("settings.dark")}
        </Choice>
        <Choice active={theme === "light"} onClick={() => setTheme("light")}>
          {t("settings.light")}
        </Choice>
      </Row>

      <Row label={t("settings.language")}>
        <select
          value={language}
          onChange={(e) => {
            const code = e.target.value as Lang;
            setLanguage(code);
            retitle(code);
          }}
          className="focus-ring min-w-[160px] rounded border border-term-border bg-term-sunken px-2 py-1 text-xs text-term-text focus:border-term-accent"
        >
          {LANGUAGES.map(({ code, label }) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </select>
      </Row>

      <Row label={t("settings.scheme")}>
        {(
          [
            ["classic", t("settings.schemeClassic")],
            ["cn", t("settings.schemeCn")],
          ] as [CandleScheme, string][]
        ).map(([key, label]) => (
          <Choice key={key} active={scheme === key} onClick={() => setScheme(key)}>
            <span className="mr-1.5 inline-flex items-center gap-px align-middle">
              {/* Stable per-scheme preview that still tracks the theme palette: classic = green
                 up / red down, cn flips them. `green`/`red` are recovered from the live tokens
                 by undoing the active scheme's swap, so each preview shows its own convention. */}
              <span
                className="inline-block h-2.5 w-1.5 rounded-sm"
                style={{ backgroundColor: key === "cn" ? schemeRed : schemeGreen }}
              />
              <span
                className="inline-block h-2.5 w-1.5 rounded-sm"
                style={{ backgroundColor: key === "cn" ? schemeGreen : schemeRed }}
              />
            </span>
            {label}
          </Choice>
        ))}
      </Row>

      <Row label={t("settings.candleCustom")}>
        {/* Per-direction overrides. Setting either writes an inline --term-up/--term-down token
            that wins over the scheme preset; Reset clears both back to the scheme default. */}
        <label className="flex items-center gap-1.5 text-[11px] text-term-muted">
          {t("settings.candleUp")}
          <input
            type="color"
            value={candleUp ?? DEFAULT_CANDLE[theme][scheme].up}
            onChange={(e) => setCandleUp(e.target.value)}
            className="focus-ring h-5 w-7 cursor-pointer rounded border border-term-border bg-transparent p-0"
            aria-label="custom up candle color"
            title={t("settings.candleUp")}
          />
        </label>
        <label className="flex items-center gap-1.5 text-[11px] text-term-muted">
          {t("settings.candleDown")}
          <input
            type="color"
            value={candleDown ?? DEFAULT_CANDLE[theme][scheme].down}
            onChange={(e) => setCandleDown(e.target.value)}
            className="focus-ring h-5 w-7 cursor-pointer rounded border border-term-border bg-transparent p-0"
            aria-label="custom down candle color"
            title={t("settings.candleDown")}
          />
        </label>
        {(candleUp || candleDown) && (
          <button
            onClick={() => {
              setCandleUp(null);
              setCandleDown(null);
            }}
            className="focus-ring ml-1 rounded text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text"
          >
            {t("settings.reset")}
          </button>
        )}
      </Row>

      <Row label={t("settings.accent")}>
        {ACCENT_SWATCHES.map((hex) => (
          <button
            key={hex}
            onClick={() => setAccent(hex)}
            className={cx(
              "focus-ring h-5 w-5 rounded-full border-2 transition-opacity",
              (accent ?? DEFAULT_ACCENT[theme]).toLowerCase() === hex.toLowerCase()
                ? "border-term-text"
                : "border-transparent opacity-70 hover:opacity-100",
            )}
            style={{ backgroundColor: hex }}
            aria-label={`accent ${hex}`}
          />
        ))}
        {/* User-pinned swatches: click to apply, hover to reveal a × that unpins it. */}
        {savedAccents.map((hex) => (
          <span key={hex} className="group relative inline-flex">
            <button
              onClick={() => setAccent(hex)}
              className={cx(
                "focus-ring h-5 w-5 rounded-full border-2 transition-opacity",
                (accent ?? DEFAULT_ACCENT[theme]).toLowerCase() === hex
                  ? "border-term-text"
                  : "border-transparent opacity-70 hover:opacity-100",
              )}
              style={{ backgroundColor: hex }}
              aria-label={`saved accent ${hex}`}
            />
            <button
              onClick={() => removeSavedAccent(hex)}
              className="focus-ring absolute -right-1 -top-1 hidden h-3 w-3 items-center justify-center rounded-full bg-term-elev text-[8px] leading-none text-term-muted shadow-elev-1 hover:text-term-text group-hover:flex"
              aria-label={t("settings.removeSwatch")}
              title={t("settings.removeSwatch")}
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="color"
          value={accent ?? DEFAULT_ACCENT[theme]}
          onChange={(e) => setAccent(e.target.value)}
          className="focus-ring h-5 w-7 cursor-pointer rounded border border-term-border bg-transparent p-0"
          aria-label="custom accent color"
          title={t("settings.accent")}
        />
        {/* Pin the current color. Hidden for presets/already-saved values and when the
            shelf is full of a fresh set — saveAccent itself dedupes and caps at the max. */}
        {(() => {
          const cur = accent?.toLowerCase() ?? null;
          const isPreset = !!cur && ACCENT_SWATCHES.some((h) => h.toLowerCase() === cur);
          const canSave =
            !!cur && !isPreset && !savedAccents.includes(cur) && savedAccents.length < SAVED_ACCENT_MAX;
          return canSave ? (
            <button
              onClick={() => accent && saveAccent(accent)}
              className="focus-ring flex h-5 w-5 items-center justify-center rounded-full border border-dashed border-term-border text-term-muted hover:border-term-text hover:text-term-text"
              aria-label={t("settings.saveSwatch")}
              title={t("settings.saveSwatch")}
            >
              +
            </button>
          ) : null;
        })()}
        <button
          onClick={() => setAccent(null)}
          className="focus-ring ml-1 rounded text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text"
        >
          {t("settings.reset")}
        </button>
      </Row>

      <Row label={t("settings.emoji")}>
        <Choice active={showEmoji} onClick={() => setShowEmoji(true)}>
          {t("settings.emojiOn")}
        </Choice>
        <Choice active={!showEmoji} onClick={() => setShowEmoji(false)}>
          {t("settings.emojiOff")}
        </Choice>
      </Row>

      <Row label={t("settings.icons")}>
        <Choice active={showIcons} onClick={() => setShowIcons(true)}>
          {t("settings.iconsOn")}
        </Choice>
        <Choice active={!showIcons} onClick={() => setShowIcons(false)}>
          {t("settings.iconsOff")}
        </Choice>
      </Row>
    </div>
  );
}
