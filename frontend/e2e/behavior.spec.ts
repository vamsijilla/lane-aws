import { readFileSync } from "node:fs";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

/**
 * Behavior tests against the running app (`make dev`), run with `make e2e`. They act on the
 * "duplicated" perturbation, a full copy of the sample packet, so the demo submission and its
 * audit trail are never touched. Resolving a conflict is undone before the test ends.
 */

const API = process.env.LANE_API_URL ?? "http://localhost:8000";

function sandboxId(): string {
  if (process.env.LANE_BEHAVIOR_SUBMISSION) return process.env.LANE_BEHAVIOR_SUBMISSION;
  const report = JSON.parse(readFileSync("../evals/report/perturbations.json", "utf8")) as {
    results: { name: string; submission_id: string }[];
  };
  const found = report.results.find((r) => r.name === "duplicated");
  if (!found) throw new Error("Run make perturb first: the behavior tests use its copy.");
  return found.submission_id;
}

async function token(role: string): Promise<string> {
  const res = await fetch(`${API}/api/v1/auth/demo-login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ role }),
  });
  return ((await res.json()) as { token: string }).token;
}

/** Sign the browser in as a role before the app loads. */
async function signIn(page: Page, role: string) {
  const t = await token(role);
  await page.addInitScript((value) => window.localStorage.setItem("lane.session", value), t);
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 15_000 });
}

const lens = (page: Page) => page.getByRole("dialog", { name: "Evidence" });

test("a date of birth opens the date of birth, not the license number beside it", async ({
  page,
}) => {
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}/drivers`);
  await settle(page);
  const row = page.locator("tr", { has: page.locator("td", { hasText: /^Date of birth$/ }) });
  await row.first().locator("td").last().locator("[data-evidence]").first().click();
  await expect(lens(page)).toBeVisible();
  // The lens names the fact it shows; it must be the birth date field.
  const path = lens(page)
    .locator("dt", { hasText: "Field" })
    .locator("xpath=following-sibling::dd[1]");
  await expect(path).toHaveText(/dob|date_of_birth|birth/i);
  await expect(path).not.toHaveText(/license/i);
  await page.keyboard.press("Escape");
  await expect(lens(page)).toBeHidden();
});

