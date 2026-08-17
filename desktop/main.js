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
const backendLog = []

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
    if (!shuttingDown && mainWindow) {
      dialog.showErrorBox(
        'Backend stopped',
        `The Python backend exited unexpectedly (code ${code}).\n\n` +
          `Last output:\n${backendLog.slice(-15).join('')}`,
      )
    }
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

  mainWindow.loadURL(loadingPage('Starting the Python backend…'))

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

function stopBackend() {
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
  startBackend()

  try {
    await waitForBackend()
    if (mainWindow) await mainWindow.loadURL(BASE_URL)
  } catch (err) {
    if (mainWindow) {
      mainWindow.loadURL(loadingPage('Backend failed to start.'))
    }
    dialog.showErrorBox(
      'Backend did not start',
      `${err.message}\n\nLast output:\n${backendLog.slice(-15).join('') || '(no output)'}\n\n` +
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
