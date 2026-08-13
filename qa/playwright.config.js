// @ts-check
const { defineConfig, devices } = require("@playwright/test");

// KEN QA config. The app is auth-gated: tests log in through the real
// username/password flow. Default target is the SANDBOX (isolated copy on
// :8766, token auth off, janak/sandbox-pass) so tests never touch live data;
// override with KEN_BASE_URL to point elsewhere (e.g. the live :8756).
const BASE = process.env.KEN_BASE_URL || "http://127.0.0.1:8766";

module.exports = defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: BASE,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