test("conflicts: keyboard moves, a person resolves with a reason, and can reopen", async ({
  page,
}) => {
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}/conflicts`);
  await settle(page);
  const cards = page.locator("ol > li");
  const active = page.locator('ol > li[aria-current="true"]');
  await expect(active).toHaveCount(1);
  // Each conflict is a compact row; its first cell is the field that disagrees
  const titleOf = (row: typeof active) => row.locator("button[aria-expanded] > span").first();
  const first = await titleOf(active).textContent();

  await page.keyboard.press("j");
  await expect(titleOf(active)).not.toHaveText(first ?? "");
  // Enter opens the selected row in place
  await page.keyboard.press("Enter");
  await expect(active.locator("button[aria-expanded]")).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("k");
  await expect(titleOf(active)).toHaveText(first ?? "");

  // E opens the evidence; a number key picks that value and asks why
  await page.keyboard.press("e");
  await expect(lens(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("1");
  await expect(active.getByText(/^Why is .* right\?$/)).toBeVisible();

  const card = cards.first();
  const title = (await titleOf(card).textContent()) ?? "";
  await card.getByRole("button", { name: "Use this value" }).first().click();
  const resolve = card.getByRole("button", { name: "Resolve conflict" });
  await expect(resolve).toBeDisabled(); // a reason is required
  await card.getByPlaceholder(/confirmed with the agent/).fill("Behavior test, reopened below");
  await resolve.click();
  await expect(page.getByText(/Rules ran again after/)).toBeVisible();

  const settled = page.locator("section", { hasText: "Settled" }).locator("li", { hasText: title });
  await expect(settled).toBeVisible();
  await settled.getByRole("button", { name: "Reopen" }).click();
  await expect(settled).toHaveCount(0);
  await expect(page.locator("ol > li", { hasText: title })).toHaveCount(1);
});

test("roles: a viewer cannot reveal or resolve, ops cannot see pages", async ({ page }) => {
  const id = sandboxId();
  await signIn(page, "viewer");
  await page.goto(`/submissions/${id}/drivers`);
  await settle(page);
  // License numbers stay masked in every column
  await expect(page.locator("td", { hasText: /\w{3}•••\w{3}/ }).first()).toBeVisible();
  const row = page.locator("tr", { has: page.locator("td", { hasText: /^License number$/ }) });
  await row.first().locator("td").last().locator("[data-evidence]").first().click();
  await expect(lens(page).getByText("Personal data hidden for this role")).toBeVisible();
  await expect(lens(page).getByRole("button", { name: "Reveal" })).toHaveCount(0);
  await page.keyboard.press("Escape");

  await page.goto(`/submissions/${id}/conflicts`);
  await settle(page);
  await expect(page.locator("ol > li").first()).toBeVisible(); // the conflicts are shown...
  await expect(page.getByRole("button", { name: "Use this value" })).toHaveCount(0); // ...not actionable

  // Ops runs the queue but never sees document pages
  const pages = (await (
    await fetch(`${API}/api/v1/submissions/${id}/pages`, {
      headers: { authorization: `Bearer ${await token("underwriter")}` },
    })
  ).json()) as { id: string }[];
  const image = await fetch(`${API}/api/v1/pages/${pages[0].id}/image`, {
    headers: { authorization: `Bearer ${await token("ops")}` },
  });
  expect(image.status).toBe(403);
});

/** Sign in from the sign-in screen as one of the demo's people. */
async function signInOnScreen(page: Page, person: RegExp) {
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByRole("button", { name: person }).click();
  await settle(page);
}

/** Switch: the whole cache goes, the page reloads on the sign-in screen. */
async function switchPerson(page: Page) {
  await page.getByRole("button", { name: "Switch" }).click();
  await page.waitForLoadState("load");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
}

test("switch: viewer to underwriter and back, on the same Brief", async ({ page }) => {
  const res = await fetch(`${API}/api/v1/submissions`, {
    headers: { authorization: `Bearer ${await token("underwriter")}` },
  });
  const id = ((await res.json()) as { id: string }[])[0].id;
  const decisions = page.getByRole("group", { name: "Decision" });
  const revealOnDrivers = async () => {
    await page.goto(`/submissions/${id}/drivers`);
    await settle(page);
    const row = page.locator("tr", { has: page.locator("td", { hasText: /^License number$/ }) });
    await row.first().locator("td").last().locator("[data-evidence]").first().click();
    // Wait for the lens to show the value: a Reveal button, or the masked note for this role
    await expect(
      lens(page)
        .getByRole("button", { name: "Reveal" })
        .or(lens(page).getByText("Personal data hidden for this role")),
    ).toBeVisible();
    const n = await lens(page).getByRole("button", { name: "Reveal" }).count();
    await page.keyboard.press("Escape");
    return n;
  };

  await page.goto(`/submissions/${id}`);
  await signInOnScreen(page, /Guest reviewer, Viewer/);
  await expect(decisions).toHaveCount(0);
  expect(await revealOnDrivers()).toBe(0);

  await switchPerson(page);
  await signInOnScreen(page, /Priya Shah, Underwriter/);
  await expect(page.getByText("Priya Shah, Underwriter")).toBeVisible();
  await page.goto(`/submissions/${id}`);
  await settle(page);
  await expect(decisions).toBeVisible();
  expect(await revealOnDrivers()).toBe(1);

  await switchPerson(page);
  await signInOnScreen(page, /Guest reviewer, Viewer/);
  await page.goto(`/submissions/${id}`);
  await settle(page);
  await expect(decisions).toHaveCount(0);
  expect(await revealOnDrivers()).toBe(0);
});

test("an underwriter can reveal, and the reveal is recorded", async ({ page }) => {
  const id = sandboxId();
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${id}/drivers`);
  await settle(page);
  const row = page.locator("tr", { has: page.locator("td", { hasText: /^License number$/ }) });
  await row.first().locator("td").last().locator("[data-evidence]").first().click();
  await lens(page).getByRole("button", { name: "Reveal" }).click();
  await expect(lens(page).getByText("Reveal recorded in the audit trail")).toBeVisible();
  await expect(lens(page).locator(".font-mono.font-semibold").first()).not.toHaveText(/•/);
});

/** Resolve the "Owner drives" conflict to one of its values, with a reason. */
async function resolveOwnerDrives(page: Page, value: "No" | "Yes") {
  const card = page.locator("ol > li", { has: page.locator("h3", { hasText: /^Owner drives$/ }) });
  const index = value === "No" ? 0 : 1;
  await card.getByRole("button", { name: "Use this value" }).nth(index).click();
  await card
    .getByPlaceholder(/confirmed with the agent/)
    .fill(`Behavior test: owner drives ${value}`);
  await card.getByRole("button", { name: "Resolve conflict" }).click();
}

async function reopenOwnerDrives(page: Page) {
  const settled = page.locator("section", { hasText: "Settled" }).locator("li", {
    hasText: "Owner drives",
  });
  await settled.getByRole("button", { name: "Reopen" }).click();
  await expect(settled).toHaveCount(0);
}

