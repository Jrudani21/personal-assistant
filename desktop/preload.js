// KEN Desktop preload — exposes the owner token to the renderer so the app
// opens straight into KEN without a sign-in page. The token is read from
// data/.ken_tokens.json (same file the web server uses) and never sent
// anywhere but the local 127.0.0.1 server.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("kenDesktop", {
  isDesktop: true,
  platform: process.platform,
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
  },
  // Returns the owner token (or null) — pulled in the main process.
  getToken: () => ipcRenderer.invoke("ken:get-token"),
});
