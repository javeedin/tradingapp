/**
 * Electron main process.
 *
 * Runs the whole app as one desktop window: it starts the Python backend as a
 * child process, waits for it to answer /api/health, then loads the dashboard
 * that the backend itself serves. Because the UI and the API come from the same
 * origin there is no CORS handling and no dev proxy involved.
 *
 * The backend is owned by this process — it is shut down when the window closes,
 * so quitting the app cannot leave a stray trading process running in the
 * background.
 */

const { app, BrowserWindow, dialog, shell } = require('electron')
const { spawn, execFile } = require('node:child_process')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')

const PROJECT_ROOT = path.resolve(__dirname, '..')
const BACKEND_DIR = path.join(PROJECT_ROOT, 'backend')
const FRONTEND_DIST = path.join(PROJECT_ROOT, 'frontend', 'dist')

// Bound to loopback deliberately: this API can place real orders, so it must
// never listen on a routable interface.
const HOST = '127.0.0.1'
const PORT = Number(process.env.API_PORT || 8000)
const BASE_URL = `http://${HOST}:${PORT}`

// Backend startup budget. Cold starts import pandas/numpy/duckdb, which is
// slow on Windows the first time.
const HEALTH_TIMEOUT_MS = 90_000
const HEALTH_POLL_MS = 400

let backend = null
let mainWindow = null
let shuttingDown = false
// True when a backend was already running and we attached to it rather than
// starting our own. We must not kill a process we did not start.
let adoptedExistingBackend = false
// Whether we spawned a backend, and whether the dashboard ever loaded. Together
// these decide if an exit is a startup failure (reported once, by the startup
// path) or a crash during use (reported by the exit handler).
let backendSpawned = false
let dashboardLoaded = false
let splashLoad = Promise.resolve()
const backendLog = []

// Chromium's ERR_ABORTED. Raised when one navigation supersedes another, which
// is exactly what happens when the dashboard replaces the splash screen — the
// page loads fine, only the abandoned request rejects.
const ERR_ABORTED = -3

/** Remember recent backend output so a startup failure can be shown to the user. */
function recordLog(chunk) {
  const text = chunk.toString()
  backendLog.push(text)
  if (backendLog.length > 200) backendLog.shift()
  process.stdout.write(`[backend] ${text}`)
}

/**
 * Locate a Python interpreter, preferring the project's virtualenv.
 *
 * The venv is checked first so the app uses the environment the dependencies
 * were actually installed into, rather than whatever `python` happens to be on
 * PATH.
 */
function findPython() {
  const candidates =
    process.platform === 'win32'
      ? [
          path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe'),
          path.join(BACKEND_DIR, 'venv', 'Scripts', 'python.exe'),
        ]
      : [
          path.join(BACKEND_DIR, '.venv', 'bin', 'python'),
          path.join(BACKEND_DIR, 'venv', 'bin', 'python'),
        ]

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  // Fall back to PATH; if this is wrong the spawn error surfaces to the user.
  return process.platform === 'win32' ? 'python' : 'python3'
}

function startBackend() {
  const python = findPython()
  console.log(`[desktop] starting backend: ${python} -m app.api.main`)

  backend = spawn(python, ['-m', 'app.api.main'], {
    cwd: BACKEND_DIR,
    env: {
      ...process.env,
      API_HOST: HOST,
      API_PORT: String(PORT),
      PYTHONUNBUFFERED: '1', // so startup logs reach us before a crash
    },
    // Inheriting a console on Windows would pop up a terminal window.
    windowsHide: true,
  })

  backend.stdout.on('data', recordLog)
  backend.stderr.on('data', recordLog)

  backend.on('error', (err) => {
    recordLog(`failed to spawn: ${err.message}\n`)
  })

  backend.on('exit', (code, signal) => {
    console.log(`[desktop] backend exited code=${code} signal=${signal}`)
    backend = null
    // A failure before the dashboard ever loaded is a startup failure; the
    // startup path reports it with a diagnosis. Reporting here too would show
    // the user two dialogs for one problem.
    if (!shuttingDown && dashboardLoaded && mainWindow) {
      dialog.showErrorBox(
        'Backend stopped',
        `The Python backend exited unexpectedly (code ${code}).\n\n` +
          `Last output:\n${backendLog.slice(-15).join('')}`,
      )
    }
  })

  backendSpawned = true
}

/** Single health probe. Resolves true if something is already serving. */
function probeBackend(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const request = http.get(`${BASE_URL}/api/health`, (response) => {
      response.resume()
      resolve(response.statusCode === 200)
    })
    request.on('error', () => resolve(false))
    request.setTimeout(timeoutMs, () => {
      request.destroy()
      resolve(false)
    })
  })
}

/** Resolves once the backend answers, or rejects after the timeout. */
function waitForBackend() {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS

  return new Promise((resolve, reject) => {
    const poll = () => {
      if (shuttingDown) return

      const request = http.get(`${BASE_URL}/api/health`, (response) => {
        response.resume()
        if (response.statusCode === 200) {
          resolve()
        } else {
          retry()
        }
      })

      request.on('error', retry)
      request.setTimeout(2000, () => request.destroy())
    }

    const retry = () => {
      // If the process we started has already died there is nothing to wait
      // for — fail immediately rather than burning the full timeout.
      if (backendSpawned && backend === null) {
        reject(new Error('The Python backend exited during startup'))
        return
      }
      if (Date.now() > deadline) {
        reject(new Error(`Backend did not respond within ${HEALTH_TIMEOUT_MS / 1000}s`))
        return
      }
      setTimeout(poll, HEALTH_POLL_MS)
    }

    poll()
  })
}

