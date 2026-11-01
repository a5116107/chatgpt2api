import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  workers: 2,
  timeout: 45000,
  expect: { timeout: 12000 },
  outputDir: "../artifacts/frontend/test-results",
  reporter: [["list"], ["json", { outputFile: "../artifacts/frontend/test-results.json" }]],
  use: {
    baseURL: "http://127.0.0.1:3097",
    ...devices["Desktop Chrome"],
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    reducedMotion: "reduce",
  },
  webServer: {
    command:
      process.env.PLAYWRIGHT_STATIC === "1"
        ? "node tests/serve-static.mjs"
        : "npx next dev --webpack -H 127.0.0.1 -p 3097",
    url: "http://127.0.0.1:3097/login/",
    reuseExistingServer: false,
    timeout: 120000,
    env: { NEXT_PUBLIC_API_URL: "" },
  },
});