test("gate: owner drives changes DRV-01 and the route live, both ways", async ({ page }) => {
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}/conflicts`);
  await settle(page);

  // The owner does not drive: the owner is excluded, DRV-01 passes
  await resolveOwnerDrives(page, "No");
  const panel = page.locator('[aria-live="polite"]', { hasText: "Rules ran again" });
  await expect(panel).toContainText("DRV-01");
  await expect(panel).toContainText("Pass");
  // The diff follows the person to the rules tab, where DRV-01 is marked
  await page.locator('a[href$="/rules"]').first().click();
  await expect(page.locator("li.rule-changed", { hasText: "DRV-01" })).toContainText(
    "was potential decline",
  );
  await expect(page.getByRole("button", { name: /Changed by the last rerun 1/ })).toBeVisible();
  await page.locator('a[href$="/conflicts"]').first().click();
  await reopenOwnerDrives(page);

  // The owner drives without a CDL: the header sign turns to decline
  await resolveOwnerDrives(page, "Yes");
  await expect(page.getByRole("status", { name: /Recommendation: Decline/ }).first()).toBeVisible();
  await reopenOwnerDrives(page);
  await expect(page.getByRole("status", { name: /Recommendation: Refer/ }).first()).toBeVisible();
});

test("an underwriter corrects a value with a reason, sees the rerun, and undoes it", async ({
  page,
}) => {
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}/operations`);
  await settle(page);
  const value = page
    .locator("div", { has: page.locator("dt", { hasText: /^Years in business$/ }) })
    .last()
    .locator("dd [data-evidence]");
  await value.click();
  await lens(page).getByRole("button", { name: "Correct this value" }).click();
  await lens(page)
    .getByPlaceholder(/Enter a number/)
    .fill("7");
  await lens(page)
    .getByPlaceholder(/the agent sent/)
    .fill("Behavior test: agent confirmed");
  await lens(page).getByRole("button", { name: "Correct and rerun" }).click();
  await expect(page.locator('[aria-live="polite"]', { hasText: "Rules ran again" })).toContainText(
    "corrected",
  );
  await expect(lens(page).getByText("Corrected to 7")).toBeVisible();
  await expect(value).toContainText("Corrected");

  await lens(page).getByRole("button", { name: "Undo correction" }).click();
  await expect(lens(page).getByRole("button", { name: "Correct this value" })).toBeVisible();
  await expect(value).not.toContainText("Corrected");
});

test("replay reproduces the stored output exactly, from the cache", async ({ page }) => {
  test.setTimeout(180_000); // every page is read again, OCR included
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}/audit`);
  await settle(page);
  await page.getByRole("button", { name: "Replay this submission" }).click();
  const latest = page.locator("main ul > li").first();
  await expect(latest).toContainText("Replay");
  await expect(latest).toContainText("Output identical", { timeout: 150_000 });
  await expect(latest).toContainText("$0.00");
  await expect(page.getByText(/Replay: output identical/).first()).toBeVisible();
});

test("a document read by a known layout shows its landmarks on the page", async ({ page }) => {
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}/documents`);
  await settle(page);
  await page.locator("main button", { hasText: "AL 23-24.pdf" }).first().click();
  await expect(page.getByText("Known layout").first()).toBeVisible();
  await page.getByRole("button", { name: "Show landmarks" }).first().click();
  await expect(lens(page)).toBeVisible();
  await expect(
    lens(page).getByText(/Recognised as .* landmarks, with no model call/),
  ).toBeVisible();
  await expect(lens(page).locator("svg rect, svg path").first()).toBeVisible(); // boxes drawn
});

/** Every page's text, as rendered, with collapsed sections opened. */
const PAGES = [
  "",
  "/operations",
  "/coverage",
  "/documents",
  "/drivers",
  "/vehicles",
  "/conflicts",
  "/missing",
  "/rules",
  "/findings",
  "/actions",
  "/audit",
];

const PROBLEMS: [string, RegExp][] = [
  ["[object Object]", /\[object Object\]/],
  ["a '?' placeholder", /(^|\s)\?(\s|$)|\?-\?/m],
  ["a doubled space", /\S {2,}\S/],
  ["a space before a comma", / ,/],
];

test("no placeholder or stray punctuation in any rendered page", async ({ page }) => {
  const rows = (await (await fetch(`${API}/api/v1/submissions`)).json()) as { id: string }[];
  const paths = [
    "/",
    "/evals",
    "/rules-library",
    ...PAGES.map((p) => `/submissions/${rows[0].id}${p}`),
  ];
  const found: string[] = [];
  for (const path of paths) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(600);
    await page.evaluate(() => {
      document.querySelectorAll("details:not([open])").forEach((d) => d.setAttribute("open", ""));
      document.querySelectorAll("button").forEach((b) => {
        const t = (b.textContent ?? "").trim();
        if (/^Show \d+ more/.test(t)) b.click();
      });
    });
    await page.waitForTimeout(300);
    const text = await page.locator("main").innerText();
    for (const [what, re] of PROBLEMS) {
      for (const line of text.split("\n")) {
        if (re.test(line)) found.push(`${path}: ${what}: ${line.trim().slice(0, 120)}`);
      }
    }
  }
  expect(found, found.join("\n")).toEqual([]);
});