/** Minimal splash shown while Python boots, so the window is never blank. */
function loadingPage(message) {
  return `data:text/html;charset=utf-8,${encodeURIComponent(`
    <html><head><meta charset="utf-8"><title>Trading Dashboard</title></head>
    <body style="margin:0;display:flex;align-items:center;justify-content:center;
                 height:100vh;background:#f4f6f9;color:#17202c;
                 font-family:-apple-system,Segoe UI,Roboto,sans-serif">
      <div style="text-align:center">
        <div style="font-size:15px;font-weight:600;margin-bottom:8px">Trading Dashboard</div>
        <div style="font-size:13px;color:#5a6675">${message}</div>
      </div>
    </body></html>`)}`
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 950,
    minWidth: 1000,
    minHeight: 700,
    backgroundColor: '#f4f6f9',
    title: 'Trading Dashboard',
    webPreferences: {
      // The renderer only needs to talk HTTP to localhost — it gets no Node
      // access, which keeps the attack surface the same as an ordinary browser.
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  // Tracked so the dashboard navigation can wait for it to settle. Navigating
  // while this is still in flight aborts it, and the aborted load rejects.
  splashLoad = mainWindow
    .loadURL(loadingPage('Starting the Python backend…'))
    .catch(() => {})

  // External links (the Breeze login) belong in the real browser, not in a
  // frameless Electron window with no address bar.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) shell.openExternal(url)
    return { action: 'deny' }
  })

  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(BASE_URL)) {
      event.preventDefault()
      shell.openExternal(url)
    }
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

/**
 * Navigate the window from the splash to the dashboard.
 *
 * Waits for the splash's own load to settle first — when a backend is already
 * running the health check passes in milliseconds, so without this the two
 * navigations overlap and Chromium aborts one of them. An ERR_ABORTED that
 * still leaves us on the dashboard is treated as success, since the page did
 * load; only the superseded request failed.
 */
async function loadDashboard() {
  await splashLoad

  try {
    await mainWindow.loadURL(BASE_URL)
  } catch (err) {
    const aborted = err && (err.errno === ERR_ABORTED || /\(-3\)/.test(String(err.message)))
    const arrived = mainWindow && mainWindow.webContents.getURL().startsWith(BASE_URL)
    if (!aborted || !arrived) throw err
    console.log('[desktop] ignoring superseded navigation; dashboard loaded')
  }
}

function stopBackend() {
  // Never kill a backend we did not start — the user may be running it in a
  // terminal deliberately, and closing this window should not take it down.
  if (adoptedExistingBackend) return
  if (!backend || backend.killed) return
  const pid = backend.pid
  console.log(`[desktop] stopping backend pid=${pid}`)

  if (process.platform === 'win32') {
    // uvicorn spawns children; SIGTERM alone leaves them running on Windows.
    execFile('taskkill', ['/pid', String(pid), '/T', '/F'], () => {})
  } else {
    backend.kill('SIGTERM')
    // Escalate if it ignores the polite request.
    setTimeout(() => backend && !backend.killed && backend.kill('SIGKILL'), 5000)
  }
}

app.whenReady().then(async () => {
  if (!fs.existsSync(FRONTEND_DIST)) {
    dialog.showErrorBox(
      'Dashboard not built',
      'The frontend has not been built yet.\n\n' +
        'Run this once, from the frontend folder:\n\n    npm install\n    npm run build',
    )
    app.quit()
    return
  }

  createWindow()

  // Attach to a backend that is already running rather than starting a second
  // one. The database is DuckDB, which allows a single writer, so a second
  // instance would crash on startup unable to open the file — and it would
  // take the port too. This makes "already running in a terminal" work instead
  // of failing.
  adoptedExistingBackend = await probeBackend()

  if (adoptedExistingBackend) {
    console.log('[desktop] found a backend already running — attaching to it')
  } else {
    startBackend()
  }

  try {
    await waitForBackend()
    if (mainWindow) {
      await loadDashboard()
      dashboardLoaded = true
    }
  } catch (err) {
    if (mainWindow) {
      mainWindow.loadURL(loadingPage('Backend failed to start.'))
    }

    const output = backendLog.join('')
    // The single-writer conflict is the one failure users actually hit, and
    // its raw traceback buries the cause. Name it directly.
    const locked = /being used by another process|Conflicting lock|already open/i.test(output)

    dialog.showErrorBox(
      locked ? 'Another instance is already running' : 'Backend did not start',
      locked
        ? 'The database is locked by another process.\n\n' +
            'A backend is already running — most likely "python -m app.api.main" ' +
            'in a terminal, or another copy of this app.\n\n' +
            'Close it, then start this app again.'
        : `${err.message}\n\nLast output:\n${backendLog.slice(-15).join('') || '(no output)'}\n\n` +
            'Check that dependencies are installed:\n    pip install -e ".[dev]"',
    )
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})

app.on('window-all-closed', () => {
  shuttingDown = true
  stopBackend()
  if (process.platform !== 'darwin') app.quit()
})

// Backstop: never let the trading backend outlive the app, however it exits.
app.on('before-quit', () => {
  shuttingDown = true
  stopBackend()
})

process.on('exit', stopBackend)
