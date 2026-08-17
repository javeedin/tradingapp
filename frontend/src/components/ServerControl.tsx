import { useEffect, useState } from 'react'
import { api } from '../api'

export default function ServerControl() {
  const [serverRunning, setServerRunning] = useState(false)
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Check server status on mount
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const status = await api.serverStatus()
        setServerRunning(status.running)
        setError(null)
      } catch (err) {
        setError((err as Error).message)
      } finally {
        setLoading(false)
      }
    }

    checkStatus()
    // Poll status every 5 seconds
    const interval = setInterval(checkStatus, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleToggle = async () => {
    setToggling(true)
    setError(null)
    try {
      const action = serverRunning ? api.serverStop : api.serverStart
      const result = await action()
      setServerRunning(result.running)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setToggling(false)
    }
  }

  if (loading) {
    return (
      <div className="panel">
        <div className="panel-head">
          <span>Background Server</span>
        </div>
        <div className="panel-body">
          <div style={{ textAlign: 'center', padding: '12px', color: 'var(--text-dim)' }}>
            Loading status...
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <span>Background Server</span>
        <div className="spacer" />
        <span
          style={{
            fontSize: 12,
            padding: '4px 12px',
            borderRadius: 4,
            background: serverRunning ? 'var(--green)' : 'var(--red)',
            color: 'white',
          }}
        >
          {serverRunning ? '● Running' : '○ Stopped'}
        </span>
      </div>
      <div className="panel-body">
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>
              Trading Cycle Status
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
              {serverRunning
                ? 'Background trading cycle is active'
                : 'Background trading cycle is paused'}
            </div>
          </div>
          <button
            className={serverRunning ? 'danger' : 'primary'}
            onClick={handleToggle}
            disabled={toggling}
            style={{ whiteSpace: 'nowrap' }}
          >
            {toggling ? 'Changing...' : serverRunning ? 'Stop Server' : 'Start Server'}
          </button>
        </div>

        {error && (
          <div className="notice error" style={{ marginTop: 12, marginBottom: 0 }}>
            {error}
          </div>
        )}
      </div>
    </div>
  )
}