test("the submission header steps aside on scroll and the tabs stay in one row", async ({
  page,
}) => {
  await signIn(page, "underwriter");
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/submissions/${sandboxId()}`);
  await settle(page);
  const bar = page.locator("[data-compact]");
  const views = page.getByRole("navigation", { name: "Submission views" });
  const topbar = page.locator("#topbar-context");
  await expect(bar).toHaveCount(0);
  await expect(topbar).toHaveText("");

  await page.mouse.wheel(0, 900);
  await expect(bar).toHaveCount(1);
  await expect(topbar.getByRole("status")).toBeVisible(); // the route sign moved up
  const height = await bar.evaluate((el) => el.getBoundingClientRect().height);
  expect(height).toBeLessThanOrEqual(96);
  const overflow = await views.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(overflow).toBe(0);

  await page.mouse.wheel(0, -2000);
  await expect(bar).toHaveCount(0);
  await expect(topbar).toHaveText("");
});

test("command palette: a rule id and a conflict open their place; ? and G then a letter", async ({
  page,
}) => {
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}`);
  await settle(page);
  const palette = page.getByRole("dialog", { name: "Go to" });

  await page.keyboard.press("ControlOrMeta+k");
  await expect(palette).toBeVisible();
  await palette.getByRole("combobox").fill("DRV-01");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/rules#DRV-01$/);
  await expect(page.locator("#DRV-01")).toBeInViewport();

  await page.keyboard.press("ControlOrMeta+k");
  await palette.getByRole("combobox").fill("owner drives");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/conflicts#conflict-/);
  await expect(page.locator('li[aria-current="true"] h3')).toHaveText("Owner drives");

  await page.keyboard.press("g");
  await page.keyboard.press("r");
  await expect(page).toHaveURL(/\/rules$/);
  await page.keyboard.press("Shift+?");
  await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
});

test("accessibility: no serious or critical axe violation on any page, light and dark", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await signIn(page, "underwriter");
  const id = sandboxId();
  const paths = [
    "/",
    "/evals",
    "/rules-library",
    "/ops",
    ...PAGES.map((p) => `/submissions/${id}${p}`),
  ];
  const found: string[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const path of paths) {
      await page.goto(path);
      await settle(page);
      const result = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      for (const v of result.violations) {
        if (v.impact !== "serious" && v.impact !== "critical") continue;
        const where = v.nodes
          .slice(0, 3)
          .map((n) => n.target.join(" "))
          .join(" | ");
        found.push(`${scheme} ${path}: ${v.id} (${v.nodes.length}): ${where}`);
      }
    }
  }
  expect(found, found.join("\n")).toEqual([]);
});

test("a value worked out by code opens the facts it came from, with a box on each", async ({
  page,
}) => {
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}/conflicts`);
  await settle(page);
  const card = page.locator("ol > li", { has: page.locator("h3", { hasText: /^Owner drives$/ }) });
  // The "Yes" side is not printed anywhere: code works it out from the driver schedules
  await card.locator("button[data-evidence]", { hasText: /^Yes/ }).first().click();
  await expect(lens(page)).toBeVisible();
  await expect(lens(page)).toContainText("Worked out by code from these facts");
  // A spreadsheet cell or a box on a page marks the fact it was worked out from
  await expect(lens(page).locator(".lens-cell, rect.lens-draw").first()).toBeVisible({
    timeout: 15_000,
  });
});

test("a rule opens from the Brief into the Rules panel, and its status explains itself by keyboard", async ({
  page,
}) => {
  await signIn(page, "underwriter");
  await page.goto(`/submissions/${sandboxId()}`);
  await settle(page);
  await page.getByRole("link", { name: /^DRV-01 / }).click();
  await expect(page).toHaveURL(/\/rules#DRV-01$/);
  const panel = page.getByRole("complementary", { name: "Rule detail" });
  await expect(panel.getByRole("heading", { name: "Driver CDLs" })).toBeVisible();
  for (const part of [
    "What it checks",
    "The guideline says",
    "What Lane found",
    "What would change it",
    "Based on",
  ])
    await expect(panel.getByRole("heading", { name: part })).toBeVisible();
  // The status word in the panel is reachable by keyboard and says what it means
  await panel.locator('span[tabindex="0"]').first().focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("tooltip")).toContainText(/decline/i);
});
