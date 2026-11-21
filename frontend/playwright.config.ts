import { defineConfig } from "@playwright/test";

const baseURL = process.env.AGENTPROOF_E2E_URL ?? "http://127.0.0.1:3000";
const parsed = new URL(baseURL);
if (!["http:", "https:"].includes(parsed.protocol) || !["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname) || parsed.username || parsed.password) {
  throw new Error("Playwright must use a loopback dashboard URL for fixture tests.");
}

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: "list",
  use: {
    baseURL,
    browserName: "chromium",
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    },
  },
});
