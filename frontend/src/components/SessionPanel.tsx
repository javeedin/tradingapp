import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'

interface Props {
  connected: boolean
  loginUrl?: string
  sessionAgeHours: number | null
  onConnected: () => void
}

/**
 * Query-string keys Breeze has been observed to use for the session token on
 * the post-login redirect. Matched case-insensitively because the casing is not
 * consistent across Breeze's own documentation.
 */
const TOKEN_PARAM_KEYS = ['apisession', 'api_session', 'session_token', 'sessiontoken']

/**
 * Pull the session token out of the redirect URL, if Breeze put one there.
 *
 * Breeze sends the browser back to the configured redirect URL with the token
 * appended. Pointing that at the dashboard means we can read it directly
 * instead of asking the user to copy it out of the address bar every morning.
 */
function readTokenFromUrl(): string | null {
  const params = new URLSearchParams(window.location.search)
  for (const [key, value] of params.entries()) {
    if (TOKEN_PARAM_KEYS.includes(key.toLowerCase()) && value.trim()) {
      return value.trim()
    }
  }
  return null
}

/** Remove the token from the address bar so it does not linger in history. */
function stripTokenFromUrl(): void {
  const params = new URLSearchParams(window.location.search)
  let changed = false
  for (const key of [...params.keys()]) {
    if (TOKEN_PARAM_KEYS.includes(key.toLowerCase())) {
      params.delete(key)
      changed = true
    }
  }
  if (!changed) return

  const query = params.toString()
  window.history.replaceState(
    {},
    '',
    `${window.location.pathname}${query ? `?${query}` : ''}`,
  )
}

/**
 * Breeze session tokens expire daily, so there is no way to keep the bot
 * running unattended across days without re-authenticating each morning.
 * This panel is that daily ritual.
 */
export default function SessionPanel({
  connected,
  loginUrl,
  sessionAgeHours,
  onConnected,
}: Props) {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null)
  const [autoDetected, setAutoDetected] = useState(false)

  // StrictMode runs effects twice in development. Without this guard the
  // auto-connect would fire two session requests on a single redirect.
  const autoConnectAttempted = useRef(false)

  const connect = useCallback(
    async (value: string, automatic = false) => {
      const trimmed = value.trim()
      if (!trimmed) return

      setBusy(true)
      setMessage(null)
      try {
        const result = await api.submitSession(trimmed)
        setMessage({
          kind: result.backfill_error ? 'warn' : 'success',
          text: result.backfill_error
            ? `Session established, but backfill failed: ${result.backfill_error}`
            : result.message,
        })
        setToken('')
        setAutoDetected(false)
        onConnected()
      } catch (err) {
        setMessage({
          kind: 'error',
          text: automatic
            ? `Auto-connect failed: ${(err as Error).message}. The token is filled in below — try Connect.`
            : (err as Error).message,
        })
        // Keep the token in the box on failure so it can be retried without
        // going through the Breeze login again.
        setToken(trimmed)
      } finally {
        setBusy(false)
      }
    },
    [onConnected],
  )

  useEffect(() => {
    if (autoConnectAttempted.current) return

    const detected = readTokenFromUrl()
    if (!detected) return

    autoConnectAttempted.current = true
    setToken(detected)
    setAutoDetected(true)
    // Clear it from the URL immediately — a refresh should not replay a
    // stale token, and it should not sit in browser history.
    stripTokenFromUrl()

    if (!connected) {
      void connect(detected, true)
    }
    // Intentionally runs once on mount: the redirect token is only ever
    // present on the initial load after returning from Breeze.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Breeze tokens are same-day only; flag one that is getting old.
  const stale = sessionAgeHours !== null && sessionAgeHours > 8

  return (
    <div className="panel">
      <div className="panel-head">
        <span>Breeze Session</span>
        <span className={`badge ${connected ? 'ok' : 'off'}`}>
          <span className="dot" />
          {connected ? 'connected' : 'not connected'}
        </span>
        {connected && sessionAgeHours !== null && (
          <span className="dim" style={{ fontSize: 11, fontWeight: 400 }}>
            {sessionAgeHours.toFixed(1)}h old
          </span>
        )}
      </div>
      <div className="panel-body">
        {!connected && !autoDetected && (
          <div className="notice info">
            The Breeze session token expires every day. Click <strong>Open Breeze login</strong>,
            sign in, and you will be sent back here — the token is picked up from the redirect
            automatically, no copying required.
          </div>
        )}

        {autoDetected && busy && (
          <div className="notice info">Token detected from the Breeze redirect — connecting…</div>
        )}

        {stale && (
          <div className="notice warn">
            This session is {sessionAgeHours!.toFixed(1)} hours old and may expire mid-session.
            Refresh it before the next trading day.
          </div>
        )}

        {message && <div className={`notice ${message.kind}`}>{message.text}</div>}

        <div className="row">
          {loginUrl && (
            <a href={loginUrl} target="_blank" rel="noreferrer">
              <button type="button">Open Breeze login ↗</button>
            </a>
          )}
          <input
            style={{ flex: 1, minWidth: 220 }}
            placeholder="Paste today's API_Session token (or use the login button)"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && connect(token)}
          />
          <button
            className="primary"
            onClick={() => connect(token)}
            disabled={busy || !token.trim()}
          >
            {busy ? 'Connecting…' : 'Connect'}
          </button>
        </div>
      </div>
    </div>
  )
}
