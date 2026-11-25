# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: dashboard.spec.ts >> SensorGuard AI Dashboard — Live FastAPI + PostgreSQL E2E >> navigates Simulator Lab scenarios, compares research results, and opens Methodology
- Location: e2e\dashboard.spec.ts:114:3

# Error details

```
Test timeout of 45000ms exceeded.
```

```
Error: page.waitForResponse: Test timeout of 45000ms exceeded.
```

# Page snapshot

```yaml
- generic [ref=e1]:
  - generic [ref=e2]:
    - generic [ref=e3]:
      - generic [ref=e4]:
        - generic [ref=e7]: "Strict Pilot Mode: Archived Q1 2024 UK AURN Network"
        - generic [ref=e8]: — Zero live telemetry or medical health claims. Point-in-time historical validation only.
      - generic [ref=e9]:
        - generic [ref=e10]: is_archived_pilot=True
        - generic [ref=e11]: FastAPI Connected
        - button "Enable demonstration fallback mode" [ref=e13] [cursor=pointer]: Toggle Demo Mode
    - banner [ref=e14]:
      - generic [ref=e20]:
        - heading "SensorGuard AI Private Staging v0.5.3" [level=1] [ref=e21]:
          - text: SensorGuard AI
          - generic [ref=e22]: Private Staging v0.5.3
        - paragraph [ref=e23]: Sensitivity-Aware Guarded Estimation (SAGE) for Environmental Sensor Outage Resilience
      - navigation "Primary sections" [ref=e24]:
        - button "Station Forecasts & Map" [ref=e25] [cursor=pointer]
        - button "Simulator Lab & Ablations" [ref=e28] [cursor=pointer]
        - button "SAGE Methodology" [ref=e31] [cursor=pointer]
    - main [ref=e34]:
      - generic [ref=e35]:
        - generic [ref=e36]:
          - generic [ref=e37]:
            - generic [ref=e38]:
              - generic [ref=e39]:
                - heading "Reliability Simulator Lab & Benchmark Audits" [level=2] [ref=e40]
                - generic [ref=e41]: SIMULATED OUTAGE
              - paragraph [ref=e42]: Target-dependent nonrandom blackout parameterized by logit sensitivity γ = 1.5. Features rebuilt strictly from visible history.
            - generic [ref=e43]:
              - button "natural" [ref=e44] [cursor=pointer]
              - button "mcar_20" [ref=e45] [cursor=pointer]
              - button "mnar_gamma_1.5" [active] [ref=e46] [cursor=pointer]
              - button "burst_24h" [ref=e47] [cursor=pointer]
          - generic [ref=e48]:
            - generic [ref=e49]:
              - generic [ref=e50]: SG-BENCH-20261125-040900
              - generic [ref=e51]: "|"
              - generic [ref=e52]: "hash: 33805c42097fc86e"
            - generic [ref=e53]:
              - generic [ref=e54]: SG-SAGE-ABLATION-20261124-162613
              - generic [ref=e55]: "|"
              - generic [ref=e56]: "hash: 33805c42097fc86e"
            - generic [ref=e57]:
              - generic [ref=e58]: SG-SIM-SYNTHETIC
              - generic [ref=e59]: "|"
              - generic [ref=e60]: "hash: deterministic_simulation"
          - table [ref=e62]:
            - rowgroup [ref=e63]:
              - row [ref=e64]:
                - columnheader "Method" [ref=e65]
                - columnheader "Calibrator / Policy" [ref=e66]
                - columnheader "Overall PICP" [ref=e67]
                - columnheader "Published PICP" [ref=e68]
                - columnheader "MPIW (Width)" [ref=e69]
                - columnheader "Winkler" [ref=e70]
                - columnheader "Abstention" [ref=e71]
            - rowgroup [ref=e72]:
              - row [ref=e73]:
                - cell "persistence" [ref=e74]
                - cell "Split Conformal" [ref=e75]
                - cell "95.6%" [ref=e76]
                - cell "96.11%" [ref=e77]
                - cell "2.00 µg/m³" [ref=e78]
                - cell "4.14" [ref=e79]
                - cell "0.95%" [ref=e80]
              - row [ref=e81]:
                - cell "seasonal_naive" [ref=e82]
                - cell "Split Conformal" [ref=e83]
                - cell "99.5%" [ref=e84]
                - cell "99.67%" [ref=e85]
                - cell "21.42 µg/m³" [ref=e86]
                - cell "21.81" [ref=e87]
                - cell "0.95%" [ref=e88]
              - row [ref=e89]:
                - cell "spatial_neighbor" [ref=e90]
                - cell "Split Conformal" [ref=e91]
                - cell "96.4%" [ref=e92]
                - cell "96.56%" [ref=e93]
                - cell "10.79 µg/m³" [ref=e94]
                - cell "11.83" [ref=e95]
                - cell "0.95%" [ref=e96]
              - row [ref=e97]:
                - cell "ridge" [ref=e98]
                - cell "Split Conformal" [ref=e99]
                - cell "94.6%" [ref=e100]
                - cell "95.11%" [ref=e101]
                - cell "2.23 µg/m³" [ref=e102]
                - cell "4.19" [ref=e103]
                - cell "0.95%" [ref=e104]
              - row [ref=e105]:
                - cell "lightgbm_point" [ref=e106]
                - cell "Split Conformal" [ref=e107]
                - cell "99.8%" [ref=e108]
                - cell "99.93%" [ref=e109]
                - cell "13.22 µg/m³" [ref=e110]
                - cell "13.45" [ref=e111]
                - cell "0.95%" [ref=e112]
              - row [ref=e113]:
                - cell "lightgbm_quantile" [ref=e114]
                - cell "CQR (Romano et al. 2019)" [ref=e115]
                - cell "99.8%" [ref=e116]
                - cell "99.93%" [ref=e117]
                - cell "11.42 µg/m³" [ref=e118]
                - cell "11.70" [ref=e119]
                - cell "0.95%" [ref=e120]
              - row [ref=e121]:
                - cell "lightgbm_rolling" [ref=e122]
                - cell "Rolling Conformal (168h trailing)" [ref=e123]
                - cell "90.1%" [ref=e124]
                - cell "90.52%" [ref=e125]
                - cell "3.43 µg/m³" [ref=e126]
                - cell "4.99" [ref=e127]
                - cell "0.95%" [ref=e128]
              - row [ref=e129]:
                - cell "lightgbm_aci" [ref=e130]
                - cell "Adaptive Conformal Inference (Gibbs & Candès 2021)" [ref=e131]
                - cell "99.4%" [ref=e132]
                - cell "99.63%" [ref=e133]
                - cell "141.58 µg/m³" [ref=e134]
                - cell "141.93" [ref=e135]
                - cell "0.95%" [ref=e136]
              - row [ref=e137]:
                - cell "sage_gamma_0" [ref=e138]
                - cell "SAGE Point Conformal (Gamma=0 MAR)" [ref=e139]
                - cell "99.8%" [ref=e140]
                - cell "99.93%" [ref=e141]
                - cell "13.22 µg/m³" [ref=e142]
                - cell "13.45" [ref=e143]
                - cell "0.95%" [ref=e144]
              - row [ref=e145]:
                - cell "sage_no_abstain" [ref=e146]
                - cell "SAGE Sensitivity Envelope (Always Published)" [ref=e147]
                - cell "99.9%" [ref=e148]
                - cell "99.85%" [ref=e149]
                - cell "16.98 µg/m³" [ref=e150]
                - cell "17.10" [ref=e151]
                - cell "0.00%" [ref=e152]
              - row [ref=e153]:
                - cell "sage_full" [ref=e154]
                - cell "SAGE Sensitivity Envelope + Guarded Policy" [ref=e155]
                - cell "99.9%" [ref=e156]
                - cell "99.96%" [ref=e157]
                - cell "16.98 µg/m³" [ref=e158]
                - cell "17.10" [ref=e159]
                - cell "0.95%" [ref=e160]
        - generic [ref=e161]:
          - heading "SAGE Architectural Ablations (mnar_gamma_1.5, 3-Seed Mean)" [level=3] [ref=e162]
          - table [ref=e168]:
            - rowgroup [ref=e169]:
              - row [ref=e170]:
                - columnheader "Ablation Condition" [ref=e171]
                - columnheader "Overall PICP" [ref=e172]
                - columnheader "Published PICP" [ref=e173]
                - columnheader "Mean Width" [ref=e174]
                - columnheader "Mean D_sens" [ref=e175]
                - columnheader "Abstention" [ref=e176]
            - rowgroup [ref=e177]:
              - row [ref=e178]:
                - cell "full_sage" [ref=e179]
                - cell "99.85%" [ref=e180]
                - cell "99.96%" [ref=e181]
                - cell "16.99 µg/m³" [ref=e182]
                - cell "3.32 µg/m³" [ref=e183]
                - cell "1.09%" [ref=e184]
              - row [ref=e185]:
                - cell "no_sensitivity_grid" [ref=e186]
                - cell "99.78%" [ref=e187]
                - cell "99.92%" [ref=e188]
                - cell "13.22 µg/m³" [ref=e189]
                - cell "0.00 µg/m³" [ref=e190]
                - cell "1.09%" [ref=e191]
              - row [ref=e192]:
                - cell "no_abstention" [ref=e193]
                - cell "99.85%" [ref=e194]
                - cell "99.85%" [ref=e195]
                - cell "16.99 µg/m³" [ref=e196]
                - cell "3.32 µg/m³" [ref=e197]
                - cell "0.00%" [ref=e198]
              - row [ref=e199]:
                - cell "no_spatial_features" [ref=e200]
                - cell "99.85%" [ref=e201]
                - cell "99.96%" [ref=e202]
                - cell "17.91 µg/m³" [ref=e203]
                - cell "4.32 µg/m³" [ref=e204]
                - cell "1.09%" [ref=e205]
              - row [ref=e206]:
                - cell "no_age_features" [ref=e207]
                - cell "99.85%" [ref=e208]
                - cell "99.85%" [ref=e209]
                - cell "17.55 µg/m³" [ref=e210]
                - cell "3.50 µg/m³" [ref=e211]
                - cell "0.00%" [ref=e212]
        - generic [ref=e216]:
          - generic [ref=e217]: Honest Scientific Interpretation & Calibration Limits
          - paragraph [ref=e218]: Under natural Q1 2024 conditions, Persistence (2.00 µg/m³) and LightGBM + CQR (11.35 µg/m³) produce sharper intervals and better Winkler scores than SAGE (17.01 µg/m³ overall). No formal MNAR coverage guarantee is claimed; stale-history intervals (age > 12h) are explicitly withheld by the safety guard rather than delivered as published forecasts.
    - contentinfo [ref=e219]:
      - generic [ref=e220]: "Data Source: UK Department for Environment, Food & Rural Affairs (DEFRA) AURN Q1 2024 (OGL v3)."
      - generic [ref=e221]: "Model: SAGE-LIGHTGBM v1.0.0 • SHA-256 Verified (e552ca09a378…)"
  - alert [ref=e222]
```

