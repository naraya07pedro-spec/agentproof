import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { expect, test } from "@playwright/test";

test("runs both fixtures, inspects stored evidence, compares and replays", async ({ page }, testInfo) => {
  const token = process.env.AGENTPROOF_E2E_TOKEN;
  if (!token) throw new Error("Set AGENTPROOF_E2E_TOKEN to a local fixture workspace token.");

  const source = await readFile(new URL("../../examples/defensive-suite.json", import.meta.url), "utf8");
  const suite = { ...JSON.parse(source), name: `Browser suite ${randomUUID().slice(0, 8)}` };

  await page.goto("/");
  await page.getByLabel("API token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Run history" })).toBeVisible();
  await page.getByLabel("Suite JSON", { exact: true }).fill(JSON.stringify(suite));
  await page.getByRole("button", { name: "Save suite" }).click();
  await expect(page.getByRole("option", { name: suite.name })).toHaveCount(1);

  await page.getByLabel("Fixture agent").selectOption("vulnerable");
  await page.getByRole("button", { name: "Run evaluation" }).click();
  const ledger = page.getByRole("region", { name: "Run history" });
  await expect(ledger.getByRole("row").filter({ hasText: suite.name }).first()).toContainText("Fail");

  const runDetails = page.getByRole("region", { name: "Run details" });
  const failingCase = runDetails.locator("summary").filter({ hasText: "prompt-injection" });
  await expect(failingCase).toBeVisible();
  await expect(failingCase).toContainText("Fail");
  await failingCase.click();
  await expect(runDetails.getByRole("heading", { name: "Assertions", exact: true })).toBeVisible();
  await expect(runDetails.getByRole("heading", { name: "Trace events" })).toBeVisible();
  await expect(runDetails.getByText("deterministic simulation")).toBeVisible();

  await page.getByLabel("Fixture agent").selectOption("corrected");
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await expect(ledger.getByRole("row").filter({ hasText: suite.name }).first()).toContainText("Pass");
  await page.getByLabel("Compare with").selectOption({ index: 1 });
  const comparison = runDetails.getByRole("table", { name: "Case outcomes for both stored runs" });
  await expect(comparison.getByRole("columnheader").nth(1)).toContainText("corrected");
  await expect(comparison.getByRole("columnheader").nth(2)).toContainText("vulnerable");
  const injectionComparison = comparison.getByRole("row").filter({ hasText: "prompt-injection" });
  await expect(injectionComparison.getByRole("cell").nth(1)).toHaveText("Pass");
  await expect(injectionComparison.getByRole("cell").nth(2)).toHaveText("Fail");

  if (process.env.AGENTPROOF_E2E_SCREENSHOT === "1") {
    await page.screenshot({ path: testInfo.outputPath("stored-run-comparison.png"), fullPage: true });
  }

  await runDetails.getByRole("button", { name: "Replay run" }).click();
  await expect(runDetails.getByText("Replayed from", { exact: true })).toBeVisible();
  await expect(ledger.getByRole("row").filter({ hasText: suite.name }).first()).toContainText("Replay");

  await page.reload();
  await expect(page.getByRole("heading", { name: "Connect to AgentProof" })).toBeVisible();
  await page.getByLabel("API token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const storedReplay = ledger.getByRole("row").filter({ hasText: suite.name }).filter({ hasText: "Replay" }).first();
  await expect(storedReplay).toContainText("Pass");
  await storedReplay.getByRole("button").click();
  await expect(runDetails.getByText("Replayed from", { exact: true })).toBeVisible();
});

test("dashboard API rejects invalid routes and oversized bodies", async ({ request }) => {
  const unsupported = await request.get("/api/v1/health", { headers: { Authorization: "Bearer test" } });
  expect(unsupported.status()).toBe(404);

  const unauthorized = await request.get("/api/v1/runs");
  expect(unauthorized.status()).toBe(401);

  const oversized = await request.post("/api/v1/suites", {
    headers: { Authorization: "Bearer test" },
    data: { value: "x".repeat(131_072) },
  });
  expect(oversized.status()).toBe(413);
});

test("dashboard upload deadline closes an incomplete request", async ({ baseURL }) => {
  const target = new URL("/api/v1/suites", baseURL);
  const send = target.protocol === "https:" ? httpsRequest : httpRequest;
  const result = await new Promise<{ status?: number; connection?: string }>((resolve, reject) => {
    const upload = send(target, {
      method: "POST",
      headers: { Authorization: "Bearer test", "Content-Type": "application/json" },
    }, (response) => {
      response.resume();
      response.on("end", () => resolve({ status: response.statusCode, connection: response.headers.connection }));
      response.on("error", reject);
    });
    upload.setTimeout(10_000, () => upload.destroy(new Error("No upload deadline response")));
    upload.on("error", reject);
    upload.write("{");
  });
  expect(result).toEqual({ status: 408, connection: "close" });
});
