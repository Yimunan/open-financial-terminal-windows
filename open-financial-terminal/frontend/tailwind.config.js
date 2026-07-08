/** @type {import('tailwindcss').Config} */
// All colors come from CSS variables set per data-theme on <html>, so light/dark is a
// one-attribute switch and charts can read the same tokens via getComputedStyle.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        term: {
          bg: "rgb(var(--term-bg) / <alpha-value>)",
          panel: "rgb(var(--term-panel) / <alpha-value>)",
          elev: "rgb(var(--term-elev) / <alpha-value>)",
          sunken: "rgb(var(--term-sunken) / <alpha-value>)",
          border: "rgb(var(--term-border) / <alpha-value>)",
          text: "rgb(var(--term-text) / <alpha-value>)",
          muted: "rgb(var(--term-muted) / <alpha-value>)",
          accent: "rgb(var(--term-accent) / <alpha-value>)",
          up: "rgb(var(--term-up) / <alpha-value>)",
          down: "rgb(var(--term-down) / <alpha-value>)",
        },
      },
      boxShadow: {
        "elev-1": "0 1px 2px rgb(var(--term-shadow) / 0.30)",
        "elev-2": "0 4px 12px rgb(var(--term-shadow) / 0.35)",
        "elev-3": "0 12px 32px rgb(var(--term-shadow) / 0.45)",
        "glow-accent": "0 0 0 1px rgb(var(--term-accent) / 0.4), 0 0 12px rgb(var(--term-accent) / 0.25)",
      },
      fontFamily: {
        // "* Variable" = the self-hosted @fontsource-variable families (loaded in main.tsx).
        // Plain "Inter"/"JetBrains Mono" stay as fallbacks if a user has them installed.
        sans: ["Inter Variable", "Inter", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono Variable", "JetBrains Mono", "Cascadia Code", "Consolas", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
