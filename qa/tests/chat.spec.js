const { test, expect } = require("@playwright/test");
const { loginAs } = require("./helpers");

test.describe("Chat", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page);
  });

  test("composer exists and typing a message shows chat view", async ({ page }) => {
    await expect(page.locator("#input")).toBeVisible({ timeout: 10_000 });
    await page.locator("#input").fill("hello");
    await page.locator("#input").press("Enter");
    // Chat view should be visible with the user message
    await expect(page.locator("#chatView")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("#msgs .msg.user").first()).toBeVisible({ timeout: 15_000 });
  });
});
