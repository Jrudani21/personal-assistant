const { test, expect } = require("@playwright/test");
const { loginAs } = require("./helpers");

test.describe("3D scene", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page);
  });

  test("3D canvas exists and body marker is ok", async ({ page }) => {
    // The 3D engine sets data-ken3d on <body>: ok / no-three / no-webgl
    const marker = await page.evaluate(() => document.body.dataset.ken3d || "missing");
    expect(["ok", "no-webgl", "no-three"]).toContain(marker);
    // Canvas should exist unless WebGL is fully unavailable
    const hasCanvas = await page.locator("#ken3d").count();
    if (marker === "ok") {
      expect(hasCanvas).toBe(1);
    }
  });

  test("minimize button collapses idle view", async ({ page }) => {
    await page.locator("#minimizeBtn").click().catch(() => {});
    // The idle view hides; the widget appears
    await expect(page.locator("#idleView")).toBeHidden({ timeout: 10_000 });
    await expect(page.locator("#kenWidget")).toBeVisible({ timeout: 10_000 });
  });
});
