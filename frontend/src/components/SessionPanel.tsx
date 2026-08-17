import { useState } from 'react'
import { api } from '../api'

interface Props {
  connected: boolean
  loginUrl?: string
  sessionAgeHours: number | null
  onConnected: () => void
}

/**
 * Breeze session tokens expire daily, so there is no way to keep the bot
 * running unattended across days without pasting a fresh token each morning.
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

  const submit = async () => {
    if (!token.trim()) return
    setBusy(true)
    setMessage(null)
    try {
      const result = await api.submitSession(token.trim())
      setMessage({
        kind: result.backfill_error ? 'warn' : 'success',
        text: result.backfill_error
          ? `Session established, but backfill failed: ${result.backfill_error}`
          : result.message,
      })
      setToken('')
      onConnected()
    } catch (err) {
      setMessage({ kind: 'error', text: (err as Error).message })
    } finally {
      setBusy(false)
    }
  }

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
        {!connected && (
          <div className="notice info">
            The Breeze session token expires every day. Open the login page, sign in, then copy
            the <code>API_Session</code> value from the redirected URL and paste it below.
          </div>
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
            placeholder="Paste today's API_Session token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
          />
          <button className="primary" onClick={submit} disabled={busy || !token.trim()}>
            {busy ? 'Connecting…' : 'Connect'}
          </button>
        </div>
      </div>
    </div>
  )
}
