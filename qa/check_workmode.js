// Force work mode in a headless browser and screenshot both modes.
const { chromium } = require("@playwright/test");

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();

  // Login first (get a session cookie), then load the app.
  await page.goto("http://127.0.0.1:8756/");
  const TEST_USER = process.env.KEN_TEST_USER || "janak";
  const TEST_PW = process.env.KEN_TEST_PASSWORD;
  if (!TEST_PW) { console.error("Set KEN_TEST_PASSWORD before running this script."); process.exit(1); }
  await page.locator("#pwForm input[name=username]").fill(TEST_USER);
  await page.locator("#pwForm input[name=password]").fill(TEST_PW);
  await page.locator("#pwForm button[type=submit]").click();
  await page.waitForSelector("#sidebar", { timeout: 20000 }).catch(() => {});

  // Work mode (forced)
  await page.evaluate(() => localStorage.setItem("ken_mode", "work"));
  await page.reload();
  await page.waitForSelector("#greeting", { timeout: 15000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "$LOCALAPPDATA/ken3d_check/ken_workmode.png".replace("$LOCALAPPDATA", process.env.LOCALAPPDATA) });
  const workInfo = await page.evaluate(() => ({
    workmode: document.body.classList.contains("workmode"),
    standby: document.querySelector("#standbyLabel")?.textContent,
    greeting: document.querySelector("#greeting")?.textContent,
    cardsHidden: getComputedStyle(document.querySelector("#featureGrid")).display,
    btn: document.querySelector("#modeBtn")?.textContent,
  }));
  console.log("WORK MODE:", JSON.stringify(workInfo));

  // Fun mode (forced)
  await page.evaluate(() => localStorage.setItem("ken_mode", "fun"));
  await page.reload();
  await page.waitForSelector("#greeting", { timeout: 15000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: process.env.LOCALAPPDATA + "/ken3d_check/ken_funmode.png" });
  const funInfo = await page.evaluate(() => ({
    workmode: document.body.classList.contains("workmode"),
    standby: document.querySelector("#standbyLabel")?.textContent,
    cardsHidden: getComputedStyle(document.querySelector("#featureGrid")).display,
    btn: document.querySelector("#modeBtn")?.textContent,
  }));
  console.log("FUN MODE:", JSON.stringify(funInfo));

  await browser.close();
})();
