import { defineConfig } from "vitest/config";

// happy-dom gives the store tests a DOM-like environment (localStorage for zustand `persist`,
// plus crypto/performance for id generation). No React rendering is needed.
export default defineConfig({
  test: {
    environment: "happy-dom",
  },
});
