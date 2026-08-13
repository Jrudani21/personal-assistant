// Shared helpers: login as a real user, build the login URL, etc.
// Credentials come from env (KEN_QA_USER / KEN_QA_PASS); tests never hardcode.

const { expect } = require("@playwright/test");

async function loginAs(page, { user = process.env.KEN_QA_USER || "janak", pass = process.env.KEN_QA_PASS || "" } = {}) {
  if (!pass) throw new Error("KEN_QA_PASS env var required for auth tests");
  await page.goto("/");
  // Sign-in page: either the password form or (if already authed) straight in.
  const pwForm = page.locator("#pwForm");
  if (await pwForm.isVisible().catch(() => false)) {
    await pwForm.locator('input[name="username"]').fill(user);
    await pwForm.locator('input[name="password"]').fill(pass);
    await pwForm.locator('button[type="submit"]').click();
    await page.waitForURL(/\/ken\/?$/, { timeout: 15_000 }).catch(() => {});
    // After a successful login the page reloads into the app; wait for the
    // layout to appear.
    await page.locator("#sidebar").waitFor({ state: "visible", timeout: 15_000 }).catch(() => {});
  }
  // If still on the sign-in page, fail loudly.
  if (await page.locator("#pwForm").isVisible().catch(() => false)) {
    throw new Error("Login failed — still on the sign-in page");
  }
}

module.exports = { loginAs };
