const { test, expect } = require("@playwright/test");
const { loginAs } = require("./helpers");

test.describe("Sidebar layout", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page);
  });

  test("sidebar has all nav items", async ({ page }) => {
    const sb = page.locator("#sidebar");
    for (const label of ["New chat", "Chats", "Brain", "Memory", "Tasks", "Docs", "Deep analysis", "Fleet", "Settings", "Log out"]) {
      await expect(sb.getByText(label, { exact: false }).first()).toBeVisible({ timeout: 10_000 });
    }
  });

  test("New chat resets to idle view", async ({ page }) => {
    await page.locator("#sbNewChat").click();
    await expect(page.locator("#idleView")).toBeVisible({ timeout: 10_000 });
  });

  test("Memory panel opens from sidebar", async ({ page }) => {
    await page.locator("#sbMemory").click();
    await expect(page.locator("#memScrim")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("#memBody")).toBeVisible();
    // Close it
    await page.locator('#memScrim [data-close="memScrim"]').click();
    await expect(page.locator("#memScrim")).not.toBeVisible();
  });

  test("Log out returns to the sign-in page", async ({ page }) => {
    await page.locator("#sbLogout").click();
    await expect(page.locator("#pwForm")).toBeVisible({ timeout: 15_000 });
  });
});
