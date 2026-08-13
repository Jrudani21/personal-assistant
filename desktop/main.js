// KEN Desktop — Electron main process (dual mode).
//
// LOCAL mode (default): runs against the KEN service on this machine.
//   - starts the local backend (ken_service.py) if it isn't up
//   - auto-injects the OWNER token so the owner's own machine opens straight in
//
// REMOTE mode (KEN_URL set to a non-localhost URL, or KEN_REMOTE=1):
//   - does NOT start a local backend and does NOT inject any token
//   - just loads the remote URL; the user signs in with their own
//     username/password (accounts created by the owner)
//   - this is how other people use the owner's DeepSeek key: the key lives on
//     the owner's server, their machine is just a client

const { app, BrowserWindow, Tray, Menu, nativeImage, shell, ipcMain } = require("electron");

// Disable Chromium's GPU sandbox: on some Windows/GPU combos the GPU process
// crashes on launch inside packaged apps, which takes the window down with it.
// The scene still renders (SwiftShader/ANGLE fallback); fixes a blank window.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("no-sandbox");
const { spawn } = require("child_process");
const net = require("net");
const fs = require("fs");
const path = require("path");

// Remote URL override: packaged builds ship ken-config.json either next to the
// exe (dev/unpacked) or in resources/ (installer); env var wins. Empty = local.
let KEN_URL = process.env.KEN_URL || "";
const KEN_PORT = 8756;

function findConfig() {
  const candidates = [
    path.join(path.dirname(process.execPath), "ken-config.json"),        // beside exe
    path.join(path.dirname(process.execPath), "resources", "ken-config.json"), // installer
  ];
  for (const p of candidates) {
    try {
      const cfg = JSON.parse(fs.readFileSync(p, "utf8"));
      if (cfg.url) return cfg.url;
    } catch (e) { /* keep looking */ }
  }
  return "";
}
KEN_URL = KEN_URL || findConfig();

const REMOTE = !!KEN_URL && !/^(http:\/\/)?(127\.0\.0\.1|localhost)(:\d+)?$/.test(KEN_URL);
if (!KEN_URL) KEN_URL = "http://127.0.0.1:" + KEN_PORT;
console.log("[KEN] mode=" + (REMOTE ? "REMOTE (" + KEN_URL + ")" : "LOCAL (" + KEN_URL + ")"));

// Owner token for LOCAL auto sign-in only. Never read or sent in remote mode.
function readOwnerToken() {
  if (REMOTE) return null;
  try {
    const p = path.join(__dirname, "..", "data", ".ken_tokens.json");
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    return data && data.owner ? String(data.owner) : null;
  } catch (e) {
    return null;
  }
}

let win = null;
let tray = null;
let kenProcess = null;
let quitting = false;

// --- ensure the LOCAL backend is up (remote mode never does this) ---------
function isKenUp() {
  return new Promise((resolve) => {
    const s = net.connect(KEN_PORT, "127.0.0.1");
    s.setTimeout(1500);
    s.on("connect", () => { s.destroy(); resolve(true); });
    s.on("error", () => resolve(false));
    s.on("timeout", () => { s.destroy(); resolve(false); });
  });
}

function startKen() {
  if (REMOTE) return;
  const pyw = "C:\\Users\\Janak's PC\\AppData\\Local\\Programs\\Python\\Python312\\pythonw.exe";
  const script = path.join(__dirname, "..", "ken_service.py");
  if (!fs.existsSync(pyw) || !fs.existsSync(script)) {
    console.warn("[KEN] backend launcher not found; expecting the service already running.");
    return;
  }
  kenProcess = spawn(pyw, [script], {
    cwd: path.dirname(script),
    detached: true,
    stdio: "ignore",
  });
  kenProcess.unref();
  console.log("[KEN] started backend (pid " + kenProcess.pid + ")");
}

// --- window ----------------------------------------------------------------
function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 940,
    minHeight: 600,
    title: "KEN",
    backgroundColor: "#0D2F86",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadURL(KEN_URL + "/");

  // LOCAL only: auto sign-in with the owner token. Remote users sign in with
  // their own credentials via the login page.
  if (!REMOTE) {
    const token = readOwnerToken();
    if (token) {
      win.webContents.once("did-finish-load", () => {
        const u = win.webContents.getURL();
        if (!u.includes("token=")) {
          win.loadURL(KEN_URL + "/?token=" + encodeURIComponent(token));
        }
      });
    }
  }

  ipcMain.handle("ken:get-token", () => (REMOTE ? null : readOwnerToken()));

  // Open external links in the OS browser, never inside the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http")) shell.openExternal(url);
    return { action: "deny" };
  });

  // Close-to-tray: hide instead of quit unless we're actually quitting.
  win.on("close", (e) => {
    if (!quitting) {
      e.preventDefault();
      win.hide();
    }
  });

  win.on("closed", () => { win = null; });
}

// --- tray -------------------------------------------------------------------
function createTray() {
  let img = nativeImage.createFromPath(path.join(__dirname, "icon.png"));
  if (img.isEmpty()) img = nativeImage.createEmpty();
  tray = new Tray(img.resize({ width: 16, height: 16 }));
  tray.setToolTip("KEN — personal AI assistant");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open KEN", click: () => { if (win) { win.show(); win.focus(); } } },
    { type: "separator" },
    { label: "Quit", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", () => { if (win) { win.show(); win.focus(); } });
}

// --- lifecycle ---------------------------------------------------------------
app.whenReady().then(async () => {
  if (REMOTE) {
    // No local backend to wait for — straight to the window.
    createWindow();
    createTray();
    return;
  }
  const up = await isKenUp();
  if (!up) startKen();
  let tries = 0;
  const poll = async () => {
    tries++;
    if (await isKenUp()) {
      createWindow();
      createTray();
    } else if (tries < 20) {
      setTimeout(poll, 1000);
    } else {
      console.error("[KEN] backend never came up on :" + KEN_PORT);
      app.quit();
    }
  };
  poll();
});

app.on("before-quit", () => { quitting = true; });
app.on("window-all-closed", () => { /* stay resident in tray */ });
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0 && !win) createWindow();
});
