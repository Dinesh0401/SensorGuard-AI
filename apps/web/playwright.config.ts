import fs from "node:fs";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const useLocalChrome = !process.env.CI && fs.existsSync(chromePath);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  timeout: 45_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: useLocalChrome
        ? {
            ...devices["Desktop Chrome"],
            channel: "chrome",
          }
        : {
            ...devices["Desktop Chrome"],
          },
    },
  ],
  outputDir: path.join(__dirname, "test-results"),
});