# Test source

```ts
  31  |     }
  32  |   });
  33  | 
  34  |   return { consoleErrors, pageErrors, failedRequests, badApiResponses, other404s };
  35  | }
  36  | 
  37  | test.describe("SensorGuard AI Dashboard — Live FastAPI + PostgreSQL E2E", () => {
  38  |   test("loads live station catalog, switches to Manchester Piccadilly, inspects intervals and slices", async ({
  39  |     page,
  40  |   }) => {
  41  |     const diag = attachNetworkDiagnostics(page);
  42  | 
  43  |     await page.goto("/", { waitUntil: "networkidle" });
  44  | 
  45  |     // 1. Live API connectedness (must NOT show demo banner on initial load)
  46  |     await expect(page.getByText("FastAPI Connected")).toBeVisible();
  47  |     await expect(page.getByText("STRICT PILOT MODE: ARCHIVED Q1 2024 UK AURN NETWORK")).toBeVisible();
  48  |     await expect(page.getByText(/DEMO MODE ACTIVE/i)).toHaveCount(0);
  49  | 
  50  |     // 2. Verify all 8 UK AURN station buttons appear in the station selector
  51  |     await expect(page.getByText("UK AURN 8-Station Network (Q1 2024)")).toBeVisible();
  52  |     await expect(page.getByText("8 Stations")).toBeVisible();
  53  |     for (const stName of [
  54  |       "London Harlington - UKA00472",
  55  |       "London Hillingdon - UKA00266",
  56  |       "Westminster - Marylebone Road",
  57  |       "Bexley - Slade Green",
  58  |       "Southampton Centre - UKA00235",
  59  |       "Wicken Fen - UKA00362",
  60  |       "St Osyth - UKA00445",
  61  |       "Manchester Piccadilly - UKA00248",
  62  |     ]) {
  63  |       await expect(page.getByRole("button", { name: new RegExp(stName) }).first()).toBeVisible();
  64  |     }
  65  | 
  66  |     // 3. Verify initial London Harlington forecasts table & non-gap policy indicators
  67  |     await expect(page.getByRole("heading", { name: "London Harlington - UKA00472" })).toBeVisible();
  68  |     await expect(page.getByText("Historical Reliability (Q1 2024)")).toBeVisible();
  69  |     await expect(page.getByText("Target UTC")).toBeVisible();
  70  |     await expect(page.getByText("D_sens")).toBeVisible();
  71  |     await expect(page.getByText("24 timesteps (gaps not filled)")).toBeVisible();
  72  |     await expect(page.getByText("3.32").first()).toBeVisible();
  73  | 
  74  |     // 4. Select Manchester Piccadilly (#2312) and confirm live API reload
  75  |     const manchesterForecastPromise = page.waitForResponse(
  76  |       (res) => res.url().includes("/api/v1/stations/") && res.url().includes("/forecasts") && res.status() === 200
  77  |     );
  78  |     await page.getByRole("button", { name: /Manchester Piccadilly - UKA00248 \(#2312\)/ }).click();
  79  |     await manchesterForecastPromise;
  80  | 
  81  |     await expect(page.getByRole("heading", { name: "Manchester Piccadilly - UKA00248" })).toBeVisible();
  82  |     await expect(page.getByText(/Lat:\s*53\.48/)).toBeVisible();
  83  | 
  84  |     // 5. Select Bexley - Slade Green (#157 HEALTHY) to verify active HEALTHY status + interval table
  85  |     const bexleyForecastPromise = page.waitForResponse(
  86  |       (res) => res.url().includes("/api/v1/stations/") && res.url().includes("/forecasts") && res.status() === 200
  87  |     );
  88  |     await page.getByRole("button", { name: /Bexley - Slade Green \(#157\)/ }).click();
  89  |     await bexleyForecastPromise;
  90  |     await expect(page.getByRole("heading", { name: "Bexley - Slade Green" })).toBeVisible();
  91  | 
  92  |     // 6. Inspect Sensitivity Slice dropdown across envelope -> gamma=2.0 -> gamma=0.0 -> envelope
  93  |     const sliceSelect = page.locator("select").first();
  94  |     await sliceSelect.selectOption("2");
  95  |     await expect(sliceSelect).toHaveValue("2");
  96  |     await sliceSelect.selectOption("0");
  97  |     await expect(sliceSelect).toHaveValue("0");
  98  |     await sliceSelect.selectOption("envelope");
  99  |     await expect(sliceSelect).toHaveValue("envelope");
  100 | 
  101 |     // 7. Assert zero API failures, zero failed requests, zero page errors
  102 |     expect(diag.pageErrors).toEqual([]);
  103 |     expect(diag.failedRequests).toEqual([]);
  104 |     expect(diag.badApiResponses).toEqual([]);
  105 |     // Ignore only browser implicit /favicon.ico 404 if static export omits favicon.ico
  106 |     const nonFavicon404 = diag.other404s.filter((item) => !item.endsWith("/favicon.ico"));
  107 |     expect(nonFavicon404).toEqual([]);
  108 |     const nonFaviconConsoleErrors = diag.consoleErrors.filter(
  109 |       (msg) => !(msg.includes("404") && diag.other404s.every((u) => u.endsWith("/favicon.ico")))
  110 |     );
  111 |     expect(nonFaviconConsoleErrors).toEqual([]);
  112 |   });
  113 | 
  114 |   test("navigates Simulator Lab scenarios, compares research results, and opens Methodology", async ({
  115 |     page,
  116 |   }) => {
  117 |     const diag = attachNetworkDiagnostics(page);
  118 |     await page.goto("/", { waitUntil: "networkidle" });
  119 | 
  120 |     // Switch to Simulator Lab & Ablations tab
  121 |     await page.getByRole("button", { name: "Simulator Lab & Ablations" }).click();
  122 |     await expect(page.getByText("Reliability Simulator Lab & Benchmark Audits")).toBeVisible();
  123 |     await expect(page.getByText("NATURAL OBSERVED")).toBeVisible();
  124 |     await expect(page.getByText("SG-BENCH-20261125-040900")).toBeVisible();
  125 |     await expect(page.getByText("SG-SAGE-ABLATION-20261124-162613")).toBeVisible();
  126 |     await expect(page.getByText("lightgbm_quantile")).toBeVisible();
  127 |     await expect(page.getByText("sage_full")).toBeVisible();
  128 |     await expect(page.getByText("full_sage")).toBeVisible();
  129 | 
  130 |     // Switch to mnar_gamma_1.5 scenario via live backend call
> 131 |     const mnarResp = page.waitForResponse(
      |                           ^ Error: page.waitForResponse: Test timeout of 45000ms exceeded.
  132 |       (res) => res.url().includes("/api/v1/research/experiments/") && res.status() === 200
  133 |     );
  134 |     await page.getByRole("button", { name: "mnar_gamma_1.5" }).click();
  135 |     await mnarResp;
  136 |     await expect(page.getByText(/SIMULATED OUTAGE/i)).toBeVisible();
  137 | 
  138 |     // Switch to burst_24h scenario
  139 |     const burstResp = page.waitForResponse(
  140 |       (res) => res.url().includes("/api/v1/research/experiments/") && res.status() === 200
  141 |     );
  142 |     await page.getByRole("button", { name: "burst_24h" }).click();
  143 |     await burstResp;
  144 |     await expect(page.getByText(/SIMULATED OUTAGE/i)).toBeVisible();
  145 | 
  146 |     // Switch back to natural regime
  147 |     const natResp = page.waitForResponse(
  148 |       (res) => res.url().includes("/api/v1/research/experiments/") && res.status() === 200
  149 |     );
  150 |     await page.getByRole("button", { name: "natural" }).click();
  151 |     await natResp;
  152 |     await expect(page.getByText("NATURAL OBSERVED")).toBeVisible();
  153 | 
  154 |     // Switch to SAGE Methodology tab
  155 |     await page.getByRole("button", { name: "SAGE Methodology" }).click();
  156 |     await expect(page.getByText("SAGE Scientific Methodology")).toBeVisible();
  157 |     await expect(page.getByText(/No unconditional or distribution-free coverage guarantee/i)).toBeVisible();
  158 | 
  159 |     expect(diag.pageErrors).toEqual([]);
  160 |     expect(diag.failedRequests).toEqual([]);
  161 |     expect(diag.badApiResponses).toEqual([]);
  162 |   });
  163 | 
  164 |   test("strictly isolates manual Demo Mode toggle and unreachable-API fallback notice", async ({
  165 |     browser,
  166 |   }) => {
  167 |     // Part A: Explicit Demo Mode toggle in connected session
  168 |     const ctxLive = await browser.newContext();
  169 |     const pageLive = await ctxLive.newPage();
  170 |     await pageLive.goto("/", { waitUntil: "networkidle" });
  171 |     await expect(pageLive.getByText("FastAPI Connected")).toBeVisible();
  172 | 
  173 |     await pageLive.getByRole("button", { name: "Toggle Demo Mode" }).click();
  174 |     await expect(pageLive.getByText("FastAPI Connected")).toHaveCount(0);
  175 |     await expect(pageLive.getByText(/DEMO/i).first()).toBeVisible();
  176 | 
  177 |     // Toggle back to live API mode
  178 |     await pageLive.getByRole("button", { name: "Toggle Demo Mode" }).click();
  179 |     await expect(pageLive.getByText("FastAPI Connected")).toBeVisible();
  180 |     await ctxLive.close();
  181 | 
  182 |     // Part B: Unreachable backend must never claim "FastAPI Connected"
  183 |     const ctxOffline = await browser.newContext();
  184 |     const pageOffline = await ctxOffline.newPage();
  185 |     await pageOffline.route("**/api/**", (route) => route.abort("connectionrefused"));
  186 |     await pageOffline.route("**/health**", (route) => route.abort("connectionrefused"));
  187 | 
  188 |     await pageOffline.goto("/", { waitUntil: "domcontentloaded" });
  189 |     await expect(pageOffline.getByText("FastAPI Connected")).toHaveCount(0);
  190 |     await expect(pageOffline.getByText(/DEMO|Offline|unavailable/i).first()).toBeVisible();
  191 |     await ctxOffline.close();
  192 |   });
  193 | });
  194 | 
```