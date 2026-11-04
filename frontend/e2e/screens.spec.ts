import { readFileSync } from "node:fs";

import { expect, type Page, test } from "@playwright/test";

const API = process.env.LANE_API_URL ?? "http://localhost:8000";
const OUT = "../docs/screens";

const TABS = [
  ["brief", ""],
  ["operations", "/operations"],
  ["coverage", "/coverage"],
  ["documents", "/documents"],
  ["audit", "/audit"],
  ["drivers", "/drivers"],
  ["vehicles", "/vehicles"],
  ["conflicts", "/conflicts"],
  ["missing", "/missing"],
  ["rules", "/rules"],
  ["findings", "/findings"],
  ["actions", "/actions"],
] as const;

/** The eval baseline (the processed sample packet), so screenshots always show the same packet. */
async function submissionId(): Promise<string> {
  if (process.env.LANE_SCREEN_SUBMISSION) return process.env.LANE_SCREEN_SUBMISSION;
  if (process.env.LANE_SUBMISSION_ID) return process.env.LANE_SUBMISSION_ID;
  const report = JSON.parse(readFileSync("../evals/report/sample_packet.json", "utf8")) as {
    submission_id?: string;
  };
  if (report.submission_id) return report.submission_id;
  const res = await fetch(`${API}/api/v1/submissions`);
  const rows = (await res.json()) as { id: string; recommendation: string | null }[];
  const done = rows.find((r) => r.recommendation);
  if (!done) throw new Error("No processed submission. Run make run first.");
  return done.id;
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 15_000 });
  await page.waitForTimeout(400);
}

test("inbox and app pages", async ({ page }, info) => {
  for (const [name, path] of [
    ["inbox", "/"],
    ["evals", "/evals"],
    ["rules-library", "/rules-library"],
  ] as const) {
    await page.goto(path);
    await settle(page);
    await page.screenshot({ path: `${OUT}/${info.project.name}/${name}.png`, fullPage: true });
  }
});

test("every submission tab", async ({ page }, info) => {
  const id = await submissionId();
  // LANE_SCREENS=conflicts,documents re-shoots only those tabs
  const only = process.env.LANE_SCREENS?.split(",");
  for (const [name, path] of TABS.filter(([n]) => !only || only.includes(n))) {
    await page.goto(`/submissions/${id}${path}`);
    await settle(page);
    await page.screenshot({ path: `${OUT}/${info.project.name}/${name}.png`, fullPage: true });
  }
});

test("evidence lens", async ({ page }, info) => {
  const id = await submissionId();
  await page.goto(`/submissions/${id}`);
  await settle(page);
  await page.locator("dd [data-evidence]").first().click(); // the first key number
  await page.getByRole("dialog", { name: "Evidence" }).waitFor();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${info.project.name}/lens-page.png` });

  await page.goto(`/submissions/${id}/drivers`);
  await settle(page);
  await page.locator("td [data-evidence]").first().click();
  await page.getByRole("dialog", { name: "Evidence" }).waitFor();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${info.project.name}/lens-drivers.png` });
});

test("privacy", async ({ page }, info) => {
  const id = await submissionId();
  // A license photo: blurred, with the reveal control
  await page.goto(`/submissions/${id}/drivers`);
  await settle(page);
  await page.locator('button[title^="License card for"]').first().click();
  await page.getByRole("dialog", { name: "Evidence" }).waitFor();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${info.project.name}/privacy-license-photo.png` });
  await page.keyboard.press("Escape");

  // A date of birth in a worksheet: the cell and the printed text masked
  const dob = page.locator("[data-evidence]").filter({ hasText: /••••/ }).first();
  if (await dob.count()) {
    await dob.click();
    await page.getByRole("dialog", { name: "Evidence" }).waitFor();
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${OUT}/${info.project.name}/privacy-sheet-dob.png` });
    await page.keyboard.press("Escape");
  }

  // Delete asks for the applicant name
  await page.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").filter({ hasText: "Type" }).waitFor();
  await page.screenshot({ path: `${OUT}/${info.project.name}/privacy-delete.png` });
});

/**
 * Live intake, replayed from a recorded run of the real pipeline (e2e/fixtures/live-events.json),
 * so the animation can be reviewed without reprocessing. Captured mid-run and at the end.
 */
test("live intake", async ({ page }, info) => {
  const id = await submissionId();
  const events = (await import("./fixtures/live-events.json", { with: { type: "json" } }))
    .default as {
    type: string;
    data: unknown;
  }[];
  const body = (list: typeof events) =>
    list.map((e, i) => `id: ${i}\nevent: ${e.type}\ndata: ${JSON.stringify(e.data)}\n\n`).join("") +
    "event: end\ndata: {}\n\n";

  await page.route(`**/api/v1/submissions/${id}`, async (route) => {
    const res = await route.fetch();
    const json = await res.json();
    json.submission.status = "processing";
    await route.fulfill({ response: res, json });
  });

  for (const [name, cut] of [
    ["live-intake-reading", events.findIndex((e) => e.type === "document.extracted")],
    ["live-intake-done", events.length],
  ] as const) {
    await page.unroute("**/events*");
    await page.route("**/events*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: body(events.slice(0, cut)),
      }),
    );
    await page.goto(`/submissions/${id}`);
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/${info.project.name}/${name}.png`, fullPage: true });
  }
});
