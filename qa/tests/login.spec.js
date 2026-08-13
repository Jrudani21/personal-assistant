const { test, expect } = require("@playwright/test");
const { loginAs } = require("./helpers");

test.describe("Login / auth", () => {
  test("sign-in page renders with password form", async ({ page }) => {
    await page.goto("/");
    // Unauthenticated -> 401 sign-in page with both forms
    await expect(page.locator("#pwForm")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("#tokenForm")).toBeAttached();
    await expect(page.locator("#pwForm input[name=username]")).toBeVisible();
    await expect(page.locator("#pwForm input[name=password]")).toBeVisible();
  });

  test("password login reaches the app", async ({ page }) => {
    await loginAs(page);
    await expect(page.locator("#sidebar")).toBeVisible({ timeout: 15_000 });
    // The greeting should show (idle view)
    await expect(page.locator("#greeting")).toBeVisible({ timeout: 15_000 });
  });

  test("bad password shows an error, not a crash", async ({ page }) => {
    await page.goto("/");
    const pwForm = page.locator("#pwForm");
    await pwForm.locator('input[name="username"]').fill("janak");
    await pwForm.locator('input[name="password"]').fill("definitely-wrong");
    await pwForm.locator('button[type="submit"]').click();
    // Should show the inline error text, not navigate away
    await expect(page.locator("#pwForm .err")).toBeVisible({ timeout: 10_000 });
  });
});
