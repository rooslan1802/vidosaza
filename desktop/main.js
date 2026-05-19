const { app, BrowserWindow, dialog } = require("electron");
const { execFile, spawn } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");

let backend = null;
let mainWindow = null;
let logFile = null;

function log(message) {
  const line = `[${new Date().toISOString()}] ${message}\n`;
  console.log(message);
  if (logFile) {
    try {
      fs.appendFileSync(logFile, line);
    } catch (error) {
      console.error(error);
    }
  }
}

function resourcePath(...parts) {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, ...parts);
  }
  return path.join(__dirname, "..", ...parts);
}

function findPython() {
  const candidates = [
    resourcePath("venv", "bin", "python"),
    resourcePath("venv", "bin", "python3"),
    path.join(__dirname, "..", "venv", "bin", "python"),
    "python3",
    "python",
  ];
  return candidates.find((candidate) => {
    if (candidate.includes(path.sep)) return fs.existsSync(candidate);
    return true;
  });
}

function findBackendBinary() {
  const binaryName = process.platform === "win32" ? "video-date-backend.exe" : "video-date-backend";
  const candidates = [
    resourcePath("backend", "video-date-backend", binaryName),
    resourcePath("backend", binaryName),
    path.join(__dirname, "..", "dist", "video-date-backend", binaryName),
    path.join(__dirname, "..", "dist", binaryName),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
    server.on("error", reject);
  });
}

function waitForServer(url, timeoutMs = 90000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      fetch(`${url}/health`)
        .then((response) => {
          if (response.ok) resolve();
          else throw new Error(`HTTP ${response.status}`);
        })
        .catch((error) => {
          if (Date.now() - started > timeoutMs) reject(error);
          else setTimeout(tryOnce, 350);
        });
    };
    tryOnce();
  });
}

function killProcessTree(pid) {
  if (!pid) return;
  if (process.platform === "win32") {
    execFile("taskkill", ["/pid", String(pid), "/T", "/F"], () => {});
    return;
  }
  execFile("pgrep", ["-P", String(pid)], (error, stdout) => {
    if (!error && stdout.trim()) {
      stdout
        .trim()
        .split(/\s+/)
        .forEach((childPid) => killProcessTree(Number(childPid)));
    }
    try {
      process.kill(pid, "SIGTERM");
    } catch (killError) {
      // Already closed.
    }
  });
}

async function startBackend() {
  const port = await getFreePort();
  const backendBinary = findBackendBinary();
  const python = app.isPackaged ? null : findPython();
  const serverPath = resourcePath("web_app", "server.py");
  const cwd = app.isPackaged ? process.resourcesPath : path.join(__dirname, "..");
  const command = backendBinary || python;
  const args = backendBinary ? [] : [serverPath];
  if (!command) {
    throw new Error(`Backend не найден. Лог: ${logFile}`);
  }

  log(`Backend command: ${command}`);
  log(`Backend cwd: ${cwd}`);
  log(`Backend port: ${port}`);

  backend = spawn(command, args, {
    cwd,
    env: {
      ...process.env,
      PORT: String(port),
      DESKTOP_APP: "1",
      PYTHONUNBUFFERED: "1",
      RESOURCE_ROOT: resourcePath(),
      WEB_APP_ROOT: resourcePath("web_app"),
      APP_DATA_DIR: app.getPath("userData"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  backend.stdout.on("data", (data) => log(`[backend stdout] ${data.toString().trim()}`));
  backend.stderr.on("data", (data) => log(`[backend stderr] ${data.toString().trim()}`));
  backend.on("exit", (code, signal) => log(`Backend exited with code ${code}, signal ${signal}`));
  backend.on("error", (error) => log(`Backend spawn error: ${error.message}`));

  const url = `http://127.0.0.1:${port}`;
  log(`Waiting for ${url}`);
  await waitForServer(url);
  log(`Backend ready: ${url}`);
  return url;
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1120,
    height: 840,
    minWidth: 760,
    minHeight: 680,
    title: "Video Date Overlay",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  await mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
    <style>
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0d1112; color: #f4f7f4; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      main { text-align: center; }
      .mark { width: 72px; height: 72px; margin: 0 auto 18px; border-radius: 18px; background: linear-gradient(135deg, #56d7a3, #f3c969); display: grid; place-items: center; color: #07120e; font-size: 46px; font-weight: 900; }
      p { margin: 0; color: #a5b0ad; }
    </style>
    <main><div class="mark">V</div><p>Запускаю обработчик видео...</p></main>
  `)}`);

  const url = await startBackend();
  await mainWindow.loadURL(url);
}

app.whenReady().then(() => {
  logFile = path.join(app.getPath("userData"), "desktop-backend.log");
  try {
    fs.mkdirSync(path.dirname(logFile), { recursive: true });
    fs.writeFileSync(logFile, "");
  } catch (error) {
    console.error(error);
  }
  log(`App packaged: ${app.isPackaged}`);
  log(`Resources path: ${process.resourcesPath}`);
  createWindow().catch((error) => {
    log(`Startup error: ${error.stack || error.message}`);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
        <style>
          body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0d1112; color: #f4f7f4; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
          main { width: min(760px, calc(100vw - 40px)); }
          .mark { width: 72px; height: 72px; margin-bottom: 18px; border-radius: 18px; background: linear-gradient(135deg, #56d7a3, #f3c969); display: grid; place-items: center; color: #07120e; font-size: 46px; font-weight: 900; }
          p { color: #a5b0ad; line-height: 1.45; }
          code { display: block; padding: 12px; border-radius: 8px; background: #151b1c; color: #f3c969; white-space: pre-wrap; }
        </style>
        <main>
          <div class="mark">V</div>
          <h1>Не удалось запустить обработчик видео</h1>
          <p>${escapeHtml(error.message)}</p>
          <p>Файл лога:</p>
          <code>${escapeHtml(logFile || "")}</code>
        </main>
      `)}`).catch(() => {});
    }
    dialog.showErrorBox("Не удалось запустить приложение", `${error.message}\n\nЛог: ${logFile}`);
  });
});

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", () => {
  if (backend && !backend.killed) killProcessTree(backend.pid);
});
