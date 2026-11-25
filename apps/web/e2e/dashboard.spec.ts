import { expect, test, type Page } from "@playwright/test";

const API_BASE = process.env.PLAYWRIGHT_API_URL || "http://127.0.0.1:8000";

function attachNetworkDiagnostics(page: Page) {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedRequests: string[] = [];
  const badApiResponses: string[] = [];
  const other404s: string[] = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push(msg.text());
    }
  });
  page.on("pageerror", (err) => {
    pageErrors.push(err.message);
  });
  page.on("requestfailed", (req) => {
    failedRequests.push(`${req.method()} ${req.url()} :: ${req.failure()?.errorText}`);
  });
  page.on("response", (res) => {
    if (res.status() >= 400) {
      const url = res.url();
      if (url.startsWith(API_BASE)) {
        badApiResponses.push(`${res.status()} ${url}`);
      } else {
        other404s.push(`${res.status()} ${url}`);
      }
    }
  });

  return { consoleErrors, pageErrors, failedRequests, badApiResponses, other404s };
}

test.describe("SensorGuard AI Dashboard — Live FastAPI + PostgreSQL E2E", () => {
  test("loads live station catalog, switches to Manchester Piccadilly, inspects intervals and slices", async ({
    page,
  }) => {
    const diag = attachNetworkDiagnostics(page);

    await page.goto("/", { waitUntil: "networkidle" });

    // 1. Live API connectedness (must NOT show demo banner on initial load)
    await expect(page.getByText("FastAPI Connected")).toBeVisible();
    await expect(page.getByText("STRICT PILOT MODE: ARCHIVED Q1 2024 UK AURN NETWORK")).toBeVisible();
    await expect(page.getByText(/DEMO MODE ACTIVE/i)).toHaveCount(0);

    // 2. Verify all 8 UK AURN station buttons appear in the station selector
    await expect(page.getByText("UK AURN 8-Station Network (Q1 2024)")).toBeVisible();
    await expect(page.getByText("8 Stations")).toBeVisible();
    for (const stName of [
      "London Harlington - UKA00472",
      "London Hillingdon - UKA00266",
      "Westminster - Marylebone Road",
      "Bexley - Slade Green",
      "Southampton Centre - UKA00235",
      "Wicken Fen - UKA00362",
      "St Osyth - UKA00445",
      "Manchester Piccadilly - UKA00248",
    ]) {
      await expect(page.getByRole("button", { name: new RegExp(stName) }).first()).toBeVisible();
    }

    // 3. Verify initial London Harlington forecasts table & non-gap policy indicators
    await expect(page.getByRole("heading", { name: "London Harlington - UKA00472" })).toBeVisible();
    await expect(page.getByText("Historical Reliability (Q1 2024)")).toBeVisible();
    await expect(page.getByText("Target UTC")).toBeVisible();
    await expect(page.getByText("D_sens")).toBeVisible();
    await expect(page.getByText("24 timesteps (gaps not filled)")).toBeVisible();
    await expect(page.getByText("3.32").first()).toBeVisible();

    // 4. Select Manchester Piccadilly (#2312) and confirm live API reload
    const manchesterForecastPromise = page.waitForResponse(
      (res) => res.url().includes("/api/v1/stations/") && res.url().includes("/forecasts") && res.status() === 200
    );
    await page.getByRole("button", { name: /Manchester Piccadilly - UKA00248 \(#2312\)/ }).click();
    await manchesterForecastPromise;

    await expect(page.getByRole("heading", { name: "Manchester Piccadilly - UKA00248" })).toBeVisible();
    await expect(page.getByText(/Lat:\s*53\.48/)).toBeVisible();

    // 5. Select Bexley - Slade Green (#157 HEALTHY) to verify active HEALTHY status + interval table
    const bexleyForecastPromise = page.waitForResponse(
      (res) => res.url().includes("/api/v1/stations/") && res.url().includes("/forecasts") && res.status() === 200
    );
    await page.getByRole("button", { name: /Bexley - Slade Green \(#157\)/ }).click();
    await bexleyForecastPromise;
    await expect(page.getByRole("heading", { name: "Bexley - Slade Green" })).toBeVisible();

    // 6. Inspect Sensitivity Slice dropdown across envelope -> gamma=2.0 -> gamma=0.0 -> envelope
    const sliceSelect = page.locator("select").first();
    await sliceSelect.selectOption("2");
    await expect(sliceSelect).toHaveValue("2");
    await sliceSelect.selectOption("0");
    await expect(sliceSelect).toHaveValue("0");
    await sliceSelect.selectOption("envelope");
    await expect(sliceSelect).toHaveValue("envelope");

    // 7. Assert zero API failures, zero failed requests, zero page errors
    expect(diag.pageErrors).toEqual([]);
    expect(diag.failedRequests).toEqual([]);
    expect(diag.badApiResponses).toEqual([]);
    // Ignore only browser implicit /favicon.ico 404 if static export omits favicon.ico
    const nonFavicon404 = diag.other404s.filter((item) => !item.endsWith("/favicon.ico"));
    expect(nonFavicon404).toEqual([]);
    const nonFaviconConsoleErrors = diag.consoleErrors.filter(
      (msg) => !(msg.includes("404") && diag.other404s.every((u) => u.endsWith("/favicon.ico")))
    );
    expect(nonFaviconConsoleErrors).toEqual([]);
  });

  test("navigates Simulator Lab scenarios, compares research results, and opens Methodology", async ({
    page,
  }) => {
    const diag = attachNetworkDiagnostics(page);
    await page.goto("/", { waitUntil: "networkidle" });

    // Switch to Simulator Lab & Ablations tab
    await page.getByRole("button", { name: "Simulator Lab & Ablations" }).click();
    await expect(page.getByText("Reliability Simulator Lab & Benchmark Audits")).toBeVisible();
    await expect(page.getByText("NATURAL OBSERVED")).toBeVisible();
    await expect(page.getByText("SG-BENCH-20261125-040900")).toBeVisible();
    await expect(page.getByText("SG-SAGE-ABLATION-20261124-162613")).toBeVisible();
    await expect(page.getByText("lightgbm_quantile")).toBeVisible();
    await expect(page.getByText("sage_full")).toBeVisible();
    await expect(page.getByText("full_sage")).toBeVisible();

    // Switch to mnar_gamma_1.5 scenario via live backend call
    const mnarResp = page.waitForResponse(
      (res) => res.url().includes("/api/v1/research/experiments/") && res.status() === 200
    );
    await page.getByRole("button", { name: "mnar_gamma_1.5" }).click();
    await mnarResp;
    await expect(page.getByText(/SIMULATED OUTAGE/i)).toBeVisible();

    // Switch to burst_24h scenario
    const burstResp = page.waitForResponse(
      (res) => res.url().includes("/api/v1/research/experiments/") && res.status() === 200
    );
    await page.getByRole("button", { name: "burst_24h" }).click();
    await burstResp;
    await expect(page.getByText(/SIMULATED OUTAGE/i)).toBeVisible();

    // Switch back to natural regime
    const natResp = page.waitForResponse(
      (res) => res.url().includes("/api/v1/research/experiments/") && res.status() === 200
    );
    await page.getByRole("button", { name: "natural" }).click();
    await natResp;
    await expect(page.getByText("NATURAL OBSERVED")).toBeVisible();

    // Switch to SAGE Methodology tab
    await page.getByRole("button", { name: "SAGE Methodology" }).click();
    await expect(page.getByText("SAGE Scientific Methodology")).toBeVisible();
    await expect(page.getByText(/No unconditional or distribution-free coverage guarantee/i)).toBeVisible();

    expect(diag.pageErrors).toEqual([]);
    expect(diag.failedRequests).toEqual([]);
    expect(diag.badApiResponses).toEqual([]);
  });

  test("strictly isolates manual Demo Mode toggle and unreachable-API fallback notice", async ({
    browser,
  }) => {
    // Part A: Explicit Demo Mode toggle in connected session
    const ctxLive = await browser.newContext();
    const pageLive = await ctxLive.newPage();
    await pageLive.goto("/", { waitUntil: "networkidle" });
    await expect(pageLive.getByText("FastAPI Connected")).toBeVisible();

    await pageLive.getByRole("button", { name: "Toggle Demo Mode" }).click();
    await expect(pageLive.getByText("FastAPI Connected")).toHaveCount(0);
    await expect(pageLive.getByText(/DEMO/i).first()).toBeVisible();

    // Toggle back to live API mode
    await pageLive.getByRole("button", { name: "Toggle Demo Mode" }).click();
    await expect(pageLive.getByText("FastAPI Connected")).toBeVisible();
    await ctxLive.close();

    // Part B: Unreachable backend must never claim "FastAPI Connected"
    const ctxOffline = await browser.newContext();
    const pageOffline = await ctxOffline.newPage();
    await pageOffline.route("**/api/**", (route) => route.abort("connectionrefused"));
    await pageOffline.route("**/health**", (route) => route.abort("connectionrefused"));

    await pageOffline.goto("/", { waitUntil: "domcontentloaded" });
    await expect(pageOffline.getByText("FastAPI Connected")).toHaveCount(0);
    await expect(pageOffline.getByText(/DEMO|Offline|unavailable/i).first()).toBeVisible();
    await ctxOffline.close();
  });
});
