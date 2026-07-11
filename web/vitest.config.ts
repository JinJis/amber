import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import path from "path";

// IMP-8 / UX-4: the web unit-test runner. Component tests run in jsdom; run via
// `npm test` (docker: see scripts/test_all.sh web step).
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname) } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.ts", "**/*.test.tsx"],
  },
});
