import { defineConfig } from "@playwright/test";

/**
 * Screenshot reviews: every screen in light and dark at a 1440x900 laptop
 * size, written to ../docs/screens/, plus behavior tests. Run `make screens` or `make e2e` while
 * `make dev` is up.
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  workers: 2,
  reporter: [["list"]],
  use: {
    baseURL: process.env.LANE_WEB_URL ?? "http://localhost:3000",
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  },
  projects: [
    // Screenshot reviews (make screens)
    { name: "light", testMatch: "screens.spec.ts", use: { colorScheme: "light" } },
    { name: "dark", testMatch: "screens.spec.ts", use: { colorScheme: "dark" } },
    // Behavior tests (make e2e)
    { name: "behavior", testMatch: "behavior.spec.ts", use: { colorScheme: "light" } },
  ],
});
